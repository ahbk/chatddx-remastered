# The runner: work in progress

The runner sends a trial's requests to an engine and writes what happened to the ledger. Remastered has every piece
a run is built from, but nothing yet strings them together: `render`, `Trial`, `Execution`, `prepare_case`, the
ledger records, `Store`, the catalog, the World inventory with `url_of`/`confirm`, and the fake vLLM.

This file split off from `agents/wip-repl.md`, which is parked. The repl is the runner's first caller, not its only
one: a portal, a worker and `chatddx samples` (`agents/wip-sample-data.md`, "Left behind") would call it too.

"Decided" means the user said so. "Take" is the agent's recommendation and still open.

## Decided
- **Clearance (wip-repl D1).** An interim list in the World inventory says which sources an endpoint is cleared
  for. The runner refuses to send case-derived content from a sensitive source anywhere else. A database grant
  can replace the list later (`agents/wip-clearance.md`, "Open", 2).
- **Streaming (D2).** Streaming happens only on the wire:
  - `Call.request` is the fingerprint of `render`'s body;
  - the runner adds `stream` and `stream_options` after fingerprinting;
  - it reassembles the chunks into the completion that `Call.response` holds.
  - This needs a proposed amendment to `docs/factors.md`, "Rendering", which says `stream` isn't sent.
- **Seeds (D3).** `seed none` is dropped. Every trial has seeds, and `render` leaves the seed out on a greedy
  skeleton, so the runner has nothing to do here.
- **Dependencies (D4).** `rich` and Typer are for the shell. The runner imports neither, nor pydantic-ai or httpx,
  and speaks HTTP through the stdlib, as `inventory/serving.py:served` does.

## What the runner is
**In:**
- a `Trial` and a registry holding its closure;
- an endpoint's name;
- an `Execution`;
- the World inventory;
- the person running it;
- a connection.

**Out:**
- a run log in the ledger: `RunStarted`, one `RunItem` per item, and `RunFinished`;
- the run's owner entry (`docs/catalog.md`, "Owners": "a run's owner should be whoever started it. That is a rule
  for the runner");
- a stream of events for whoever is watching.

**Not its job:**
- turning a response into an answer, or scoring it (the scoring pass, `agents/wip-repl.md`, D5);
- creating catalog threads;
- anything on screen.

**One item:**
1. Read the vignette: `Inventory.source(case.vignette.source).fetch(id)`.
2. `prepare_case(case, raw, get, skeleton.appendix_layout, trial.cleanup)`. Its findings go in `RunFinished`.
3. `render(skeleton, model=engine.served_model_name, seed=trial.seeds[r], fills=prepared.fills)`.
4. Fingerprint the body (`fingerprint_request`), then send it with `stream: true` and
   `stream_options.include_usage`.
5. Reassemble the response. `fingerprint_prompt_tokens` takes the prompt token ids out of it and keeps their
   fingerprint.
6. Append a `RunItem` with the key, the vignette's fingerprint and the `Call`.

## Takes for the runner
### R1. Where the clearance list lives
It can't go on the endpoints. `world/endpoints.toml` is written by `chatddx import-engine` ("import again rather
than edit"), and an included file may hold only `endpoint` and `host` tables (`inventory/inventory.py:Inventory.load`).

Take: a `[cleared]` table, only in the hand-written inventory file, mapping an endpoint to the sources it may
receive:

```toml
[cleared]
"qwen3-8b-awq@fake" = ["sample"]
```

`Inventory` checks that each named endpoint and source exists. Clearing then stays a deliberate, hand-written
statement, apart from what's imported.

A source with `sensitive = false` needs no clearance. Canaries carry nothing from a case
(`docs/factors.md`, "Canary sets"), so they need none either.

### R2. The gate
Before `RunStarted` is written, the gate checks that every sensitive source among the trial's cases is cleared for
the endpoint, and then runs `confirm`. A refusal raises, and nothing is recorded.

- Both checks run once per run, not per item, since an endpoint's binding doesn't change mid-run.
- Tools are refused on a sensitive source until there's a way to clear them (`docs/clearance.md`, "Tools").
- The gate is a module of its own, so that judge calls can use it later: the hard block covers judge engines too.

### R3. Record which endpoint a run went to
`Call` records no URL, so the ledger can't show where case-derived content went (`docs/factors.md`, "Runs don't
record where calls went").

Take: a defaulted `RunStarted.endpoint` with the endpoint's name and URL. One run talks to one endpoint. A defaulted
field leaves old seals intact (`docs/ledger.md`, "Canonical form and versions").

### R4. One name for the model an engine answers to
Today `ledger/run.py:check_run` and `inventory/serving.py:confirm` each choose between `LocalEngine.served_model_name`
and `RemoteEngine.model`, and a runner would be the third (`docs/factors.md`, "Smaller issues";
`agents/manifest-triage.md`, C4).

