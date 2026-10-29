import argparse
import json
import sys
import psycopg

from app.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a job.")
    parser.add_argument("job_id", nargs="?", default=None, help="Job ID")
    parser.add_argument("--job-id", dest="flag_job_id", default=None)
    args = parser.parse_args()

    job_id = args.flag_job_id or args.job_id
    if not job_id:
        print("Usage: python -m app.tools.inspect <job_id>", file=sys.stderr)
        sys.exit(1)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Job details
            cur.execute(
                """
                SELECT id, user_id, status, lease_owner, charged_micro, quote_micro
                FROM jobs
                WHERE id = %s;
                """,
                (job_id,),
            )
            job_row = cur.fetchone()
            if not job_row:
                print(json.dumps({"error": f"Job '{job_id}' not found"}))
                sys.exit(1)

            _, user_id, status, lease_owner, charged_micro, quote_micro = job_row

            # Map 'delivered' to 'done' for CLI consumer compatibility
            effective_status = "done" if status == "delivered" else status

            # 2. LLM calls
            cur.execute(
                """
                SELECT call_key, model, cost_micro
                FROM llm_calls
                WHERE job_id = %s
                ORDER BY created_at ASC;
                """,
                (job_id,),
            )
            llm_calls = [
                {"call_key": r[0], "model": r[1], "cost": r[2]}
                for r in cur.fetchall()
            ]

            # 3. Checkpoints
            cur.execute(
                """
                SELECT seq, node_done
                FROM checkpoints
                WHERE job_id = %s
                ORDER BY seq ASC;
                """,
                (job_id,),
            )
            checkpoints = [
                {"seq": r[0], "node_done": r[1]}
                for r in cur.fetchall()
            ]

            # 4. Account ledger rows
            cur.execute(
                """
                SELECT phase, amount_micro
                FROM account_ledger
                WHERE job_id = %s
                ORDER BY created_at ASC;
                """,
                (job_id,),
            )
            ledger = [
                {"phase": r[0], "amount": r[1]}
                for r in cur.fetchall()
            ]

            # 5. User balance
            cur.execute(
                "SELECT balance_micro FROM accounts WHERE user_id = %s;",
                (user_id,),
            )
            bal_row = cur.fetchone()
            balance_micro = bal_row[0] if bal_row else 0

    result = {
        "job_id": job_id,
        "user_id": user_id,
        "status": effective_status,
        "raw_status": status,
        "lease_owner": lease_owner,
        "charged_micro": charged_micro,
        "quote_micro": quote_micro,
        "llm_calls": llm_calls,
        "checkpoints": checkpoints,
        "ledger": ledger,
        "balance_micro": balance_micro,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
