# Ledger

The ledger records what was observed while compiling, running and scoring. They are not factors and not part of the component graph; they reference factor components by digest and each other by id. Every record table is append-only. A run or a score is a stage log (a started row, item rows and a finished row), and a new stage is a new row type, not a new table. Every run and score row is marked case-derived (`Record.case_derived`), because those logs hold completions; compilations are not. This marking is what the sensitivity policy acts on.

The suggested storage is one stage table per log type (`ledger.run_stage`, `ledger.score_stage`) keyed by id and stage, item tables (`ledger.run_item`, `ledger.canary_call`, `ledger.score_item`) and a `factor.compilation` table, each holding the record's canonical bytes (Record.canonical) in a payload column. `factor.compilation` is keyed by the digest of the record's canonical bytes, so writing the same compilation twice is a no-op.

A record is stored in its canonical form (defaults omitted, stage kept, plus the record type's schema version `v`, bumped under the same rule as components). `Record.parse` refuses a row whose `v` differs. Every record table has a `doc` jsonb copy and key columns (`run`, `case`, `replicate`, `phase`, `probe`, `view`, …) next to `payload`.

Record timestamps are normalized to UTC.

## Scope rule
This document details:
- A ledger recording each step of the request, the response and the scoring.
- Observations are recorded separately from declared factors: factors are components, observations are records.

## Runs
A run is written one row at a time as it happens.

### RunStarted
- principal author: none; written by the runner
- defined in: `ledger.py:RunStarted`
- suggested storage table: `ledger.run_stage` (stage `started`)

`RunStarted` opens a run with the run id, the time, the rig's code version, the trial, the execution settings, the canary set and when canaries run (`verify_at`, by default at start and end). This row is the run's verification plan. It is declared before the first request and never changes, but it is not part of the trial's identity, so running exactly like run X means copying X's first row. The execution settings (`Execution`) are the order (case-major, replicate-major, or shuffled with a seed), concurrency, timeout and retries. Order and concurrency affect outputs only on engines that are not batch invariant: with batch invariance declared in the engine's `env`, re-runs may be bitwise identical, and otherwise the run falls in the best-effort tier. Timeouts and retries never change a successful output. Neither case drift (comparing the observed vignette fingerprint with the case's) nor canary drift (comparing canary outputs between phases or runs) is checked yet; see "Possible design issues".

### RunItem
- principal author: none; written by the runner
- defined in: `ledger.py:RunItem`
- suggested storage table: `ledger.run_item`

A run item is keyed by (case, replicate index), never repeating the engine or seed, and holds the vignette fingerprint observed at fetch time, for drift detection, and a `Call`.

### CanaryCall
- principal author: none; written by the runner
- defined in: `ledger.py:CanaryCall`
- suggested storage table: `ledger.canary_call`

A canary call holds the phase (`start` or `end`), the canary's position in the set, and a `Call`.

### RunFinished
- principal author: none; written by the runner
- defined in: `ledger.py:RunFinished`
- suggested storage table: `ledger.run_stage` (stage `finished`)

`RunFinished` closes the log with the time, the run's findings and the seal.

The seal is the sha256 over the canonical started row and the canonical item rows sorted by their bytes, so it doesn't depend on the order rows are read back in. It covers the started row, the item rows and the canary-call rows, each sorted by their bytes, separately.

### Call
- principal author: none; written by the runner or scorer as part of another row
- defined in: `ledger.py:Call`
- suggested storage table: inside `ledger.run_item`, `ledger.canary_call` and `ledger.score_item`

A call records one exchange and is shared by run items, canary calls and judge calls. It holds the fingerprint of the wire body, the start and end times, the HTTP status, the number of attempts, any error, and the raw response. The response is stored without its `prompt_token_ids`: those are the token ids the engine actually read after applying its chat template, and since they encode the case text, only their fingerprint is kept (`fingerprint_prompt_tokens`). Comparing these fingerprints between runs of the same trial (`compare_prompt_tokens`) shows whether the engine read the same tokens, without storing case text.

