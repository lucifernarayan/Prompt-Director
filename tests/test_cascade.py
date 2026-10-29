import json
import psycopg
import pytest
from app.config import settings
from app.db.migrate import run_migrations
from app.fixtures_loader import load_fixtures
from app.graph.runner import run_job
from app.jobs import create_job
from app.llm import chaos
from app.pricing import FULL_QUOTE, COST_CAP


def conn_factory():
    return psycopg.connect(settings.DATABASE_URL, autocommit=False)


def test_happy_path_cheap_confident_delivers():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        chaos.set(conn, "force_outcome:cheap", "valid")
        chaos.set(conn, "force_confidence:cheap", "0.9")
        chaos.set(conn, "force_outcome:strong", "valid")
        chaos.set(conn, "unsafe:strong", "false")
        chaos.set(conn, "fake_citation_rate:cheap", "0.0")

        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-happy-1",
            body={"idea": "detective jack vance in neon alley"},
        )
        worker_id = "worker-1"
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job["id"]))

    run_job(conn_factory, job["id"], worker_id)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro, result FROM jobs WHERE id = %s;", (job["id"],))
            status, charged, res = cur.fetchone()
            assert status == "delivered"
            assert charged == FULL_QUOTE
            assert res is not None
            assert len(res["axes"]) == 8

            # Exactly one settle row
            cur.execute("SELECT phase, amount_micro FROM account_ledger WHERE job_id = %s;", (job["id"],))
            phases = [r[0] for r in cur.fetchall()]
            assert phases.count("settle") == 1
            assert "release" not in phases

            # Check llm_calls: exactly 1 call
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (job["id"],))
            assert cur.fetchone()[0] == 1

            # Check events: stage events contain no generated text; axis events in canonical order then done
            cur.execute("SELECT type, data FROM job_events WHERE job_id = %s ORDER BY id ASC;", (job["id"],))
            events = cur.fetchall()

            stage_events = [e for e in events if e[0] == "stage"]
            for se in stage_events:
                # Stage event data must only contain node name and duration
                assert "text" not in se[1]
                assert "dsl" not in se[1]

            axis_events = [e for e in events if e[0] == "axis"]
            assert len(axis_events) == 8
            expected_order = ["subject", "emotion", "optics", "motion", "lighting", "style", "audio", "continuity"]
            actual_order = [e[1]["axis"] for e in axis_events]
            assert actual_order == expected_order

            done_events = [e for e in events if e[0] == "done"]
            assert len(done_events) == 1


def test_t4_blocked_real_person_at_intake():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-t4-gate-1",
            body={"idea": "a cinematic portrait featuring Elon Musk in neon lighting"},
        )
        worker_id = "worker-1"
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job["id"]))

    run_job(conn_factory, job["id"], worker_id)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro, result FROM jobs WHERE id = %s;", (job["id"],))
            status, charged, res = cur.fetchone()
            assert status == "blocked"
            assert charged == 0
            assert res is not None
            assert res["delivered"] is False
            assert "RIGHTS_REAL_PERSON" in res["reason_codes"]
            assert res["rewrites"]

            # No model calls made
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (job["id"],))
            assert cur.fetchone()[0] == 0

            # No axis events
            cur.execute("SELECT count(*) FROM job_events WHERE job_id = %s AND type = 'axis';", (job["id"],))
            assert cur.fetchone()[0] == 0

            # Hold fully refunded
            cur.execute("SELECT phase FROM account_ledger WHERE job_id = %s;", (job["id"],))
            phases = [r[0] for r in cur.fetchall()]
            assert "hold" in phases
            assert "release" in phases
            assert "settle" not in phases


