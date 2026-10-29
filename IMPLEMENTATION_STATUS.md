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

## Failing / known issues
- None.

## Assumptions
- `call()` receives chaos dictionary directly so tests run without requiring DB connection.
- Latency in test runs defaults to `TIME_SCALE=0.001` via `tests/conftest.py` for sub-minute test cycles.

## Cut or deferred
- None.

## Next step
- Phase 5: Memory retrieval and citation verifier (`app/memory/`).
