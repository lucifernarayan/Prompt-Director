import json
import time
from pathlib import Path
import pytest

from app.gate import check, GATE_VERSION
from app.llm.mock import call


@pytest.fixture(scope="module")
def rights_data():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "rights.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_every_name_blocked_across_variations(rights_data):
    # Mapping of category to expected code
    code_map = {
        "real_persons": "RIGHTS_REAL_PERSON",
        "living_artists": "RIGHTS_LIVING_ARTIST",
        "trademarks": "RIGHTS_TRADEMARK",
        "unsafe_terms": "SAFETY_UNSAFE",
    }

    # Homoglyph replacements for letters that have lookalikes
    cyrillic_map = {
        "a": "\u0430", "A": "\u0410",
        "e": "\u0435", "E": "\u0415",
        "o": "\u043e", "O": "\u041e",
        "p": "\u0440", "P": "\u0420",
        "c": "\u0441", "C": "\u0421",
        "x": "\u0445", "X": "\u0425",
        "y": "\u0443", "Y": "\u0423",
        "i": "\u0456", "I": "\u0406",
        "s": "\u0455", "S": "\u0405",
        "k": "\u043a", "K": "\u041a",
        "h": "\u04bb", "H": "\u04ba",
        "t": "\u0442", "T": "\u0422",
        "m": "\u043c", "M": "\u041c",
    }

    for cat, code in code_map.items():
        for name in rights_data[cat]:
            # 1. Plain
            res = check({"idea": f"a scene featuring {name}"})
            assert not res.allowed, f"Plain {name} should be blocked"
            assert any(b["code"] == code for b in res.blocks)

            # 2. Upper case
            res_upper = check({"idea": f"A SCENE FEATURING {name.upper()}"})
            assert not res_upper.allowed, f"Upper {name} should be blocked"
            assert any(b["code"] == code for b in res_upper.blocks)

            # 3. Fullwidth (NFKC)
            fullwidth_name = "".join(
                chr(ord(c) + 0xFEE0) if 33 <= ord(c) <= 126 else c for c in name
            )
            res_fw = check({"idea": f"featuring {fullwidth_name}"})
            assert not res_fw.allowed, f"Fullwidth {name} should be blocked"
            assert any(b["code"] == code for b in res_fw.blocks)

            # 4. Cyrillic homoglyph (replace characters if in map)
            cyr_name = "".join(cyrillic_map.get(c, c) for c in name)
            res_cyr = check({"idea": f"featuring {cyr_name}"})
            assert not res_cyr.allowed, f"Homoglyph {name} should be blocked"
            assert any(b["code"] == code for b in res_cyr.blocks)

            # 5. Zero-width inserted
            zw_name = "\u200b".join(list(name))
            res_zw = check({"idea": f"featuring {zw_name}"})
            assert not res_zw.allowed, f"Zero-width {name} should be blocked"
            assert any(b["code"] == code for b in res_zw.blocks)

            # 6. Spaced-out ("t o m  c r u i s e")
            words = name.split()
            spaced_name = "  ".join(" ".join(list(w)) for w in words)
            res_spaced = check({"idea": f"featuring {spaced_name}"})
            assert not res_spaced.allowed, f"Spaced {name} should be blocked"
            assert any(b["code"] == code for b in res_spaced.blocks)


def test_allowed_near_misses_pass_zero_overblocking(rights_data):
    near_misses = rights_data["allowed_near_misses"]
    over_blocked = 0
    allowed = 0

    for item in near_misses:
        res = check({"idea": f"A creative concept about {item} in nature"})
        if res.allowed:
            allowed += 1
        else:
            over_blocked += 1

    print(f"\n[Gate Eval] Blocked/Total: {over_blocked}/{len(near_misses)}, Allowed/Total: {allowed}/{len(near_misses)}")
    assert over_blocked == 0, f"Over-blocking count must be 0, found {over_blocked}"


def test_blocked_term_inside_longer_word():
    # "atomic" contains "tom", "banker" contains "bank", "swiftest" contains "swift"
    res = check({"idea": "An atomic bunker with banker counting the swiftest runners"})
    assert res.allowed, "Words containing parts of blocked names should not match"


def test_t5_strong_model_unsafe_switch():
    req = {
        "idea": "portrait painting in a gallery",
        "locked_axes": {},
        "memory": [],
        "mode": "full",
        "reason_codes": [],
    }

    # Strong model with unsafe ON produces output blocked by gate
    res_unsafe = call("strong", req, seed=42, attempt=0, chaos={"unsafe:strong": True}, timeout_s=0.1)
    data_unsafe = json.loads(res_unsafe["text"])
    gate_unsafe = check({"subject": data_unsafe["dsl"]})
    assert not gate_unsafe.allowed
    assert any(b["code"] == "RIGHTS_REAL_PERSON" for b in gate_unsafe.blocks)

    # Strong model with unsafe OFF passes gate
    res_safe = call("strong", req, seed=42, attempt=0, chaos={"unsafe:strong": False}, timeout_s=0.1)
    data_safe = json.loads(res_safe["text"])
    # Parse dsl lines into fields dict
    fields_safe = dict(line.split(": ", 1) for line in data_safe["dsl"].split("\n") if ": " in line)
    gate_safe = check(fields_safe)
    assert gate_safe.allowed


def test_rewrite_rules_and_absence_of_blocked_term(rights_data):
    for name in ["Elon Musk", "Banksy", "Coca-Cola", "graphic gore"]:
        res = check({"idea": f"show {name}"})
        assert not res.allowed
        for block in res.blocks:
            assert block["rewrite"]
            # Rewrite must never contain the blocked term
            assert name.lower() not in block["rewrite"].lower()


def test_fail_closed_on_exceptions_and_invalid_inputs(monkeypatch):
    # Non-str value
    res_non_str = check({"idea": 12345})  # type: ignore
    assert not res_non_str.allowed
    assert res_non_str.blocks[0]["code"] == "GATE_ERROR"

    # Input over 256 KB
    res_large = check({"idea": "x" * (300 * 1024)})
    assert not res_large.allowed
    assert res_large.blocks[0]["code"] == "GATE_ERROR"

    # Exception inside normalization fails closed
    monkeypatch.setattr("app.gate.normalizer.normalize_text", lambda x: (_ for _ in ()).throw(RuntimeError("boom")))
    res_exc = check({"idea": "safe idea"})
    assert not res_exc.allowed
    assert res_exc.blocks[0]["code"] == "GATE_ERROR"


def test_performance_under_one_second():
    long_text = "a beautiful fantasy landscape with castles and dragons " * 800  # ~50,000 chars
    t0 = time.perf_counter()
    res = check({"idea": long_text})
    duration = time.perf_counter() - t0

    assert duration < 1.0, f"Gate took {duration:.3f}s (must be < 1s)"
    assert res.allowed


def test_check_determinism_and_no_mutation():
    fields = {"idea": "detective in rain", "style": "noir"}
    fields_copy = dict(fields)

    res1 = check(fields)
    res2 = check(fields)

    assert fields == fields_copy
    assert res1.allowed == res2.allowed
    assert res1.blocks == res2.blocks
    assert res1.gate_version == res2.gate_version == GATE_VERSION
