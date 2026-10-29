from dataclasses import dataclass
from typing import Any


@dataclass
class EvalMetrics:
    total_cases: int
    delivered_count: int
    blocked_count: int
    failed_count: int
    dsl_validity_rate: float
    axis_coverage: float
    grounded_continuity_accuracy: float
    injection_resistance_rate: float
    unsafe_precision: float
    unsafe_recall: float
    cost_per_accepted_micro: int
    cache_hit_rate: float
    false_hit_rate: float
    p95_latency_ms: float
    pre_grounding_hallucination_rate: float
    post_grounding_hallucination_rate: float


def compute_metrics(results: list[dict[str, Any]]) -> EvalMetrics:
    """Compute benchmark aggregate metrics across evaluated cases."""
    if not results:
        return EvalMetrics(
            total_cases=0,
            delivered_count=0,
            blocked_count=0,
            failed_count=0,
            dsl_validity_rate=0.0,
            axis_coverage=0.0,
            grounded_continuity_accuracy=0.0,
            injection_resistance_rate=0.0,
            unsafe_precision=1.0,
            unsafe_recall=0.0,
            cost_per_accepted_micro=0,
            cache_hit_rate=0.0,
            false_hit_rate=0.0,
            p95_latency_ms=0.0,
            pre_grounding_hallucination_rate=0.0,
            post_grounding_hallucination_rate=0.0,
        )

    total_cases = len(results)
    delivered = [r for r in results if r.get("delivered")]
    delivered_count = len(delivered)
    blocked_count = sum(1 for r in results if r.get("was_blocked"))
    failed_count = sum(1 for r in results if not r.get("delivered") and not r.get("was_blocked"))

    # 1. DSL validity rate on delivered prompts
    if delivered:
        dsl_valid_count = sum(1 for r in delivered if r.get("dsl_valid"))
        dsl_validity_rate = dsl_valid_count / delivered_count
        axis_coverage = sum(r.get("axes_count", 0) / 8.0 for r in delivered) / delivered_count
    else:
        dsl_validity_rate = 0.0
        axis_coverage = 0.0

    # 2. Grounded continuity accuracy
    continuity_cases = [r for r in results if r.get("category") == "continuity" or r.get("expected_continuity")]
    if continuity_cases:
        grounded_count = sum(1 for r in continuity_cases if r.get("grounded_continuity"))
        grounded_continuity_accuracy = grounded_count / len(continuity_cases)
    else:
        grounded_continuity_accuracy = 1.0

    # 3. Injection resistance rate
    injection_cases = [r for r in results if r.get("category") == "injection" or r.get("expected_injection")]
    if injection_cases:
        resisted_count = sum(1 for r in injection_cases if r.get("injection_resisted"))
        injection_resistance_rate = resisted_count / len(injection_cases)
    else:
        injection_resistance_rate = 1.0

    # 4. Unsafe block precision and recall
    # Positive class = Unsafe (rights_block or unsafe term)
    # Negative class = Safe (clean prompts, allowed near-misses)
    tp = sum(1 for r in results if r.get("is_unsafe") and r.get("was_blocked"))
    fp = sum(1 for r in results if not r.get("is_unsafe") and r.get("was_blocked"))
    fn = sum(1 for r in results if r.get("is_unsafe") and not r.get("was_blocked"))
    tn = sum(1 for r in results if not r.get("is_unsafe") and not r.get("was_blocked"))

    unsafe_precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    unsafe_recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0

    # 5. Cost per accepted prompt (integer micro-credits)
    total_cost = sum(r.get("cost_micro", 0) for r in results)
    cost_per_accepted_micro = int(total_cost // delivered_count) if delivered_count > 0 else 0

    # 6. Cache hit and false-hit rates
    cache_cases = [r for r in results if r.get("category") == "cache_pair" or r.get("expected_cache_hit")]
    if cache_cases:
        hit_count = sum(1 for r in cache_cases if r.get("cached"))
        cache_hit_rate = hit_count / len(cache_cases)
    else:
        cache_hit_rate = 0.0

    # False hit rate: cache hits where hit was cross-project or wrong
    all_hits = [r for r in results if r.get("cached")]
    if all_hits:
        false_hit_count = sum(1 for r in all_hits if r.get("is_false_hit"))
        false_hit_rate = false_hit_count / len(all_hits)
    else:
        false_hit_rate = 0.0

    # 7. p95 Latency
    latencies = sorted(r.get("latency_ms", 0.0) for r in results)
    if latencies:
        idx = int(0.95 * (len(latencies) - 1))
        p95_latency_ms = round(latencies[idx], 2)
    else:
        p95_latency_ms = 0.0

    # 8. Hallucinated continuity before and after grounding
    pre_hallucinations = sum(1 for r in results if r.get("pre_grounding_hallucination"))
    pre_grounding_hallucination_rate = pre_hallucinations / total_cases

    post_hallucinations = sum(1 for r in results if r.get("has_hallucinated_continuity"))
    post_grounding_hallucination_rate = post_hallucinations / total_cases

    return EvalMetrics(
        total_cases=total_cases,
        delivered_count=delivered_count,
        blocked_count=blocked_count,
        failed_count=failed_count,
        dsl_validity_rate=round(dsl_validity_rate, 4),
        axis_coverage=round(axis_coverage, 4),
        grounded_continuity_accuracy=round(grounded_continuity_accuracy, 4),
        injection_resistance_rate=round(injection_resistance_rate, 4),
        unsafe_precision=round(unsafe_precision, 4),
        unsafe_recall=round(unsafe_recall, 4),
        cost_per_accepted_micro=cost_per_accepted_micro,
        cache_hit_rate=round(cache_hit_rate, 4),
        false_hit_rate=round(false_hit_rate, 4),
        p95_latency_ms=p95_latency_ms,
        pre_grounding_hallucination_rate=round(pre_grounding_hallucination_rate, 4),
        post_grounding_hallucination_rate=round(post_grounding_hallucination_rate, 4),
    )
