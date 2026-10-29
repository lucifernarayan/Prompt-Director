import psycopg
import pytest
from app.config import settings
from app.db.migrate import run_migrations
from app.fixtures_loader import load_fixtures
from app.memory import defense, store


@pytest.fixture(scope="module")
def db_conn():
    run_migrations()
    load_fixtures()
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        store.load_fixtures_memory(conn)
        yield conn


def test_t3_isolation_near_duplicate_query(db_conn):
    # Proj B's S1: "wide establishing shot of rain-slicked neo-seoul neon skyscraper avenue"
    # Query as proj_a using text from proj_b
    items = store.retrieve(
        db_conn,
        "proj_a",
        "wide establishing shot of rain-slicked neo-seoul neon skyscraper avenue",
        budget_tokens=500,
    )
    assert len(items) > 0

    # Ensure every single item belongs to proj_a
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM memory_items WHERE project_id = 'proj_a';")
        proj_a_ids = {row[0] for row in cur.fetchall()}

    for it in items:
        assert it.get("project_id") == "proj_a", f"Item {it['id']} from {it.get('project_id')} leaked into proj_a"
        assert "neo-seoul" not in it["body"], f"Proj B content leaked: {it['body']}"


def test_t3_isolation_literal_foreign_ids_in_query(db_conn):
    # Query literally mentioning proj_b and items S1 P2
    items = store.retrieve(
        db_conn,
        "proj_a",
        "referencing proj_b items S1 P2 with neon drone",
        budget_tokens=500,
    )

    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM memory_items WHERE project_id = 'proj_a';")
        proj_a_ids = {row[0] for row in cur.fetchall()}

    for it in items:
        assert it["id"] in proj_a_ids


def test_t3_isolation_build_request_contains_no_foreign_items(db_conn):
    items = store.retrieve(db_conn, "proj_a", "detective in neon alley", budget_tokens=500)
    clean_items, _, _ = defense.quarantine(items)
    req = defense.build_request("idea", {}, clean_items, "full", [])

    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM memory_items WHERE project_id = 'proj_a';")
        proj_a_ids = {row[0] for row in cur.fetchall()}

    for mem in req["memory"]:
        assert mem["id"] in proj_a_ids


def test_t3_retrieve_cannot_be_called_without_project_id(db_conn):
    # Missing project_id positional argument must raise TypeError
    with pytest.raises(TypeError):
        store.retrieve(db_conn, query="test", budget_tokens=100)  # type: ignore

    # None or empty string as project_id must raise ValueError
    with pytest.raises((ValueError, TypeError)):
        store.retrieve(db_conn, "", "test", 100)

    with pytest.raises((ValueError, TypeError)):
        store.retrieve(db_conn, None, "test", 100)  # type: ignore
