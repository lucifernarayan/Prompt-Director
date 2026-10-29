import concurrent.futures
import threading
import time
from typing import Any
import uuid

import psycopg
import pytest

from app.config import settings
from app.fixtures_loader import INITIAL_BALANCE_MICRO
from app.graph.runner import LeaseLost, run_job
from app.jobs import create_job
from app.pricing import FULL_QUOTE
from app.worker.claim import claim_job, heartbeat, release_lease
from app.worker.pool import get_model_semaphore, set_model_semaphore


def conn_factory():
    return psycopg.connect(settings.DATABASE_URL, autocommit=False)


def test_competing_claimers_never_get_same_job():
    """Two competing worker threads claim jobs over 50 rounds and never get the same job."""
    for i in range(50):
        with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
            job = create_job(
                conn,
                user_id="user_a",
                project_id="proj_a",
                idempotency_key=f"compete-{i}-{uuid.uuid4().hex[:6]}",
                body={"idea": f"compete job {i}"},
            )

        claimed_jobs: list[str] = []
        lock = threading.Lock()

        def try_claim(worker_id: str):
            with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
                res = claim_job(c, worker_id, lease_seconds=10)
                if res and res["id"] == job["id"]:
                    with lock:
                        claimed_jobs.append(worker_id)

        t1 = threading.Thread(target=try_claim, args=("worker-1",))
        t2 = threading.Thread(target=try_claim, args=("worker-2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # At most one worker claimed this job
        assert len(claimed_jobs) == 1, f"Round {i}: Expected 1 claim, got {claimed_jobs}"

        # Mark job delivered and expire lease so fairness does not block next round
        with psycopg.connect(settings.DATABASE_URL, autocommit=True) as cleanup_conn:
            with cleanup_conn.cursor() as cur:
                cur.execute("UPDATE jobs SET status = 'delivered', lease_expires_at = NOW() WHERE id = %s;", (job["id"],))



def test_expired_lease_reclaimed_by_another_worker():
    """An expired lease is reclaimed by another worker and resumes cleanly."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key=f"reclaim-{uuid.uuid4().hex[:6]}",
            body={"idea": "detective in neon rain"},
        )

        # Worker 1 claims with very short lease (1 second)
        with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c1:
            claimed_1 = claim_job(c1, "worker-1", lease_seconds=1)
        assert claimed_1 is not None
        assert claimed_1["id"] == job["id"]

        # Before lease expires, worker-2 cannot claim
        with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c2:
            claimed_too_early = claim_job(c2, "worker-2", lease_seconds=10)
        assert claimed_too_early is None

        # Expire lease explicitly in DB
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = %s;",
                (job["id"],),
            )

        # Now worker-2 can reclaim
        with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c3:
            claimed_2 = claim_job(c3, "worker-2", lease_seconds=10)
        assert claimed_2 is not None
        assert claimed_2["id"] == job["id"]
        assert claimed_2["lease_owner"] == "worker-2"


def test_zombie_worker_raises_lease_lost_and_writes_nothing():
    """Worker A is paused past its lease, B takes over and finishes, A resumes and raises LeaseLost and writes nothing."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key=f"zombie-{uuid.uuid4().hex[:6]}",
            body={"idea": "detective in neon alley"},
        )

    # Worker A claims
    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claimed = claim_job(c, "worker-a", lease_seconds=2)
    assert claimed is not None

    # Run Worker A for 1 step (intent), then simulate Worker A pausing
    step_count = 0

    def hook_stop_after_one(node_name: str):
        nonlocal step_count
        step_count += 1
        # Stop worker A after intent
        raise KeyboardInterrupt("Simulated pause")

    try:
        run_job(conn_factory, job["id"], "worker-a", after_node_hook=hook_stop_after_one)
    except KeyboardInterrupt:
        pass

    assert step_count == 1

    # Inspect checkpoints after Worker A's first step
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM checkpoints WHERE job_id = %s;", (job["id"],))
            ckpts_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (job["id"],))
            calls_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM job_events WHERE job_id = %s;", (job["id"],))
            events_before = cur.fetchone()[0]

            # Expire Worker A's lease
            cur.execute(
                "UPDATE jobs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = %s;",
                (job["id"],),
            )

    # Worker B reclaims and runs to completion
    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claimed_b = claim_job(c, "worker-b", lease_seconds=15)
    assert claimed_b is not None
    assert claimed_b["id"] == job["id"]

    run_job(conn_factory, job["id"], "worker-b")

    # Verify B finished the job
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, charged_micro, lease_owner FROM jobs WHERE id = %s;", (job["id"],))
            st, ch, lo = cur.fetchone()
            assert st == "delivered"
            assert ch == FULL_QUOTE
            assert lo == "worker-b"

            cur.execute("SELECT count(*) FROM checkpoints WHERE job_id = %s;", (job["id"],))
            ckpts_b = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (job["id"],))
            calls_b = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM job_events WHERE job_id = %s;", (job["id"],))
            events_b = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM account_ledger WHERE job_id = %s AND phase = 'settle';", (job["id"],))
            settle_b = cur.fetchone()[0]
            assert settle_b == 1

    # Worker A resumes and tries to run: must raise LeaseLost
    with pytest.raises(LeaseLost):
        run_job(conn_factory, job["id"], "worker-a")

    # Verify Worker A wrote NOTHING further
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM checkpoints WHERE job_id = %s;", (job["id"],))
            assert cur.fetchone()[0] == ckpts_b
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (job["id"],))
            assert cur.fetchone()[0] == calls_b
            cur.execute("SELECT count(*) FROM job_events WHERE job_id = %s;", (job["id"],))
            assert cur.fetchone()[0] == events_b
            cur.execute("SELECT count(*) FROM account_ledger WHERE job_id = %s AND phase = 'settle';", (job["id"],))
            assert cur.fetchone()[0] == 1


