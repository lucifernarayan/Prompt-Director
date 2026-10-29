import logging
import psycopg

from app.config import settings

logger = logging.getLogger(__name__)

INITIAL_BALANCE_MICRO: int = 10_000_000  # 10 credits in micro-credits


def load_fixtures(conninfo: str | None = None) -> None:
    """Seed two projects, two users, memberships, and funded accounts idempotently."""
    db_url = conninfo or settings.DATABASE_URL
    if not db_url:
        raise ValueError("DATABASE_URL is not configured")

    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
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

            # 3. Funded accounts for both users
            cur.execute(
                """
                INSERT INTO accounts (user_id, balance_micro)
                VALUES ('user_a', %s), ('user_b', %s)
                ON CONFLICT (user_id) DO UPDATE
                SET balance_micro = EXCLUDED.balance_micro
                WHERE accounts.balance_micro = 0;
                """,
                (INITIAL_BALANCE_MICRO, INITIAL_BALANCE_MICRO),
            )

    logger.info("Fixtures loaded successfully (projects, memberships, accounts).")


if __name__ == "__main__":
    load_fixtures()
