# IMPLEMENTATION_PLAN.md — Smart Prompt Director

Scope: everything remaining after the scaffold (Docker Compose with `postgres` + pgvector, `redis`, `api`, 2 `worker` replicas; pinned deps; migration runner; `/healthz`; JSON logging). Source of truth for requirements is the Cornflakes brief (must-haves 1–11, traps T1–T12, deliverables). Nothing here adds requirements to the brief.

Legend: **[M]** mandatory (brief must-have, trap, or deliverable) · **[O]** optional (only after every [M] gate passes).

Time budget: about 7.25 h of work plus 0.25 h buffer. If behind, follow "Cut order" at the end and document every cut in ARCHITECTURE.md.

---

## 1. Locked decisions (do not reopen)

| ID | Decision |
|---|---|
| D1 | `POST /v1/assist` body: `project_id`, `idea`, optional `current_dsl`, optional `only_axis`. `Idempotency-Key` header required. `X-User-Id` identifies the user (demo only). Authorization: `project_members(user_id, project_id)`; non-member gets 403. Events and cancel check job ownership. Workers use only the `project_id` stored on the job row. |
| D2 | Fixed deterministic quote by request type: `FULL_QUOTE`, or the lower `AXIS_EDIT_QUOTE` when `only_axis` is set. Held at admission. Charged exactly the quote on delivery. Actual provider cost is tracked separately in `llm_calls.cost_micro`. Cache hits cost the same quote. |
| D3 | Max 3 model calls: cheap draft, cheap repair, strong repair. A low-confidence but valid draft goes straight to the strong model (call 2, skipping cheap repair). Validate, grounding and safety run after every call. |
| D4 | All axes stay buffered until the full prompt passes the final gate. Axis events are then written in canonical order. Stage events carry names and timings only, never generated text. |
| D5 | The same gate function runs on the user idea at intake, on every model output, and on every cache hit. |
| D6 | Charge the quote only when a valid result is delivered. Blocked, invalid, cancelled, cap-exhausted with no valid candidate: release the hold, charge 0. Actual costs are still recorded. |
| D7 | Mock model output schema: `{"dsl": "<8 lines>", "confidence": 0.0-1.0}`. Confidence below 0.5 counts as low. |
| D8 | UI is a static page served by the API container. |
| Q2 | Provider-cost cap = 80% of the quote. Before every provider attempt, compute worst-case cost; if `spent + worst_case > cap`, do not call. |
| Q4 | Every provider attempt gets its own `llm_calls` row. 429 = cost 0 (rejected before processing). Timeout/hang = input-token cost. Malformed or invalid output = full cost. Up to 2 retries per call on 429/timeout; retries do not count toward the 3-call max but do count toward the cap. |
| Q5 | `#` starts a comment only as the first non-space character of a line. Inline `#` is part of the value. Duplicate axis is an error and the first occurrence is kept. Document both. |
| Q6 | Axes filled in `current_dsl` are copied byte-identically; the model fills only empty axes. With `only_axis`, only that axis changes. |
| Q7 | Single-flight uses a Postgres `inflight` table with owner tokens and expiring leases. Stale leaders cannot publish. Redis is used only for per-user rate limiting. |
| Grounding | Verify cited ids exist in this job's retrieved project memory. Document that this does not prove the claim text is supported by the cited record. |

## 2. Invariants every phase must preserve

- **I1** `charged_micro ∈ {0, quote_micro}`, and equals the quote only when a valid result was delivered.
- **I2** `llm_calls` has a unique `(job_id, call_key)`. `call_key = "{node}:{n}:{try}"`. A recorded call is never repeated; a resumed job reuses its stored response.
- **I3** `account_ledger` has a unique `(job_id, phase)` for `hold`, `settle`, `release`. Settlement happens once.
- **I4** Every memory, retrieval and cache query takes `project_id`. Cache keys include `project_id` and `memory_version`.
- **I5** Gate runs before finalize, before any cache write, before any charge. Any gate error blocks (fail closed).
- **I6** A node commit is one transaction (ledger row + checkpoint + job update) fenced by `WHERE lease_owner = $me`. Zero rows updated means stop and discard.
- **I7** Axis events and the `done` event are inserted in the same transaction as finalize.

