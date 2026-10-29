import psycopg


def try_acquire_inflight(
    conn: psycopg.Connection,
    key: str,
    job_id: str,
    owner_token: str,
    lease_seconds: int = 30,
) -> bool:
    """Attempt to acquire single-flight ownership for key.

    Returns True if acquired (as a new leader or taking over an expired lease).
    Returns False if an active unexpired leader already holds this key.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO inflight (key, job_id, owner_token, lease_expires_at)
            VALUES (%s, %s, %s, NOW() + (%s * INTERVAL '1 second'))
            ON CONFLICT (key) DO UPDATE
            SET job_id = EXCLUDED.job_id,
                owner_token = EXCLUDED.owner_token,
                lease_expires_at = EXCLUDED.lease_expires_at
            WHERE inflight.lease_expires_at < NOW()
            RETURNING owner_token;
            """,
            (key, job_id, owner_token, lease_seconds),
        )
        row = cur.fetchone()
        return bool(row and row[0] == owner_token)


def is_valid_owner(
    conn: psycopg.Connection,
    key: str,
    owner_token: str,
) -> bool:
    """Verify that owner_token is still the active unexpired leader for key."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM inflight
            WHERE key = %s AND owner_token = %s AND lease_expires_at > NOW();
            """,
            (key, owner_token),
        )
        return cur.fetchone() is not None


def release_inflight(
    conn: psycopg.Connection,
    key: str,
    owner_token: str,
) -> None:
    """Release single-flight ownership on completion or failure."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM inflight
            WHERE key = %s AND owner_token = %s;
            """,
            (key, owner_token),
        )
