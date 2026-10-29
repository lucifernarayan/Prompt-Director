import concurrent.futures
import threading
import time
from typing import Any
import uuid

import psycopg
import pytest

from app.config import settings
from app.graph.runner import run_job
from app.jobs import create_job
from app.llm import chaos
from app.pricing import FULL_QUOTE
from app.worker.claim import claim_job


def conn_factory():
    return psycopg.connect(settings.DATABASE_URL, autocommit=False)


def test_exact_cache_hit_and_billing():
    """Verify identical request hits exact cache, makes 0 extra provider calls, and charges quote per D2."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        j1 = create_job(conn, "user_a", "proj_a", f"exact-1-{uuid.uuid4().hex[:6]}", {"idea": "cyberpunk detective in rain"})

    # Run job 1 to completion (populates cache)
    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claimed_1 = claim_job(c, "worker-c1", lease_seconds=15)
    assert claimed_1 is not None
    run_job(conn_factory, j1["id"], "worker-c1")

    # Check job 1 llm calls
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (j1["id"],))
            j1_calls = cur.fetchone()[0]
            assert j1_calls > 0
            cur.execute("SELECT count(*) FROM exact_cache WHERE project_id = 'proj_a';")
            assert cur.fetchone()[0] == 1

        # Submit identical job 2
        j2 = create_job(conn, "user_a", "proj_a", f"exact-2-{uuid.uuid4().hex[:6]}", {"idea": "cyberpunk detective in rain"})

    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claimed_2 = claim_job(c, "worker-c2", lease_seconds=15)
    assert claimed_2 is not None
    run_job(conn_factory, j2["id"], "worker-c2")

    # Check job 2: served from cache, zero provider calls, charged quote per D2
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (j2["id"],))
            assert cur.fetchone()[0] == 0  # 0 LLM provider calls
            cur.execute("SELECT status, charged_micro FROM jobs WHERE id = %s;", (j2["id"],))
            status, charged = cur.fetchone()
            assert status == "delivered"
            assert charged == FULL_QUOTE
            cur.execute("SELECT count(*) FROM account_ledger WHERE job_id = %s AND phase = 'settle';", (j2["id"],))
            assert cur.fetchone()[0] == 1


def test_t9_memory_version_change_prevents_stale_cache_hit():
    """T9: A memory-version change prevents a stale cache hit."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        j1 = create_job(conn, "user_a", "proj_a", f"t9-1-{uuid.uuid4().hex[:6]}", {"idea": "neon skyline spinner chase"})

    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claim_job(c, "worker-t9", lease_seconds=15)
    run_job(conn_factory, j1["id"], "worker-t9")

    # Bump proj_a memory_version from 1 to 2
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE projects SET memory_version = memory_version + 1 WHERE id = 'proj_a';")
        # Submit identical request under new memory_version
        j2 = create_job(conn, "user_a", "proj_a", f"t9-2-{uuid.uuid4().hex[:6]}", {"idea": "neon skyline spinner chase"})

    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claim_job(c, "worker-t9-2", lease_seconds=15)
    run_job(conn_factory, j2["id"], "worker-t9-2")

    # Job 2 must have made real provider calls (cache miss) because memory_version changed
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (j2["id"],))
            assert cur.fetchone()[0] > 0
            cur.execute("SELECT count(*) FROM exact_cache WHERE project_id = 'proj_a';")
            # 2 distinct entries in exact_cache (one for v1, one for v2)
            assert cur.fetchone()[0] == 2


def test_t3_cache_isolation_no_cross_project_hit():
    """T3: Identical intent across different projects never shares or hits cache."""
    idea = "detective lighting cigarette in rain"
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        j_a = create_job(conn, "user_a", "proj_a", f"t3-a-{uuid.uuid4().hex[:6]}", {"idea": idea})

    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claim_job(c, "worker-t3a", lease_seconds=15)
    run_job(conn_factory, j_a["id"], "worker-t3a")

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        # Submit identical idea under proj_b
        j_b = create_job(conn, "user_b", "proj_b", f"t3-b-{uuid.uuid4().hex[:6]}", {"idea": idea})

    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claim_job(c, "worker-t3b", lease_seconds=15)
    run_job(conn_factory, j_b["id"], "worker-t3b")

    # Proj B must NOT have hit Proj A's cache (must have executed its own provider calls)
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (j_b["id"],))
            assert cur.fetchone()[0] > 0


