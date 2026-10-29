from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from app.gate import normalizer
from app.gate.normalizer import normalize_text

GATE_VERSION: str = "1.0.0"
MAX_GATE_INPUT_BYTES: int = 256 * 1024  # 256 KB

REWRITES: dict[str, str] = {
    "RIGHTS_REAL_PERSON": "Please use an original fictional character instead of referencing real individuals.",
    "RIGHTS_LIVING_ARTIST": "Please describe the visual style using descriptive aesthetic techniques rather than referencing living artists.",
    "RIGHTS_TRADEMARK": "Please use generic descriptions rather than protected brand names or trademarks.",
    "SAFETY_UNSAFE": "Content violates safety policy: content involving unsafe or harmful themes is refused.",
    "GATE_ERROR": "Request blocked due to an internal gate processing error.",
}


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    blocks: list[dict[str, str]] = field(default_factory=list)
    gate_version: str = GATE_VERSION


def _load_rights_index() -> dict[str, list[tuple[tuple[str, ...], str]]]:
    """Load and index rights and safety phrases by their first token."""
    rights_path = Path(__file__).resolve().parents[2] / "fixtures" / "rights.json"
    index: dict[str, list[tuple[tuple[str, ...], str]]] = {}

    if not rights_path.exists():
        return index

    with open(rights_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    category_codes = [
        ("real_persons", "RIGHTS_REAL_PERSON"),
        ("living_artists", "RIGHTS_LIVING_ARTIST"),
        ("trademarks", "RIGHTS_TRADEMARK"),
        ("unsafe_terms", "SAFETY_UNSAFE"),
    ]

    for cat_name, code in category_codes:
        for phrase in data.get(cat_name, []):
            tokens = tuple(normalize_text(phrase))
            if tokens:
                first = tokens[0]
                if first not in index:
                    index[first] = []
                index[first].append((tokens, code))

    # Sort each list by length descending so longer phrases match first
    for k in index:
        index[k].sort(key=lambda item: len(item[0]), reverse=True)

    return index


# Index loaded once at import
BLOCKED_PHRASES_INDEX = _load_rights_index()


def check(fields: dict[str, Any]) -> GateResult:
    """Pure function safety and rights gate.

    Takes fields dict (e.g. user idea, or the 8 axes).
    Returns GateResult(allowed, blocks, gate_version).
    Fails closed on any error or invalid input.
    """
    try:
        # 1. Validation of input structure
        if not isinstance(fields, dict):
            return GateResult(
                allowed=False,
                blocks=[{"code": "GATE_ERROR", "field": "", "rewrite": REWRITES["GATE_ERROR"]}],
            )

        total_bytes = 0
        for k, v in fields.items():
            if not isinstance(v, str):
                return GateResult(
                    allowed=False,
                    blocks=[{"code": "GATE_ERROR", "field": str(k), "rewrite": REWRITES["GATE_ERROR"]}],
                )
            total_bytes += len(v.encode("utf-8"))

        if total_bytes > MAX_GATE_INPUT_BYTES:
            return GateResult(
                allowed=False,
                blocks=[{"code": "GATE_ERROR", "field": "", "rewrite": REWRITES["GATE_ERROR"]}],
            )

        # 2. Token-sequence matching per field
        blocks: list[dict[str, str]] = []

        for field_name, field_val in fields.items():
            tokens = normalizer.normalize_text(field_val)
            n_tokens = len(tokens)
            i = 0

            while i < n_tokens:
                curr_tok = tokens[i]
                matched = False

                if curr_tok in BLOCKED_PHRASES_INDEX:
                    for phrase_tokens, code in BLOCKED_PHRASES_INDEX[curr_tok]:
                        k_len = len(phrase_tokens)
                        if i + k_len <= n_tokens and tuple(tokens[i : i + k_len]) == phrase_tokens:
                            blocks.append(
                                {
                                    "code": code,
                                    "field": field_name,
                                    "rewrite": REWRITES.get(code, REWRITES["GATE_ERROR"]),
                                }
                            )
                            i += k_len
                            matched = True
                            break

                if not matched:
                    i += 1

        if blocks:
            return GateResult(allowed=False, blocks=blocks, gate_version=GATE_VERSION)

        return GateResult(allowed=True, blocks=[], gate_version=GATE_VERSION)

    except Exception:
        # Fail closed on any exception
        return GateResult(
            allowed=False,
            blocks=[{"code": "GATE_ERROR", "field": "", "rewrite": REWRITES["GATE_ERROR"]}],
            gate_version=GATE_VERSION,
        )
