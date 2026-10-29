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
  - Tests run: Verified `/healthz` returns `{"db":"ok","redis":"ok"}`, worker heartbeats from 2 container hostnames, worker kill and recovery, all text files LF (0 CRLF), and cold start from scratch.

## Failing / known issues
- None. (No tests in `tests/` yet beyond `tests/__init__.py`).

## Assumptions
- Baseline scaffold corresponds to Phase 0; implementation starts with Phase 1 per `IMPLEMENTATION_PLAN.md`.
- Disagreements between plan and actual files at start:
  - Core schema `db/migrations/002_core.sql`, `app/pricing.py`, and `app/fixtures_loader.py` are specified in Phase 1 and not yet present.
  - Phase 3 specifies `TIME_SCALE` environment variable for mock latencies, to be wired into config during Phase 3.
  - Section 5 lists trap test suites (`test_injection.py`, `test_grounding.py`, etc.) which will be created alongside each phase.

## Cut or deferred
- None.

## Next step
- Phase 1: Implement `db/migrations/002_core.sql`, `app/pricing.py`, `app/fixtures_loader.py`, and gate tests (unique constraints, atomic balance hold, concurrent migration idempotency).
