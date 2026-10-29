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

## Failing / known issues
- None.

## Assumptions
- `call()` receives chaos dictionary directly so tests run without requiring DB connection.
- Latency in test runs defaults to `TIME_SCALE=0.001` via `tests/conftest.py` for sub-minute test cycles.
- `retrieve()` includes `project_id` in returned items to allow strict tenant boundary assertions.
- `taint_check` excludes standard grammatical stopwords to eliminate false positives on clean responses.
- `hallucination_rate` treats continuity `"none"` as non-hallucinated.

## Cut or deferred
- None.

## Next step
- Phase 6: Graph, worker, billing, cascade (`app/graph/`, `app/worker/`).
