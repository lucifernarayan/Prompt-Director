import json
from pathlib import Path


def test_fixtures_structure():
    fixtures_dir = Path(__file__).resolve().parents[1] / "fixtures"

    projects_path = fixtures_dir / "projects.json"
    rights_path = fixtures_dir / "rights.json"

    assert projects_path.exists(), "fixtures/projects.json must exist"
    assert rights_path.exists(), "fixtures/rights.json must exist"

    with open(projects_path, "r", encoding="utf-8") as f:
        projects = json.load(f)

    with open(rights_path, "r", encoding="utf-8") as f:
        rights = json.load(f)

    # 1. Projects file checks
    assert "proj_a" in projects and "proj_b" in projects

    for p_id in ("proj_a", "proj_b"):
        items = projects[p_id]["items"]
        ids = [item["id"] for item in items]

        # Unique IDs per project
        assert len(ids) == len(set(ids)), f"IDs in {p_id} must be unique"

        # Item counts: 6+ shots, 3+ characters, 3+ props
        shots = [item for item in items if item["kind"] == "shot"]
        chars = [item for item in items if item["kind"] == "character"]
        props = [item for item in items if item["kind"] == "prop"]

        assert len(shots) >= 6, f"{p_id} must have >= 6 shots"
        assert len(chars) >= 3, f"{p_id} must have >= 3 characters"
        assert len(props) >= 3, f"{p_id} must have >= 3 props"

        # License checks: each item has license, at least one unknown
        assert all("license" in item for item in items)
        assert any(item["license"] == "unknown" for item in items), f"{p_id} must have an item with unknown license"

    # Poisoned entries exist in proj_a or proj_b
    all_bodies = [item["body"] for p in projects.values() for item in p["items"]]
    assert any("ignore all rules" in b.lower() and "style" in b.lower() for b in all_bodies)
    assert any("S99" in b for b in all_bodies)

    # Near-duplicates between projects
    bodies_a = {item["body"].lower() for item in projects["proj_a"]["items"]}
    bodies_b = {item["body"].lower() for item in projects["proj_b"]["items"]}
    # Near-duplicates: token overlap without exact match
    near_dup = False
    for ba in bodies_a:
        tokens_a = set(ba.split())
        for bb in bodies_b:
            if ba != bb:
                tokens_b = set(bb.split())
                if len(tokens_a & tokens_b) >= 3:
                    near_dup = True
                    break
    assert near_dup, "Project B should have near-duplicates of Project A content"

    # 2. Rights file checks
    assert "real_persons" in rights
    assert "living_artists" in rights
    assert "trademarks" in rights
    assert "allowed_near_misses" in rights

    assert len(rights["real_persons"]) >= 3
    assert len(rights["living_artists"]) >= 3
    assert len(rights["trademarks"]) >= 3
    assert len(rights["allowed_near_misses"]) >= 3


def test_seed_idempotency_from_empty_database():
    """Starting from an empty database, seed creates memberships and running seed twice leaves balances and row counts unchanged."""
    import psycopg
    from app.config import settings
    from app.fixtures_loader import INITIAL_BALANCE_MICRO, load_fixtures

    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Empty the database completely
            cur.execute(
                "TRUNCATE jobs, checkpoints, llm_calls, job_events, account_ledger, exact_cache, semantic_cache, inflight, feedback, chaos CASCADE;"
            )
            cur.execute("DELETE FROM project_members;")
            cur.execute("DELETE FROM accounts;")
            cur.execute("DELETE FROM memory_items;")
            cur.execute("DELETE FROM projects;")

    # 1. First run of load_fixtures
    load_fixtures()

    # Verify memberships created
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, project_id FROM project_members ORDER BY user_id;")
            memberships = cur.fetchall()
            assert memberships == [("user_a", "proj_a"), ("user_b", "proj_b")]

            # Record initial row counts
            cur.execute("SELECT count(*) FROM projects;")
            proj_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM project_members;")
            pm_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM accounts;")
            acc_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM memory_items;")
            mem_count = cur.fetchone()[0]

            assert proj_count >= 2
            assert pm_count == 2
            assert acc_count == 2
            assert mem_count > 0

            # Simulate spending or balance deduction on user_a
            cur.execute("UPDATE accounts SET balance_micro = 7500000 WHERE user_id = 'user_a';")
            cur.execute("SELECT user_id, balance_micro FROM accounts ORDER BY user_id;")
            balances_before = dict(cur.fetchall())
            assert balances_before["user_a"] == 7500000
            assert balances_before["user_b"] == INITIAL_BALANCE_MICRO

    # 2. Second run of load_fixtures (simulating restart / concurrent execution)
    load_fixtures()

    # Verify row counts and balances remain unchanged
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM projects;")
            assert cur.fetchone()[0] == proj_count
            cur.execute("SELECT count(*) FROM project_members;")
            assert cur.fetchone()[0] == pm_count
            cur.execute("SELECT count(*) FROM accounts;")
            assert cur.fetchone()[0] == acc_count
            cur.execute("SELECT count(*) FROM memory_items;")
            assert cur.fetchone()[0] == mem_count

            # Balances must NOT be topped up or overwritten
            cur.execute("SELECT user_id, balance_micro FROM accounts ORDER BY user_id;")
            balances_after = dict(cur.fetchall())
            assert balances_after == balances_before
