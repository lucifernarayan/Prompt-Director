"""State definitions and constants for Smart Prompt Director graph."""
from typing import Any

MEMORY_BUDGET_TOKENS: int = 500


def create_initial_state(job: dict[str, Any]) -> dict[str, Any]:
    """Create initial graph state from job dictionary."""
    body = job.get("body") or {}
    return {
        "request": {
            "idea": body.get("idea", ""),
            "current_dsl": body.get("current_dsl", ""),
            "only_axis": body.get("only_axis"),
        },
        "locked_axes": {},
        "retrieved": [],
        "clean_items": [],
        "quarantined_ids": [],
        "quarantined_bodies": [],
        "model_calls_count": 0,
        "spent_micro": 0,
        "best_valid": None,
        "errors": [],
        "reason_codes": [],
        "rewrites": [],
        "flags": [],
        "ungrounded_before": 0,
        "ungrounded_after": 0,
        "next_node": "intent",
    }
