import sys
import psycopg

from app.config import settings
from app.llm import chaos


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.llm.chaos_cli <set|get|clear|delete|list> [args...]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        if cmd == "set":
            if len(sys.argv) < 4:
                print("Usage: python -m app.llm.chaos_cli set <name> <value>")
                sys.exit(1)
            name = sys.argv[2]
            value = sys.argv[3]
            chaos.set(conn, name, value)
            print(f"OK: set {name}={value}")

        elif cmd == "get":
            if len(sys.argv) < 3:
                print("Usage: python -m app.llm.chaos_cli get <name>")
                sys.exit(1)
            name = sys.argv[2]
            val = chaos.get(conn, name)
            print(val if val is not None else "")

        elif cmd in ("clear", "delete"):
            if len(sys.argv) < 3:
                print("Usage: python -m app.llm.chaos_cli clear <name>")
                sys.exit(1)
            name = sys.argv[2]
            chaos.delete(conn, name)
            print(f"OK: cleared {name}")

        elif cmd == "list":
            switches = chaos.get_all(conn)
            for k, v in switches.items():
                print(f"{k}={v}")

        else:
            print(f"Unknown chaos command '{cmd}'")
            sys.exit(1)


if __name__ == "__main__":
    main()
