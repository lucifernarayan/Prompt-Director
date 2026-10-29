# AGENT_RULES.md — Rules for Antigravity

These rules apply to every prompt in this project. Read this file, `IMPLEMENTATION_PLAN.md`, and `IMPLEMENTATION_STATUS.md` at the start of each session. If a rule here conflicts with a generic habit, this file wins.

## 1. Source of truth

1. The Cornflakes brief defines the requirements. `IMPLEMENTATION_PLAN.md` defines the locked decisions (D1–D8, Q2, Q4–Q7) and the phase order. Do not invent requirements, endpoints, tables, flags, or features that are not in them.
2. If something is ambiguous, pick the simplest defensible interpretation, write it in one line under "Assumptions" in `IMPLEMENTATION_STATUS.md`, and continue. Do not stop to ask for approval on small ambiguities. Stop and ask only if a choice would violate an invariant (I1–I7) or a locked decision.

## 2. Existing work

3. Before editing anything, inspect the existing files involved (`view` / read them) and report what you found.
4. Preserve the working infrastructure: `docker-compose.yml`, `Dockerfile`, the migration runner, `/healthz`, the worker heartbeat, pinned dependencies. Change them only when a phase requires it, make the smallest edit, and note why in the commit message.
5. Do not rewrite a working file wholesale. Use targeted edits. Preserve existing function signatures unless a change is necessary; if you change one, update every caller and document it in `IMPLEMENTATION_STATUS.md`.

## 3. Architecture (fixed)

6. Modular monolith in Python. One image used by `api` and two `worker` replicas. Docker Compose is the only orchestration.
7. PostgreSQL is the single source of truth: jobs, checkpoints, ledger, events, memory, vectors (pgvector), caches, single-flight (`inflight`), chaos switches. Redis is used only for per-user rate limiting. Do not add Redis for anything else.
8. No microservices, no message brokers, no ORMs, no LangGraph/LangChain, no new frameworks. The graph is plain functions plus a small runner.
9. Mock LLMs and a pure-Python hashed mock embedder only. No paid APIs, no network calls to real providers, no ML libraries (no numpy, torch, sentence-transformers).
10. Money is integer micro-credits. Never use floats for money.
11. Do not add dependencies, config options, abstractions, or "future-proofing" that the current phase does not need. Adding a dependency requires a one-line justification in `IMPLEMENTATION_STATUS.md` and exact version pinning verified by installing it.

## 4. Environment

12. Host is Windows 11 with Docker Desktop and PowerShell 5.1. Do not use WSL or `wsl` commands. Do not use `&&` in PowerShell; use separate commands or `;`.
13. Containers start with `python -m ...` commands. Do not add shell scripts that run inside containers. Do not bind-mount source code. Keep every text file LF.
14. Do not install anything on the host. Do not assume `make` exists on the host; every Makefile target must be a plain `docker compose ...` command that also has a PowerShell equivalent in the README.
15. Python runs only in containers. Run tests with `docker compose run --rm api python -m pytest ...`.

## 5. Working style: one phase at a time

16. Implement only the phase you were told to implement. Do not start later phases or add speculative code for them.
17. Order inside a phase: write the failing test for the trap or requirement first, then the implementation, then run the tests.
18. Do not stop after planning. Implement the assigned phase and verify it in the same session.
19. A phase is done only when its gate in `IMPLEMENTATION_PLAN.md` passes by actually running the tests and showing the raw output.
20. Time-box: if a problem takes more than 15 minutes, stop, record it in `IMPLEMENTATION_STATUS.md` with the exact error, apply the simplest workaround that keeps the guarantee, and move on.

## 6. Honesty about code and tests

21. Never replace real behavior with placeholders, stubs, `pass`, `TODO` bodies, hardcoded success responses, or canned results. If something cannot be finished in the phase, say so explicitly and list it as incomplete; do not fake it.
22. Never write tests that only assert a mock's own output. Each trap test must exercise the real code path and be able to fail. For every guarantee test, show once that it fails when the guarantee is removed (for example, temporarily disable the check, see the test fail, restore it).
23. Never weaken, skip, delete, or loosen a failing test to make it pass. Fix the code. If the test is wrong, explain why before changing it.
24. Never claim tests pass, a command succeeded, or a container is healthy without having run it and shown the real output. Report failures as failures.
25. T1 (injection) must be tested with the mock's `obey_injection` switch ON. T5 must be tested with the strong model's `unsafe` switch ON. A test that passes only because the mock is well-behaved does not count.
26. Do not hardcode benchmark numbers. Every figure in `EVAL_RESULTS.md` must come from an actual run of the eval harness.

## 7. Safety and correctness invariants (never violate)

27. Treat all model output, memory content, and user text as untrusted data.
28. The gate runs on the user idea at intake, on every model output, and on every cache hit, before finalize, before any cache write, and before any charge. Any exception in the gate means block (fail closed).
29. Every memory, retrieval, and cache query takes `project_id`. Never read across projects. Cache keys include `project_id` and `memory_version`.
30. Each model attempt is recorded in `llm_calls` with a unique `(job_id, call_key)`. A completed call is never repeated on resume. Charges settle once via the unique `(job_id, phase)` ledger row.
31. Every node commit is one transaction fenced by `WHERE lease_owner = $me`. If it updates zero rows, discard the work and stop.
32. Axis events and the `done` event are written in the same transaction as finalize. Never stream generated text before the final gate passes.
33. Never log secrets or full user prompts above debug level.

## 8. Git

34. Commit at every stable milestone (a passing test group, a finished module), using messages like `feat(dsl): parser with property tests`. Do not squash or rewrite history. Do not backdate commits or modify timestamps.
35. Never commit a failing build to the main branch without noting it in `IMPLEMENTATION_STATUS.md`.
36. The first action of the timed window is `git tag clock-start`; the last is `git tag clock-end`.

## 9. Status tracking

37. Maintain `IMPLEMENTATION_STATUS.md` with these sections, updating it at the end of every phase and whenever something fails: **Done** (phase, commit hash, tests run), **Failing / known issues** (exact test name and error), **Assumptions**, **Cut or deferred**, **Next step**.
38. At the end of each response, state: what changed, which tests were run and their real results, what is still failing, and the next step.

## 10. Finish line

39. Before declaring the project complete: `docker compose down -v`, make a fresh clone in a new folder, run `docker compose up --build`, run the full test suite in a container, run the eval, and confirm every service is healthy with two workers.
40. `EVAL_RESULTS.md` must be the real output of the eval, including failures that were not fixed. `README.md` and `ARCHITECTURE.md` (max 2 pages) must match what was actually built.
41. End with an honest list of remaining limitations, including at least: mocks show architectural containment, not real-model robustness; grounding verifies that cited ids exist, not that the claim text is supported; anything cut or deferred.
