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
