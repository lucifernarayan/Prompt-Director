import os
import json
import pytest

from evals.cases import generate_benchmark_cases, TestCase
from evals.calibration import calibrate_semantic_threshold, CALIBRATION_PAIRS
from evals.metrics import compute_metrics, EvalMetrics
from evals.report import generate_markdown_report


def test_benchmark_cases_determinism_and_coverage():
    """Benchmark generator is strictly deterministic and covers all required categories with >= 100 cases."""
    cases1 = generate_benchmark_cases(seed=42)
    cases2 = generate_benchmark_cases(seed=42)

    # 1. Total cases >= 100
    assert len(cases1) >= 100
    assert len(cases1) == len(cases2)

    # 2. Strict determinism across runs
    for c1, c2 in zip(cases1, cases2):
        assert c1.id == c2.id
        assert c1.category == c2.category
        assert c1.idea == c2.idea
        assert c1.project_id == c2.project_id
        assert c1.seed == c2.seed

    # 3. All required categories are covered
    categories = {c.category for c in cases1}
    required_categories = {
        "clean",
        "continuity",
        "injection",
        "rights_block",
        "allowed_near_miss",
        "cross_project",
        "cache_pair",
    }
    missing = required_categories - categories
    assert not missing, f"Missing required categories: {missing}"


def test_metrics_calculation_on_controlled_fixtures():
    """Metrics calculation correctly evaluates DSL validity, grounding, injection resistance, and safety precision/recall."""
    # Synthetic controlled results
    results = [
        # Clean safe delivered
        {
            "category": "clean",
            "delivered": True,
            "dsl_valid": True,
            "axes_count": 8,
            "grounded_continuity": True,
            "has_hallucinated_continuity": False,
            "pre_grounding_hallucination": False,
            "injection_resisted": True,
            "was_blocked": False,
            "is_unsafe": False,
            "cached": False,
            "cost_micro": 1000000,
            "latency_ms": 150.0,
        },
        # Clean safe delivered with mock hallucination before grounding that got stripped
        {
            "category": "continuity",
            "delivered": True,
            "dsl_valid": True,
            "axes_count": 8,
            "grounded_continuity": True,
            "has_hallucinated_continuity": False,
            "pre_grounding_hallucination": True,
            "injection_resisted": True,
            "was_blocked": False,
            "is_unsafe": False,
            "cached": False,
            "cost_micro": 1000000,
            "latency_ms": 200.0,
        },
        # Rights block correctly blocked (True Positive)
        {
            "category": "rights_block",
            "delivered": False,
            "dsl_valid": False,
            "axes_count": 0,
            "grounded_continuity": True,
            "has_hallucinated_continuity": False,
            "pre_grounding_hallucination": False,
            "injection_resisted": True,
            "was_blocked": True,
            "is_unsafe": True,
            "cached": False,
            "cost_micro": 0,
            "latency_ms": 10.0,
        },
        # Allowed near miss allowed (True Negative)
        {
            "category": "allowed_near_miss",
            "delivered": True,
            "dsl_valid": True,
            "axes_count": 8,
            "grounded_continuity": True,
            "has_hallucinated_continuity": False,
            "pre_grounding_hallucination": False,
            "injection_resisted": True,
            "was_blocked": False,
            "is_unsafe": False,
            "cached": False,
            "cost_micro": 1000000,
            "latency_ms": 120.0,
        },
        # Injection resisted
        {
            "category": "injection",
            "delivered": True,
            "dsl_valid": True,
            "axes_count": 8,
            "grounded_continuity": True,
            "has_hallucinated_continuity": False,
            "pre_grounding_hallucination": False,
            "injection_resisted": True,
            "was_blocked": False,
            "is_unsafe": False,
            "cached": False,
            "cost_micro": 1000000,
            "latency_ms": 180.0,
        },
        # Cache hit
        {
            "category": "cache_pair",
            "delivered": True,
            "dsl_valid": True,
            "axes_count": 8,
            "grounded_continuity": True,
            "has_hallucinated_continuity": False,
            "pre_grounding_hallucination": False,
            "injection_resisted": True,
            "was_blocked": False,
            "is_unsafe": False,
            "cached": True,
            "is_false_hit": False,
            "cost_micro": 1000000,
            "latency_ms": 25.0,
        },
    ]

    m = compute_metrics(results)

    # 5 out of 6 delivered (1 blocked)
    assert m.total_cases == 6
    assert m.delivered_count == 5
    assert m.blocked_count == 1
    # DSL validity of delivered prompts is 100%
    assert m.dsl_validity_rate == 1.0
    # Axis coverage of delivered prompts is 1.0 (8/8)
    assert m.axis_coverage == 1.0
    # Grounded continuity accuracy: 100%
    assert m.grounded_continuity_accuracy == 1.0
    # Injection resistance: 100%
    assert m.injection_resistance_rate == 1.0
    # Safety precision: TP=1, FP=0 -> 1.0; recall: TP=1, FN=0 -> 1.0
    assert m.unsafe_precision == 1.0
    assert m.unsafe_recall == 1.0
    # Cost per accepted prompt: (5 * 1000000) / 5 = 1000000
    assert m.cost_per_accepted_micro == 1000000
    # Pre-grounding hallucination: 1/6; post-grounding: 0/6
    assert m.pre_grounding_hallucination_rate == pytest.approx(1 / 6, rel=1e-3)
    assert m.post_grounding_hallucination_rate == 0.0
    # Cache hit rate: 1/1 = 1.0; false hit rate: 0.0
    assert m.cache_hit_rate == 1.0
    assert m.false_hit_rate == 0.0


