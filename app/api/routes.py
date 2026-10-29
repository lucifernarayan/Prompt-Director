import asyncio
import json
import logging
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import psycopg
from pydantic import BaseModel

from app.config import settings
from app.jobs import Conflict, InsufficientCredit, create_job, settle
from app.rate_limit import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/assist")


class AssistRequest(BaseModel):
    project_id: str
    idea: str
    current_dsl: str | None = None
    only_axis: str | None = None


@router.post("", status_code=202)
def create_assist_job(
    body: AssistRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    """POST /v1/assist: Create an async prompt assist job with idempotency and balance hold."""
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=400, detail="X-User-Id header is required")
    if not body.project_id or not body.project_id.strip() or not body.idea or not body.idea.strip():
        raise HTTPException(status_code=400, detail="project_id and idea are required")

    # Rate limit check via Redis
    allowed, retry_after = check_rate_limit(x_user_id)
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={"detail": "Rate limit exceeded"},
        )

    # Insert job and take hold inside one transaction
    try:
        with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
            job = create_job(
                conn,
                user_id=x_user_id,
                project_id=body.project_id,
                idempotency_key=idempotency_key,
                body=body.model_dump(exclude_none=True),
            )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except InsufficientCredit as e:
        raise HTTPException(status_code=402, detail=str(e))
    except Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error("Failed to create job: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return JSONResponse(
        status_code=202,
        content={"id": job["id"], "status": job["status"]},
    )


@router.get("/{id}/events")
async def get_assist_events(
    id: str,
    request: Request,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
):
    """GET /v1/assist/{id}/events: Stream job events via SSE, supporting Last-Event-ID replay."""
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=400, detail="X-User-Id header is required")

    # Verify job existence and ownership
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, status FROM jobs WHERE id = %s;", (id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")
            job_user_id, _ = row
            if job_user_id != x_user_id:
                raise HTTPException(status_code=403, detail="Forbidden")

    async def event_generator():
        current_id = 0
        if last_event_id:
            try:
                current_id = int(last_event_id)
            except ValueError:
                current_id = 0

        while True:
            if await request.is_disconnected():
                break

            events = []
            with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, type, data
                        FROM job_events
                        WHERE job_id = %s AND id > %s
                        ORDER BY id ASC;
                        """,
                        (id, current_id),
                    )
                    events = cur.fetchall()

            done_received = False
            for eid, etype, edata in events:
                current_id = eid
                data_str = json.dumps(edata)
                yield f"id: {eid}\nevent: {etype}\ndata: {data_str}\n\n"
                if etype == "done":
                    done_received = True
                    break

            if done_received:
                break

            await asyncio.sleep(0.05)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{id}/cancel")
def cancel_assist_job(
    id: str,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    """POST /v1/assist/{id}/cancel: Set cancel flag and release hold with charge 0."""
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=400, detail="X-User-Id header is required")

    with psycopg.connect(settings.DATABASE_URL, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, status, cancel_requested, lease_owner, lease_expires_at
                FROM jobs
                WHERE id = %s
                FOR UPDATE;
                """,
                (id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")

            j_id, owner_id, status, cancel_requested, lease_owner, lease_expires_at = row
            if owner_id != x_user_id:
                raise HTTPException(status_code=403, detail="Forbidden")

            # Terminal status is no-op
            if status in ("delivered", "failed", "blocked", "cancelled"):
                conn.rollback()
                return {"id": id, "status": status, "cancel_requested": cancel_requested}

            # Set cancel_requested flag
            cur.execute("UPDATE jobs SET cancel_requested = TRUE WHERE id = %s;", (id,))

            cur.execute("SELECT NOW();")
            now = cur.fetchone()[0]
            is_active_lease = (lease_owner is not None and lease_expires_at is not None and lease_expires_at > now)

            if not is_active_lease:
                settle(conn, id, delivered=False)
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = 'cancelled'
                    WHERE id = %s;
                    """,
                    (id,),
                )
                cur.execute(
                    """
                    INSERT INTO job_events (job_id, type, data)
                    VALUES (%s, 'done', '{"status": "cancelled"}'::jsonb);
                    """,
                    (id,),
                )
                conn.commit()
                return {"id": id, "status": "cancelled", "cancel_requested": True}
            else:
                conn.commit()
                return {"id": id, "status": "cancelling", "cancel_requested": True}