The request fingerprint (Call.request) is `fingerprint_request` taken over over the body's canonical bytes,
with its top-level keys sorted and nested key order kept, so two requests whose schemas list properties in
a different order get different fingerprints. (`src/chatddx/ledger/ledger.py:107`)

### Run
- principal author: none; assembled from stored rows
- defined in: `ledger.py:Run`
- suggested storage table: none

`Run` reassembles a run's log from its rows and rejects stages out of order (`started` → `finished`) or rows from another run; `Run.finish()` produces the finished row. `check_run` compares the run with its trial. It raises for items the trial does not contain, duplicate items, and canary calls outside the plan (a phase not in `verify_at`, a position not in the set, or any canary call when no set is named). It warns when a finished run is missing items, when the engine returned a different model name than declared, when items have no prompt-token fingerprint, and when rows changed after sealing.

## Scores
A score is written the same way as a run.

### ScoreStarted
- principal author: none; written by the scorer
- defined in: `ledger.py:ScoreStarted`
- suggested storage table: `ledger.score_stage` (stage `started`)

`ScoreStarted` holds the score id, the run it scores, the time, the rig's code version, the scorer code actually running and the scoring.

### ScoreItem
- principal author: none; written by the scorer
- defined in: `ledger.py:ScoreItem`
- suggested storage table: `ledger.score_item`

A score item holds a run item's key, a view's position, the value (a number, or empty if the item couldn't be scored), optional detail and the judge calls made for it.

### JudgeCall
- principal author: none; written by the scorer as part of a score item
- defined in: `ledger.py:JudgeCall`
- suggested storage table: inside `ledger.score_item`

A judge call names the judge, the index of the seed used and the `Call`.

### ScoreFinished
- principal author: none; written by the scorer
- defined in: `ledger.py:ScoreFinished`
- suggested storage table: `ledger.score_stage` (stage `finished`)

`ScoreFinished` closes the log with the time, the findings and the seal over the started row and all item rows. Scores have no canary calls. Judge calls live inside score items, so they are sealed with them.

Just as with `RunFinished`, the seal is the sha256 over the canonical started row and the canonical item rows sorted by their bytes, so it doesn't depend on the order rows are read back in.

### Score
- principal author: none; assembled from stored rows
- defined in: `ledger.py:Score`
- suggested storage table: none

`Score` reassembles a score's log the way `Run` does, and `Score.finish()` produces the finished row. `check_score` raises when the score belongs to another run, a view position doesn't exist, an item isn't in the run, or a judge call uses a judge the scorer's views don't name or a seed index out of range. It warns when rows changed after sealing.

## Compilation
- principal author: none; written by the compiler
- defined in: `ledger.py:Compilation`
- suggested storage table: `factor.compilation`

A compilation is a row with a recipe, the skeleton it produced, the compiler's code version and timestamp. It is the lineage from portal chunks to the frozen request; recompiling with a newer compiler adds a new row, not a new factor. It holds no case text and is not case-derived.

`factor.compilation` is keyed by the compilation's digest (`Compilation.digest`) which catalog skeleton references. [1. is this not true for all factors? 2.does this contradict the paragraph above?]

## Possible design issues

### Policy for case-derived content deferred
Completions are stored in plain text in run items, and they can quote or reveal sensitive information.

Completions are in the raw responses of `RunItem.call`, `CanaryCall.call` (non-sensitive) and `JudgeCall.call`.
A policy for such case-derived content has not settled:
 - database-level read restriction on case-derived record tables;
 - a destination check when records are exported.

Note: Part of it exists now: the case-derived tables are in their own ledger schema, and at tier 1 chatddx_reader can read factor but not ledger. Who gets that role is still open.

- The engine's `system_fingerprint` is kept in each call's raw response (`Call.system_fingerprint`) but never compared between calls or runs.

### Misc
- **Records aren't in bundles.** How the ledger is delivered together with the cage isn't specified.
- Nothing compares declared and observed scorer code. ScoreStarted.scorer_code is never checked against Scorer.code, so a mismatch goes unnoticed.

## Proposed amendments
