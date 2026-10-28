from datetime import datetime, timezone
import json
import logging
import os
import signal
import socket
import sys
import time
import psycopg

from app.config import settings
from app.obs.logging import setup_logging

logger = logging.getLogger(__name__)

running = True


def handle_shutdown(signum, frame):
    global running
    logger.info("Worker received shutdown signal (%s), exiting...", signum)
    running = False


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


def check_postgres() -> bool:
    if not settings.DATABASE_URL:
        return False
    try:
        with psycopg.connect(settings.DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                return cur.fetchone() == (1,)
    except Exception:
        return False


def main():
    setup_logging(settings.LOG_LEVEL)
    worker_id = socket.gethostname()
    logger.info("Worker %s started", worker_id)

    while running:
        db_reachable = check_postgres()
        payload = {
            "worker_id": worker_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "db": "ok" if db_reachable else "down",
        }
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()

        # Sleep in small slices to be responsive to SIGTERM
        for _ in range(50):
            if not running:
                break
            time.sleep(0.1)

    logger.info("Worker %s stopped cleanly", worker_id)


if __name__ == "__main__":
    main()
