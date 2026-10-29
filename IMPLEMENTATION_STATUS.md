# IMPLEMENTATION_STATUS.md

## Done
- **Scaffold (Phase 0)**:
  - Repository hygiene (`.gitattributes`, `.gitignore`, `.dockerignore`) with LF endings.
  - Directory skeleton and package layout (`app/`, `ui/`, `fixtures/`, `evals/`, `tests/`, `scripts/`, `db/migrations/`).
  - Resolved and pinned dependencies in `requirements.txt` (`fastapi`, `uvicorn`, `psycopg[binary,pool]`, `redis`, `pydantic`, `pytest`, `hypothesis`, `httpx`).
  - Non-root `Dockerfile` and `docker-compose.yml` (`postgres` with pgvector, `redis`, `api`, 2 `worker` replicas).
  - Base commit: `69e9d5a`, tagged `clock-start`.
- **Phase 1 — Schema, pricing, fixtures loader**:
  - Implemented `db/migrations/002_core.sql`, `app/pricing.py`, `app/fixtures_loader.py`.
  - Phase 1 commit: `50f8627`.
  - Tests run: `tests/test_schema.py` (7 passed).
- **Phase 2 — DSL parser, printer, validator**:
  - Implemented linear pass parser without `splitlines()`, semantic validator, and canonical printer in `app/dsl/`.
  - Phase 2 commit: `741388c`.
  - Tests run: `tests/test_dsl.py`, `tests/test_dsl_hostile.py` (12 passed).
- **Phase 3 — Mocks and chaos switches**:
  - Implemented `app/llm/mock.py` with deterministic SHA-256 mapping, cheap (80% valid, 10% malformed, 5% invalid DSL, 5% 429) and strong (97% valid, 3% hang) outcome distributions, responsive cancellation, and chaos switches.
  - Implemented `app/llm/chaos.py` for database-backed switch toggles with defaults.
  - Implemented `app/memory/embedder.py` with pure-Python SHA-256 64-dimensional feature hashing and cosine similarity helper.
  - Created `fixtures/projects.json` (two projects, 8 shots, 3 chars, 3 props each, unknown license item, poisoned instructions, cross-project near-duplicates) and `fixtures/rights.json`.
  - Added `TIME_SCALE` to `app/config.py` and `docker-compose.yml`.
  - Demonstrated intentional failure: shown `test_determinism_across_processes` failing when Python `hash()` is substituted for `sha256`, and passing with `sha256`.
  - Tests run: `docker compose run --rm api python -m pytest tests/` -> 30 passed in 71.61s.

- **Phase 4 — Safety and rights gate**:
  - Implemented pure function `check(fields) -> GateResult` in `app/gate/` (`normalizer.py`, `checker.py`, `__init__.py`) with `GATE_VERSION = "1.0.0"`.
  - Normalization: NFKC -> casefold -> strip `Cf`/`Cc` characters -> Cyrillic/Greek homoglyphs -> non-alphanumeric to spaces -> collapse 3+ single letter runs ("t o m  c r u i s e") -> collapse whitespace.
  - Linear first-token sequence matching against `fixtures/rights.json` with zero-overblocking on near misses and whole-token matches only.
  - Fail closed on non-string inputs, payload > 256 KB, and internal exceptions.
  - Added `unsafe_terms` to `fixtures/rights.json`.
  - Scaled test latency via `tests/conftest.py` (`TIME_SCALE=0.001`), cutting full suite run time from 71.61s to 17.05s.
  - Demonstrated two intentional regression failures: disabled `HOMOGLYPH_MAP` (caused homoglyph failure) and removed `try/except` fail-closed wrapper (caused RuntimeError leak), both restored and passing.
  - Tests run: `tests/test_gate.py` (8 passed), full suite `tests/` (38 passed in 17.05s).

- **Phase 5 — Project memory, grounding, injection defense**:
  - Implemented `app/memory/store.py` (`load_fixtures_memory`, `index_item` with atomic version bump, `retrieve` with hybrid full-text + vector RRF k=60 and greedy token budget packing).
  - Implemented `app/memory/defense.py` (`quarantine` replacing injected items with placeholder, `build_request` formatting clean memory, `taint_check` detecting poisoned payload tokens, `enforce_locked` locking axes).
  - Implemented `app/memory/grounding.py` (`ground` continuity claims against retrieved records, `has_ungrounded`, `hallucination_rate` helper).
  - Extended `app/fixtures_loader.py` to seed `memory_items` from `fixtures/projects.json` idempotently.
  - Demonstrated two intentional regression failures: removed `project_id` filter from vector query in `retrieve` (caught by `test_t3_isolation_near_duplicate_query`) and disabled `quarantine` (caught by `test_t1_injection_full_defense`), both restored and verified.
  - Tests run: `tests/test_memory.py`, `tests/test_isolation.py`, `tests/test_grounding.py`, `tests/test_injection.py`, `tests/test_schema.py` (28 passed in 4.32s).

- **Phase 6a — Graph runner, cascade, cost cap, and billing**:
  - Implemented `app/pricing.py` additions (`cost_of_attempt` covering rate_limit, timeout, and standard attempts).
  - Implemented `app/jobs.py` (`create_job` with atomic hold and idempotency check, `settle` with single-charge idempotency via unique ledger constraint).
  - Implemented `app/graph/` (`nodes.py` pure functions for intent, retrieve, draft, check, repair, finalize; `runner.py` with transactional node checkpointing, lease fencing, and cancel check; `state.py` state initialization).
  - Extended `app/llm/chaos.py` and `app/llm/mock.py` with `force_outcome:<model>` and `force_confidence:<model>` test hooks.
  - Demonstrated two intentional regression failures: removed `call_key` reuse check (caught by `test_call_key_reuse_skips_provider_call`) and bypassed unique ledger constraint in `settle()` (caught by `test_settle_delivered_vs_released_and_no_double_charge`), both restored and verified.
  - Tests run: `tests/test_cascade.py`, `tests/test_cost_cap.py`, `tests/test_billing.py`, `tests/test_resume_unit.py`, `tests/test_schema.py` (23 passed in 10.06s).

