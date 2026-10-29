import hashlib
import json
from typing import Any
import uuid

import psycopg
from psycopg.errors import UniqueViolation

from app.pricing import AXIS_EDIT_QUOTE, FULL_QUOTE, get_cost_cap


class Conflict(Exception):
    """Raised when an idempotency key is reused with a different request body."""
    pass


class InsufficientCredit(Exception):
    """Raised when an account does not have enough balance to hold the quote."""
    pass


def create_job(
    conn: psycopg.Connection,
    user_id: str,
    project_id: str,
    idempotency_key: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Create a new job with an atomic balance hold inside one transaction.

    Fails closed on any error.
    """
    if not user_id or not project_id or not idempotency_key:
        raise ValueError("user_id, project_id, and idempotency_key are required")

    request_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()

    with conn.cursor() as cur:
        # 1. Check idempotency
        cur.execute(
            """
            SELECT id, user_id, project_id, idempotency_key, request_hash, status,
                   quote_micro, cost_cap_micro, hold_micro, charged_micro, flags, result
            FROM jobs
            WHERE user_id = %s AND idempotency_key = %s;
            """,
            (user_id, idempotency_key),
        )
        existing = cur.fetchone()
        if existing:
            if existing[4] != request_hash:
                raise Conflict(f"Idempotency key '{idempotency_key}' reused with different request body")
            return {
                "id": existing[0],
                "user_id": existing[1],
                "project_id": existing[2],
                "idempotency_key": existing[3],
                "request_hash": existing[4],
                "status": existing[5],
                "quote_micro": existing[6],
                "cost_cap_micro": existing[7],
                "hold_micro": existing[8],
                "charged_micro": existing[9],
                "flags": existing[10],
                "result": existing[11],
                "body": body,
            }

        # 2. Check project membership
        cur.execute(
            "SELECT 1 FROM project_members WHERE user_id = %s AND project_id = %s;",
            (user_id, project_id),
        )
        if not cur.fetchone():
            raise PermissionError(f"User '{user_id}' is not a member of project '{project_id}'")

        # 3. Determine quote and cap
        only_axis = body.get("only_axis")
        quote = AXIS_EDIT_QUOTE if only_axis else FULL_QUOTE
        cost_cap = get_cost_cap(quote)

        # 4. Atomic balance hold
        cur.execute(
            """
            UPDATE accounts
            SET balance_micro = balance_micro - %s
            WHERE user_id = %s AND balance_micro >= %s
            RETURNING balance_micro;
            """,
            (quote, user_id, quote),
        )
        bal_row = cur.fetchone()
        if not bal_row:
            raise InsufficientCredit(f"User '{user_id}' has insufficient balance for quote {quote}")

        job_id = str(uuid.uuid4())

        # 5. Insert account ledger hold
        cur.execute(
            """
            INSERT INTO account_ledger (job_id, phase, amount_micro)
            VALUES (%s, 'hold', %s);
            """,
            (job_id, quote),
        )

        # 6. Insert job row
        flags_data = {"body": body}
        cur.execute(
            """
            INSERT INTO jobs (
                id, user_id, project_id, idempotency_key, request_hash, status,
                quote_micro, cost_cap_micro, hold_micro, charged_micro, flags
            )
            VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s, %s, 0, %s::jsonb);
            """,
            (job_id, user_id, project_id, idempotency_key, request_hash, quote, cost_cap, quote, json.dumps(flags_data)),
        )

    return {
        "id": job_id,
        "user_id": user_id,
        "project_id": project_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "status": "pending",
        "quote_micro": quote,
        "cost_cap_micro": cost_cap,
        "hold_micro": quote,
        "charged_micro": 0,
        "flags": {},
        "result": None,
        "body": body,
    }


def settle(conn: psycopg.Connection, job_id: str, delivered: bool) -> None:
    """Settle billing for a job inside the caller's transaction.

    If delivered:
      - insert ledger 'settle'
      - set jobs.charged_micro = quote_micro
    Else:
      - insert ledger 'release'
      - refund hold to user's account
      - set jobs.charged_micro = 0

    Unique (job_id, phase) constraint guarantees idempotent settlement.
    """
    with conn.cursor() as cur:
        # Check job details
        cur.execute(
            "SELECT user_id, quote_micro, hold_micro, status FROM jobs WHERE id = %s;",
            (job_id,),
        )
        job_row = cur.fetchone()
        if not job_row:
            return

        user_id, quote_micro, hold_micro, current_status = job_row

        # Check existing settlement ledger rows
        cur.execute(
            "SELECT phase FROM account_ledger WHERE job_id = %s AND phase IN ('settle', 'release');",
            (job_id,),
        )
        existing_phases = [r[0] for r in cur.fetchall()]

        if delivered:
            if "settle" in existing_phases:
                return  # Already settled
            try:
                cur.execute(
                    """
                    INSERT INTO account_ledger (job_id, phase, amount_micro)
                    VALUES (%s, 'settle', %s)
                    ON CONFLICT (job_id, phase) DO NOTHING;
                    """,
                    (job_id, quote_micro),
                )
            except UniqueViolation:
                return

            cur.execute(
                """
                UPDATE jobs
                SET charged_micro = %s, status = 'delivered'
                WHERE id = %s;
                """,
                (quote_micro, job_id),
            )
        else:
            if "release" in existing_phases or "settle" in existing_phases:
                return  # Already released or settled
            try:
                cur.execute(
                    """
                    INSERT INTO account_ledger (job_id, phase, amount_micro)
                    VALUES (%s, 'release', %s)
                    ON CONFLICT (job_id, phase) DO NOTHING;
                    """,
                    (job_id, hold_micro),
                )
            except UniqueViolation:
                return

            # Refund the held amount to user account
            cur.execute(
                """
                UPDATE accounts
                SET balance_micro = balance_micro + %s
                WHERE user_id = %s;
                """,
                (hold_micro, user_id),
            )

            new_status = current_status if current_status in ("blocked", "cancelled") else "failed"
            cur.execute(
                """
                UPDATE jobs
                SET charged_micro = 0, status = %s
                WHERE id = %s;
                """,
                (new_status, job_id),
            )
