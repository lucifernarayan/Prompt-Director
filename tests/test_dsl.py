import time
import pytest
from hypothesis import given, strategies as text_strategies, settings as hyp_settings

from app.dsl import parse, validate, print_dsl, AXES


def test_valid_dsl_parsing_and_printing():
    raw = """
    # Leading comment
    Subject: A detective in a rain-slicked alley
    Emotion: tense and brooding
    Optics: 35mm anamorphic, shallow depth of field
    Motion: slow push-in # inline comments are part of the value
    Lighting: high-contrast neon reflections
    Style: neo-noir cinematic
    Audio: distant sirens and dripping water
    Continuity: trench coat from scene 1 [prop:P1]
    """
    axes, errors = parse(raw)
    assert errors == []
    assert len(axes) == 8
    assert axes["subject"] == "A detective in a rain-slicked alley"
    # Inline comment is preserved as part of the value
    assert axes["motion"] == "slow push-in # inline comments are part of the value"

    # Semantic validation passes
    val_errors = validate(axes)
    assert val_errors == []

    # Canonical printing
    printed = print_dsl(axes)
    lines = printed.split("\n")
    assert len(lines) == 8
    for i, axis in enumerate(AXES):
        assert lines[i].startswith(f"{axis}: ")
    assert "# Leading comment" not in printed

    # Idempotent round-trip
    reparsed, re_errors = parse(printed)
    assert re_errors == []
    assert reparsed == axes
    assert print_dsl(reparsed) == printed


def test_crlf_and_lone_cr_and_tabs():
    raw = "subject:\tA hero\r\nemotion:\tjoy\roptics: 50mm\n"
    axes, errors = parse(raw)
    assert errors == []
    assert axes["subject"] == "A hero"
    assert axes["emotion"] == "joy"
    assert axes["optics"] == "50mm"


def test_colon_in_value():
    raw = "subject: title: the return: chapter 1"
    axes, errors = parse(raw)
    assert errors == []
    assert axes["subject"] == "title: the return: chapter 1"


def test_duplicate_axis_keeps_first():
    raw = "subject: First occurrence\nSubject: Second occurrence\n"
    axes, errors = parse(raw)
    assert axes["subject"] == "First occurrence"
    assert any(e["code"] == "E_DUPLICATE_AXIS" for e in errors)


def test_unknown_axis_and_missing_colon():
    raw = "invalid_axis: value\njust plain text with no colon\n"
    axes, errors = parse(raw)
    assert any(e["code"] == "E_UNKNOWN_AXIS" for e in errors)
    assert any(e["code"] == "E_NO_COLON" for e in errors)


def test_validation_missing_and_empty_and_too_long():
    # Incomplete axes
    axes = {"subject": "Hero"}
    errors = validate(axes)
    assert any(e["code"] == "E_MISSING_AXIS" for e in errors)

    # Empty value
    axes_empty = {k: "val" for k in AXES}
    axes_empty["emotion"] = "   "
    errors_empty = validate(axes_empty)
    assert any(e["code"] == "E_EMPTY_VALUE" for e in errors_empty)

    # Value over 200 chars
    axes_long = {k: "val" for k in AXES}
    axes_long["style"] = "x" * 201
    errors_long = validate(axes_long)
    assert any(e["code"] == "E_VALUE_TOO_LONG" for e in errors_long)


def test_unicode_handling():
    raw = """
    subject: 英雄在雨中 (Hero in rain) 🌧️
    emotion: café naïve décor
    optics: עִבְרִית anamorphic
    motion: 🏃‍♂️ combining e\u0301 mark
    lighting: 昼夜
    style: 日本のアニメ
    audio: 🎵 binaural
    continuity: [shot:S1]
    """
    axes, errors = parse(raw)
    assert errors == []
    assert "英雄在雨中" in axes["subject"]
    assert "café" in axes["emotion"]
    assert "עִבְרִית" in axes["optics"]


@given(text_strategies.text())
@hyp_settings(max_examples=100, deadline=1000)
def test_hypothesis_parse_never_raises(s):
    # parse must never raise an unhandled exception on arbitrary input
    axes, errors = parse(s)
    assert isinstance(axes, dict)
    assert isinstance(errors, list)
