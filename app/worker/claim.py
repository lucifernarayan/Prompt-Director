from typing import Any
import psycopg

CLAIM_ADVISORY_LOCK_ID: int = 884422


def claim_job(
    conn: psycopg.Connection,
    worker_id: str,
    lease_seconds: int | float = 15,
) -> dict[str, Any] | None:
    """Claim one job for worker_id inside a single transaction with lease fencing and fair ordering.

    Concurrency control design:
    1. pg_advisory_xact_lock:
       Serializes the evaluation of active unexpired lease counts per user across all concurrent
       claiming workers. Without this transaction-scoped advisory lock, two workers running at
       READ COMMITTED could simultaneously read user A's count as 1 and both claim a job for
       user A, violating the invariant that no user holds more than 2 active leases at once.
    2. FOR UPDATE OF j SKIP LOCKED:
       Prevents workers from blocking one another when selecting job rows. If another worker
       is already locking a candidate job in an in-flight transaction, this worker skips it
       immediately and claims the next eligible candidate without delay or lock contention.
    """
    with conn.cursor() as cur:
        # Acquire transaction-scoped advisory lock for consistent fairness counts
        cur.execute("SELECT pg_advisory_xact_lock(%s);", (CLAIM_ADVISORY_LOCK_ID,))

        # Select ONE job respecting fairness (fewest running jobs per user, max 2 concurrent leases)
        cur.execute(
            """
            SELECT j.id
            FROM jobs j
            WHERE j.status IN ('pending', 'queued', 'running')
              AND (j.lease_owner IS NULL OR j.lease_expires_at IS NULL OR j.lease_expires_at <= NOW())
              AND (j.run_after IS NULL OR j.run_after <= NOW())
              AND (
                  SELECT COUNT(*)
                  FROM jobs u
                  WHERE u.user_id = j.user_id
                    AND u.lease_expires_at > NOW()
                    AND u.lease_owner IS NOT NULL
                    AND u.status NOT IN ('delivered', 'failed', 'cancelled', 'blocked')
              ) < 2
            ORDER BY (
                SELECT COUNT(*)
                FROM jobs u
                WHERE u.user_id = j.user_id
                  AND u.lease_expires_at > NOW()
                  AND u.lease_owner IS NOT NULL
                  AND u.status NOT IN ('delivered', 'failed', 'cancelled', 'blocked')
            ) ASC, j.created_at ASC
            FOR UPDATE OF j SKIP LOCKED
            LIMIT 1;
            """
        )
        row = cur.fetchone()
        if not row:
            return None

        job_id = row[0]

        # Update lease and transition status to running
        cur.execute(
            """
            UPDATE jobs
            SET lease_owner = %s,
                lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                status = 'running'
            WHERE id = %s
            RETURNING id, user_id, project_id, idempotency_key, request_hash, status,
                      cancel_requested, lease_owner, lease_expires_at, run_after, quote_micro,
                      cost_cap_micro, hold_micro, charged_micro, flags, result, created_at;
            """,
            (worker_id, lease_seconds, job_id),
        )
        claimed = cur.fetchone()
        if not claimed:
            return None

        return {
            "id": claimed[0],
            "user_id": claimed[1],
            "project_id": claimed[2],
            "idempotency_key": claimed[3],
            "request_hash": claimed[4],
            "status": claimed[5],
            "cancel_requested": claimed[6],
            "lease_owner": claimed[7],
            "lease_expires_at": claimed[8],
            "run_after": claimed[9],
            "quote_micro": claimed[10],
            "cost_cap_micro": claimed[11],
            "hold_micro": claimed[12],
            "charged_micro": claimed[13],
            "flags": claimed[14] or {},
            "body": (claimed[14] or {}).get("body", {}),
            "result": claimed[15],
            "created_at": claimed[16],
        }


def heartbeat(
    conn: psycopg.Connection,
    job_id: str,
    worker_id: str,
    lease_seconds: int | float = 15,
) -> bool:
    """Extend lease expiration time if worker_id is still the lease owner."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET lease_expires_at = NOW() + (%s * INTERVAL '1 second')
            WHERE id = %s AND lease_owner = %s;
            """,
            (lease_seconds, job_id, worker_id),
        )
        return cur.rowcount == 1


def release_lease(
    conn: psycopg.Connection,
    job_id: str,
    worker_id: str,
) -> None:
    """Release a lease immediately by expiring it, only if worker_id is still the owner."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET lease_expires_at = NOW()
            WHERE id = %s AND lease_owner = %s;
            """,
            (job_id, worker_id),
        )