Take: `RemoteEngine.served_model_name`, returning `model`, so all three read the same property.

### R5. Credentials
An endpoint's `credential` is only a name, and nothing stores secrets. The remote Gemma engine will need one.

Take: the name is an environment variable. The runner reads it at send time and sends it as
`Authorization: Bearer`. It never goes in a record. Until this is decided, an endpoint with a credential is refused.

### R6. Rows are committed as they're written
`Store.append` opens `conn.transaction()`. On a connection that isn't in autocommit mode, a statement outside such
a block opens a transaction that only the caller commits. `Catalog.note` and `People.find` are such statements.
Every `conn.transaction()` after that is only a savepoint inside it, so the run's rows wouldn't be durable until
the caller commits.

Take: the runner wants an autocommit connection, and refuses one that isn't. Each row then lands as it's written,
and a crash leaves a readable log that isn't finished. A later `resume(run)` could append the missing items and
finish it, since nothing forbids that.

### R7. Retries, timeouts and the runaway cutoff
- **`Execution.retries`.** Retry when no response started (a refused connection, a reset, a timeout before the
  first byte) or on a 429 or 5xx. Never retry once chunks have arrived. `Call.attempts` counts the tries, and
  `started_at` is the first try's.
- **`Execution.timeout_s`.** A whole-call limit, checked between chunks, with the socket's timeout as a backstop.
- **Runaway.** The old runner stopped a stream after 100 tokens of nothing but whitespace
  (old `src/chatddx/runtime/run.py:RUNAWAY`). The runner hangs up (the fake notices), and puts
  `stopped: nothing but whitespace for 100 tokens` in `Call.error`. The response so far is kept.

  Like a timeout, the cutoff decides what answer gets recorded. Take: make it an execution setting
  (`Execution.whitespace_limit`, default 100), so `RunStarted` records it.

### R8. When an item can't be sent or gets an error back
- **The vignette is missing or can't be read** (`LookupError`, or `StructuralError` for text that isn't UTF-8).
  No request goes out, so there's no `Call` and no `RunItem`. The runner adds a finding, `case.unreadable`, to
  `RunFinished`, and goes on. `check_run` then reports `run.incomplete`.
- **The HTTP request fails** (a 4xx, or a 5xx after retries). `Call.status` holds the status, `Call.response` the
  error body and `Call.error` its message, and the run goes on. A dead endpoint gives every item an error rather
  than stopping the run (warnings, not crashes: `docs/chatddx.md`, "Compromises"). Stopping is the caller's choice.

### R9. Stopping
- **`stop()`** ends the run after the item under way. The repl's first Ctrl-C will call it.
- **A `KeyboardInterrupt` during a call** hangs up and records the call with `Call.error = "stopped"`. It appends
  that item and ends the iteration.
- **Either way**, the caller then finishes the run, and `check_run` reports what's missing.

### R10. Data that mustn't leak
Request bodies hold the vignette.
- Never log them.
- Never put them in exception messages or events.

The response is case-derived, but storing it is what `RunItem` is for (`docs/ledger.md`, "Case-derived records").
Events carry the streamed text to the caller, who decides what to show.

## Shape
`src/chatddx/runner/`. These names are a sketch and can change.

- **`wire.py`**
  - `post(url, body, *, credential, timeout)` sends to `/v1/chat/completions`. It returns the parsed SSE chunks
    of a stream, or the JSON body of an error, with its status.
  - `assemble(chunks)` gives the completion, following the fake's stream (`docs/vllm.md`, "The stream"):
    - `role` from the opening chunk;
    - `content` and `reasoning` concatenated;
    - tool calls merged by index, their arguments concatenated;
    - `finish_reason` from the finish chunk;
    - `token_ids` concatenated;
    - `usage` from the usage chunk;
    - `system_fingerprint` from the finish or usage chunk (item 15);
    - `prompt_token_ids` from the first chunk only (item 1).
- **`gate.py`**: R1 and R2.
- **`send.py`**: one request in, events out, and a `Call` at the end. It handles retries, the timeout, the runaway
  cutoff and stopping (R7, R9).
- **`run.py`**:
  - `Runner(conn, inventory, by)`.
  - `.start(trial, registry, endpoint, execution)`:
    1. Run the gate.
    2. `Store.add` the trial's closure.
    3. Append `RunStarted`, with `rig()`, `execution`, `tool_code` and the endpoint.
    4. Write the owner entry.
    5. Return a `Running`.
  - Iterating a `Running` sends the items in `Execution.schedule` order and gives events.
  - `.finish()` appends `RunFinished` with the findings, then returns `check_run`'s findings.
