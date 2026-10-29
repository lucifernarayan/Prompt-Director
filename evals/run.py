import os
import sys
import time
from pathlib import Path

# Ensure isolated test database is targeted
if "prompt_director_test" not in os.environ.get("DATABASE_URL", ""):
    current_url = os.environ.get("DATABASE_URL", "")
    if "prompt_director" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("prompt_director", "prompt_director_test")
    elif not current_url:
        os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@postgres:5432/prompt_director_test"

import psycopg

from app.config import settings
from app.db.migrate import ensure_database, run_migrations
from app.fixtures_loader import INITIAL_BALANCE_MICRO, load_fixtures
from app.llm import chaos
from evals.calibration import calibrate_semantic_threshold
from evals.cases import generate_benchmark_cases
from evals.metrics import compute_metrics
from evals.report import generate_markdown_report
from evals.runner import run_baseline, run_system


def reset_test_db():
    """Reset isolated test database state cleanly before system eval."""
    target_db = "prompt_director_test"
    if "prompt_director_eval" in settings.DATABASE_URL:
        target_db = "prompt_director_eval"
    ensure_database(target_db, settings.DATABASE_URL)
    run_migrations()
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE jobs, checkpoints, llm_calls, job_events, account_ledger, exact_cache, semantic_cache, inflight, feedback, chaos CASCADE;"
            )
            cur.execute("DELETE FROM accounts WHERE user_id NOT IN ('user_a', 'user_b');")
            cur.execute("DELETE FROM project_members WHERE user_id NOT IN ('user_a', 'user_b');")
            cur.execute(
                "UPDATE accounts SET balance_micro = %s WHERE user_id IN ('user_a', 'user_b');",
                (INITIAL_BALANCE_MICRO * 100,),  # Ample balance for all 120 benchmark cases
            )
    load_fixtures()
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        chaos.set(conn, "obey_injection", "true")


def main():
    print("=" * 70)
    print("PROMPT DIRECTOR BENCHMARK EVALUATION (PHASE 9)")
    print(f"Target Database: {settings.DATABASE_URL}")
    time_scale = float(os.environ.get("TIME_SCALE", getattr(settings, "TIME_SCALE", 0.001)))
    print(f"Time Scale: {time_scale}")
    print("=" * 70)

    # 1. Generate benchmark cases
    cases = generate_benchmark_cases(seed=42)
    print(f"Generated {len(cases)} benchmark cases from fixed seed 42.")

    # 2. Semantic cache threshold calibration
    print("\nRunning semantic cache threshold calibration...")
    calib_result = calibrate_semantic_threshold()
    print(f"Selected Threshold: {calib_result['selected_threshold']} (False-hit rate: {calib_result['false_hit_rate']:.1%})")

    # 3. Evaluate Baseline
    print("\nEvaluating Baseline (single cheap mock call, parse, ship if valid)...")
    baseline_results = []
    t_start_base = time.perf_counter()
    for case in cases:
        res = run_baseline(case)
        baseline_results.append(res)
    base_duration = time.perf_counter() - t_start_base
    base_metrics = compute_metrics(baseline_results)
    print(f"Baseline completed in {base_duration:.2f}s.")
    print(f"Baseline delivered: {base_metrics.delivered_count}/{base_metrics.total_cases} ({base_metrics.dsl_validity_rate:.1%} valid)")

    # 4. Evaluate Full System Pipeline
    print("\nResetting test database and evaluating Full System Pipeline...")
    reset_test_db()

    system_results = []
    t_start_sys = time.perf_counter()
    with psycopg.connect(settings.DATABASE_URL, autocommit=True) as conn:
        for i, case in enumerate(cases):
            res = run_system(case, conn, worker_id=f"eval-worker-{i % 2}")
            system_results.append(res)
    sys_duration = time.perf_counter() - t_start_sys
    sys_metrics = compute_metrics(system_results)
    print(f"System pipeline completed in {sys_duration:.2f}s.")
    print(f"System delivered: {sys_metrics.delivered_count}/{sys_metrics.total_cases} ({sys_metrics.dsl_validity_rate:.1%} valid)")
    print(f"System blocked: {sys_metrics.blocked_count} unsafe/rights cases (Recall: {sys_metrics.unsafe_recall:.1%})")

    # 5. Generate and write EVAL_RESULTS.md
    print("\nGenerating EVAL_RESULTS.md...")
    report_md = generate_markdown_report(
        baseline_metrics=base_metrics,
        system_metrics=sys_metrics,
        calibration=calib_result,
        time_scale=time_scale,
    )

    # Determine output path (repo root)
    output_path = Path("/app/EVAL_RESULTS.md")
    if not output_path.parent.exists():
        output_path = Path(__file__).resolve().parents[1] / "EVAL_RESULTS.md"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"Successfully generated {output_path} ({len(report_md)} bytes).")

    # 6. Print Summary Table
    print("\n" + "=" * 70)
    print("EVALUATION RESULTS SUMMARY")
    print("=" * 70)
    print(f"{'Metric':<32} | {'Baseline':<12} | {'Full System':<12}")
    print("-" * 70)
    print(f"{'DSL Validity Rate':<32} | {base_metrics.dsl_validity_rate:<12.1%} | {sys_metrics.dsl_validity_rate:<12.1%}")
    print(f"{'Axis Coverage':<32} | {base_metrics.axis_coverage:<12.1%} | {sys_metrics.axis_coverage:<12.1%}")
    print(f"{'Grounded-Continuity Accuracy':<32} | {base_metrics.grounded_continuity_accuracy:<12.1%} | {sys_metrics.grounded_continuity_accuracy:<12.1%}")
    print(f"{'Injection Resistance Rate':<32} | {base_metrics.injection_resistance_rate:<12.1%} | {sys_metrics.injection_resistance_rate:<12.1%}")
    print(f"{'Unsafe-Block Precision':<32} | {base_metrics.unsafe_precision:<12.1%} | {sys_metrics.unsafe_precision:<12.1%}")
    print(f"{'Unsafe-Block Recall':<32} | {base_metrics.unsafe_recall:<12.1%} | {sys_metrics.unsafe_recall:<12.1%}")
    print(f"{'Cost per Accepted Prompt':<32} | {base_metrics.cost_per_accepted_micro:<12} | {sys_metrics.cost_per_accepted_micro:<12}")
    print(f"{'Cache Hit Rate':<32} | {base_metrics.cache_hit_rate:<12.1%} | {sys_metrics.cache_hit_rate:<12.1%}")
    print(f"{'Cache False-Hit Rate':<32} | {base_metrics.false_hit_rate:<12.1%} | {sys_metrics.false_hit_rate:<12.1%}")
    print(f"{'p95 Latency (ms)':<32} | {base_metrics.p95_latency_ms:<12.1f} | {sys_metrics.p95_latency_ms:<12.1f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