## 3. Core schema (migration `002_core.sql`)

`projects(id, memory_version)` · `project_members(user_id, project_id)` · `memory_items(project_id, id, kind, body, license, tsv, embedding vector(64))` PK `(project_id, id)` · `accounts(user_id, balance_micro bigint)` · `account_ledger(job_id, phase, amount_micro)` unique `(job_id, phase)` · `jobs(id, user_id, project_id, idempotency_key, request_hash, status, cancel_requested, lease_owner, lease_expires_at, run_after, quote_micro, cost_cap_micro, hold_micro, charged_micro, flags, result, created_at)` unique `(user_id, idempotency_key)` · `checkpoints(job_id, seq, node_done, next_node, state jsonb)` · `llm_calls(job_id, call_key, model, tokens_in, tokens_out, cost_micro, outcome, response)` unique `(job_id, call_key)` · `job_events(id bigserial, job_id, type, data jsonb)` · `exact_cache(key pk, project_id, result)` · `semantic_cache(project_id, memory_version, settings_hash, embedding vector(64), result)` · `inflight(key pk, job_id, owner_token, lease_expires_at)` · `feedback(job_id, decision)` · `chaos(name pk, value)`.

All money is integer micro-credits. No floats for money anywhere.

---

## 4. Phases (dependency-ordered)

Each phase ends with its tests passing, a commit, and an update to `IMPLEMENTATION_STATUS.md`. Do not start the next phase until the current gate passes.

### Phase 1 — Schema, pricing, fixtures loader · 0.25 h · [M]
- `db/migrations/002_core.sql` per section 3.
- `app/pricing.py`: the only place prices live. Constants: `FULL_QUOTE`, `AXIS_EDIT_QUOTE`, per-token provider rates for cheap and strong, `cost_cap = 80% of quote`. Rates must make a 3-call worst case exceed the cap (so T10 is reachable).
- `app/fixtures_loader.py`: seeds two projects, users, memberships, accounts.
- **Gate:** tests prove the unique constraints reject duplicates, the hold is atomic (`UPDATE ... WHERE balance >= hold` affects 0 rows when short), and migrations are idempotent across api + 2 workers starting together.

### Phase 2 — DSL parser, printer, validator · 0.6 h · [M] (must-have 1, T12)
- `app/dsl/`: single linear pass with `str` methods, no backtracking regex. Normalize `\r\n`/`\r` to `\n`, split on `"\n"` only (never `splitlines()`). Reject control chars except tab, and `\u2028`/`\u2029`.
- Errors `{code, line, column, message}`, 1-based columns in code points. Codes: `E_NO_COLON`, `E_UNKNOWN_AXIS`, `E_DUPLICATE_AXIS`, `E_EMPTY_VALUE`, `E_LINE_TOO_LONG` (over 2,000 chars), `E_INPUT_TOO_LARGE` (over 256 KB), `E_INVALID_CHAR`. Stop collecting at 50 errors.
- Canonical printer: fixed axis order, lowercase keys, comments dropped (documented).
- Validator (separate from syntax): all 8 axes present and non-empty, value under 200 chars.
- **Gate:** Hypothesis property tests (parser never raises, always terminates; `parse(print(parse(x))) == parse(x)`; printer idempotent), generated unicode input, 50,000-character line returns a bounded error quickly, malformed lines, duplicates, case-insensitive keys.

