import uuid
import pytest
import psycopg
from psycopg.errors import UniqueViolation, CheckViolation

from app.config import settings
from app.db.migrate import run_migrations


def get_db_connection():
    return psycopg.connect(settings.DATABASE_URL, autocommit=True)


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Ensure database migrations are applied before running tests."""
    run_migrations()


def test_pricing_constants():
    """Verify pricing constants satisfy:
    (a) single strong call at worst case fits inside the cap
    (b) cheap + cheap + strong at worst case exceeds the cap
    """
    from app import pricing

    assert pricing.FULL_QUOTE > pricing.AXIS_EDIT_QUOTE
    assert pricing.COST_CAP == (pricing.FULL_QUOTE * 80) // 100

    cheap_worst_case = (
        pricing.CHEAP_WORST_CASE_TOKENS_IN * pricing.CHEAP_INPUT_RATE_MICRO
        + pricing.CHEAP_WORST_CASE_TOKENS_OUT * pricing.CHEAP_OUTPUT_RATE_MICRO
    )
    strong_worst_case = (
        pricing.STRONG_WORST_CASE_TOKENS_IN * pricing.STRONG_INPUT_RATE_MICRO
        + pricing.STRONG_WORST_CASE_TOKENS_OUT * pricing.STRONG_OUTPUT_RATE_MICRO
    )

    # (a) single strong call at worst case fits inside the cap
    assert strong_worst_case <= pricing.COST_CAP, (
        f"Strong worst case ({strong_worst_case}) must fit inside cap ({pricing.COST_CAP})"
    )

    # (b) cheap + cheap + strong at worst case exceeds the cap
    three_call_worst_case = cheap_worst_case + cheap_worst_case + strong_worst_case
    assert three_call_worst_case > pricing.COST_CAP, (
        f"Three call worst case ({three_call_worst_case}) must exceed cap ({pricing.COST_CAP})"
    )


def test_duplicate_idempotency_key_rejected():
    """Unique constraint on jobs(user_id, idempotency_key)."""
    user_id = f"user_{uuid.uuid4().hex[:8]}"
    idem_key = f"key_{uuid.uuid4().hex[:8]}"
    job_id_1 = f"job_{uuid.uuid4().hex[:8]}"
    job_id_2 = f"job_{uuid.uuid4().hex[:8]}"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (id, user_id, project_id, idempotency_key)
                VALUES (%s, %s, %s, %s);
                """,
                (job_id_1, user_id, "proj_test", idem_key),
            )
            with pytest.raises(UniqueViolation):
                cur.execute(
                    """
                    INSERT INTO jobs (id, user_id, project_id, idempotency_key)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (job_id_2, user_id, "proj_test", idem_key),
                )


def test_duplicate_llm_call_key_rejected():
    """Unique constraint on llm_calls(job_id, call_key)."""
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    call_key = "draft:0:0"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_calls (job_id, call_key, model, tokens_in, tokens_out, cost_micro, outcome)
                VALUES (%s, %s, 'cheap', 10, 20, 100, 'ok');
                """,
                (job_id, call_key),
            )
            with pytest.raises(UniqueViolation):
                cur.execute(
                    """
                    INSERT INTO llm_calls (job_id, call_key, model, tokens_in, tokens_out, cost_micro, outcome)
                    VALUES (%s, %s, 'cheap', 10, 20, 100, 'ok');
                    """,
                    (job_id, call_key),
                )


def test_duplicate_ledger_phase_rejected():
    """Unique constraint on account_ledger(job_id, phase) and phase CHECK constraint."""
    job_id = f"job_{uuid.uuid4().hex[:8]}"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO account_ledger (job_id, phase, amount_micro)
                VALUES (%s, 'hold', 1000);
                """,
                (job_id,),
            )
            # Duplicate (job_id, phase)
            with pytest.raises(UniqueViolation):
                cur.execute(
                    """
                    INSERT INTO account_ledger (job_id, phase, amount_micro)
                    VALUES (%s, 'hold', 1000);
                    """,
                    (job_id,),
                )

            # Invalid phase violates CHECK
            with pytest.raises(CheckViolation):
                cur.execute(
                    """
                    INSERT INTO account_ledger (job_id, phase, amount_micro)
                    VALUES (%s, 'invalid_phase', 1000);
                    """,
                    (job_id,),
                )


def test_atomic_balance_hold_when_short():
    """Hold statement affects 0 rows when balance is short."""
    user_id = f"user_{uuid.uuid4().hex[:8]}"
    hold_amount = 100_000

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (user_id, balance_micro) VALUES (%s, %s);",
                (user_id, 50_000),
            )

            # Try to hold 100_000 when balance is only 50_000
            cur.execute(
                """
                UPDATE accounts
                SET balance_micro = balance_micro - %s
                WHERE user_id = %s AND balance_micro >= %s;
                """,
                (hold_amount, user_id, hold_amount),
            )
            assert cur.rowcount == 0

            # Verify balance untouched
            cur.execute("SELECT balance_micro FROM accounts WHERE user_id = %s;", (user_id,))
            assert cur.fetchone()[0] == 50_000

            # Balance cannot be negative
            with pytest.raises(CheckViolation):
                cur.execute(
                    "UPDATE accounts SET balance_micro = -1 WHERE user_id = %s;",
                    (user_id,),
                )


def test_migrations_and_fixtures_idempotent():
    """Running migrations and fixtures twice does not fail."""
    from app.fixtures_loader import load_fixtures

    # Run migrations twice
    run_migrations()
    run_migrations()

    # Load fixtures twice
    load_fixtures()
    load_fixtures()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Check projects
            cur.execute("SELECT id FROM projects WHERE id IN ('proj_a', 'proj_b');")
            assert len(cur.fetchall()) == 2

            # Check memberships: user A in proj A only, user B in proj B only
            cur.execute("SELECT user_id, project_id FROM project_members ORDER BY user_id;")
            memberships = cur.fetchall()
            assert memberships == [("user_a", "proj_a"), ("user_b", "proj_b")]

            # Check funded accounts
            cur.execute("SELECT user_id, balance_micro FROM accounts WHERE user_id IN ('user_a', 'user_b');")
            accounts = dict(cur.fetchall())
            assert accounts["user_a"] > 0
            assert accounts["user_b"] > 0


def test_memory_items_pk_and_tsv_generated():
    """memory_items PK (project_id, id) and generated tsvector column."""
    proj_id = f"proj_{uuid.uuid4().hex[:8]}"
    item_id = f"item_{uuid.uuid4().hex[:8]}"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_items (project_id, id, kind, body, license)
                VALUES (%s, %s, 'shot', 'hero walking into sunset', 'permissive');
                """,
                (proj_id, item_id),
            )

            # Duplicate PK rejected
            with pytest.raises(UniqueViolation):
                cur.execute(
                    """
                    INSERT INTO memory_items (project_id, id, kind, body, license)
                    VALUES (%s, %s, 'shot', 'hero walking into sunset', 'permissive');
                    """,
                    (proj_id, item_id),
                )

            # Generated tsvector contains tokens
            cur.execute(
                "SELECT tsv FROM memory_items WHERE project_id = %s AND id = %s;",
                (proj_id, item_id),
            )
            tsv = cur.fetchone()[0]
            assert "hero" in tsv
            assert "sunset" in tsv
