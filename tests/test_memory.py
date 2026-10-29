import psycopg
import pytest
from app.config import settings
from app.db.migrate import run_migrations
from app.fixtures_loader import load_fixtures
from app.memory import embedder, store


@pytest.fixture(scope="module")
def db_conn():
    run_migrations()
    load_fixtures()
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        store.load_fixtures_memory(conn)
        yield conn


def test_load_fixtures_memory_idempotent(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM memory_items WHERE id = 'S100';")

    # Running twice should not raise errors or duplicate records
    store.load_fixtures_memory(db_conn)
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM memory_items WHERE project_id = 'proj_a';")
        count_a = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM memory_items WHERE project_id = 'proj_b';")
        count_b = cur.fetchone()[0]

    assert count_a == 14  # 8 shots + 3 characters + 3 props
    assert count_b == 13  # 7 shots + 3 characters + 3 props


def test_retrieval_hybrid_returns_relevant_items(db_conn):
    # Query for rain-slicked alley / detective
    items = store.retrieve(db_conn, "proj_a", "rain slicked alley detective neon", budget_tokens=500)
    assert len(items) > 0

    item_ids = [it["id"] for it in items]
    # S1, S2, C1 are highly relevant to rain, alley, detective, neon
    assert any(x in item_ids for x in ["S1", "S2", "C1"])

    # Each item must have id, kind, body, license, and fused_score
    first = items[0]
    assert "id" in first
    assert "kind" in first
    assert "body" in first
    assert "license" in first
    assert "fused_score" in first
    assert first["fused_score"] > 0


def test_retrieval_rrf_deterministic(db_conn):
    items1 = store.retrieve(db_conn, "proj_a", "chase on catwalk during thunderstorm", budget_tokens=500)
    items2 = store.retrieve(db_conn, "proj_a", "chase on catwalk during thunderstorm", budget_tokens=500)

    assert len(items1) == len(items2)
    assert [x["id"] for x in items1] == [x["id"] for x in items2]
    assert [x["fused_score"] for x in items1] == [x["fused_score"] for x in items2]


def test_retrieval_unknown_license_never_returned(db_conn):
    # S6 in proj_a has license: "unknown"
    # Even if query matches S6 body directly
    items = store.retrieve(
        db_conn,
        "proj_a",
        "holographic billboard glitched reflection puddle noodle shop",
        budget_tokens=1000,
    )
    item_ids = [it["id"] for it in items]
    assert "S6" not in item_ids, "Item with unknown license must never be returned"


def test_budget_packing_never_exceeds_budget(db_conn):
    budget = 40  # Tight budget
    items = store.retrieve(db_conn, "proj_a", "neo tokyo detective spinner blaster", budget_tokens=budget)

    total_tokens = sum(len(it["body"]) // 4 for it in items)
    assert total_tokens <= budget, f"Packed tokens ({total_tokens}) exceeded budget ({budget})"


def test_index_item_bumps_version_and_becomes_retrievable(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT memory_version FROM projects WHERE id = 'proj_a';")
        old_ver = cur.fetchone()[0]

    new_item = {
        "id": "S100",
        "kind": "shot",
        "body": "cybernetic falcon soaring over towering solar array",
        "license": "permissive",
    }
    new_ver = store.index_item(db_conn, "proj_a", new_item)
    assert new_ver == old_ver + 1

    # Verify project version in DB
    with db_conn.cursor() as cur:
        cur.execute("SELECT memory_version FROM projects WHERE id = 'proj_a';")
        db_ver = cur.fetchone()[0]
    assert db_ver == new_ver

    # Verify item is retrievable
    retrieved = store.retrieve(db_conn, "proj_a", "cybernetic falcon solar array", budget_tokens=500)
    retrieved_ids = [it["id"] for it in retrieved]
    assert "S100" in retrieved_ids

    # Clean up test item
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM memory_items WHERE project_id = 'proj_a' AND id = 'S100';")
