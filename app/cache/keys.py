import hashlib
import json
from typing import Any

from app.gate import GATE_VERSION

SCHEMA_VERSION: str = "002_core"
DEFAULT_MODEL_CONFIG: dict[str, str] = {
    "cheap": "cheap",
    "strong": "strong",
}


def canonical_json(data: Any) -> str:
    """Format data as deterministic canonical JSON with sorted keys and compact separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def compute_settings_hash(
    only_axis: str | None = None,
    current_dsl: str | None = None,
    model_config: dict[str, str] | None = None,
    schema_version: str = SCHEMA_VERSION,
    gate_version: str = GATE_VERSION,
) -> str:
    """Compute sha256 settings_hash covering model config, schema version, gate version, only_axis, canonical current_dsl."""
    payload = {
        "model_config": model_config or DEFAULT_MODEL_CONFIG,
        "schema_version": schema_version,
        "gate_version": gate_version,
        "only_axis": only_axis or "",
        "current_dsl": (current_dsl or "").strip(),
    }
    canon = canonical_json(payload)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def compute_exact_key(
    normalized_intent: str,
    project_id: str,
    memory_version: int,
    settings_hash: str,
) -> str:
    """Compute exact cache key: sha256(canonical_json{normalized_intent, project_id, memory_version, settings_hash})."""
    payload = {
        "normalized_intent": normalized_intent.strip(),
        "project_id": project_id,
        "memory_version": int(memory_version),
        "settings_hash": settings_hash,
    }
    canon = canonical_json(payload)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
