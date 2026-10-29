from app.dsl.parser import AXES


def print_dsl(axes: dict[str, str]) -> str:
    """Format axes dictionary into canonical DSL text.

    Uses fixed axis order, lowercase keys, 'key: value', one line per axis.
    Comments from parsed source text are dropped in canonical output.
    """
    lines: list[str] = []
    for axis in AXES:
        if axis in axes:
            lines.append(f"{axis}: {axes[axis]}")
    return "\n".join(lines)