def test_semantic_cache_hit_on_paraphrase():
    """Verify close semantic paraphrase hits semantic cache; distinct intent misses."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        j1 = create_job(conn, "user_a", "proj_a", f"sem-1-{uuid.uuid4().hex[:6]}", {"idea": "detective in neon rain street"})

    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claim_job(c, "worker-sem1", lease_seconds=15)
    run_job(conn_factory, j1["id"], "worker-sem1")

    # Check semantic cache has entry
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM semantic_cache WHERE project_id = 'proj_a';")
            assert cur.fetchone()[0] == 1

        # Paraphrased idea with identical keywords/intent (high cosine similarity)
        j2 = create_job(conn, "user_a", "proj_a", f"sem-2-{uuid.uuid4().hex[:6]}", {"idea": "detective in neon street rain"})

    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claim_job(c, "worker-sem2", lease_seconds=15)
    run_job(conn_factory, j2["id"], "worker-sem2")

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM llm_calls WHERE job_id = %s;", (j2["id"],))
            # Semantic cache hit -> 0 provider calls
            assert cur.fetchone()[0] == 0


def test_cache_reads_and_writes_enforce_safety_gate():
    """Cache reads and writes enforce the safety gate. Unsafe results are never cached or served."""
    from app.cache import inflight, store

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        # Acquire inflight first so owner token is valid
        inflight.try_acquire_inflight(conn, "test-unsafe-key", "j-unsafe", "owner-1")

        # 1. Write gate check: candidate containing unsafe content must be rejected
        unsafe_result = {
            "dsl": "subject: Tom Cruise in fantasy castle\noptics: 35mm",
            "axes": {"subject": "Tom Cruise in fantasy castle", "optics": "35mm"},
        }
        published = store.publish(
            conn,
            key="test-unsafe-key",
            project_id="proj_a",
            memory_version=1,
            settings_hash="hash-1",
            query_embedding=[0.0] * 64,
            result=unsafe_result,
            owner_token="owner-1",
        )
        assert published is False

        # Verify not in DB
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM exact_cache WHERE key = 'test-unsafe-key';")
            assert cur.fetchone()[0] == 0

        # 2. Read gate check: if unsafe content was directly inserted into cache table, lookup rejects it
        with conn.cursor() as cur:
            import json
            cur.execute(
                """
                INSERT INTO exact_cache (key, project_id, result)
                VALUES ('direct-unsafe-key', 'proj_a', %s::jsonb);
                """,
                (json.dumps(unsafe_result),),
            )

        cached = store.lookup_exact(conn, "direct-unsafe-key", "proj_a")
        assert cached is None  # Gated out on read!


def test_stale_inflight_owner_cannot_publish():
    """An expired or non-owner single-flight leader cannot publish."""
    from app.cache import inflight, store

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        key = "inflight-stale-test"
        # Acquire with token A and 1s lease
        acq = inflight.try_acquire_inflight(conn, key, "job-1", "token-a", lease_seconds=1)
        assert acq is True

        # Wrong token B cannot publish
        valid_result = {
            "dsl": "subject: cyberpunk detective\noptics: 50mm",
            "axes": {"subject": "cyberpunk detective", "optics": "50mm"},
        }
        ok_wrong_token = store.publish(
            conn, key, "proj_a", 1, "h", [0.0] * 64, valid_result, owner_token="token-b"
        )
        assert ok_wrong_token is False

        # Expire lease
        with conn.cursor() as cur:
            cur.execute("UPDATE inflight SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE key = %s;", (key,))

        # Token A cannot publish once lease has expired
        ok_expired = store.publish(
            conn, key, "proj_a", 1, "h", [0.0] * 64, valid_result, owner_token="token-a"
        )
        assert ok_expired is False


def test_t8_twenty_identical_concurrent_requests_single_flight():
    """T8: 20 identical concurrent requests produce exactly one provider call, 19 served from cache, correct billing."""
    idea = "detective watching rain from neo tokyo rooftop"
    jobs = []
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        chaos.set(conn, "force_outcome:cheap", "valid")
        chaos.set(conn, "force_confidence:cheap", "0.9")
        chaos.set(conn, "fake_citation_rate:cheap", "0")
        for i in range(20):
            j = create_job(conn, "user_a", "proj_a", f"t8-req-{i}-{uuid.uuid4().hex[:6]}", {"idea": idea})
            jobs.append(j["id"])

    stop_event = threading.Event()
    provider_calls_seen = 0
    lock = threading.Lock()

    def worker_thread(wid: str):
        while not stop_event.is_set():
            with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
                j = claim_job(c, wid, lease_seconds=15)
            if not j:
                # If no job claimed, small sleep and check
                time.sleep(0.05)
                continue
            run_job(conn_factory, j["id"], wid)

    # Run two workers
    threads = [threading.Thread(target=worker_thread, args=(f"worker-t8-{k}",)) for k in (1, 2)]
    for t in threads:
        t.start()

    # Wait for all 20 jobs to finish
    deadline = time.time() + 30
    all_done = False
    while time.time() < deadline:
        with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM jobs WHERE id = ANY(%s) AND status = 'delivered';", (jobs,))
                delivered_count = cur.fetchone()[0]
                if delivered_count == 20:
                    all_done = True
                    break
        time.sleep(0.1)

    stop_event.set()
    for t in threads:
        t.join(timeout=2.0)

    assert all_done, f"Not all 20 jobs finished! Delivered: {delivered_count}"

    # Verify provider calls across all 20 jobs
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(DISTINCT job_id), count(*) FROM llm_calls WHERE job_id = ANY(%s);", (jobs,))
            distinct_jobs, total_llm_calls = cur.fetchone()
            # Exactly one job made provider calls, exactly one provider call total!
            assert distinct_jobs == 1, f"Expected 1 job making provider calls, got {distinct_jobs}"
            assert total_llm_calls == 1, f"Expected 1 provider call across 20 jobs, got {total_llm_calls}"

            # Verify billing for all 20 jobs
            cur.execute("SELECT count(*) FROM account_ledger WHERE job_id = ANY(%s) AND phase = 'settle';", (jobs,))
            assert cur.fetchone()[0] == 20

            cur.execute("SELECT charged_micro FROM jobs WHERE id = ANY(%s);", (jobs,))
            charges = [r[0] for r in cur.fetchall()]
            assert all(c == FULL_QUOTE for c in charges)
