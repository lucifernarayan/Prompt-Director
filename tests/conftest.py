import os
import psycopg
import pytest

# Ensure test suite runs with tiny TIME_SCALE so mock sleeps do not stall tests
os.environ["TIME_SCALE"] = "0.001"

from app.config import settings
from app.db.migrate import run_migrations
from app.fixtures_loader import INITIAL_BALANCE_MICRO, load_fixtures


@pytest.fixture(autouse=True)
def clean_db():
    run_migrations()
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE jobs, checkpoints, llm_calls, job_events, account_ledger, exact_cache, semantic_cache, inflight, feedback, chaos CASCADE;"
            )
            cur.execute("DELETE FROM accounts WHERE user_id NOT IN ('user_a', 'user_b');")
            cur.execute(
                "UPDATE accounts SET balance_micro = %s WHERE user_id IN ('user_a', 'user_b');",
                (INITIAL_BALANCE_MICRO,),
            )
    load_fixtures()

