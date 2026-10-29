import json
import threading
import time
import uuid
import psycopg
import pytest
from starlette.testclient import TestClient

from app.api.main import app
from app.config import settings
from app.graph.runner import run_job
from app.jobs import create_job
from app.worker.claim import claim_job


def conn_factory():
    return psycopg.connect(settings.DATABASE_URL, autocommit=False)


@pytest.fixture
def client():
    return TestClient(app)


def test_sse_authorization(client):
    """GET /v1/assist/{id}/events checks X-User-Id and ownership."""
    # 1. Missing X-User-Id returns 400
    resp_no_user = client.get(f"/v1/assist/{uuid.uuid4()}/events")
    assert resp_no_user.status_code == 400

    # 2. Unknown job returns 404
    resp_404 = client.get(f"/v1/assist/{uuid.uuid4()}/events", headers={"X-User-Id": "user_a"})
    assert resp_404.status_code == 404

    # 3. Create job for user_a
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(conn, "user_a", "proj_a", f"sse-auth-{uuid.uuid4().hex[:6]}", {"idea": "cyberpunk detective"})

    # 4. user_b accessing user_a's job returns 403
    resp_403 = client.get(f"/v1/assist/{job['id']}/events", headers={"X-User-Id": "user_b"})
    assert resp_403.status_code == 403


def test_t11_sse_reconnect_with_last_event_id_no_missing_no_duplicates(client):
    """T11: disconnect and reconnect with Last-Event-ID yields no missing and no duplicate axis events."""
    # 1. Create a job
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id="user_a",
            project_id="proj_a",
            idempotency_key=f"t11-{uuid.uuid4().hex[:6]}",
            body={"idea": "detective watching rain from neo tokyo rooftop"},
        )
        job_id = job["id"]

    # 2. Run the job to completion with worker
    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as c:
        claimed = claim_job(c, "worker-t11", lease_seconds=15)
    assert claimed is not None
    run_job(conn_factory, job_id, "worker-t11")

    # 3. Verify job finished and events are present in job_events table
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, type, data FROM job_events WHERE job_id = %s ORDER BY id ASC;", (job_id,))
            all_db_events = cur.fetchall()
            assert len(all_db_events) > 8

    # 4. First SSE connection: stream events until we receive 3 events, then disconnect
    first_stream_events = []
    last_seen_id = None

    with client.stream("GET", f"/v1/assist/{job_id}/events", headers={"X-User-Id": "user_a"}) as stream:
        assert stream.status_code == 200
        assert "text/event-stream" in stream.headers.get("content-type", "")

        current_event = {}
        for line in stream.iter_lines():
            line = line.strip()
            if not line:
                if current_event and "event" in current_event and "id" in current_event:
                    first_stream_events.append(dict(current_event))
                    last_seen_id = int(current_event["id"])
                    current_event = {}
                    # Disconnect after receiving 3 events (including some stage/axis events)
                    if len(first_stream_events) >= 3:
                        break
                continue
            if line.startswith("id:"):
                current_event["id"] = line.split(":", 1)[1].strip()
            elif line.startswith("event:"):
                current_event["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                current_event["data"] = json.loads(line.split(":", 1)[1].strip())

    assert len(first_stream_events) >= 3
    assert last_seen_id is not None

    # 5. Second SSE connection: reconnect with Last-Event-ID = last_seen_id
    second_stream_events = []

    with client.stream(
        "GET",
        f"/v1/assist/{job_id}/events",
        headers={"X-User-Id": "user_a", "Last-Event-ID": str(last_seen_id)},
    ) as stream:
        assert stream.status_code == 200

        current_event = {}
        for line in stream.iter_lines():
            line = line.strip()
            if not line:
                if current_event and "event" in current_event and "id" in current_event:
                    second_stream_events.append(dict(current_event))
                    if current_event["event"] == "done":
                        break
                    current_event = {}
                continue
            if line.startswith("id:"):
                current_event["id"] = line.split(":", 1)[1].strip()
            elif line.startswith("event:"):
                current_event["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                current_event["data"] = json.loads(line.split(":", 1)[1].strip())

    # 6. Verify replay guarantees
    first_ids = {int(e["id"]) for e in first_stream_events}
    second_ids = {int(e["id"]) for e in second_stream_events}

    # No duplicate event IDs between first and second connection
    intersection = first_ids.intersection(second_ids)
    assert not intersection, f"Duplicate event IDs found across reconnect: {intersection}"

    # Every event in second connection had id > last_seen_id
    for e in second_stream_events:
        assert int(e["id"]) > last_seen_id

    # Combine all received events
    all_received = first_stream_events + second_stream_events

    # Extract all axis events
    axis_events = [e for e in all_received if e["event"] == "axis"]
    axes_received = [e["data"]["axis"] for e in axis_events]

    # Verify: all 8 canonical axes present
    canonical_axes = ["subject", "emotion", "optics", "motion", "lighting", "style", "audio", "continuity"]
    for axis in canonical_axes:
        assert axis in axes_received, f"Missing axis event: {axis}"

    # Verify: NO duplicates among axis events
    assert len(axes_received) == 8, f"Expected exactly 8 axis events, got {len(axes_received)}: {axes_received}"
    assert len(set(axes_received)) == 8, f"Duplicate axis events received: {axes_received}"

    # Verify: final event is done
    done_events = [e for e in all_received if e["event"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["data"]["status"] == "delivered"
