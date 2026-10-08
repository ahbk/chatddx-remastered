# The runner: work in progress

The runner sends a trial's requests to an engine and writes what happened to the ledger. It strings together
pieces that already existed:
- `render`, `Trial`, `Execution` and `prepare_case`;
- the ledger records, `Store` and the catalog;
- the World inventory with `url_of` and `confirm`;
- the fake vLLM.

This file split off from `agents/wip-repl.md`, which is parked. The repl is the runner's first caller, not its only
one: a portal, a worker and `chatddx samples` (`agents/wip-sample-data.md`, "Left behind") would call it too.

"Decided" means the user said so. "Take" is the agent's recommendation and still open.

## Start with this
Phases 0–3 are done (`src/chatddx/runner/`). A trial runs at an endpoint and leaves a finished run log and its owner
entry, and `check_run` comes back clean.

It was checked twice:
- by the tests in `src/chatddx/runner/test/`;
- by hand: the `chatddx fake-vllm --runaway` process, started from `sample-world/inventory.toml`'s gpt-oss endpoint,
  ran three cases on `plan (openai/gpt-oss-20b)`. Each call was cut at 100 tokens of whitespace, the fake heard each
  hang-up, and the run recorded its endpoint and cutoff.

Next: phase 4, then 5 and 6 (see "Left").

**Real engines aren't cleared yet.** `world/inventory.toml` has no `[cleared]` table, so the gate refuses `pelle` and
`malborg` for the sample. Clearing them is a human's statement to make.

## Decided
- **Clearance (wip-repl D1).** An interim list in the World inventory says which sources an endpoint is cleared
  for. A database grant can replace it later (`agents/wip-clearance.md`, "Open", 2).
- **Streaming (D2).** Streaming happens only on the wire. `Call.request` fingerprints `render`'s body, and the stream
  is reassembled into the completion that `Call.response` holds.
- **Seeds (D3).** `seed none` is dropped.
- **Dependencies (D4).** The runner uses none of `rich`, Typer, pydantic-ai or httpx, and speaks HTTP through the
  stdlib.
- **R1–R10, as recommended.**
  - R7 with a reservation from the user: `max_tokens` is the more beaten path. They hold:
    - `Sampling.max_output_tokens` is that path, and it stays: it goes in the skeleton and bounds every answer;
    - `Execution.whitespace_limit` bounds only a runaway, and saves the time one burns till its tokens run out;
    - `whitespace_limit = None` turns the cutoff off for a run.
  - If the cutoff turns out not to earn its place, removing it is a one-field change.

## What was built
### Phase 0: outside `runner/`
- **R4.** `RemoteEngine.served_model_name` returns `model`, so `check_run`, `confirm` and the runner read one name
  (`factors/engine.py`).
- **R1.** `Inventory.cleared` and `cleared_for` (`inventory/inventory.py`). The table is read only from the
  inventory's own file and checked against its endpoints and sources. `sample-world/inventory.toml` clears both fake
  endpoints for `sample`.
- **R3.** `RunStarted.endpoint` (`ledger/run.py:Endpoint`): the endpoint's name and URL, left out by default.
- **R7.** `Execution.whitespace_limit`, 100 by default (`factors/trial.py`).
- **Test helpers.**
  - The database fixtures moved from `store/test/conftest.py` to `src/chatddx/conftest.py`.
  - The fake in a thread is `chatddx.fake_vllm.server.serving`, used by the fake's own tests, the inventory's and
    the runner's.
  - `sample_plan` is still defined twice (`inventory/test/test_serving.py`, `store/test/test_seed.py`); the runner's
    tests build their own plan.

### Phase 1: `wire.py`
- **`post`** sends to `/v1/chat/completions` and returns a `Stream` of parsed SSE chunks. Closing the stream hangs
  up.
- **`Rejected`** holds an error answer's status and body. An error sent in place of a chunk is one too, with status
  200.
- **`assemble`** adds the chunks up to the unstreamed completion. On the fake, the result is the same for text, a
  schema, a named tool and required tools. The tests compare everything but `id`, `created` and the keys that come
  back as nulls.

### Phase 2: `send.py`
`send(url, body, execution=, secret=, on_delta=, key=, backoff=)` returns a `Call`:
- **The body.** `stream` and `stream_options.include_usage` are added after fingerprinting.
- **What's kept.** The prompt token ids become a fingerprint and leave the response.
- **Retries** only where no answer came back: a connection failure, a 429 or a 5xx. Backoff is exponential. An answer
  cut short is an answer, and isn't tried again.
