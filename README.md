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
## Disclosures and limitations

This is an eight-hour technical assessment prototype. It demonstrates the requested pipeline and failure-handling patterns; it is not production-ready infrastructure.

- **Mock models:** The project uses deterministic mock LLMs and a mock embedder. Evaluation results measure this implementation against those mocks; they do not establish the quality, cost, latency, or safety of a real model provider.
- **Injection testing:** The mock can be configured to follow poisoned instructions so the application’s defenses can be tested. A 100% resistance result on this benchmark does not prove immunity to real-world or novel prompt-injection attacks.
- **Benchmark scope:** `EVAL_RESULTS.md` reports 120 deterministic, generated cases. The results are specific to those cases and seeds, not a broad production benchmark. The semantic-cache threshold was calibrated on a small labelled set of paraphrase and different-intent pairs.
- **Continuity grounding:** The checker verifies that cited IDs exist in the retrieved memory for the current project. It does not verify that the cited record semantically supports the claim’s wording.
- **Cost comparison:** Baseline cost is raw mock-provider cost; system cost is the billed quote. These use different bases and are not a like-for-like cost comparison.
- **Latency:** Evaluation latency uses simulated provider delays scaled by `TIME_SCALE=0.01`; it is not real-provider or production latency.
- **Authentication:** `X-User-Id` is a demo identity mechanism, not production authentication or authorization infrastructure.
- **Feedback:** Accept/reject feedback is stored in browser `localStorage`; it is not persisted server-side.
- **Verification:** The latest reported full test run passed 109 tests in 79.69 seconds, with one Starlette deprecation warning. The T7, T8, and T11 chaos scenarios were verified. Other individual service stop/start/restore scenarios were not all run manually.
- **Observability and deployment:** Detailed `trace_id` and per-node timing log fields were deferred. Optional Phase 12 improvements, including hosted or multi-region deployment, were not implemented.

The evaluation report records a 0.0% pre-grounding hallucinated-continuity rate for its benchmark run. This is a result for that run and should not be interpreted as evidence that raw model output cannot contain ungrounded claims.
