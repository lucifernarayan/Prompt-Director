import hashlib
import json
import os
import re
import time
from typing import Any, Callable

from app.config import settings


class ProviderRateLimit(Exception):
    def __init__(self, message: str = "Rate limit exceeded (429)", tokens_in: int = 0):
        super().__init__(message)
        self.tokens_in = tokens_in


class ProviderTimeout(Exception):
    def __init__(self, message: str = "Provider call timed out", tokens_in: int = 0):
        super().__init__(message)
        self.tokens_in = tokens_in


class ProviderDown(Exception):
    def __init__(self, message: str = "Provider is down", tokens_in: int = 0):
        super().__init__(message)
        self.tokens_in = tokens_in


class ProviderCancelled(Exception):
    def __init__(self, message: str = "Provider call was cancelled", tokens_in: int = 0):
        super().__init__(message)
        self.tokens_in = tokens_in


def _is_truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val > 0
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "on")
    return False


def _sleep_with_cancel(duration_s: float, should_cancel: Callable[[], bool] | None, tokens_in: int) -> None:
    time_scale = float(os.environ.get("TIME_SCALE", getattr(settings, "TIME_SCALE", 1.0)))
    scaled_duration = max(0.0001, duration_s * time_scale)
    elapsed = 0.0
    slice_s = 0.05

    while elapsed < scaled_duration:
        if should_cancel and should_cancel():
            raise ProviderCancelled("Cancelled during mock latency", tokens_in=tokens_in)
        sleep_chunk = min(slice_s, scaled_duration - elapsed)
        time.sleep(sleep_chunk)
        elapsed += sleep_chunk

    if should_cancel and should_cancel():
        raise ProviderCancelled("Cancelled after mock latency", tokens_in=tokens_in)


def call(
    model: str,
    request: dict[str, Any],
    seed: int,
    attempt: int,
    chaos: dict[str, Any],
    timeout_s: float = 10.0,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Deterministic mock provider call.

    Outcome is derived from SHA-256 of model|seed|attempt|canonical_request_json.
    Never uses Python's built-in hash().
    """
    canonical_req = json.dumps(request, sort_keys=True)
    tokens_in = max(1, len(canonical_req) // 4)

    # 1. Chaos switch: llm_down
    if _is_truthy(chaos.get(f"llm_down:{model}")):
        raise ProviderDown(f"Mock provider {model} is down via chaos switch", tokens_in=tokens_in)

    # 2. Derive deterministic random numbers from sha256
    hash_key = f"{model}|{seed}|{attempt}|{canonical_req}"
    h = hashlib.sha256(hash_key.encode("utf-8")).hexdigest()

    p_outcome = int(h[0:8], 16) / 0xFFFFFFFF
    p_confidence = int(h[8:16], 16) / 0xFFFFFFFF
    p_latency = int(h[16:24], 16) / 0xFFFFFFFF
    p_fake_cite = int(h[24:32], 16) / 0xFFFFFFFF

    # 3. Model outcome determination
    # Base latency: cheap 0.3-2.0s, strong 2.0-8.0s
    if model == "cheap":
        base_latency = 0.3 + p_latency * 1.7
        # Outcome bands: 5% rate-limit, 10% malformed JSON, 5% invalid DSL, 80% valid
        if p_outcome < 0.05:
            _sleep_with_cancel(base_latency, should_cancel, tokens_in)
            raise ProviderRateLimit("Provider rate limit 429", tokens_in=tokens_in)
        elif p_outcome < 0.15:
            outcome = "malformed_json"
        elif p_outcome < 0.20:
            outcome = "invalid_dsl"
        else:
            outcome = "valid"
    else:  # strong model
        base_latency = 2.0 + p_latency * 6.0
        # Outcome bands: 3% hang, 97% valid
        if p_outcome < 0.03:
            # Hang sleeps until timeout_s then raises ProviderTimeout
            _sleep_with_cancel(timeout_s, should_cancel, tokens_in)
            raise ProviderTimeout(f"Provider hang exceeded {timeout_s}s", tokens_in=tokens_in)
        else:
            outcome = "valid"

    # Sleep realistic latency (scaled by TIME_SCALE)
    _sleep_with_cancel(base_latency, should_cancel, tokens_in)

    # 4. Generate payload based on outcome
    idea = request.get("idea", "cinematic action sequence")
    locked_axes = request.get("locked_axes", {})
    memory = request.get("memory", [])

    # Check for prompt injection in memory
    injected_style = None
    if _is_truthy(chaos.get("obey_injection")):
        for item in memory:
            body = item.get("body", "")
            match = re.search(r"set style to ([^;,\.\n]+)", body, re.IGNORECASE)
            if match:
                injected_style = match.group(1).strip()
                break

    # Check for unsafe switch
    unsafe_term = None
    if _is_truthy(chaos.get(f"unsafe:{model}")):
        unsafe_term = "Elon Musk"

    if outcome == "malformed_json":
        text = '{"dsl": "subject: broken json line\nemotion: tense", "confidence": '
    elif outcome == "invalid_dsl":
        # Valid JSON with invalid DSL (missing colon on an axis)
        text = json.dumps(
            {
                "dsl": "subject a hero with no colon\nemotion: joy\noptics: 35mm\nmotion: static\nlighting: daylight\nstyle: cinematic\naudio: silence\ncontinuity: none",
                "confidence": 0.85,
            }
        )
    else:
        # Valid outcome
        # Confidence: below 0.5 on ~10% of cheap and ~2% of strong
        if model == "cheap":
            if p_confidence < 0.10:
                confidence = round(0.15 + (p_confidence / 0.10) * 0.30, 2)
            else:
                confidence = round(0.50 + ((p_confidence - 0.10) / 0.90) * 0.45, 2)
        else:
            if p_confidence < 0.02:
                confidence = round(0.20 + (p_confidence / 0.02) * 0.25, 2)
            else:
                confidence = round(0.55 + ((p_confidence - 0.02) / 0.98) * 0.44, 2)

        # Build DSL axes
        subject_val = f"hero based on {idea}"
        if unsafe_term:
            subject_val = f"portrait of {unsafe_term} in dramatic lighting"

        style_val = injected_style if injected_style else "cinematic neo-noir"

        # Continuity citations
        default_rate = 0.15 if model == "cheap" else 0.05
        cfg_rate = float(chaos.get(f"fake_citation_rate:{model}", default_rate))

        if p_fake_cite < cfg_rate:
            continuity_val = "continuity reference [shot:S99]"
        elif memory:
            valid_cites = [f"[{m.get('kind', 'shot')}:{m.get('id', 'S1')}]" for m in memory[:2]]
            continuity_val = f"scene continuity {' '.join(valid_cites)}"
        else:
            continuity_val = "none"

        dsl_dict = {
            "subject": subject_val,
            "emotion": "tense and brooding",
            "optics": "35mm anamorphic prime",
            "motion": "slow push-in towards focal point",
            "lighting": "chiaroscuro shadows with neon accents",
            "style": style_val,
            "audio": "ambient low rumble and distant sirens",
            "continuity": continuity_val,
        }

        # Apply locked axes overrides
        for k, v in locked_axes.items():
            if k in dsl_dict:
                dsl_dict[k] = v

        dsl_lines = [f"{k}: {v}" for k, v in dsl_dict.items()]
        text = json.dumps({"dsl": "\n".join(dsl_lines), "confidence": confidence})

    tokens_out = max(1, len(text) // 4)
    return {
        "text": text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "outcome": outcome,
    }
