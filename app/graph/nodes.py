import hashlib
import json
from typing import Any

from app.dsl import parse, print_dsl, validate
from app.gate import check as gate_check
from app.graph.state import MEMORY_BUDGET_TOKENS
from app.llm import chaos
from app.llm.mock import ProviderDown, ProviderRateLimit, ProviderTimeout, call as mock_call
from app.memory.defense import build_request, enforce_locked, quarantine, taint_check
from app.memory.grounding import ground, has_ungrounded
from app.memory.store import retrieve as memory_retrieve
from app.pricing import cost_of_attempt, worst_case_cost


def _get_seed(job_id: str) -> int:
    return int(hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:8], 16)


def _check_cancel(conn_read: Any, job_id: str) -> bool:
    with conn_read.cursor() as cur:
        cur.execute("SELECT cancel_requested FROM jobs WHERE id = %s;", (job_id,))
        row = cur.fetchone()
        return bool(row and row[0])


def intent_node(conn_read: Any, job: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize user idea, parse current_dsl, lock filled axes, and gate the idea."""
    new_state = dict(state)
    body = new_state.get("request") or job.get("body") or {}
    idea = body.get("idea", "").strip()
    current_dsl = body.get("current_dsl", "")
    only_axis = body.get("only_axis")

    locked_axes: dict[str, str] = {}
    if current_dsl:
        parsed_axes, _ = parse(current_dsl)
        if only_axis:
            # When only_axis is set, all axes EXCEPT only_axis are locked
            for k, v in parsed_axes.items():
                if k != only_axis and v:
                    locked_axes[k] = v
        else:
            # All filled axes in current_dsl are locked
            for k, v in parsed_axes.items():
                if v:
                    locked_axes[k] = v

    new_state["request"] = {"idea": idea, "current_dsl": current_dsl, "only_axis": only_axis}
    new_state["locked_axes"] = locked_axes

    # Gate on the idea at intake
    gate_res = gate_check({"idea": idea})
    if not gate_res.allowed:
        new_state["next_node"] = "finalize"
        new_state["outcome"] = "blocked"
        new_state["reason_codes"] = [b["code"] for b in gate_res.blocks]
        new_state["rewrites"] = [b["rewrite"] for b in gate_res.blocks]
        new_state["best_valid"] = None
        return new_state, [], []

    new_state["next_node"] = "retrieve"
    return new_state, [], []


def retrieve_node(conn_read: Any, job: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Retrieve memory items using project_id from the job row only, quarantine, and build request."""
    new_state = dict(state)
    project_id = job["project_id"]
    idea = new_state["request"]["idea"]

    items = memory_retrieve(conn_read, project_id, idea, budget_tokens=MEMORY_BUDGET_TOKENS)
    clean_items, quarantined_ids, _ = quarantine(items)

    new_state["retrieved"] = items
    new_state["clean_items"] = clean_items
    new_state["quarantined_ids"] = quarantined_ids
    new_state["quarantined_bodies"] = [it["body"] for it in items if it["id"] in quarantined_ids]

    model_req = build_request(
        idea,
        new_state["locked_axes"],
        clean_items,
        mode="full",
        reason_codes=new_state.get("reason_codes", []),
    )
    new_state["model_request"] = model_req
    new_state["next_node"] = "draft"
    return new_state, [], []


def _execute_call_with_retries(
    conn_read: Any,
    job: dict[str, Any],
    state: dict[str, Any],
    node_name: str,
    call_idx: int,
    model: str,
    req_payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
    """Execute provider call with retry loop (tries 0, 1, 2) and call_key fencing."""
    job_id = job["id"]
    seed = _get_seed(job_id)
    chaos_map = chaos.get_all(conn_read)
    cap = job.get("cost_cap_micro", 80_000)
    spent = state.get("spent_micro", 0)

    # Pre-call cap check
    if spent + worst_case_cost(model) > cap:
        return None, [], 0

    attempts: list[dict[str, Any]] = []
    response_data: dict[str, Any] | None = None
    cost_added = 0

    for try_idx in range(3):
        call_key = f"{node_name}:{call_idx}:{try_idx}"

        # 1. Reuse existing recorded call
        with conn_read.cursor() as cur:
            cur.execute(
                "SELECT outcome, cost_micro, response FROM llm_calls WHERE job_id = %s AND call_key = %s;",
                (job_id, call_key),
            )
            row = cur.fetchone()
            if row:
                outcome, stored_cost, stored_resp = row
                if outcome in ("valid", "malformed_json", "invalid_dsl"):
                    return stored_resp, [], 0
                # If stored row was rate_limit or timeout, move to next try
                continue

        # Count previous attempts for this model in this job
        with conn_read.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM llm_calls WHERE job_id = %s AND model = %s;",
                (job_id, model),
            )
            model_attempt_idx = cur.fetchone()[0] + len(
                [a for a in attempts if a["model"] == model]
            )

        cancel_fn = lambda: _check_cancel(conn_read, job_id)

        try:
            res = mock_call(
                model=model,
                request=req_payload,
                seed=seed,
                attempt=model_attempt_idx,
                chaos=chaos_map,
                timeout_s=10.0,
                should_cancel=cancel_fn,
            )
            outcome = res.get("outcome", "valid")
            tokens_in = res.get("tokens_in", 0)
            tokens_out = res.get("tokens_out", 0)
            cost = cost_of_attempt(model, tokens_in, tokens_out, outcome)
            cost_added += cost

            resp_payload = {
                "text": res.get("text", ""),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "outcome": outcome,
            }
            attempts.append(
                {
                    "call_key": call_key,
                    "model": model,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost_micro": cost,
                    "outcome": outcome,
                    "response": resp_payload,
                }
            )
            response_data = resp_payload
            break

        except ProviderRateLimit as e:
            cost = cost_of_attempt(model, e.tokens_in, 0, "rate_limit")
            cost_added += cost
            attempts.append(
                {
                    "call_key": call_key,
                    "model": model,
                    "tokens_in": e.tokens_in,
                    "tokens_out": 0,
                    "cost_micro": cost,
                    "outcome": "rate_limit",
                    "response": {"error": "ProviderRateLimit 429"},
                }
            )
            continue

        except ProviderTimeout as e:
            cost = cost_of_attempt(model, e.tokens_in, 0, "timeout")
            cost_added += cost
            attempts.append(
                {
                    "call_key": call_key,
                    "model": model,
                    "tokens_in": e.tokens_in,
                    "tokens_out": 0,
                    "cost_micro": cost,
                    "outcome": "timeout",
                    "response": {"error": "ProviderTimeout"},
                }
            )
            continue

        except ProviderDown as e:
            cost = cost_of_attempt(model, e.tokens_in, 0, "down")
            cost_added += cost
            attempts.append(
                {
                    "call_key": call_key,
                    "model": model,
                    "tokens_in": e.tokens_in,
                    "tokens_out": 0,
                    "cost_micro": cost,
                    "outcome": "down",
                    "response": {"error": "ProviderDown"},
                }
            )
            break

    return response_data, attempts, cost_added


def draft_node(conn_read: Any, job: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute cheap draft model call."""
    new_state = dict(state)
    cap = job.get("cost_cap_micro", 80_000)
    spent = new_state.get("spent_micro", 0)

    if spent + worst_case_cost("cheap") > cap:
        new_state["flags"] = list(new_state.get("flags", [])) + ["cost_cap"]
        if new_state.get("best_valid"):
            new_state["best_valid"]["flags"] = list(new_state["best_valid"].get("flags", [])) + ["cost_cap"]
        new_state["next_node"] = "finalize"
        return new_state, [], []

    resp, attempts, cost_added = _execute_call_with_retries(
        conn_read,
        job,
        new_state,
        node_name="draft",
        call_idx=0,
        model="cheap",
        req_payload=new_state["model_request"],
    )

    new_state["spent_micro"] = spent + cost_added
    new_state["model_calls_count"] = new_state.get("model_calls_count", 0) + 1
    new_state["current_candidate"] = resp
    new_state["next_node"] = "check"
    return new_state, attempts, []


def check_node(conn_read: Any, job: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate candidate: parse JSON -> parse DSL & validate -> enforce locked -> grounding -> taint check -> 8-axis gate."""
    new_state = dict(state)
    candidate_resp = new_state.get("current_candidate")
    retrieved = new_state.get("retrieved", [])
    idea = new_state["request"]["idea"]
    clean_items = new_state.get("clean_items", [])
    quarantined_bodies = new_state.get("quarantined_bodies", [])
    locked_axes = new_state.get("locked_axes", {})

    is_valid = False
    is_blocked = False
    axes: dict[str, str] = {}
    confidence = 0.0
    citations: list[tuple[str, str]] = []

    if candidate_resp and candidate_resp.get("text"):
        try:
            data = json.loads(candidate_resp["text"])
            confidence = float(data.get("confidence", 0.0))
            dsl_text = data.get("dsl", "")

            parsed_axes, parse_errs = parse(dsl_text)
            if not parse_errs:
                val_errs = validate(parsed_axes)
                if not val_errs:
                    # Enforce locked axes
                    axes, _ = enforce_locked(parsed_axes, locked_axes)

                    # Grounding check
                    cont_val = axes.get("continuity", "")
                    if has_ungrounded(cont_val, retrieved):
                        new_state["ungrounded_before"] = new_state.get("ungrounded_before", 0) + 1

                    grounded_cont, cites, _ = ground(cont_val, retrieved)
                    axes["continuity"] = grounded_cont
                    citations = cites

                    if has_ungrounded(grounded_cont, retrieved):
                        new_state["ungrounded_after"] = new_state.get("ungrounded_after", 0) + 1

                    # Taint check
                    ok, _ = taint_check(axes, idea, clean_items, quarantined_bodies)
                    if ok:
                        # Gate check on all 8 axes
                        gate_res = gate_check(axes)
                        if gate_res.allowed:
                            is_valid = True
                        else:
                            is_blocked = True
                            new_state["outcome"] = "blocked"
                            new_state["reason_codes"] = [b["code"] for b in gate_res.blocks]
                            new_state["rewrites"] = [b["rewrite"] for b in gate_res.blocks]
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            is_valid = False

    calls_count = new_state.get("model_calls_count", 1)

    if is_valid:
        valid_candidate = {
            "axes": axes,
            "dsl": print_dsl(axes),
            "citations": citations,
            "confidence": confidence,
            "flags": list(new_state.get("flags", [])),
        }
        new_state["best_valid"] = valid_candidate

        if confidence >= 0.5:
            new_state["next_node"] = "finalize"
        else:
            # Low confidence: escalate straight to strong repair if calls remain
            if calls_count < 3:
                new_state["escalate_strong"] = True
                new_state["next_node"] = "repair"
            else:
                new_state["next_node"] = "finalize"
    else:
        # Invalid or blocked candidate
        if calls_count < 3:
            new_state["next_node"] = "repair"
        else:
            new_state["next_node"] = "finalize"

    return new_state, [], []


def repair_node(conn_read: Any, job: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute cheap repair or strong repair model call."""
    new_state = dict(state)
    calls_count = new_state.get("model_calls_count", 1)

    # Determine model: call 2 is cheap repair (or strong if escalated); call 3 is strong repair
    if new_state.get("escalate_strong") or calls_count == 2:
        model = "strong"
    else:
        model = "cheap"

    cap = job.get("cost_cap_micro", 80_000)
    spent = new_state.get("spent_micro", 0)

    # Cap check
    if spent + worst_case_cost(model) > cap:
        new_state["flags"] = list(new_state.get("flags", [])) + ["cost_cap"]
        if new_state.get("best_valid"):
            new_state["best_valid"]["flags"] = list(new_state["best_valid"].get("flags", [])) + ["cost_cap"]
        new_state["next_node"] = "finalize"
        return new_state, [], []

    # Build repair request carrying only error codes and reason codes, never unsafe text
    repair_req = dict(new_state.get("model_request", {}))
    repair_req["reason_codes"] = list(new_state.get("reason_codes", []))
    repair_req["mode"] = "repair"

    resp, attempts, cost_added = _execute_call_with_retries(
        conn_read,
        job,
        new_state,
        node_name="repair",
        call_idx=calls_count,
        model=model,
        req_payload=repair_req,
    )

    new_state["spent_micro"] = spent + cost_added
    new_state["model_calls_count"] = calls_count + 1
    new_state["current_candidate"] = resp
    new_state["next_node"] = "check"
    return new_state, attempts, []


def finalize_node(conn_read: Any, job: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Assemble final result, re-run gate (I5), and create axis/done events (I7)."""
    new_state = dict(state)
    candidate = new_state.get("best_valid")
    events: list[dict[str, Any]] = []

    delivered = False
    result_data: dict[str, Any] = {}

    if candidate is not None and new_state.get("outcome") != "blocked":
        # Final gate check on assembled axes (I5)
        gate_res = gate_check(candidate["axes"])
        if gate_res.allowed:
            delivered = True
            result_data = {
                "delivered": True,
                "axes": candidate["axes"],
                "dsl": candidate["dsl"],
                "citations": candidate["citations"],
                "flags": candidate.get("flags", []),
                "quote_micro": job["quote_micro"],
            }
            # Insert axis events in canonical order followed by done
            canonical_axes = ["subject", "emotion", "optics", "motion", "lighting", "style", "audio", "continuity"]
            for axis_name in canonical_axes:
                events.append({
                    "type": "axis",
                    "data": {"axis": axis_name, "value": candidate["axes"].get(axis_name, "")},
                })
            events.append({
                "type": "done",
                "data": {"status": "delivered", "quote_micro": job["quote_micro"]},
            })
        else:
            delivered = False
            result_data = {
                "delivered": False,
                "reason_codes": [b["code"] for b in gate_res.blocks],
                "rewrites": [b["rewrite"] for b in gate_res.blocks],
            }
            events.append({
                "type": "done",
                "data": {"status": "blocked", "reason_codes": result_data["reason_codes"]},
            })
    else:
        delivered = False
        outcome_status = "blocked" if new_state.get("outcome") == "blocked" else "failed"
        result_data = {
            "delivered": False,
            "reason_codes": new_state.get("reason_codes", []),
            "rewrites": new_state.get("rewrites", []),
            "errors": new_state.get("errors", []),
        }
        events.append({
            "type": "done",
            "data": {"status": outcome_status, "reason_codes": result_data["reason_codes"]},
        })

    new_state["delivered"] = delivered
    new_state["result"] = result_data
    new_state["next_node"] = None
    return new_state, [], events


NODE_FUNCTIONS = {
    "intent": intent_node,
    "retrieve": retrieve_node,
    "draft": draft_node,
    "check": check_node,
    "repair": repair_node,
    "finalize": finalize_node,
}
