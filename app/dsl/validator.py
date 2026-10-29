from typing import Any
from app.dsl.parser import AXES

MAX_VALUE_CHARS: int = 200


def validate(axes: dict[str, str]) -> list[dict[str, Any]]:
    """Validate that all 8 axes are present, non-empty, and under 200 characters."""
    errors: list[dict[str, Any]] = []

    for axis in AXES:
        if axis not in axes:
            errors.append(
                {
                    "code": "E_MISSING_AXIS",
                    "line": 0,
                    "column": 0,
                    "message": f"Missing required axis: '{axis}'",
                }
            )
            continue

        val = axes[axis]
        if not val or not val.strip():
            errors.append(
                {
                    "code": "E_EMPTY_VALUE",
                    "line": 0,
                    "column": 0,
                    "message": f"Axis '{axis}' has empty value",
                }
            )
        elif len(val) > MAX_VALUE_CHARS:
            errors.append(
                {
                    "code": "E_VALUE_TOO_LONG",
                    "line": 0,
                    "column": 0,
                    "message": f"Axis '{axis}' exceeds {MAX_VALUE_CHARS} characters",
                }
            )

    return errors
