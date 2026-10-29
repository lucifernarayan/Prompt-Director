import logging
import psycopg

from app.config import settings
from app.memory.store import load_fixtures_memory

logger = logging.getLogger(__name__)

INITIAL_BALANCE_MICRO: int = 10_000_000  # 10 credits in micro-credits
FIXTURES_ADVISORY_LOCK_ID: int = 847291048202


def load_fixtures(conninfo: str | None = None) -> None:
    """Seed demo projects, users, memberships, funded accounts, and memory items idempotently.

    Uses ON CONFLICT DO NOTHING semantics so restarts do not top up balances or
    duplicate memory rows. Uses a PostgreSQL advisory lock to ensure concurrent
    workers and the API starting together do not race during demo seeding.
    """
    db_url = conninfo or settings.DATABASE_URL
    if not db_url:
        raise ValueError("DATABASE_URL is not configured")

    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Advisory lock prevents concurrent seeding race conditions across workers/API
            cur.execute("SELECT pg_advisory_lock(%s);", (FIXTURES_ADVISORY_LOCK_ID,))
            try:
                # 1. Projects
                cur.execute(
                    """
                    INSERT INTO projects (id, memory_version)
                    VALUES ('proj_a', 1), ('proj_b', 1)
                    ON CONFLICT (id) DO NOTHING;
                    """
                )

                # 2. Memberships: User A in Proj A only, User B in Proj B only
                cur.execute(
                    """
                    INSERT INTO project_members (user_id, project_id)
                    VALUES ('user_a', 'proj_a'), ('user_b', 'proj_b')
                    ON CONFLICT (user_id, project_id) DO NOTHING;
                    """
                )

                # 3. Funded accounts for both users (ON CONFLICT DO NOTHING so restarts do not top up balances)
                cur.execute(
                    """
                    INSERT INTO accounts (user_id, balance_micro)
                    VALUES ('user_a', %s), ('user_b', %s)
                    ON CONFLICT (user_id) DO NOTHING;
                    """,
                    (INITIAL_BALANCE_MICRO, INITIAL_BALANCE_MICRO),
                )

                # 4. Project memory items from fixtures/projects.json (ON CONFLICT DO NOTHING)
                load_fixtures_memory(conn)
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s);", (FIXTURES_ADVISORY_LOCK_ID,))

    logger.info("Fixtures loaded successfully (projects, memberships, accounts, memory).")


if __name__ == "__main__":
    load_fixtures()
