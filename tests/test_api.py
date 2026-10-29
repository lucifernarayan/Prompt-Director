import threading
import time
import uuid
import psycopg
import pytest
from starlette.testclient import TestClient

from app.api.main import app
from app.config import settings
from app.fixtures_loader import INITIAL_BALANCE_MICRO
from app.graph.runner import run_job
from app.pricing import FULL_QUOTE
from app.worker.claim import claim_job


def conn_factory():
    return psycopg.connect(settings.DATABASE_URL, autocommit=False)


@pytest.fixture
def client():
    return TestClient(app)


def test_missing_idempotency_key_returns_400(client):
    """POST /v1/assist returns 400 without Idempotency-Key."""
    resp = client.post(
        "/v1/assist",
        headers={"X-User-Id": "user_a"},
        json={"project_id": "proj_a", "idea": "cyberpunk detective"},
    )
    assert resp.status_code == 400
    assert "Idempotency-Key" in resp.json()["detail"]


def test_missing_user_id_returns_400(client):
    """POST /v1/assist returns 400 without X-User-Id."""
    resp = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"project_id": "proj_a", "idea": "cyberpunk detective"},
    )
    assert resp.status_code == 400
    assert "X-User-Id" in resp.json()["detail"]


def test_non_member_project_returns_403(client):
    """POST /v1/assist returns 403 for user not member of project."""
    resp = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-User-Id": "user_a"},
        json={"project_id": "proj_b", "idea": "cyberpunk detective"},
    )
    assert resp.status_code == 403


def test_insufficient_credit_returns_402(client):
    """POST /v1/assist returns 402 if account balance is short of quote."""
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE accounts SET balance_micro = 0 WHERE user_id = 'user_a';")

    resp = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-User-Id": "user_a"},
        json={"project_id": "proj_a", "idea": "cyberpunk detective"},
    )
    assert resp.status_code == 402
    assert "insufficient" in resp.json()["detail"].lower()


def test_idempotency_same_key_same_body_returns_same_job_202(client):
    """Same key + same body returns 202 with existing job ID, single hold."""
    key = str(uuid.uuid4())
    body = {"project_id": "proj_a", "idea": "cyberpunk detective"}

    resp1 = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": key, "X-User-Id": "user_a"},
        json=body,
    )
    assert resp1.status_code == 202
    job_id_1 = resp1.json()["id"]

    resp2 = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": key, "X-User-Id": "user_a"},
        json=body,
    )
    assert resp2.status_code == 202
    job_id_2 = resp2.json()["id"]

    assert job_id_1 == job_id_2

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs WHERE user_id = 'user_a' AND idempotency_key = %s;", (key,))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM account_ledger WHERE job_id = %s AND phase = 'hold';", (job_id_1,))
            assert cur.fetchone()[0] == 1


def test_idempotency_same_key_different_body_returns_409(client):
    """Same key + different body returns 409 Conflict."""
    key = str(uuid.uuid4())

    resp1 = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": key, "X-User-Id": "user_a"},
        json={"project_id": "proj_a", "idea": "first idea"},
    )
    assert resp1.status_code == 202

    resp2 = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": key, "X-User-Id": "user_a"},
        json={"project_id": "proj_a", "idea": "completely different idea"},
    )
    assert resp2.status_code == 409
    assert "Conflict" in resp2.json()["detail"] or "reused" in resp2.json()["detail"]


def test_cancel_releases_hold_and_sets_charge_zero(client):
    """POST /v1/assist/{id}/cancel releases the hold, refunds account, charges 0."""
    resp = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-User-Id": "user_a"},
        json={"project_id": "proj_a", "idea": "cyberpunk detective"},
    )
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    # Balance was held
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT balance_micro FROM accounts WHERE user_id = 'user_a';")
            assert cur.fetchone()[0] == INITIAL_BALANCE_MICRO - FULL_QUOTE

    # Call cancel
    resp_cancel = client.post(
        f"/v1/assist/{job_id}/cancel",
        headers={"X-User-Id": "user_a"},
    )
    assert resp_cancel.status_code == 200

    # Verify hold was released and account refunded
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT balance_micro FROM accounts WHERE user_id = 'user_a';")
            assert cur.fetchone()[0] == INITIAL_BALANCE_MICRO

            cur.execute("SELECT phase, amount_micro FROM account_ledger WHERE job_id = %s ORDER BY created_at;", (job_id,))
            phases = [r[0] for r in cur.fetchall()]
            assert "hold" in phases
            assert "release" in phases
            assert "settle" not in phases

            cur.execute("SELECT status, charged_micro, cancel_requested FROM jobs WHERE id = %s;", (job_id,))
            st, ch, cr = cur.fetchone()
            assert st == "cancelled"
            assert ch == 0
            assert cr is True


