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
  - Tests run: `tests/test_schema.py` (7 passed in 2.10s).
- **Phase 2 — DSL parser, printer, validator**:
  - Implemented single linear pass parser in `app/dsl/parser.py` using `str` methods without backtracking regex or `splitlines()`.
  - Implemented semantic validator in `app/dsl/validator.py` checking 8 non-empty axes under 200 characters.
  - Implemented canonical printer in `app/dsl/printer.py` in fixed axis order with comments dropped.
  - Exported simple public API in `app/dsl/__init__.py`: `parse(text) -> (axes, errors)`, `validate(axes) -> errors`, `print_dsl(axes) -> str`.
  - Added property tests (`tests/test_dsl.py`) with Hypothesis and hostile input tests (`tests/test_dsl_hostile.py`).
  - Tested intentional failure mode: demonstrated that switching to `str.splitlines()` breaks on U+2028 before restoring correct normalization.
  - Tests run: `docker compose run --rm api python -m pytest tests/` -> 19 passed in 5.43s.

## Failing / known issues
- None.

## Assumptions
- DSL syntax parser ignores blank lines and `#` comment lines, retaining inline `#` as part of values.
- DSL parser truncates error collection at 50 errors.
- Oversized inputs (> 256 KB) are rejected before line scanning.

## Cut or deferred
- None.

## Next step
- Phase 3: Implement mocks and chaos switches in `app/llm/mock.py` and pure-Python hashed embedder in `app/memory/embedder.py` with tests in `tests/test_mock_llm.py` and `tests/test_embedder.py`.