def test_semantic_threshold_calibration():
    """Calibration sweeps thresholds on labelled pairs and selects threshold with 0% false hits."""
    res = calibrate_semantic_threshold()
    sweep = res["sweep"]
    selected_threshold = res["selected_threshold"]
    false_hit_rate = res["false_hit_rate"]

    assert len(sweep) >= 5
    # Verify thresholds are sorted
    thresholds = [row["threshold"] for row in sweep]
    assert thresholds == sorted(thresholds)

    # Selected threshold is 0.85 per IMPLEMENTATION_PLAN.md
    assert selected_threshold == 0.85
    # At 0.85, false-hit rate on negative pairs is 0.0
    assert false_hit_rate == 0.0


def test_markdown_report_includes_required_sections_and_caveats():
    """Generated markdown report includes all required sections, comparison tables, calibration, and mock caveats."""
    # Minimal metrics fixture
    from evals.metrics import EvalMetrics
    base_m = EvalMetrics(
        total_cases=10,
        delivered_count=7,
        blocked_count=0,
        failed_count=3,
        dsl_validity_rate=0.70,
        axis_coverage=0.75,
        grounded_continuity_accuracy=0.50,
        injection_resistance_rate=0.0,
        unsafe_precision=0.0,
        unsafe_recall=0.0,
        cost_per_accepted_micro=35000,
        cache_hit_rate=0.0,
        false_hit_rate=0.0,
        p95_latency_ms=1500.0,
        pre_grounding_hallucination_rate=0.15,
        post_grounding_hallucination_rate=0.15,
    )
    sys_m = EvalMetrics(
        total_cases=10,
        delivered_count=8,
        blocked_count=2,
        failed_count=0,
        dsl_validity_rate=1.0,
        axis_coverage=1.0,
        grounded_continuity_accuracy=1.0,
        injection_resistance_rate=1.0,
        unsafe_precision=1.0,
        unsafe_recall=1.0,
        cost_per_accepted_micro=1000000,
        cache_hit_rate=0.90,
        false_hit_rate=0.0,
        p95_latency_ms=250.0,
        pre_grounding_hallucination_rate=0.15,
        post_grounding_hallucination_rate=0.0,
    )
    calib = calibrate_semantic_threshold()

    md = generate_markdown_report(base_m, sys_m, calib, time_scale=0.001)

    # Check required sections per IMPLEMENTATION_PLAN.md and AGENT_RULES.md
    assert "# EVAL_RESULTS.md — Prompt Director Benchmark Evaluation" in md
    assert "Baseline vs Full System Pipeline" in md
    assert "DSL Validity" in md
    assert "Axis Coverage" in md
    assert "Grounded-Continuity Accuracy" in md
    assert "Injection Resistance" in md
    assert "Unsafe-Block Precision" in md
    assert "Cost per Accepted Prompt" in md
    assert "p95 Latency" in md
    assert "Semantic Cache Threshold Calibration" in md
    assert "Threshold Sweep Table" in md
    assert "0.85" in md
    assert "Mock Caveats & Environmental Disclosure" in md
    assert "TIME_SCALE" in md
    assert "architectural containment" in md
    assert "Known Limitations" in md
