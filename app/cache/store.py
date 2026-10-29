import json
import logging
from typing import Any
import psycopg

from app.cache.inflight import is_valid_owner, release_inflight
from app.dsl import parse
from app.gate import check as gate_check

logger = logging.getLogger(__name__)

SEMANTIC_CACHE_THRESHOLD: float = 0.85


def _vector_literal(vec: list[float]) -> str:
    """Format list of floats as a PostgreSQL vector literal '[x,y,z]'."""
    return f"[{','.join(str(round(x, 6)) for x in vec)}]"


def _check_result_safety(result: dict[str, Any]) -> bool:
    """Run gate safety check on cached or candidate result. Returns True if allowed."""
    try:
        axes = result.get("axes")
        if not axes and result.get("dsl"):
            parsed_dsl, _ = parse(result["dsl"])
            axes = parsed_dsl.as_dict()

        if not axes:
            return False

        gate_res = gate_check(axes)
        return gate_res.allowed
    except Exception as e:
        logger.warning("Safety gate error during cache verification: %s", e)
        return False


def lookup_exact(
    conn: psycopg.Connection,
    key: str,
    project_id: str,
) -> dict[str, Any] | None:
    """Lookup result in exact_cache by key and project_id.

    Gates result before returning. Discards unsafe hits.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT result
            FROM exact_cache
            WHERE key = %s AND project_id = %s;
            """,
            (key, project_id),
        )
        row = cur.fetchone()
        if not row:
            return None

        result_data = row[0]
        if not _check_result_safety(result_data):
            logger.warning("Exact cache hit rejected by safety gate (key: %s)", key)
            return None

        return result_data


def lookup_semantic(
    conn: psycopg.Connection,
    project_id: str,
    memory_version: int,
    settings_hash: str,
    query_embedding: list[float],
    threshold: float = SEMANTIC_CACHE_THRESHOLD,
) -> dict[str, Any] | None:
    """Lookup closest match in semantic_cache at or above similarity threshold.

    Strictly filters by project_id, memory_version, and settings_hash.
    Gates result before returning.
    """
    vec_literal = _vector_literal(query_embedding)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT result, (1 - (embedding <=> %s::vector)) AS similarity
            FROM semantic_cache
            WHERE project_id = %s
              AND memory_version = %s
              AND settings_hash = %s
              AND (1 - (embedding <=> %s::vector)) >= %s
            ORDER BY similarity DESC
            LIMIT 1;
            """,
            (vec_literal, project_id, int(memory_version), settings_hash, vec_literal, threshold),
        )
        row = cur.fetchone()
        if not row:
            return None

        result_data = row[0]
        if not _check_result_safety(result_data):
            logger.warning("Semantic cache hit rejected by safety gate")
            return None

        return result_data


def publish(
    conn: psycopg.Connection,
    key: str,
    project_id: str,
    memory_version: int,
    settings_hash: str,
    query_embedding: list[float],
    result: dict[str, Any],
    owner_token: str,
) -> bool:
    """Publish a validated, safe result to exact_cache and semantic_cache.

    Enforces that:
    1. The result passes the safety gate.
    2. Only the current, unexpired single-flight owner token may publish.
    """
    # 1. Gate check candidate
    if not _check_result_safety(result):
        logger.warning("Refusing to cache result that failed safety gate (key: %s)", key)
        return False

    # 2. Verify single-flight leadership
    if not is_valid_owner(conn, key, owner_token):
        logger.warning("Refusing publish from expired or non-owner token (key: %s, token: %s)", key, owner_token)
        return False

    # 3. Store in exact and semantic caches
    vec_literal = _vector_literal(query_embedding)
    with conn.cursor() as cur:
        # Exact cache write
        cur.execute(
            """
            INSERT INTO exact_cache (key, project_id, result)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (key) DO UPDATE
            SET result = EXCLUDED.result, created_at = NOW();
            """,
            (key, project_id, json.dumps(result)),
        )

        # Semantic cache write
        cur.execute(
            """
            INSERT INTO semantic_cache (project_id, memory_version, settings_hash, embedding, result)
            VALUES (%s, %s, %s, %s::vector, %s::jsonb);
            """,
            (project_id, int(memory_version), settings_hash, vec_literal, json.dumps(result)),
        )

        # 4. Release single-flight lease
        release_inflight(conn, key, owner_token)

    return True
