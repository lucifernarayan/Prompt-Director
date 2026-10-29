import psycopg
import pytest
from app.config import settings
from app.db.migrate import run_migrations
from app.fixtures_loader import load_fixtures
from app.graph.runner import run_job
from app.jobs import create_job
from app.llm import chaos
from app.pricing import FULL_QUOTE


@pytest.fixture(autouse=True)
def clean_db():
    run_migrations()
    load_fixtures()
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE jobs, checkpoints, llm_calls, job_events, account_ledger CASCADE;")
    load_fixtures()


def conn_factory():
    return psycopg.connect(settings.DATABASE_URL, autocommit=False)


def test_t10_cost_cap_refuses_call_and_returns_flagged_best_valid():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        chaos.set(conn, "force_outcome:cheap", "valid,malformed")
        chaos.set(conn, "force_confidence:cheap", "0.35")
        chaos.set(conn, "force_outcome:strong", "valid")

        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-cost-cap-best-valid-1",
            body={"idea": "detective jack vance"},
        )
        worker_id = "worker-1"

        # Set cost_cap_micro to 15,000 micro-credits
        # Cheap call costs ~10,000. Second call (strong worst case 70,000) exceeds 15,000 cap!
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET cost_cap_micro = 15000, lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job["id"]))

    run_job(conn_factory, job["id"], worker_id)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro, result FROM jobs WHERE id = %s;", (job["id"],))
            status, charged, res = cur.fetchone()
            # If best_valid exists, delivered with cost_cap flag, charged == quote
            assert status == "delivered"
            assert charged == FULL_QUOTE
            assert "cost_cap" in res.get("flags", [])

            # Verify spent never exceeded cap
            cur.execute("SELECT sum(cost_micro) FROM llm_calls WHERE job_id = %s;", (job["id"],))
            total_spent = cur.fetchone()[0] or 0
            assert total_spent <= 15000


def test_t10_cost_cap_with_no_valid_candidate_not_delivered_charge_zero():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        # First call malformed, cap set very low so repair cannot run
        chaos.set(conn, "force_outcome:cheap", "malformed,malformed")
        chaos.set(conn, "force_outcome:strong", "valid")

        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-cost-cap-no-valid-1",
            body={"idea": "detective jack vance"},
        )
        worker_id = "worker-1"

        with conn.cursor() as cur:
            # Set cap to 5,000 micro-credits
            cur.execute("UPDATE jobs SET cost_cap_micro = 5000, lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job["id"]))

    run_job(conn_factory, job["id"], worker_id)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro, result FROM jobs WHERE id = %s;", (job["id"],))
            status, charged, res = cur.fetchone()
            assert status in ("failed", "cap_exhausted")
            assert charged == 0
            assert res is None or res.get("delivered") is False

            # Hold fully refunded
            cur.execute("SELECT phase FROM account_ledger WHERE job_id = %s;", (job["id"],))
            phases = [r[0] for r in cur.fetchall()]
            assert "release" in phases
            assert "settle" not in phases
