from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import psycopg
import redis

from app.api.routes import router as assist_router
from app.config import settings
from app.db.migrate import run_migrations
from app.fixtures_loader import load_fixtures
from app.obs.logging import setup_logging

logger = logging.getLogger(__name__)


def get_ui_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "ui",
        Path.cwd() / "ui",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.LOG_LEVEL)
    logger.info("Starting up API, running migrations...")
    try:
        run_migrations()
        logger.info("Migrations completed successfully.")
        # Demo seeding: seed initial demo projects, users, memberships, accounts, and memory items idempotently
        load_fixtures()
        logger.info("Demo fixtures seeded successfully.")
    except Exception as e:
        logger.error("Failed to run migrations or seed fixtures at startup: %s", e)
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(assist_router)


@app.get("/healthz")
def healthz():
    db_status = "down"
    try:
        with psycopg.connect(settings.DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                if cur.fetchone() == (1,):
                    db_status = "ok"
    except Exception as e:
        logger.warning("Postgres health check failed: %s", e)
        db_status = "down"

    redis_status = "down"
    try:
        r = redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        if r.ping():
            redis_status = "ok"
    except Exception as e:
        logger.warning("Redis health check failed: %s", e)
        redis_status = "down"

    status_code = 200 if (db_status == "ok" and redis_status == "ok") else 503
    return JSONResponse(
        content={"db": db_status, "redis": redis_status},
        status_code=status_code,
    )


@app.get("/metrics")
def get_metrics():
    """GET /metrics: JSON with jobs by status and total charged cost from the ledger."""
    jobs_by_status: dict[str, int] = {}
    total_charged_micro: int = 0
    try:
        with psycopg.connect(settings.DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, count(*) FROM jobs GROUP BY status;")
                for status, cnt in cur.fetchall():
                    jobs_by_status[status] = cnt

                cur.execute("SELECT COALESCE(SUM(amount_micro), 0) FROM account_ledger WHERE phase = 'settle';")
                row = cur.fetchone()
                if row:
                    total_charged_micro = int(row[0])
    except Exception as e:
        logger.error("Failed to collect metrics: %s", e)
        return JSONResponse(status_code=500, content={"error": "Failed to collect metrics"})

    return JSONResponse(
        content={
            "jobs_by_status": jobs_by_status,
            "total_charged_micro": total_charged_micro,
        }
    )


# Mount static files at root AFTER defining routes like /healthz and /metrics
ui_dir = get_ui_dir()
if ui_dir.exists():
    app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000)