def test_t5_strong_unsafe_switch_blocks_and_unsafe_off_delivers():
    # 1. Unsafe ON: cheap malformed twice -> strong called with unsafe ON -> blocked by gate
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        chaos.set(conn, "force_outcome:cheap", "malformed,malformed")
        chaos.set(conn, "unsafe:strong", "true")

        job_unsafe = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-t5-unsafe-1",
            body={"idea": "detective jack vance investigating crime scene"},
        )
        worker_id = "worker-1"
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job_unsafe["id"]))

    run_job(conn_factory, job_unsafe["id"], worker_id)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro, result FROM jobs WHERE id = %s;", (job_unsafe["id"],))
            status, charged, res = cur.fetchone()
            assert status == "blocked"
            assert charged == 0
            assert "RIGHTS_REAL_PERSON" in res["reason_codes"]

            # Assert NO unsafe text in result or job events
            res_str = json.dumps(res)
            assert "Elon Musk" not in res_str

            cur.execute("SELECT type, data FROM job_events WHERE job_id = %s;", (job_unsafe["id"],))
            for etype, edata in cur.fetchall():
                assert "Elon Musk" not in json.dumps(edata)

    # 2. Same request with unsafe OFF delivers
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        chaos.set(conn, "force_outcome:cheap", "malformed,malformed")
        chaos.set(conn, "force_outcome:strong", "valid")
        chaos.set(conn, "unsafe:strong", "false")

        job_safe = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-t5-safe-1",
            body={"idea": "detective jack vance investigating crime scene"},
        )
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job_safe["id"]))

    run_job(conn_factory, job_safe["id"], worker_id)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro FROM jobs WHERE id = %s;", (job_safe["id"],))
            status_safe, charged_safe = cur.fetchone()
            assert status_safe == "delivered"
            assert charged_safe == FULL_QUOTE


def test_t6_cheap_malformed_twice_then_strong_valid_cascade():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        chaos.set(conn, "force_outcome:cheap", "malformed,malformed")
        chaos.set(conn, "force_outcome:strong", "valid")
        chaos.set(conn, "unsafe:strong", "false")

        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-t6-cascade-1",
            body={"idea": "hacker maya hacking terminal"},
        )
        worker_id = "worker-1"
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job["id"]))

    run_job(conn_factory, job["id"], worker_id)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro FROM jobs WHERE id = %s;", (job["id"],))
            status, charged = cur.fetchone()
            assert status == "delivered"
            assert charged == FULL_QUOTE

            # Exactly 3 model calls (cheap draft, cheap repair, strong repair)
            cur.execute("SELECT call_key, model, cost_micro, outcome FROM llm_calls WHERE job_id = %s ORDER BY created_at ASC;", (job["id"],))
            calls = cur.fetchall()
            assert len(calls) == 3
            assert calls[0][1] == "cheap"
            assert calls[1][1] == "cheap"
            assert calls[2][1] == "strong"

            total_cost = sum(c[2] for c in calls)
            assert total_cost <= COST_CAP, f"Total spend {total_cost} exceeded cap {COST_CAP}"


def test_low_confidence_valid_cheap_draft_escalates_to_strong():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        # cheap draft valid but low confidence (e.g. 0.35) -> strong valid
        chaos.set(conn, "force_outcome:cheap", "valid")
        chaos.set(conn, "force_confidence:cheap", "0.35")
        chaos.set(conn, "force_outcome:strong", "valid")
        chaos.set(conn, "unsafe:strong", "false")

        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-low-conf-1",
            body={"idea": "low confidence test idea"},
        )
        worker_id = "worker-1"
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job["id"]))

    # Pass seed that produces low confidence (<0.5) for cheap model
    # Seed 17 in cheap model produces p_confidence < 0.10 -> confidence < 0.5
    # Or force confidence via hook / test
    run_job(conn_factory, job["id"], worker_id)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (job["id"],))
            n_calls = cur.fetchone()[0]
            assert n_calls == 2  # Low confidence cheap draft escalated straight to strong (2 calls total)


def test_retries_on_rate_limit_and_timeout():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        # First attempt 429, second attempt valid
        chaos.set(conn, "force_outcome:cheap", "rate_limit,valid")
        chaos.set(conn, "force_confidence:cheap", "0.9")

        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-retry-429-1",
            body={"idea": "retry test idea"},
        )
        worker_id = "worker-1"
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job["id"]))

    run_job(conn_factory, job["id"], worker_id)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro FROM jobs WHERE id = %s;", (job["id"],))
            status, charged = cur.fetchone()
            assert status == "delivered"
            assert charged == FULL_QUOTE

            cur.execute("SELECT call_key, cost_micro, outcome FROM llm_calls WHERE job_id = %s ORDER BY created_at ASC;", (job["id"],))
            calls = cur.fetchall()
            assert len(calls) == 2
            # First attempt was rate_limit with 0 cost
            assert calls[0][1] == 0
            assert calls[0][2] == "rate_limit"
            assert calls[0][0].endswith(":0")  # try 0
            assert calls[1][0].endswith(":1")  # try 1 (retry)
