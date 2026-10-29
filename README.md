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

The planned combined runner `scripts/chaos.ps1` is not present in this checkout. Its invocation syntax and scenarios below are **unverified** until that file exists. `make chaos` currently reports that it is not implemented. `scripts/t7.ps1` exists for T7; it has not been run as part of this documentation task. The commands invoking the missing runner are placeholders for its expected scenario names, not verified CLI syntax.

| Scenario | Bash | PowerShell |
|---|---|---|
| T7: kill a worker between draft and validation, then verify resume | `pwsh -File scripts/chaos.ps1 -Scenario T7` **unverified**; existing script: `pwsh -File scripts/t7.ps1` | `./scripts/t7.ps1` (present; execution unverified); combined runner: `./scripts/chaos.ps1 -Scenario T7` **unverified** |
| T8: submit 20 identical requests and check single-flight | `pwsh -File scripts/chaos.ps1 -Scenario T8` **unverified** | `./scripts/chaos.ps1 -Scenario T8` **unverified** |
| T11: disconnect and reconnect SSE with `Last-Event-ID` | `pwsh -File scripts/chaos.ps1 -Scenario T11` **unverified** | `./scripts/chaos.ps1 -Scenario T11` **unverified** |
| Take down the cheap mock LLM | `docker compose run --rm api python -m app.llm.chaos_cli set llm_down:cheap true` | `docker compose run --rm api python -m app.llm.chaos_cli set llm_down:cheap true` |
| Take down Redis | `docker compose stop redis` | `docker compose stop redis` |
| Take down the vector store (pgvector is provided by PostgreSQL here) | `docker compose stop postgres` | `docker compose stop postgres` |
| Take down one worker | `docker kill "$(docker compose ps -q worker | head -n 1)"` | `$worker = docker compose ps -q worker | Select-Object -First 1; docker kill $worker` |

Restore services with `docker compose up -d`. Clear the LLM switch with `docker compose run --rm api python -m app.llm.chaos_cli clear llm_down:cheap`. Stopping PostgreSQL also takes down the application's primary database, so the vector-store scenario is a database outage in this setup.

## Disclosure and limitations

The environment scaffold (Phase 0) predates the timed window; see git tag [`clock-start`](commit 69e9d5a).

`IMPLEMENTATION_STATUS.md` currently lists no failing/known issues and no cuts or deferrals. It records `TIME_SCALE=0.001` for tests, among other assumptions, and names Phase 10 as the next step. In the current tree, `ui/index.html` is only a plain title, so the planned editor and streaming interaction are not implemented. The combined chaos runner is absent; only the T7 PowerShell script is present. The mock's injection-obedience mode is intentionally adversarial and does not represent model behavior. See [ARCHITECTURE.md](ARCHITECTURE.md) for design limits and billing behavior.
