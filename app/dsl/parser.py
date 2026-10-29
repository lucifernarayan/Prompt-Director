from typing import Any

AXES: tuple[str, ...] = (
    "subject",
    "emotion",
    "optics",
    "motion",
    "lighting",
    "style",
    "audio",
    "continuity",
)

MAX_INPUT_BYTES: int = 256 * 1024  # 256 KB
MAX_LINE_CHARS: int = 2000
MAX_ERRORS: int = 50


def parse(text: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Parse raw DSL text into an axes dictionary and a list of syntax errors.

    Single linear pass using str methods only. Never uses splitlines().
    """
    if not isinstance(text, str):
        return {}, [
            {
                "code": "E_INVALID_INPUT",
                "line": 1,
                "column": 1,
                "message": "Input must be a string",
            }
        ]

    # Rejection of oversized input before scanning (over 256 KB)
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        return {}, [
            {
                "code": "E_INPUT_TOO_LARGE",
                "line": 1,
                "column": 1,
                "message": "Input exceeds 256 KB limit",
            }
        ]

    # Normalize \r\n and \r to \n, then split on \n only
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    axes: dict[str, str] = {}
    errors: list[dict[str, Any]] = []

    for line_idx, raw_line in enumerate(lines, start=1):
        if len(errors) >= MAX_ERRORS:
            break

        # Check for line length over 2000 characters
        if len(raw_line) > MAX_LINE_CHARS:
            errors.append(
                {
                    "code": "E_LINE_TOO_LONG",
                    "line": line_idx,
                    "column": 1,
                    "message": "Line exceeds 2000 characters",
                }
            )
            # Do not store the value if line is too long
            continue

        # Check for invalid characters: control chars except tab (\t), plus U+2028 and U+2029
        has_invalid_char = False
        for col_idx, c in enumerate(raw_line, start=1):
            cp = ord(c)
            if (cp < 32 and c != "\t") or cp == 127 or cp in (0x2028, 0x2029):
                errors.append(
                    {
                        "code": "E_INVALID_CHAR",
                        "line": line_idx,
                        "column": col_idx,
                        "message": f"Invalid character U+{cp:04X}",
                    }
                )
                has_invalid_char = True
                if len(errors) >= MAX_ERRORS:
                    break

        if has_invalid_char:
            continue

        # Blank lines ignored
        stripped = raw_line.strip()
        if not stripped:
            continue

        # Comment lines: first non-space character is #
        lstripped = raw_line.lstrip(" \t")
        if lstripped.startswith("#"):
            continue

        # Colon delimiter check
        if ":" not in raw_line:
            errors.append(
                {
                    "code": "E_NO_COLON",
                    "line": line_idx,
                    "column": len(raw_line) - len(lstripped) + 1,
                    "message": "Missing colon delimiter on line",
                }
            )
            continue

        # Split at first colon
        key_raw, _, val_raw = raw_line.partition(":")
        col_start = len(raw_line) - len(lstripped) + 1
        key = key_raw.strip().lower()

        # Key must be one of the 8 canonical axes
        if key not in AXES:
            errors.append(
                {
                    "code": "E_UNKNOWN_AXIS",
                    "line": line_idx,
                    "column": col_start,
                    "message": f"Unknown axis: '{key_raw.strip()}'",
                }
            )
            continue

        # Duplicate axis: keep the first occurrence
        if key in axes:
            errors.append(
                {
                    "code": "E_DUPLICATE_AXIS",
                    "line": line_idx,
                    "column": col_start,
                    "message": f"Duplicate axis '{key}' (kept first occurrence)",
                }
            )
            continue

        # Store trimmed value
        axes[key] = val_raw.strip()

    return axes, errors