def test_heartbeat_keeps_lease_alive():
    """Heartbeat extends lease expiration so job is not stolen while running."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key=f"hb-{uuid.uuid4().hex[:6]}",
            body={"idea": "detective in neon alley"},
        )

    # Claim with 1 second lease
    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claimed = claim_job(c, "worker-hb", lease_seconds=1)
    assert claimed is not None

    # Heartbeat 3 times every 0.3s
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as hb_conn:
        for _ in range(3):
            time.sleep(0.3)
            ok = heartbeat(hb_conn, job["id"], "worker-hb", lease_seconds=1)
            assert ok is True

    # Total elapsed ~0.9s from initial claim; lease expiration should be ~1s in future
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT lease_expires_at > NOW() FROM jobs WHERE id = %s;", (job["id"],))
            assert cur.fetchone()[0] is True

    # Stale heartbeat (wrong worker_id) returns False
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as hb_conn:
        bad_ok = heartbeat(hb_conn, job["id"], "other-worker", lease_seconds=1)
        assert bad_ok is False


def test_terminal_jobs_and_future_run_after_never_claimed():
    """Terminal jobs and jobs with run_after in future are never claimed."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        j1 = create_job(conn, "user_a", "proj_a", f"term-1-{uuid.uuid4().hex[:6]}", {"idea": "idea 1"})
        j2 = create_job(conn, "user_a", "proj_a", f"term-2-{uuid.uuid4().hex[:6]}", {"idea": "idea 2"})

        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status = 'delivered' WHERE id = %s;", (j1["id"],))
            cur.execute("UPDATE jobs SET run_after = NOW() + INTERVAL '10 minutes' WHERE id = %s;", (j2["id"],))

    # Try claiming: neither should be claimed
    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claimed = claim_job(c, "worker-1", lease_seconds=10)
    assert claimed is None

    # Once run_after has passed, j2 can be claimed
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET run_after = NOW() - INTERVAL '1 second' WHERE id = %s;", (j2["id"],))

    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claimed_after = claim_job(c, "worker-1", lease_seconds=10)
    assert claimed_after is not None
    assert claimed_after["id"] == j2["id"]


