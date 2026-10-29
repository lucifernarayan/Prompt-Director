# IMPLEMENTATION_STATUS.md

## Done
- **Scaffold (Phase 0)**:
  - Repository hygiene (`.gitattributes`, `.gitignore`, `.dockerignore`) with LF endings.
  - Directory skeleton and package layout (`app/`, `ui/`, `fixtures/`, `evals/`, `tests/`, `scripts/`, `db/migrations/`).
  - Resolved and pinned dependencies in `requirements.txt` (`fastapi`, `uvicorn`, `psycopg[binary,pool]`, `redis`, `pydantic`, `pytest`, `hypothesis`, `httpx`).
  - Non-root `Dockerfile` and `docker-compose.yml` (`postgres` with pgvector, `redis`, `api`, 2 `worker` replicas).
  - PostgreSQL advisory-lock migration runner (`app/db/migrate.py`) and initial migration (`db/migrations/001_extensions.sql`).
  - Baseline `/healthz` endpoint, static UI file serving, JSON logging (`app/obs/logging.py`), and worker heartbeat loop (`app/worker/main.py`).
  - Base commit: `69e9d5a`, tagged `clock-start`.
- **Phase 1 — Schema, pricing, fixtures loader**:
  - Implemented `db/migrations/002_core.sql` covering all section 3 tables, unique constraints (`jobs(user_id, idempotency_key)`, `llm_calls(job_id, call_key)`, `account_ledger(job_id, phase)`, `memory_items` PK `(project_id, id)`), generated tsvector on `memory_items.body` with GIN index, and CHECK constraints (`account_ledger.phase IN ('hold', 'settle', 'release')`, `accounts.balance_micro >= 0`).
  - Implemented `app/pricing.py` with integer micro-credits only (`FULL_QUOTE = 100_000`, `AXIS_EDIT_QUOTE = 40_000`, `COST_CAP = 80_000`), worst-case token counts, and model rates satisfying: (a) single strong call at worst case (70,000) fits inside cap, and (b) cheap + cheap + strong at worst case (90,000) exceeds cap.
  - Implemented `app/fixtures_loader.py` to seed two projects, two users, isolated memberships, and funded accounts idempotently.
  - Added tests in `tests/test_schema.py` and verified phase gate.
  - Tests run: `docker compose run --rm api python -m pytest tests/test_schema.py` -> 7 passed in 2.10s.
  - Verified concurrent startup: API and two worker containers started together without migration race conditions, `/healthz` returned 200 OK (`{"db":"ok","redis":"ok"}`).

## Failing / known issues
- None.

## Assumptions
- `chaos.value` is stored as `TEXT` for simplicity across boolean, float, and string switches.
- Fixture test accounts funded with 10 credits (`10_000_000` micro-credits).

## Cut or deferred
- None.

## Next step
- Phase 2: Implement DSL parser, canonical printer, and validator in `app/dsl/` with Hypothesis property tests and hostile input tests (`tests/test_dsl_hostile.py`).
