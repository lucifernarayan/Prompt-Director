import pytest
import psycopg
from app.config import settings
from app.db.migrate import run_migrations
from app.fixtures_loader import load_fixtures
from app.jobs import Conflict, InsufficientCredit, create_job, settle
from app.pricing import AXIS_EDIT_QUOTE, FULL_QUOTE


@pytest.fixture(autouse=True)
def clean_db():
    run_migrations()
    load_fixtures()
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE jobs, checkpoints, llm_calls, job_events, account_ledger CASCADE;")
    load_fixtures()


def test_create_job_quote_selection():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        # Full quote
        job_full = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-quote-full-1",
            body={"idea": "detective in neon rain"},
        )
        assert job_full["quote_micro"] == FULL_QUOTE
        assert job_full["cost_cap_micro"] == (FULL_QUOTE * 80) // 100
        assert job_full["hold_micro"] == FULL_QUOTE

        # Axis edit quote
        job_axis = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-quote-axis-1",
            body={"idea": "detective in neon rain", "only_axis": "style"},
        )
        assert job_axis["quote_micro"] == AXIS_EDIT_QUOTE
        assert job_axis["cost_cap_micro"] == (AXIS_EDIT_QUOTE * 80) // 100
        assert job_axis["hold_micro"] == AXIS_EDIT_QUOTE


def test_idempotency_same_body_returns_same_job_different_body_raises_conflict():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job1 = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-idem-1",
            body={"idea": "detective in neon rain"},
        )

        # Same key, same body -> returns identical job
        job2 = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-idem-1",
            body={"idea": "detective in neon rain"},
        )
        assert job1["id"] == job2["id"]

        # Same key, different body -> raises Conflict
        with pytest.raises(Conflict):
            create_job(
                conn,
                user_id="user_a",
                project_id="proj_a",
                idempotency_key="key-idem-1",
                body={"idea": "different idea entirely"},
            )


def test_insufficient_credit_raises_and_preserves_balance():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        # Create a poor user with 0 balance
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO accounts (user_id, balance_micro)
                VALUES ('poor_user', 0)
                ON CONFLICT (user_id) DO UPDATE SET balance_micro = 0;
                INSERT INTO project_members (user_id, project_id)
                VALUES ('poor_user', 'proj_a')
                ON CONFLICT DO NOTHING;
                """
            )

        with pytest.raises(InsufficientCredit):
            create_job(
                conn,
                user_id="poor_user",
                project_id="proj_a",
                idempotency_key="key-poor-1",
                body={"idea": "detective in neon rain"},
            )

        # Verify balance is still 0 and no job/ledger was created
        with conn.cursor() as cur:
            cur.execute("SELECT balance_micro FROM accounts WHERE user_id = 'poor_user';")
            assert cur.fetchone()[0] == 0

            # Clean up poor_user so test_schema.py is not affected
            cur.execute("DELETE FROM project_members WHERE user_id = 'poor_user';")
            cur.execute("DELETE FROM accounts WHERE user_id = 'poor_user';")


def test_settle_delivered_vs_released_and_no_double_charge():
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        # 1. Delivered flow
        job_deliv = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-settle-deliv-1",
            body={"idea": "detective in neon rain"},
        )
        with conn.cursor() as cur:
            cur.execute("SELECT balance_micro FROM accounts WHERE user_id = 'user_a';")
            bal_after_hold = cur.fetchone()[0]

        # Settle as delivered inside a transaction
        with conn.transaction():
            settle(conn, job_deliv["id"], delivered=True)

        with conn.cursor() as cur:
            cur.execute("SELECT charged_micro, status FROM jobs WHERE id = %s;", (job_deliv["id"],))
            charged, status = cur.fetchone()
            assert charged == FULL_QUOTE
            assert status == "delivered"

            # Check ledger has hold and settle
            cur.execute("SELECT phase, amount_micro FROM account_ledger WHERE job_id = %s;", (job_deliv["id"],))
            phases = {r[0]: r[1] for r in cur.fetchall()}
            assert "hold" in phases
            assert "settle" in phases
            assert "release" not in phases

            # Balance should NOT have refunded
            cur.execute("SELECT balance_micro FROM accounts WHERE user_id = 'user_a';")
            assert cur.fetchone()[0] == bal_after_hold

        # Double settle is idempotent / no-op
        with conn.transaction():
            settle(conn, job_deliv["id"], delivered=True)

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM account_ledger WHERE job_id = %s AND phase = 'settle';", (job_deliv["id"],))
            assert cur.fetchone()[0] == 1

        # 2. Released flow (not delivered)
        job_rel = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key="key-settle-rel-1",
            body={"idea": "detective in neon rain"},
        )
        with conn.cursor() as cur:
            cur.execute("SELECT balance_micro FROM accounts WHERE user_id = 'user_a';")
            bal_before_release = cur.fetchone()[0]

        with conn.transaction():
            settle(conn, job_rel["id"], delivered=False)

        with conn.cursor() as cur:
            cur.execute("SELECT charged_micro, status FROM jobs WHERE id = %s;", (job_rel["id"],))
            charged_rel, status_rel = cur.fetchone()
            assert charged_rel == 0
            assert status_rel in ("failed", "blocked", "cancelled")

            # Check ledger has hold and release
            cur.execute("SELECT phase, amount_micro FROM account_ledger WHERE job_id = %s;", (job_rel["id"],))
            phases_rel = {r[0]: r[1] for r in cur.fetchall()}
            assert "hold" in phases_rel
            assert "release" in phases_rel
            assert "settle" not in phases_rel

            # Balance should be refunded by hold_micro
            cur.execute("SELECT balance_micro FROM accounts WHERE user_id = 'user_a';")
            assert cur.fetchone()[0] == bal_before_release + FULL_QUOTE
