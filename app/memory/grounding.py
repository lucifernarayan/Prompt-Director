import re
from typing import Any

TAG_PATTERN = re.compile(r"\[([a-zA-Z0-9_-]+):([a-zA-Z0-9_-]+)\]")


def ground(
    value: str,
    retrieved_items: list[dict[str, Any]],
) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Ground continuity axis claims against retrieved project memory.

    Continuity value format:
      Claims separated by ';', each claim = text followed by one or more tags
      in '[kind:ID]' format (e.g. '[shot:S1]', '[prop:P2]'); or literal 'none'.

    Grounding verifies that a cited id exists in the retrieved project memory;
    it does not prove the claim text is supported by that record.

    A claim is ungrounded if it has no tags, or if any tag's (kind, id) is not
    in this job's retrieved items. Ungrounded claims are removed. If no grounded
    claims remain, the value becomes 'none'.

    Returns (new_value, citations, removed_claims).
    """
    if not value or not isinstance(value, str):
        return "none", [], []

    raw = value.strip()
    if raw.lower() == "none":
        return "none", [], []

    valid_pairs: set[tuple[str, str]] = {
        (item.get("kind", ""), item.get("id", "")) for item in retrieved_items
    }

    raw_claims = [c.strip() for c in raw.split(";") if c.strip()]
    grounded_claims: list[str] = []
    citations: list[tuple[str, str]] = []
    removed: list[str] = []

    for claim in raw_claims:
        tags = TAG_PATTERN.findall(claim)
        if not tags:
            # No tags at all -> ungrounded
            removed.append(claim)
            continue

        # Check if ALL tags in this claim exist in retrieved memory
        all_valid = all(tag in valid_pairs for tag in tags)

        if all_valid:
            grounded_claims.append(claim)
            for tag in tags:
                if tag not in citations:
                    citations.append(tag)
        else:
            removed.append(claim)

    if grounded_claims:
        new_value = "; ".join(grounded_claims)
    else:
        new_value = "none"

    return new_value, citations, removed


def has_ungrounded(value: str, retrieved_items: list[dict[str, Any]]) -> bool:
    """Check if a continuity value contains any ungrounded claims.

    Returns True if any ungrounded claim was found, False otherwise.
    Used to measure hallucination rates before grounding.
    """
    if not value or not isinstance(value, str):
        return False
    if value.strip().lower() == "none":
        return False

    _, _, removed = ground(value, retrieved_items)
    return len(removed) > 0


def hallucination_rate(
    raw_values: list[str],
    retrieved_items: list[dict[str, Any]],
) -> dict[str, float]:
    """Calculate the continuity hallucination rate before and after grounding.

    Returns dict with keys 'before' and 'after' (float rates between 0.0 and 1.0).
    """
    if not raw_values:
        return {"before": 0.0, "after": 0.0}

    n = len(raw_values)
    count_before = sum(1 for val in raw_values if has_ungrounded(val, retrieved_items))

    # After grounding
    grounded_vals = [ground(val, retrieved_items)[0] for val in raw_values]
    count_after = sum(1 for val in grounded_vals if has_ungrounded(val, retrieved_items))

    return {
        "before": round(count_before / n, 4),
        "after": round(count_after / n, 4),
    }