- **Events**: an item starting (key, seed), a reasoning delta, a content delta, a tool-call delta (the token count
  from each chunk's `token_ids`), an item ending (its `RunItem`), and a finding.

  They're synchronous. `Store` has no async API (`docs/store.md`, "Known gaps"), and `concurrency` 1 needs none.

## Phases
### 0. Prerequisites, outside `runner/`
- R4: `RemoteEngine.served_model_name`, used by `check_run` and `confirm`.
- R1: the `[cleared]` table in `Inventory`, with its checks and a test.
- R3 and R7: `RunStarted.endpoint` and `Execution.whitespace_limit`, if taken.
- **Shared test helpers.** `sample_plan` exists twice (`inventory/test/test_serving.py`,
  `store/test/test_seed.py`), and so do the fake-in-a-thread `serving` helpers (`test_serving.py`,
  `fake_vllm/test/test_fake_vllm_server.py`). The database fixtures live in `store/test/conftest.py`, which
  `runner/test/` can't see. Take:
  - move the fixtures to `src/chatddx/conftest.py`;
  - give the helpers one home, such as `chatddx.fake_vllm.server.serving(...)`.

### 1. Wire
`wire.py`, tested against the fake in a thread on port 0:
- the reassembled stream gives the same message, finish reason, token ids, usage, system fingerprint and prompt
  token ids as the unstreamed completion of the same body;
- the fake's refusals come back with their status and body (`docs/vllm.md`, item 13);
- hanging up is heard (`test_a_client_that_hangs_up_is_heard_at_once_and_its_request_aborted`).

### 2. Call
`send.py`, tested on:
- the seed sent, and left out on a greedy skeleton;
- prompt token ids fingerprinted and not stored;
- the request fingerprinted without `stream`;
- runaway, with the fake's `--runaway`;
- a stop mid-stream;
- a retry after a refused connection, counted in `attempts`;
- the timeout;
- the credential sent but not recorded (R5).

### 3. Run
`run.py` and `gate.py`.

Tests use:
- a migrated database and `People.add`;
- the sample plan's registry (`seed.plan_factors` is pure, so no catalog seeding is needed);
- cases from `sample-world` or a `MemorySource`;
- a temporary World inventory whose fake endpoint points at the test's port and has a `[cleared]` table.

They cover:
- a clean `check_run`;
- the owner entry;
- each row durable as it's written (R6);
- `case.drift`, and `case.unreadable`;
- the gate refusing an uncleared sensitive source, with nothing recorded;
- `confirm` refusing an endpoint that serves another digest;
- `stop()` giving `run.incomplete`;
- `attestation.model` staying quiet.

### 4. Execution
- Shuffled and replicate-major order.
- `concurrency` above 1, in threads with one writer, refused above the endpoint's `max_jobs`.

Tested by `check_execution` staying quiet.

### 5. Tool rounds
- The `next_request` loop up to `skeleton.max_rounds`, recorded as `Turn` and `ToolRun`.
- Tools found with `core/rig.py:entry`, and their code recorded in `RunStarted.tool_code`.
- R2 gates them.

`plan-web` can't call its tool on vLLM 0.24 anyway (G8; `docs/vllm.md`, item 10), so this comes late.

### 6. Canaries
- A helper that makes a request body from a canary (`agents/manifest-triage.md`, C6).
- Start and end phases per `verify_at`, recorded as `CanaryCall`.
- No clearance needed, but `confirm` still applies.

The sample has no canary set yet.

### Afterwards
- The scoring pass (`agents/wip-repl.md`, D5, D8).
- `chatddx samples` back on the runner.
- A runner finding for reasoning that wasn't heeded: the old `bench/outcome.py:unheeded` warned when thinking came
  back on a cell that asked for none, or none came back on one that asked for it.

## Docs to propose
Agents propose; humans write (`AGENTS.md`, "Docs").
- `docs/factors.md`, "Rendering": the runner streams on the wire only (D2).
- `docs/clearance.md`: the interim `[cleared]` table and the gate (R1, R2).
- `docs/ledger.md`, "RunStarted", and `docs/factors.md`, "Execution": `endpoint` and `whitespace_limit`, if taken
  (R3, R7).
- `docs/catalog.md`, "Owners": the runner writes the owner.
- `docs/store.md`, "Known gaps": rows committed as they're written, and the runner's autocommit connection (R6).

## Still open from elsewhere
- **`return_token_ids` on remote engines** (`agents/manifest-triage.md`, D): a non-vLLM API may refuse it. The
  fake and vLLM accept it, and the remote engine waits for an endpoint anyway.
- **Concurrent writers** (`docs/catalog.md`, "Concurrent writers"). The runner writes only its own run's rows and
  one owner entry, so it adds no new conflict.