- **Cut short**, keeping what came:
  - after `whitespace_limit` tokens of whitespace in a row;
  - when `timeout_s` has passed since the stream started, with the socket's timeout as a backstop.
- **A KeyboardInterrupt** hangs up and raises `Stopped`, which carries the call so far, with `error = "stopped"`.
- **`Delta` events** carry reasoning, content or tool-call text and the running token count, from each chunk's
  `token_ids`.

### Phase 3: `gate.py` and `run.py`
**`gate.check`** refuses before anything is written:
- an endpoint serving another engine than the trial's (a check the plan hadn't listed);
- a sensitive source the endpoint isn't cleared for;
- tools on a sensitive source;
- an endpoint that `confirm` can't confirm.

**`gate.secret`** reads the variable an endpoint's `credential` names (R5) and refuses when it isn't set. The secret
goes only into the `Authorization` header; a test checks that no stored row holds it.

**`Runner(conn, inventory, by)`** refuses a connection that isn't in autocommit mode (R6). `.start(trial, registry,
endpoint, execution)`:
1. Refuses `concurrency` above 1 and a skeleton with tools.
2. Runs the gate.
3. `Store.add`s the trial's closure.
4. Appends `RunStarted`, with `rig()`, the execution and the endpoint.
5. Writes the run's owner entry.

**`Running.send(on_event)`** sends the items in `Execution.schedule` order:
- each item is `fetch`, `prepare_case`, `render` with `engine.served_model_name`, then `send`, then appending a
  `RunItem`;
- a case that can't be read is a `case.unreadable` finding, and the run goes on (R8);
- `stop()` ends after the item under way;
- a KeyboardInterrupt during a call appends that item as stopped and is raised again (R9).

**`Running.finish()`** appends `RunFinished` with the findings the runner saw, then returns `check_run`'s.

**Changed from the plan: events go to a callback** (`on_event`), not to an iterator. A KeyboardInterrupt raised
while the caller handles an event then lands inside the runner's call, which records it. With a generator, it would
land in the caller's frame while the runner sat suspended at a `yield`. The events are `ItemStarted`, `Delta`,
`ItemEnded` and `Noted`.

## Left
### 4. Execution
- **Order.** Shuffled and replicate-major order already work, through `Execution.schedule`. They still need a test.
- **Concurrency above 1.** Threads, with one writer, refused above the endpoint's `max_jobs`.
- `check_execution` should stay quiet.

### 5. Tool rounds
- The `next_request` loop up to `skeleton.max_rounds`, recorded as `Turn` and `ToolRun`.
- Tools found with `core/rig.py:entry`, and recorded in `RunStarted.tool_code`.
- Lift the refusal in `Runner.start`. The gate keeps refusing tools on a sensitive source.

`plan-web` can't call its tool on vLLM 0.24 anyway (G8; `docs/vllm.md`, item 10).

### 6. Canaries
- A helper that makes a request body from a canary (`agents/manifest-triage.md`, C6).
- Start and end phases per `verify_at`, recorded as `CanaryCall`. They need no clearance, but `confirm` still applies.

### Afterwards
- **The scoring pass** (`agents/wip-repl.md`, D5, D8).
- **`chatddx samples`** back on the runner.
- **A runner finding for reasoning that wasn't heeded**, like the old `bench/outcome.py:unheeded`.
- **Resuming.** `resume(run)` would append an unfinished log's missing items: `Running.send` already skips items
  that are in.
- **A summary of failed calls.** `check_run` says nothing about `Call.error`. A run whose every call was cut short
  checks clean, and only the items show it. A UI can count them, or the runner could note them.

## Docs proposed
These are in the docs' "Proposed amendments" sections:
- `docs/factors.md`:
  - "Rendering": the runner streams;
  - "Execution": `whitespace_limit`;
  - "Smaller issues": the model name is now one property;
  - "Runs don't record where calls went": `RunStarted.endpoint`.
- `docs/ledger.md`, "RunStarted": `endpoint`.
- `docs/clearance.md`: the `[cleared]` table and the gate.
- `docs/catalog.md`, "Owners": the runner writes a run's owner.
- `docs/store.md`: where the test fixtures live, and why the runner wants autocommit.

## Still open from elsewhere
- **`return_token_ids` on remote engines** (`agents/manifest-triage.md`, D): a non-vLLM API may refuse it. `render`
  sends it for every engine.
- **Concurrent writers** (`docs/catalog.md`, "Concurrent writers"). The runner writes only its own run's rows and one
  owner entry.
