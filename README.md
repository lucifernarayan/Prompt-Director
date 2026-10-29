# Prompt Director

Prompt Director is a small asynchronous service that turns a project idea into a structured eight-axis prompt DSL. It retrieves project memory, checks grounding and safety, and uses a deterministic mock LLM so the pipeline can be exercised without an external model provider.

## Run it

From the repository root, start the Compose services:

```sh
docker compose up --build -d
```

Compose publishes the API on port `8000`; the static UI is at [http://localhost:8000/](http://localhost:8000/). `GET /healthz` checks PostgreSQL and Redis and returns 200 only when both respond. PostgreSQL with pgvector, Redis, the API, and two worker replicas are defined in `docker-compose.yml`.

The API startup path runs migrations and **demo seeding on API start**. It loads the demo projects, users, memberships, accounts, and memory items idempotently from `fixtures/projects.json`. This behavior is verified in the current code. Demo identity is passed in `X-User-Id`; this is not production authentication.

## Tests and evaluation

Run the test suite in the API container:

```sh
docker compose run --rm api python -m pytest -q
```

Run the deterministic evaluation harness with Make:

```sh
make eval
```

PowerShell equivalent:

```powershell
docker compose run -e DATABASE_URL=postgresql://postgres:postgres@postgres:5432/prompt_director_test -v .:/app --rm api python -m evals.run
```

The harness writes `EVAL_RESULTS.md` in the mounted repository. It uses 120 fixed-seed cases. The model is a mock: it obeys injections by design when the `obey_injection` chaos switch is enabled. `TIME_SCALE` reduces simulated provider latency; Compose sets it to `0.01`, while tests set it to `0.001`.

## Chaos scenarios

The operator harness `scripts/chaos.ps1` runs scenarios for fault tolerance and recovery (Windows: make not required):

```powershell
.\scripts\chaos.ps1 t7             # T7: kill a worker, verify transactional recovery
.\scripts\chaos.ps1 t8             # T8: submit 20 identical requests, verify single-flight coalescing
.\scripts\chaos.ps1 t11            # T11: SSE reconnect and replay guarantee
.\scripts\chaos.ps1 stop-redis     # Stop Redis
.\scripts\chaos.ps1 start-redis    # Start Redis
.\scripts\chaos.ps1 stop-worker    # Stop one worker container
.\scripts\chaos.ps1 start-worker   # Start 2 worker replicas
.\scripts\chaos.ps1 stop-postgres  # Stop Postgres
.\scripts\chaos.ps1 start-postgres # Start Postgres
.\scripts\chaos.ps1 restore        # Restore all services with 2 workers
```

Make equivalent:
```sh
make chaos ACTION=t8
make chaos ACTION=t11
```

## Disclosure and limitations

The environment scaffold (Phase 0) predates the timed window; see git tag [`clock-start`](commit 69e9d5a).

`IMPLEMENTATION_STATUS.md` currently lists no failing/known issues and no cuts or deferrals. It records `TIME_SCALE=0.001` for tests, among other assumptions, and names Phase 10 as the next step. In the current tree, `ui/index.html` is only a plain title, so the planned editor and streaming interaction are not implemented. The combined chaos runner is absent; only the T7 PowerShell script is present. The mock's injection-obedience mode is intentionally adversarial and does not represent model behavior. See [ARCHITECTURE.md](ARCHITECTURE.md) for design limits and billing behavior.
