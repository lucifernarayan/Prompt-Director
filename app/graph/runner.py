import json
import time
from typing import Any, Callable

import psycopg

from app.graph.nodes import NODE_FUNCTIONS
from app.graph.state import create_initial_state
from app.jobs import settle


class LeaseLost(Exception):
    """Raised when lease ownership is lost or fenced out by another worker."""
    pass


def run_job(
    conn_factory: Callable[[], psycopg.Connection],
    job_id: str,
    worker_id: str,
    after_node_hook: Callable[[str], None] | None = None,
) -> None:
    """Execute graph runner for a single job with transactional node checkpoints and lease fencing."""
    while True:
        with conn_factory() as conn:
            # 1. Read job details and check lease / terminal status
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, project_id, status, cancel_requested, lease_owner,
                           quote_micro, cost_cap_micro, hold_micro, charged_micro, flags, result
                    FROM jobs
                    WHERE id = %s;
                    """,
                    (job_id,),
                )
                job_row = cur.fetchone()
                if not job_row:
                    return

                (
                    j_id, user_id, project_id, status, cancel_requested, lease_owner,
                    quote_micro, cost_cap_micro, hold_micro, charged_micro, flags, result
                ) = job_row

                # Terminal status is a no-op
                if status in ("delivered", "failed", "cancelled", "blocked"):
                    return

                # Check lease ownership
                if lease_owner != worker_id:
                    raise LeaseLost(f"Worker '{worker_id}' is not the lease owner (current: '{lease_owner}')")

                # Check cancellation
                if cancel_requested:
                    with conn.transaction():
                        settle(conn, job_id, delivered=False)
                        cur.execute(
                            "UPDATE jobs SET status = 'cancelled' WHERE id = %s AND lease_owner = %s;",
                            (job_id, worker_id),
                        )
                        cur.execute(
                            """
                            INSERT INTO job_events (job_id, type, data)
                            VALUES (%s, 'done', '{"status": "cancelled"}'::jsonb);
                            """,
                            (job_id,),
                        )
                    return

                job_dict = {
                    "id": j_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "status": status,
                    "cancel_requested": cancel_requested,
                    "lease_owner": lease_owner,
                    "quote_micro": quote_micro,
                    "cost_cap_micro": cost_cap_micro,
                    "hold_micro": hold_micro,
                    "charged_micro": charged_micro,
                    "flags": flags or {},
                    "body": (flags or {}).get("body", {}),
                    "result": result,
                }

                # 2. Load latest checkpoint
                cur.execute(
                    """
                    SELECT seq, node_done, next_node, state
                    FROM checkpoints
                    WHERE job_id = %s
                    ORDER BY seq DESC
                    LIMIT 1;
                    """,
                    (job_id,),
                )
                ckpt = cur.fetchone()

                if ckpt:
                    seq, node_done, next_node, state = ckpt
                    if not next_node:
                        return
                    current_node = next_node
                else:
                    seq = 0
                    current_node = "intent"
                    state = create_initial_state(job_dict)

            # 3. Execute pure node function
            if current_node not in NODE_FUNCTIONS:
                raise ValueError(f"Unknown node '{current_node}'")

            t0 = time.perf_counter()
            new_state, attempts, events = NODE_FUNCTIONS[current_node](conn, job_dict, state)
            duration_ms = max(1, int((time.perf_counter() - t0) * 1000))

            # 4. Commit ONE transaction: attempts + checkpoint + stage event + fenced lease update
            with conn.transaction():
                with conn.cursor() as cur:
                    # Insert LLM attempts
                    for att in attempts:
                        cur.execute(
                            """
                            INSERT INTO llm_calls (
                                job_id, call_key, model, tokens_in, tokens_out,
                                cost_micro, outcome, response
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                            ON CONFLICT (job_id, call_key) DO NOTHING;
                            """,
                            (
                                job_id,
                                att["call_key"],
                                att["model"],
                                att["tokens_in"],
                                att["tokens_out"],
                                att["cost_micro"],
                                att["outcome"],
                                json.dumps(att["response"]),
                            ),
                        )

                    # Insert stage event (node name and duration only, no generated text)
                    cur.execute(
                        """
                        INSERT INTO job_events (job_id, type, data)
                        VALUES (%s, 'stage', %s::jsonb);
                        """,
                        (job_id, json.dumps({"node": current_node, "duration_ms": duration_ms})),
                    )

                    # Insert checkpoint
                    new_seq = seq + 1
                    cur.execute(
                        """
                        INSERT INTO checkpoints (job_id, seq, node_done, next_node, state)
                        VALUES (%s, %s, %s, %s, %s::jsonb);
                        """,
                        (
                            job_id,
                            new_seq,
                            current_node,
                            new_state.get("next_node"),
                            json.dumps(new_state),
                        ),
                    )

                    # If finalize node, persist result and settle
                    if current_node == "finalize":
                        # Insert axis and done events
                        for evt in events:
                            cur.execute(
                                """
                                INSERT INTO job_events (job_id, type, data)
                                VALUES (%s, %s, %s::jsonb);
                                """,
                                (job_id, evt["type"], json.dumps(evt["data"])),
                            )

                        final_result = new_state.get("result", {})
                        delivered = new_state.get("delivered", False)
                        outcome = new_state.get("outcome")
                        settle(conn, job_id, delivered=delivered)

                        final_status = "delivered" if delivered else ("blocked" if outcome == "blocked" else "failed")
                        cur.execute(
                            """
                            UPDATE jobs
                            SET result = %s::jsonb, status = %s
                            WHERE id = %s;
                            """,
                            (json.dumps(final_result), final_status, job_id),
                        )

                    # Fenced lease update: zero rows updated raises LeaseLost
                    cur.execute(
                        """
                        UPDATE jobs
                        SET lease_expires_at = NOW() + INTERVAL '15 seconds'
                        WHERE id = %s AND lease_owner = %s AND NOT cancel_requested;
                        """,
                        (job_id, worker_id),
                    )
                    if cur.rowcount == 0:
                        raise LeaseLost(f"Lease lost or cancel requested on job '{job_id}' during commit")

        # 5. Post-commit hook (runs outside transaction)
        if after_node_hook is not None:
            after_node_hook(current_node)

        # 6. Check if workflow finished
        if current_node == "finalize" or new_state.get("next_node") is None:
            break
