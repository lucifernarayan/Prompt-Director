import time
import pytest

from app.dsl import parse


def test_line_too_long_under_one_second_and_no_store():
    # 50,000 characters on one line
    long_line = "subject: " + ("x" * 50_000)
    t0 = time.perf_counter()
    axes, errors = parse(long_line)
    duration = time.perf_counter() - t0

    assert duration < 1.0, f"Parsing took {duration:.3f}s (must be < 1s)"
    assert any(e["code"] == "E_LINE_TOO_LONG" for e in errors)
    # Does not store the value if line is too long
    assert "subject" not in axes


def test_input_too_large_rejected_before_scanning():
    # Over 256 KB (e.g. 300 KB)
    large_input = "subject: hero\n" * 25_000  # ~350 KB
    assert len(large_input.encode("utf-8")) > 256 * 1024

    t0 = time.perf_counter()
    axes, errors = parse(large_input)
    duration = time.perf_counter() - t0

    assert duration < 0.2
    assert axes == {}
    assert len(errors) == 1
    assert errors[0]["code"] == "E_INPUT_TOO_LARGE"


def test_error_collection_stops_at_50():
    # 100 lines of garbage with no colon
    garbage = "\n".join(f"garbage_line_{i}" for i in range(100))
    axes, errors = parse(garbage)
    assert len(errors) == 50
    assert all(e["code"] in ("E_NO_COLON", "E_UNKNOWN_AXIS") for e in errors)


def test_invalid_characters_and_u2028():
    # Control character \x00, \x07 (bell), \x1f (unit separator)
    raw_ctrl = "subject: hero\x00with null\n"
    axes, errors = parse(raw_ctrl)
    assert any(e["code"] == "E_INVALID_CHAR" for e in errors)

    # U+2028 LINE SEPARATOR inside a line must trigger E_INVALID_CHAR (not silently split)
    raw_u2028 = "subject: hero\u2028with line separator\n"
    axes, errors = parse(raw_u2028)
    assert any(e["code"] == "E_INVALID_CHAR" for e in errors)

    # U+2029 PARAGRAPH SEPARATOR inside a line must trigger E_INVALID_CHAR
    raw_u2029 = "subject: hero\u2029with paragraph separator\n"
    axes, errors = parse(raw_u2029)
    assert any(e["code"] == "E_INVALID_CHAR" for e in errors)
