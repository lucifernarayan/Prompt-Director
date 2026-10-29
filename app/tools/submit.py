import argparse
import sys
import uuid
import psycopg

from app.config import settings
from app.jobs import create_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit a prompt job.")
    parser.add_argument("user", nargs="?", default=None, help="User ID")
    parser.add_argument("project", nargs="?", default=None, help="Project ID")
    parser.add_argument("idea", nargs="?", default=None, help="Prompt idea")
    parser.add_argument("key", nargs="?", default=None, help="Optional idempotency key")

    parser.add_argument("--user", dest="flag_user", default=None)
    parser.add_argument("--project", dest="flag_project", default=None)
    parser.add_argument("--idea", dest="flag_idea", default=None)
    parser.add_argument("--key", dest="flag_key", default=None)

    args = parser.parse_args()

    user = args.flag_user or args.user
    project = args.flag_project or args.project
    idea = args.flag_idea or args.idea
    key = args.flag_key or args.key or str(uuid.uuid4())

    if not user or not project or not idea:
        print("Usage: python -m app.tools.submit <user> <project> <idea> [key]", file=sys.stderr)
        sys.exit(1)

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        job = create_job(
            conn,
            user_id=user,
            project_id=project,
            idempotency_key=key,
            body={"idea": idea},
        )

    # Print the job id as the last line of output
    print(job["id"])


if __name__ == "__main__":
    main()