def test_fairness_ordering_and_max_two_leases_per_user():
    """Fairness: 20 jobs from User A and 1 from User B: B finishes before A's last 10 jobs.
    No user holds more than 2 leases at once."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        # User A's 20 jobs
        a_jobs = []
        for i in range(20):
            job_a = create_job(
                conn,
                user_id="user_a",
                project_id="proj_a",
                idempotency_key=f"fair-a-{i}-{uuid.uuid4().hex[:6]}",
                body={"idea": f"a idea {i}"},
            )
            a_jobs.append(job_a["id"])

        # User B's 1 job
        job_b = create_job(
            conn,
            user_id="user_b",
            project_id="proj_b",
            idempotency_key=f"fair-b-{uuid.uuid4().hex[:6]}",
            body={"idea": "b idea"},
        )
        b_id = job_b["id"]

    completion_order = []
    max_active_leases = {"user_a": 0, "user_b": 0}
    lock = threading.Lock()
    stop_event = threading.Event()

    def sampler():
        while not stop_event.is_set():
            with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT user_id, COUNT(*)
                        FROM jobs
                        WHERE lease_expires_at > NOW()
                          AND lease_owner IS NOT NULL
                          AND status NOT IN ('delivered', 'failed', 'cancelled', 'blocked')
                        GROUP BY user_id;
                        """
                    )
                    for uid, cnt in cur.fetchall():
                        with lock:
                            if cnt > max_active_leases.get(uid, 0):
                                max_active_leases[uid] = cnt
            time.sleep(0.05)

    sampler_thread = threading.Thread(target=sampler, daemon=True)
    sampler_thread.start()

    def worker_thread(wid: str):
        while not stop_event.is_set():
            with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
                j = claim_job(c, wid, lease_seconds=15)
            if not j:
                break
            run_job(conn_factory, j["id"], wid)
            with lock:
                completion_order.append(j["id"])

    t1 = threading.Thread(target=worker_thread, args=("worker-f1",))
    t2 = threading.Thread(target=worker_thread, args=("worker-f2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    stop_event.set()
    sampler_thread.join(timeout=1.0)

    # Check results
    assert b_id in completion_order
    b_index = completion_order.index(b_id)
    # B finishes before A's last 10 jobs (i.e. among first 11 jobs finished out of 21)
    assert b_index < 11, f"User B's job finished at position {b_index} (expected < 11)"
    # Max active leases for any user was <= 2
    assert max_active_leases["user_a"] <= 2, f"User A exceeded max 2 leases: {max_active_leases['user_a']}"
    assert max_active_leases["user_b"] <= 2, f"User B exceeded max 2 leases: {max_active_leases['user_b']}"


def test_bounded_model_pool():
    """With MODEL_POOL_SIZE=2 and 6 concurrent attempts, maximum concurrency is 2."""
    set_model_semaphore(2)
    current_concurrency = 0
    max_observed_concurrency = 0
    lock = threading.Lock()

    def task():
        nonlocal current_concurrency, max_observed_concurrency
        sem = get_model_semaphore()
        with sem:
            with lock:
                current_concurrency += 1
                if current_concurrency > max_observed_concurrency:
                    max_observed_concurrency = current_concurrency
            time.sleep(0.1)
            with lock:
                current_concurrency -= 1

    threads = [threading.Thread(target=task) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_observed_concurrency == 2
    # Restore semaphore to default
    set_model_semaphore(4)


def test_sigterm_shutdown_releases_lease():
    """SIGTERM-style shutdown releases the lease so another worker claims at once."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key=f"sigterm-{uuid.uuid4().hex[:6]}",
            body={"idea": "detective in neon alley"},
        )

    # Worker 1 claims
    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c1:
        claimed_1 = claim_job(c1, "worker-sig1", lease_seconds=60)
    assert claimed_1 is not None

    # Simulate shutdown: worker 1 releases lease
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as c_rel:
        release_lease(c_rel, job["id"], "worker-sig1")

    # Worker 2 can claim immediately
    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c2:
        claimed_2 = claim_job(c2, "worker-sig2", lease_seconds=60)
    assert claimed_2 is not None
    assert claimed_2["id"] == job["id"]
    assert claimed_2["lease_owner"] == "worker-sig2"
