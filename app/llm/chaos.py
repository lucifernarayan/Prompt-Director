import psycopg

CHAOS_DEFAULTS: dict[str, str] = {
    "llm_down:cheap": "false",
    "llm_down:strong": "false",
    "unsafe:cheap": "false",
    "unsafe:strong": "false",
    "obey_injection": "false",
    "fake_citation_rate:cheap": "0.15",
    "fake_citation_rate:strong": "0.05",
    "force_outcome:cheap": "",
    "force_outcome:strong": "",
    "force_confidence:cheap": "",
    "force_confidence:strong": "",
}


def get_all(conn: psycopg.Connection) -> dict[str, str]:
    """Retrieve all chaos switches from the chaos table merged with defaults."""
    result = dict(CHAOS_DEFAULTS)
    with conn.cursor() as cur:
        cur.execute("SELECT name, value FROM chaos;")
        for name, value in cur.fetchall():
            result[name] = value
    return result


def set(conn: psycopg.Connection, name: str, value: str) -> None:
    """Set or update a chaos switch in the chaos table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chaos (name, value)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value;
            """,
            (name, str(value)),
        )
