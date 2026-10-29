# Architecture

Prompt Director accepts an idea for a project and produces a canonical eight-axis DSL prompt. The current provider is a deterministic mock. The API records asynchronous jobs, workers claim them, and clients receive persisted server-sent events (SSE).

## Graph, recovery, and isolation

The graph runs `intent → retrieve → draft → check → repair → finalize`. Each node is a plain state transformation. After a node runs, one database transaction records its model attempts, stage event, checkpoint, and fenced lease update. Finalization also inserts the result events and settlement in that transaction. The checkpoint stores the completed node, next node, and serialized state, so a worker can resume at the next node after a crash. Calls have stable `(job_id, call_key)` uniqueness; a resumed execution reuses a recorded successful response instead of calling the provider again. Workers claim with `FOR UPDATE SKIP LOCKED`, hold expiring leases, and heartbeat. Commits check `lease_owner`, fencing out stale workers.

A job's `project_id` comes from its stored job row. Memory retrieval takes that ID and filters both keyword and vector queries by it. Cache reads and writes also carry `project_id`; semantic cache scope additionally includes memory version and settings hash. Membership is checked at admission. The project ID filter is the demo tenant boundary; `X-User-Id` is caller-supplied demo identity, not production authentication.

## Grounding and injection defense

Memory retrieval combines PostgreSQL full-text ranking and exact pgvector cosine ranking, then fuses ranks with Reciprocal Rank Fusion and packs items under a token budget. Items with unknown or absent licenses are excluded. Instruction-like items are quarantined and replaced with placeholders before being sent to the mock. Output is checked against tokens found only in quarantined text (taint check), and axes already supplied in `current_dsl` are restored byte-for-byte after generation. With `only_axis`, all other supplied axes are locked.

Continuity claims must contain citations whose `(kind, id)` pairs exist among the items retrieved for this job; claims with missing or unknown citations are removed, and an empty result becomes `none`. This establishes only that the identifier was retrieved, not that its record supports the claim. Taint checking catches copied tokens, not semantic paraphrases. The mock's `obey_injection` switch deliberately demonstrates a model following poisoned instructions; it is not evidence that the defense defeats arbitrary real models.

## Gates, cascade, and cost

The rights/safety gate runs on the intake idea, each model output, the assembled result, and cache reads and writes. Errors block with `GATE_ERROR`. DSL validation and grounding also run after each response. This keeps unchecked candidates from delivery or cache, but gate errors reject work.

The cascade starts cheap, may try cheap repair, then strong repair; a low-confidence valid draft goes directly to strong repair. It allows at most three calls, with retries recorded separately. Before each attempt, worst-case cost is checked against a cap of 80% of the quote. At the cap, the system delivers the best checked candidate or fails without charge. Repairs receive validation/reason codes, not unsafe generated text. Provider costs are tracked separately from the fixed quote.

Exact cache identity hashes canonical JSON containing normalized intent, project ID, memory version, and a settings hash (model configuration, schema and gate versions, `only_axis`, and canonical current DSL). Semantic lookup is scoped by project, memory version, and settings hash. A PostgreSQL `inflight` row with an owner token and expiring lease coalesces concurrent identical work; followers re-check cache and defer without retaining a worker slot, and stale leaders cannot publish. The semantic cutoff is 0.85. `evals/calibration.py` sweeps labelled paraphrases and different intents; the status report reports 0.0% false hits on its negative pairs. This small synthetic set does not guarantee general false-match behavior.

## Billing policy

Admission atomically holds 100,000 micro-credits for full generation or 40,000 for an axis edit. Settlement is idempotent. A valid delivery, including a cache hit, pays the quote; other terminal outcomes release the hold. Provider attempt cost is separate: 429 costs zero, timeout costs input tokens, and other attempts use input and output rates.

| Outcome | Hold action | User charge |
|---|---|---:|
| Delivered, including cache hit | Settle once | Full quote |
| Cancelled | Release | 0 |
| Blocked or failed, including no valid candidate after cap exhaustion | Release | 0 |
| API rate-limited before job admission | No job hold | 0 |
| Provider rate-limited (429), then later delivered | Settle on delivery | Full quote |
| Provider rate-limited and job ultimately fails | Release | 0 |
| Provider timeout, then later delivered | Settle on delivery | Full quote |
| Provider timeout and job ultimately fails | Release | 0 |

This is the user quote; provider costs can still accrue on retries, failures, or cancellation. Logic is in `app/pricing.py`, `app/jobs.py`, and graph finalization.

## Scope and limitations

`IMPLEMENTATION_STATUS.md` reports no known failing issues and no cuts/deferred items, while its next-step entry still names Phase 10. The UI tree currently serves only a bare `ui/index.html` title, so the planned editor, diff, and accept/reject workflow are not present. The planned combined chaos harness is also absent; only `scripts/t7.ps1` exists. The environment scaffold predates the timed implementation window (tag `clock-start`, commit `69e9d5a`).

The mock, fixtures, and synthetic evals do not establish production reliability or semantic correctness. `TIME_SCALE` shortens mock delays. Redis handles rate limiting; pgvector shares PostgreSQL with the job database, so its outage also removes that database.

