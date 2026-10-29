import json
from pathlib import Path
import re
from typing import Any

from app.memory.embedder import embed


def _vector_literal(vec: list[float]) -> str:
    """Format list of floats as a PostgreSQL vector literal '[x,y,z]'."""
    return f"[{','.join(str(round(x, 6)) for x in vec)}]"


def load_fixtures_memory(conn: Any) -> None:
    """Load fixtures/projects.json into memory_items idempotently."""
    fixtures_path = Path(__file__).resolve().parents[2] / "fixtures" / "projects.json"
    if not fixtures_path.exists():
        return

    with open(fixtures_path, "r", encoding="utf-8") as f:
        projects_data = json.load(f)

    with conn.cursor() as cur:
        for proj_id, proj_info in projects_data.items():
            # Ensure project exists
            cur.execute(
                """
                INSERT INTO projects (id, memory_version)
                VALUES (%s, 1)
                ON CONFLICT (id) DO NOTHING;
                """,
                (proj_id,),
            )

            for item in proj_info.get("items", []):
                item_id = item["id"]
                kind = item.get("kind", "shot")
                body = item.get("body", "")
                license_val = item.get("license", "permissive")
                emb = embed(body)
                vec_str = _vector_literal(emb)

                cur.execute(
                    """
                    INSERT INTO memory_items (project_id, id, kind, body, license, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (project_id, id) DO UPDATE SET
                        kind = EXCLUDED.kind,
                        body = EXCLUDED.body,
                        license = EXCLUDED.license,
                        embedding = EXCLUDED.embedding;
                    """,
                    (proj_id, item_id, kind, body, license_val, vec_str),
                )


def index_item(conn: Any, project_id: str, item: dict[str, Any]) -> int:
    """Upsert a memory row, compute its embedding, and bump projects.memory_version by 1.

    Executed in ONE transaction. Returns the new memory_version.
    """
    if not project_id or not isinstance(project_id, str):
        raise ValueError("project_id is required and must be a non-empty string")

    item_id = item["id"]
    kind = item.get("kind", "shot")
    body = item.get("body", "")
    license_val = item.get("license", "permissive")

    emb = embed(body)
    vec_str = _vector_literal(emb)

    with conn.cursor() as cur:
        # Upsert item
        cur.execute(
            """
            INSERT INTO memory_items (project_id, id, kind, body, license, embedding)
            VALUES (%s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (project_id, id) DO UPDATE SET
                kind = EXCLUDED.kind,
                body = EXCLUDED.body,
                license = EXCLUDED.license,
                embedding = EXCLUDED.embedding;
            """,
            (project_id, item_id, kind, body, license_val, vec_str),
        )

        # Bump version in projects
        cur.execute(
            """
            INSERT INTO projects (id, memory_version)
            VALUES (%s, 2)
            ON CONFLICT (id) DO UPDATE SET
                memory_version = projects.memory_version + 1
            RETURNING memory_version;
            """,
            (project_id,),
        )
        row = cur.fetchone()
        new_version = row[0] if row else 1

    return int(new_version)


def retrieve(
    conn: Any,
    project_id: str,
    query: str,
    budget_tokens: int,
) -> list[dict[str, Any]]:
    """Retrieve relevant memory items for project_id under budget_tokens.

    Hybrid search:
      - Keyword: Postgres full-text over generated tsv column using sanitized OR query (top 20)
      - Vector: Cosine distance (<=>) against query embedding (top 20, exact scan)
      - Fused with Reciprocal Rank Fusion (k=60), ties broken by id
      - Excludes items whose license is null or 'unknown'
      - Greedily packs in rank order under budget_tokens (len(body)//4 per item)
    """
    if not project_id or not isinstance(project_id, str):
        raise ValueError("project_id is required and must be a non-empty string")

    # 1. Keyword search (top 20)
    raw_tokens = [t for t in re.findall(r"[a-zA-Z0-9]+", query or "") if t]
    kw_ranked: list[dict[str, Any]] = []

    with conn.cursor() as cur:
        if raw_tokens:
            ts_query = " | ".join(raw_tokens)
            cur.execute(
                """
                SELECT project_id, id, kind, body, license, ts_rank(tsv, to_tsquery('english', %s)) AS rank
                FROM memory_items
                WHERE project_id = %s
                  AND license IS NOT NULL
                  AND LOWER(license) != 'unknown'
                  AND tsv @@ to_tsquery('english', %s)
                ORDER BY rank DESC, id ASC
                LIMIT 20;
                """,
                (ts_query, project_id, ts_query),
            )
            for row in cur.fetchall():
                kw_ranked.append(
                    {
                        "project_id": row[0],
                        "id": row[1],
                        "kind": row[2],
                        "body": row[3],
                        "license": row[4],
                    }
                )

        # 2. Vector search (top 20)
        q_emb = embed(query)
        vec_str = _vector_literal(q_emb)

        cur.execute(
            """
            SELECT project_id, id, kind, body, license, (embedding <=> %s::vector) AS dist
            FROM memory_items
            WHERE project_id = %s
              AND license IS NOT NULL
              AND LOWER(license) != 'unknown'
            ORDER BY embedding <=> %s::vector ASC, id ASC
            LIMIT 20;
            """,
            (vec_str, project_id, vec_str),
        )
        vec_ranked: list[dict[str, Any]] = []
        for row in cur.fetchall():
            vec_ranked.append(
                {
                    "project_id": row[0],
                    "id": row[1],
                    "kind": row[2],
                    "body": row[3],
                    "license": row[4],
                }
            )

    # 3. Reciprocal Rank Fusion (RRF, k=60)
    k = 60
    kw_ranks = {item["id"]: (rank + 1, item) for rank, item in enumerate(kw_ranked)}
    vec_ranks = {item["id"]: (rank + 1, item) for rank, item in enumerate(vec_ranked)}

    all_ids = set(kw_ranks.keys()) | set(vec_ranks.keys())
    fused: list[tuple[float, str, dict[str, Any]]] = []

    for item_id in all_ids:
        score = 0.0
        item_data = None
        if item_id in kw_ranks:
            score += 1.0 / (k + kw_ranks[item_id][0])
            item_data = kw_ranks[item_id][1]
        if item_id in vec_ranks:
            score += 1.0 / (k + vec_ranks[item_id][0])
            item_data = vec_ranks[item_id][1]

        if item_data is not None:
            fused.append((score, item_id, item_data))

    # Sort descending by score, ties broken by id ascending
    fused.sort(key=lambda x: (-x[0], x[1]))

    # 4. Greedy token budget packing (len(body)//4 per item)
    packed: list[dict[str, Any]] = []
    spent_tokens = 0

    for score, item_id, item in fused:
        token_cost = len(item["body"]) // 4
        if spent_tokens + token_cost <= budget_tokens:
            packed_item = dict(item)
            packed_item["fused_score"] = round(score, 6)
            packed.append(packed_item)
            spent_tokens += token_cost

    return packed
