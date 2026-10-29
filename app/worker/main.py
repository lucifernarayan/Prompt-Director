from datetime import datetime, timezone
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from typing import Any

import psycopg

from app.config import settings
from app.graph.runner import LeaseLost, run_job
from app.llm import chaos
from app.obs.logging import setup_logging
from app.worker.claim import claim_job, heartbeat, release_lease

logger = logging.getLogger(__name__)

running = True


def log_worker_event(event: str, job_id: str | None, call_key: str | None, worker_id: str) -> None:
    """Log structured JSON event to stdout."""
    payload = {
        "event": event,
        "job_id": job_id,
        "call_key": call_key,
        "worker_id": worker_id,
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def handle_shutdown(signum, frame):
    global running
    logger.info("Worker received signal %s, initiating graceful shutdown...", signum)
    running = False


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


class WorkerTerminated(Exception):
    """Raised to break out of runner when SIGTERM is received after node commit."""
    pass


def run_worker_loop(
    worker_id: str | None = None,
    lease_seconds: int | float | None = None,
    stop_after_empty: bool = False,
    max_jobs: int | None = None,
) -> None:
    """Execute the main claiming worker loop."""
    global running
    if worker_id is None:
        worker_id = socket.gethostname()
    if lease_seconds is None:
        lease_seconds = settings.WORKER_LEASE_SECONDS

    jobs_processed = 0

    while running:
        if max_jobs is not None and jobs_processed >= max_jobs:
            break

        # 1. Claim a job inside one transaction
        claimed_job: dict[str, Any] | None = None
        try:
            with psycopg.connect(settings.DATABASE_URL, autocommit=False) as claim_conn:
                claimed_job = claim_job(claim_conn, worker_id, lease_seconds)
                claim_conn.commit()
        except Exception as e:
            logger.warning("Error during job claim on worker %s: %s", worker_id, e)
            time.sleep(0.5)
            continue

        if claimed_job is None:
            if stop_after_empty:
                break
            # Sleep 0.5 s in slices of 0.1 s checking running flag
            for _ in range(5):
                if not running:
                    break
                time.sleep(0.1)
            continue

        job_id = claimed_job["id"]
        jobs_processed += 1
        log_worker_event("claim", job_id, None, worker_id)

        # 2. Start background heartbeat thread (heartbeat every lease_seconds / 3 seconds)
        hb_stop = threading.Event()
        hb_lost = threading.Event()
        hb_interval = max(0.1, float(lease_seconds) / 3.0)

        def heartbeat_worker() -> None:
            try:
                with psycopg.connect(settings.DATABASE_URL, autocommit=True) as hb_conn:
                    while not hb_stop.is_set():
                        hb_stop.wait(hb_interval)
                        if hb_stop.is_set():
                            break
                        ok = heartbeat(hb_conn, job_id, worker_id, lease_seconds)
                        if not ok:
                            hb_lost.set()
                            break
            except Exception as e:
                logger.warning("Heartbeat worker exception on %s: %s", worker_id, e)
                hb_lost.set()

        hb_thread = threading.Thread(target=heartbeat_worker, daemon=True)
        hb_thread.start()

        # 3. Define after_node_hook for node commit logging, SIGTERM shutdown, and chaos hook
        def after_node_hook(node_name: str) -> None:
            # Log node commit
            log_worker_event("node_commit", job_id, node_name, worker_id)

            # Check if SIGTERM arrived: release lease and exit cleanly
            if not running:
                with psycopg.connect(settings.DATABASE_URL, autocommit=True) as rel_conn:
                    release_lease(rel_conn, job_id, worker_id)
                raise WorkerTerminated(f"Worker {worker_id} stopping on SIGTERM")

            # Chaos hook: test hook for simulating delays/crashes
            # if chaos has pause_after:<node> = N (seconds), sleep N seconds in slices of 0.1 s while heartbeat keeps running
            try:
                with psycopg.connect(settings.DATABASE_URL, autocommit=True) as chaos_conn:
                    pause_val = chaos.get(chaos_conn, f"pause_after:{node_name}")
                if pause_val:
                    pause_s = float(pause_val)
                    if pause_s > 0:
                        t_end = time.time() + pause_s
                        while time.time() < t_end:
                            if hb_lost.is_set() or not running:
                                break
                            time.sleep(0.1)
            except Exception as e:
                logger.warning("Error evaluating pause_after chaos switch: %s", e)

        # 4. Execute runner in main thread
        try:
            run_job(
                conn_factory=lambda: psycopg.connect(settings.DATABASE_URL, autocommit=False),
                job_id=job_id,
                worker_id=worker_id,
                after_node_hook=after_node_hook,
                heartbeat_lost=hb_lost.is_set,
            )
        except LeaseLost:
            logger.info("Worker %s lost lease on job %s, dropping job.", worker_id, job_id)
        except WorkerTerminated:
            logger.info("Worker %s terminated mid-job %s, released lease.", worker_id, job_id)
            break
        except Exception as e:
            logger.exception("Unexpected error executing job %s on worker %s: %s", job_id, worker_id, e)
        finally:
            hb_stop.set()
            hb_thread.join(timeout=1.0)

    logger.info("Worker %s loop exited cleanly.", worker_id)


def main() -> None:
    setup_logging(settings.LOG_LEVEL)
    worker_id = socket.gethostname()
    logger.info("Worker %s started", worker_id)
    run_worker_loop(worker_id=worker_id)


if __name__ == "__main__":
    main()