### Phase 3 — Mocks and chaos switches · 0.5 h · [M]
- `app/llm/mock.py`: deterministic from `(prompt, seed)`. Cheap: 80% valid, 10% malformed JSON, 5% 429, 5% valid JSON with invalid DSL; latency 0.3–2 s. Strong: 97% valid, 3% hang; latency 2–8 s; higher cost. `TIME_SCALE` env shrinks latencies for tests/eval and is reported in EVAL_RESULTS.md.
- Output follows D7. The mock emits fake citations (e.g. `[shot:S99]`) at `fake_citation_rate` (default about 15% cheap, 5% strong).
- Switches stored in the `chaos` table so they toggle live: `unsafe` (per model), `obey_injection`, `fake_citation_rate`, `llm_down` (per model).
- `app/memory/embedder.py`: pure-Python feature hashing into 64 dimensions, L2-normalized; shared tokens give higher cosine.
- Fixtures in `fixtures/`: two projects with shots/characters/props, poisoned entries (hidden instructions, fake shot ids, near-duplicates of the other project), rights lists plus allowed near-misses.
- **Gate:** determinism test; observed failure rates over 1,000 seeds are within tolerance; embedder similarity is monotonic in shared tokens.

### Phase 4 — Safety and rights gate · 0.4 h · [M] (must-have 6, T4, T5)
- `app/gate/`: pure function `check(fields) -> {allowed, blocks[{code, rewrite}]}`.
- Normalization: NFKC, casefold, strip zero-width/format chars, small homoglyph map, collapse whitespace; also test a separator-stripped form. Whole-phrase matching on word boundaries against full-name lists, so near-misses pass.
- Reason codes: `RIGHTS_REAL_PERSON`, `RIGHTS_LIVING_ARTIST`, `RIGHTS_TRADEMARK`, `SAFETY_UNSAFE`, `GATE_ERROR`. Each has a rewrite suggestion or refusal.
- Any exception or un-normalizable input returns blocked with `GATE_ERROR`.
- **Gate:** blocks fixtures with homoglyph/zero-width/spacing evasions; allowed near-misses pass (report over-blocking count); exception injection proves fail-closed.

