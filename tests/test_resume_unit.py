import psycopg
import pytest
from app.config import settings
from app.db.migrate import run_migrations
from app.fixtures_loader import load_fixtures
from app.graph.runner import LeaseLost, run_job
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


def test_crash_after_draft_commit_resumes_without_duplicate_call_key():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-crash-resume-1",
            body={"idea": "detective in neon alley"},
        )
        worker_1 = "worker-crash-1"
        worker_2 = "worker-resume-2"
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_1, job["id"]))

    # Simulate crash right after draft commit
    def crash_after_draft(node_name):
        if node_name == "draft":
            raise RuntimeError("Simulated crash after draft commit")

    with pytest.raises(RuntimeError, match="Simulated crash"):
        run_job(conn_factory, job["id"], worker_1, after_node_hook=crash_after_draft)

    # Checkpoint exists for draft
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT node_done, next_node FROM checkpoints WHERE job_id = %s ORDER BY seq DESC LIMIT 1;", (job["id"],))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "draft"
            assert row[1] == "check"

            # Hand lease over to worker_2
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_2, job["id"]))

    # Second worker resumes
    run_job(conn_factory, job["id"], worker_2)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro FROM jobs WHERE id = %s;", (job["id"],))
            status, charged = cur.fetchone()
            assert status == "delivered"
            assert charged == FULL_QUOTE

            # Assert NO duplicate llm_calls call_keys
            cur.execute("SELECT call_key, count(*) FROM llm_calls WHERE job_id = %s GROUP BY call_key HAVING count(*) > 1;", (job["id"],))
            duplicates = cur.fetchall()
            assert duplicates == []

            # Exactly one settle row
            cur.execute("SELECT phase, count(*) FROM account_ledger WHERE job_id = %s AND phase = 'settle' GROUP BY phase;", (job["id"],))
            assert cur.fetchone()[1] == 1


def test_call_key_reuse_skips_provider_call():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-reuse-check-1",
            body={"idea": "detective in rain"},
        )
        worker_id = "worker-1"
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (worker_id, job["id"]))
            # Pre-populate an llm_calls row for draft:0:0
            cur.execute(
                """
                INSERT INTO llm_calls (job_id, call_key, model, tokens_in, tokens_out, cost_micro, outcome, response)
                VALUES (%s, 'draft:0:0', 'cheap', 100, 50, 1000, 'valid', '{"text": "{\\"dsl\\": \\"subject: test\\", \\"confidence\\": 0.9}", "outcome": "valid"}'::jsonb);
                """,
                (job["id"],),
            )

    # When draft runs, it must reuse the stored call and NOT make a new attempt
    from app.graph.nodes import draft_node
    from app.graph.state import create_initial_state
    state = create_initial_state(job)
    state["model_request"] = {"idea": "detective in rain"}
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        _, attempts, _ = draft_node(conn, job, state)
        assert attempts == [], "Expected 0 new attempts because draft:0:0 was already recorded"


def test_crash_before_commit_resumes_cleanly():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        chaos.set(conn, "force_outcome:cheap", "valid")
        chaos.set(conn, "force_confidence:cheap", "0.9")
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-crash-before-commit-1",
            body={"idea": "detective in neon rain"},
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
            # Exactly one row for that call_key
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s AND call_key = 'draft:0:0';", (job["id"],))
            assert cur.fetchone()[0] == 1
            # Exactly one charge
            cur.execute("SELECT count(*) FROM account_ledger WHERE job_id = %s AND phase = 'settle';", (job["id"],))
            assert cur.fetchone()[0] == 1


def test_fencing_stale_worker_raises_lease_lost_and_writes_nothing():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-fencing-1",
            body={"idea": "detective in neon alley"},
        )
        real_owner = "worker-real"
        impostor = "worker-impostor"
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_owner = %s, lease_expires_at = NOW() + INTERVAL '30s' WHERE id = %s;", (real_owner, job["id"]))

    # Impostor attempts to run
    with pytest.raises(LeaseLost):
        run_job(conn_factory, job["id"], impostor)

    # Verify impostor wrote nothing
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM checkpoints WHERE job_id = %s;", (job["id"],))
            assert cur.fetchone()[0] == 0

            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (job["id"],))
            assert cur.fetchone()[0] == 0

            cur.execute("SELECT count(*) FROM job_events WHERE job_id = %s;", (job["id"],))
            assert cur.fetchone()[0] == 0