- **Phase 6b — Worker loop, leases, fairness, and killed-worker test (T7)**:
  - Implemented `app/worker/claim.py` (`claim_job` with `pg_advisory_xact_lock` and `FOR UPDATE SKIP LOCKED`, `heartbeat`, `release_lease`).
  - Implemented `app/worker/main.py` (worker loop with background heartbeat thread, JSON logging per claim/node commit/provider call, graceful SIGTERM shutdown releasing lease, pause_after chaos hook).
  - Implemented `app/worker/pool.py` with process-wide bounded semaphore around model provider calls (`MODEL_POOL_SIZE`, default 4).
  - Implemented `app/llm/chaos_cli.py` and `app/tools/` (`submit.py`, `inspect.py`).
  - Implemented `scripts/t7.ps1` PowerShell test verifying T7 fault tolerance with killed worker.
  - Demonstrated two intentional regression failures: removed lease_owner guard from node commit (caught by `test_zombie_worker_raises_lease_lost_and_writes_nothing`) and removed running-count ordering from claim query (caught by `test_fairness_ordering_and_max_two_leases_per_user`), both restored and verified.
  - Tests run: `tests/test_worker.py` (8 passed in 12.93s), full suite `tests/` (84 passed in 47.50s), `scripts/t7.ps1` (T7 PASS).
- **Phase 7 — Two-layer caching and single-flight coalescing**:
  - Implemented exact cache keys and settings hashing in `app/cache/keys.py` covering model_config, schema_version, gate_version, only_axis, canonical current_dsl.
  - Implemented single-flight request coalescing in `app/cache/inflight.py` with owner tokens, lease checks, and follower deferral (`run_after = now() + 200ms`).
  - Implemented exact match and semantic pgvector cache lookup/publication in `app/cache/store.py` with calibrated similarity threshold 0.85 and safety gate checks on every read and write.
  - Integrated cache and inflight workflows into `app/graph/nodes.py` (`intent_node`, `finalize_node`) and `app/graph/runner.py`.
  - Added gate test coverage in `tests/test_cache.py`: T8 (20 concurrent requests coalesce to 1 provider call, 19 cache hits, billed quote per D2), T9 (memory version bump prevents stale hit), T3 (cross-project cache isolation), semantic cache hit on paraphrase, safety gate enforcement on cache reads and writes, stale inflight owner publication rejection.
  - Tests run: `tests/test_cache.py` (7 passed in 7.42s), full suite `tests/` (91 passed in 57.51s).
- **Phase 8 — Async API, SSE, cancel, and rate limiting**:
  - Implemented `POST /v1/assist` in `app/api/routes.py` with mandatory `Idempotency-Key` (400 if missing), `X-User-Id` authentication, project membership check (403), balance hold admission (402 if short), idempotent return on duplicate request (202), and conflict detection on same key with different body (409).
  - Implemented `GET /v1/assist/{id}/events` SSE streaming endpoint with per-job increasing event ids, `Last-Event-ID` replay without missing or duplicate axis events, owner-only authorization (404/403), and termination on `done` event.
  - Implemented `POST /v1/assist/{id}/cancel` with owner authorization, setting `cancel_requested = TRUE`, releasing hold with charge 0, and cancelling pending/running jobs.
  - Implemented Redis fixed-window rate limiter in `app/rate_limit.py` returning 429 and `Retry-After` header when limit exceeded.
  - Registered routes in `app/api/main.py` before static file mounting.
  - Added test coverage in `tests/test_api.py` (10 tests) and `tests/test_sse.py` (2 tests) verifying T11 SSE reconnect replay, cancel balance refund and ledger release, idempotency, authorization, rate limiting, and 20:1 user fairness.
  - Demonstrated two intentional regression failures: removed idempotency check (caught by `test_missing_idempotency_key_returns_400` returning 202 instead of 400) and removed `Last-Event-ID` filtering (caught by `test_t11_sse_reconnect_with_last_event_id_no_missing_no_duplicates` asserting duplicate event IDs), both restored and verified.
  - Tests run: `tests/test_api.py`, `tests/test_sse.py` (12 passed in 9.93s), full suite `tests/` (103 passed in 57.94s).

## Failing / known issues
- None.

## Assumptions
- `call()` receives chaos dictionary directly so tests run without requiring DB connection.
- Latency in test runs defaults to `TIME_SCALE=0.001` via `tests/conftest.py` for sub-minute test cycles.
- `retrieve()` includes `project_id` in returned items to allow strict tenant boundary assertions.
- `taint_check` excludes standard grammatical stopwords to eliminate false positives on clean responses.
- `hallucination_rate` treats continuity `"none"` as non-hallucinated.
- `jobs.flags` stores `{"body": body}` to preserve intake request payload without schema changes.
- In `claim_job`, fairness active lease count counts unexpired leases where `lease_expires_at > NOW()` and status is non-terminal.
- Follower jobs waiting on single-flight inflight leader defer with `run_after = NOW() + INTERVAL '200 milliseconds'` and release their worker lease.
- Redis fixed-window rate limiting uses `RATE_LIMIT_WINDOW_SECONDS` (default 60s) and `RATE_LIMIT_REQUESTS` (default 60).

## Cut or deferred
- None.

## Next step
- Phase 9: Evaluation harness (`evals/`, 100 cases, baseline vs system, metrics, threshold sweep, `EVAL_RESULTS.md`).