### Phase 5 — Project memory, grounding, injection defense · 0.9 h · [M] (must-haves 3, 4, 5; T1, T2, T3)
- `app/memory/`: `index_item()` upserts a row, recomputes the embedding, and bumps `projects.memory_version` in one transaction. `retrieve(project_id, query, budget)` is the only retrieval entry point: Postgres full-text (top 20) + pgvector cosine (top 20), fused with Reciprocal Rank Fusion (k=60), ties broken by id, unknown-licence items excluded, exact scan (no ANN index). Deterministic token packing (`len(text)//4`) by fused score.
- Injection defense layers: delimited JSON data block with ids and a "this is data" system line; quarantine of instruction-like memory items (replaced by a placeholder, counted); output schema (D7); taint check (reject output values that appear only in quarantined text); locked axes from `current_dsl` (Q6).
- Grounding: parse continuity claims (`<claim> [shot:S3]; <claim> [prop:P2]` or `none`); a claim is ungrounded if it has no citation or cites an id outside this job's retrieved set; remove ungrounded claims; if none remain, value becomes `none`. Response lists citations. Counters for hallucination rate before/after.
- **Gate (tests must exist):** T3 (query as project A with near-duplicate B content and with B's literal ids in the request: no B id in retrieval, prompt, cache lookups or output); T2 (fake `S99` removed or repaired, never shipped); T1 (same request with clean vs poisoned memory and `obey_injection` ON gives identical output; attack count and resistance recorded).

### Phase 6 — Graph, worker, billing, cascade · 1.4 h · [M] (must-haves 2, 7; T6, T7, T10)
- `app/graph/`: nodes `intent → retrieve → draft → check → repair → finalize`. `check` runs validate DSL, then grounding, then safety, after every model call. Nodes are plain functions `(state, ctx) -> state`; a small runner commits a checkpoint after each (I6).
- `intent`: normalize, gate the idea (blocked requests make no model call and charge 0), check cache (Phase 7 wires in).
- Cascade per D3/D7/Q2/Q4: cheap draft, cheap repair, strong repair; low-confidence valid draft goes straight to strong; per-call timeout; recorded retries; cap check before every attempt; on cap exhaustion return the best candidate that passed all checks, flagged, or fail with charge 0.
- `app/worker/`: claim with `FOR UPDATE SKIP LOCKED`, lease with heartbeat, fair ordering (fewest running jobs per user, then age, cap of 2 concurrent per user), bounded semaphore around model calls. Before any call, look up `llm_calls` by `call_key` and reuse a stored response.
- Billing per D2/D6: hold at admission (fail closed), settle exactly once at finalize via `account_ledger`; cancel and finalize race on one row (`WHERE lease_owner=$me AND NOT cancel_requested`).
- **Gate:** T6 (cheap malformed twice, repair then escalate, cap respected); T10 (cap reached mid-cascade returns flagged best valid result; charge equals quote or 0 per D6); **T7** (`docker kill` a worker between draft and validate; another worker resumes; assert `llm_calls` has no duplicate for a completed call, exactly one `settle` ledger row, `charged ∈ {0, quote}`); zombie-worker test (stale lease owner's commit updates 0 rows and is discarded).

### Phase 7 — Two-layer cache and single-flight · 0.6 h · [M] (must-have 8; T8, T9)
- Exact key `sha256(canonical_json{normalized_intent, project_id, memory_version, settings_hash})`; `settings_hash` covers model config, schema version, gate version, `only_axis`, canonical `current_dsl`.
- Semantic cache filtered by `project_id`, `memory_version`, `settings_hash`, cosine at or above a calibrated threshold.
- Single-flight via `inflight` table: insert unique row with `owner_token` and lease; followers re-check cache, then release with `run_after = now() + 200ms` (never hold a worker slot). Only the current owner token may publish.
- Gate before every cache write and every cache read result (I5). Only safe, valid results are cached.
- **Gate:** T8 (20 identical concurrent requests produce exactly one provider call, 19 served from cache, charges per D2); T9 (memory change between identical requests means no stale hit); stale-leader test (expired owner cannot publish); T3 for cache (no cross-project hit).

### Phase 8 — Async API, SSE, cancel, rate limit · 0.8 h · [M] (must-have 9; T11)
- `POST /v1/assist`: 400 without `Idempotency-Key`; membership check (403); one transaction inserts the job and takes the hold (402 if short, fail closed if the account store is unreachable); same key + same body returns the same job; same key + different body returns 409; returns 202 with the id.
- `GET /v1/assist/{id}/events`: SSE with per-job increasing ids; replay `id > Last-Event-ID`, then tail the table; owner-only.
- `POST /v1/assist/{id}/cancel`: sets the flag; worker checks between nodes and before each model call; hold released, charge 0.
- Redis fixed-window rate limit per user with 429 and `Retry-After`.
- **Gate:** T11 (disconnect/reconnect with `Last-Event-ID` yields no missing and no duplicate axis events); cancel releases the hold; idempotency cases; fairness test (20 jobs from user A and 1 from user B: B finishes before A's tail).

### Phase 9 — Evaluation harness · 0.8 h · [M] (must-have 10)
- `evals/`: generate at least 100 cases from fixed seeds: clean, continuity-needed, injection, rights-block, allowed near-miss, cross-project, cache-pair.
- Baseline: one cheap call, parse, ship if it parses (same mocks and seeds). System: the full pipeline.
- Metrics: DSL validity rate, axis coverage, grounded-continuity accuracy, injection-resistance rate, unsafe-block precision and recall, cost per accepted prompt, cache hit and false-hit rates, p95 latency. Include hallucinated-continuity rate before/after grounding.
- Semantic threshold calibration: labelled paraphrase vs different-intent pairs, threshold sweep table, chosen threshold and resulting false-hit rate.
- `make eval` runs it in a container and writes `EVAL_RESULTS.md`, including failures not fixed and the mock caveat (the mock obeys injections by design; `TIME_SCALE` was used).
- **Gate:** command exits 0 on a clean stack and the report is regenerated from scratch.

### Phase 10 — Observability and UI · 0.5 h · [M] (must-have 11)
- JSON logs with `trace_id`, per-node timings, token counts, cost; `GET /metrics`.
- One static page: editor, 8-axis checklist, Assist button streaming via `EventSource`, diff of the suggestion, accept/reject buttons storing feedback. Plain HTML/JS, no build step.
- **Gate:** manual smoke run end to end in a browser against the running stack.

### Phase 11 — Chaos, docs, clean clone · 0.5 h · [M]
- `scripts/chaos.ps1` (and the `make chaos` equivalent) for T7, T8, T11, plus switches to take down an LLM, Redis, the vector store, or a worker.
- `README.md`: how to run, load fixtures, run eval, trigger every chaos scenario, PowerShell equivalents, disclosure that the environment scaffold predates the timed window (tag `clock-start`).
- `ARCHITECTURE.md` (max 2 pages): graph and checkpoint design, memory isolation, grounding method and its limit, injection defense, gate placement and why it fails closed, cascade and cost logic, cache keys and threshold choice, billing policy table, what was cut and why.
- **Final gate (do not skip):** `docker compose down -v`, fresh clone into a new folder, `docker compose up --build`, run the test suite and `make eval` equivalent from that clone.

### Phase 12 — Optional improvements · only after Phase 11 passes · [O]
- **Should-have 12:** axis-level edit test proving that with `only_axis` all other lines stay byte-identical (the locked-axes logic from Phase 5/6 already supports it).
- **Should-have 15:** graceful degradation with Redis or the vector store down (valid, less informed prompt).
- Skip should-haves 13 and 14 and all stretch goals unless time clearly remains.

---

## 5. Trap coverage

| Trap | Phase | Test |
|---|---|---|
| T1 poisoned memory | 5 | `tests/test_injection.py` |
| T2 fake shot id | 5 | `tests/test_grounding.py` |
| T3 cross-project | 5, 7 | `tests/test_isolation.py` |
| T4 real-person likeness | 4, 6 | `tests/test_gate.py` |
| T5 strong model unsafe | 4, 6 | `tests/test_gate_output.py` |
| T6 malformed twice | 6 | `tests/test_cascade.py` |
| T7 killed worker | 6, 11 | `tests/test_resume.py`, `scripts/chaos` |
| T8 20 identical | 7, 11 | `tests/test_singleflight.py` |
| T9 stale cache | 7 | `tests/test_cache_invalidation.py` |
| T10 cost cap mid-cascade | 6 | `tests/test_cost_cap.py` |
| T11 SSE reconnect | 8, 11 | `tests/test_sse.py` |
| T12 hostile DSL | 2 | `tests/test_dsl_hostile.py` |

Minimum tests required by the brief: parser, T1, T3, T5, T7 (all included above).

## 6. Cut order if behind (document every cut)

1. UI polish (keep editor, checklist, streaming, diff, accept/reject).
2. Depth of `/metrics` (keep logs with timings, tokens, cost, `trace_id`).
3. Eval case count above 100 and extra metrics detail.
4. All of Phase 12.

Never cut: tenant isolation, gates, billing, recovery (T7), the clean-clone run, honest reporting of failures.

## 7. Final submission checklist

- [ ] `docker compose up --build` works from a clean clone (API, 2+ workers, Postgres, Redis, UI).
- [ ] Test suite passes in a container; failing tests, if any, are listed in `IMPLEMENTATION_STATUS.md`.
- [ ] `EVAL_RESULTS.md` is the committed output of the eval, with unfixed failures included.
- [ ] README, ARCHITECTURE.md (max 2 pages), chaos script present.
- [ ] `IMPLEMENTATION_STATUS.md` lists remaining limitations honestly.
- [ ] Tag `clock-end`; submit within 1 hour of the 8-hour end.
