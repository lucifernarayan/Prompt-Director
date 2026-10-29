import logging
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import psycopg
from psycopg import errors, sql

from app.config import settings

logger = logging.getLogger(__name__)

MIGRATION_LOCK_ID = 847291048201


def get_maintenance_url(db_url: str, maint_db: str = "postgres") -> str:
    parts = urlsplit(db_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{maint_db}", parts.query, parts.fragment))


def ensure_database(target_db_name: str, conninfo: str | None = None) -> None:
    """Connect to the server's maintenance database and CREATE DATABASE if it does not exist.

    Handles concurrent creation safely via DuplicateDatabase exception.
    """
    db_url = conninfo or settings.DATABASE_URL
    if not db_url:
        raise ValueError("DATABASE_URL is not configured")

    # Connect to maintenance database (try postgres first, fallback to template1)
    maint_conn = None
    try:
        maint_conn = psycopg.connect(get_maintenance_url(db_url, "postgres"), autocommit=True)
    except Exception:
        maint_conn = psycopg.connect(get_maintenance_url(db_url, "template1"), autocommit=True)

    with maint_conn:
        with maint_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (target_db_name,))
            if not cur.fetchone():
                try:
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db_name)))
                    logger.info("Created database %s", target_db_name)
                except errors.DuplicateDatabase:
                    logger.debug("Database %s was concurrently created", target_db_name)


def get_migrations_dir() -> Path:
    env_dir = os.environ.get("MIGRATIONS_DIR")
    if env_dir:
        return Path(env_dir)
    candidates = [
        Path(__file__).resolve().parents[2] / "db" / "migrations",
        Path.cwd() / "db" / "migrations",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def run_migrations(conninfo: str | None = None) -> list[str]:
    db_url = conninfo or settings.DATABASE_URL
    if not db_url:
        raise ValueError("DATABASE_URL is not configured")

    migrations_dir = get_migrations_dir()
    if not migrations_dir.exists():
        logger.warning("Migrations directory %s does not exist", migrations_dir)
        return []

    sql_files = sorted(migrations_dir.glob("*.sql"))
    applied: list[str] = []

    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Take Postgres advisory lock
            cur.execute("SELECT pg_advisory_lock(%s);", (MIGRATION_LOCK_ID,))
            try:
                # 2. Create schema_migrations table
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        name text PRIMARY KEY
                    );
                    """
                )
                # 3. Check already applied migrations
                cur.execute("SELECT name FROM schema_migrations;")
                already_applied = {row[0] for row in cur.fetchall()}

                # 4. Apply each migration in filename order exactly once
                for sql_file in sql_files:
                    if sql_file.name not in already_applied:
                        logger.info("Applying migration: %s", sql_file.name)
                        sql_content = sql_file.read_text(encoding="utf-8")
                        cur.execute(sql_content)
                        cur.execute(
                            "INSERT INTO schema_migrations (name) VALUES (%s);",
                            (sql_file.name,),
                        )
                        applied.append(sql_file.name)
                    else:
                        logger.debug("Migration %s already applied", sql_file.name)
            finally:
                # 5. Release advisory lock
                cur.execute("SELECT pg_advisory_unlock(%s);", (MIGRATION_LOCK_ID,))

    return applied


if __name__ == "__main__":
    run_migrations()