def test_cancel_authorization(client):
    """Cancel endpoint rejects unknown jobs with 404 and other users' jobs with 403."""
    resp_404 = client.post(
        f"/v1/assist/{uuid.uuid4()}/cancel",
        headers={"X-User-Id": "user_a"},
    )
    assert resp_404.status_code == 404

    # Create job for user_a
    resp_create = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-User-Id": "user_a"},
        json={"project_id": "proj_a", "idea": "cyberpunk detective"},
    )
    job_id = resp_create.json()["id"]

    # user_b attempts to cancel user_a's job
    resp_403 = client.post(
        f"/v1/assist/{job_id}/cancel",
        headers={"X-User-Id": "user_b"},
    )
    assert resp_403.status_code == 403


def test_redis_rate_limiting_returns_429_with_retry_after(client, monkeypatch):
    """Redis fixed-window rate limit returns 429 and Retry-After header."""
    user = f"rate_user_{uuid.uuid4().hex[:6]}"
    # Monkeypatch env vars to small limit of 2 requests per 60s
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")

    # Seed user in DB with balance and project membership
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO accounts (user_id, balance_micro) VALUES (%s, 10000000);", (user,))
            cur.execute("INSERT INTO project_members (user_id, project_id) VALUES (%s, 'proj_a');", (user,))

    # First request
    resp1 = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-User-Id": user},
        json={"project_id": "proj_a", "idea": "idea 1"},
    )
    assert resp1.status_code == 202

    # Second request
    resp2 = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-User-Id": user},
        json={"project_id": "proj_a", "idea": "idea 2"},
    )
    assert resp2.status_code == 202

    # Third request exceeds limit of 2
    resp3 = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-User-Id": user},
        json={"project_id": "proj_a", "idea": "idea 3"},
    )
    assert resp3.status_code == 429
    assert "Retry-After" in resp3.headers
    retry_after = int(resp3.headers["Retry-After"])
    assert 1 <= retry_after <= 60


def test_api_fairness_user_b_finishes_before_user_a_tail(client):
    """Fairness test via API: User A submits 20 jobs, User B submits 1 job. User B finishes before User A's tail."""
    a_jobs = []
    for i in range(20):
        resp = client.post(
            "/v1/assist",
            headers={"Idempotency-Key": f"api-fair-a-{i}-{uuid.uuid4().hex[:6]}", "X-User-Id": "user_a"},
            json={"project_id": "proj_a", "idea": f"idea a {i}"},
        )
        assert resp.status_code == 202
        a_jobs.append(resp.json()["id"])

    # User B submits 1 job
    resp_b = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": f"api-fair-b-{uuid.uuid4().hex[:6]}", "X-User-Id": "user_b"},
        json={"project_id": "proj_b", "idea": "idea b"},
    )
    assert resp_b.status_code == 202
    b_job_id = resp_b.json()["id"]

    # Run two worker threads
    stop_event = threading.Event()
    def worker_thread(wid: str):
        while not stop_event.is_set():
            with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
                j = claim_job(c, wid, lease_seconds=15)
            if not j:
                time.sleep(0.05)
                continue
            run_job(conn_factory, j["id"], wid)

    threads = [threading.Thread(target=worker_thread, args=(f"api-worker-{k}",)) for k in (1, 2)]
    for t in threads:
        t.start()

    # Poll until User B finishes, recording how many User A jobs have finished at that moment
    deadline = time.time() + 30
    b_finished_at_a_count = None
    while time.time() < deadline:
        with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM jobs WHERE id = %s;", (b_job_id,))
                b_st = cur.fetchone()[0]
                if b_st == "delivered":
                    cur.execute("SELECT count(*) FROM jobs WHERE id = ANY(%s) AND status = 'delivered';", (a_jobs,))
                    b_finished_at_a_count = cur.fetchone()[0]
                    break
        time.sleep(0.1)

    stop_event.set()
    for t in threads:
        t.join(timeout=2.0)

    assert b_finished_at_a_count is not None, "User B's job did not finish in time!"
    # User B must finish before User A's tail (e.g. before User A completes all 20 jobs)
    assert b_finished_at_a_count < 20, f"User B finished after all 20 of User A's jobs (count: {b_finished_at_a_count})"


def test_get_metrics_endpoint(client):
    """GET /metrics returns JSON with jobs by status and total charged cost from ledger."""
    # Create a job so metrics has data
    resp_post = client.post(
        "/v1/assist",
        headers={"Idempotency-Key": f"metrics-test-{uuid.uuid4().hex[:6]}", "X-User-Id": "user_a"},
        json={"project_id": "proj_a", "idea": "metrics test idea"},
    )
    assert resp_post.status_code == 202

    resp = client.get("/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs_by_status" in data
    assert "total_charged_micro" in data
    assert isinstance(data["jobs_by_status"], dict)
    assert isinstance(data["total_charged_micro"], int)
    assert data["jobs_by_status"].get("pending", 0) >= 1
