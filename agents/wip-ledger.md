# Ledger: reconciliation in progress

Working notes for a drop-in replacement of `docs/ledger.md` (target: `agents/ledger.md`). Baseline `e635b97`.
"Decided" means the user said so; "Take" is the agent's recommendation and still open.

Code: `src/chatddx/ledger/ledger.py` (below: `ledger.py`), tests `src/chatddx/ledger/test/test_ledger.py` (6 pass).
The package imports only `chatddx.factors`, so the code already leans on factors alone; the doc doesn't.

## Constraints
- The doc leans on `docs/factors.md` and the code only.
- Completeness is preserved; language is plain.
- `docs/factors.md` links into the ledger doc by section name: `Call`, `Compilation`, `RunStarted`, `JudgeCall`,
  `Run`. The replacement keeps those headings.

## Inventory
| Name | What it is |
| --- | --- |
| `Record` | Base of stored rows: canonical form with `v`, `parse` refusing another `v`, class-level `case_derived` (True). |
| `Call` | One HTTP exchange: request fingerprint, times, status, response minus `prompt_token_ids`, prompt-token fingerprint, attempts, error. |
| `fingerprint_prompt_tokens`, `fingerprint_request` | Fingerprint helpers, both with an optional HMAC key. |
| `ToolRun`, `Turn` | One tool the runner ran; one tool round (tools run + next call). |
| `ItemKey` | (case, replicate). |
| `RunStarted`, `RunItem`, `CanaryCall`, `RunFinished`, `Run` | Run log rows and their reassembly. |
| `JudgeCall`, `ScoreStarted`, `ScoreItem`, `ScoreFinished`, `Score` | Score log rows and their reassembly. |
| `Compilation` | Recipe → skeleton lineage; not case-derived; keyed by its own digest. |
| `check_run`, `check_score`, `compare_prompt_tokens` | Checks; raise `StructuralError` or return findings. |

## Issues

### A. The doc says something the code doesn't do
- **A1** "`case_derived` is what the sensitivity policy acts on" (`docs/ledger.md:3`): nothing reads
  `Record.case_derived` (`ledger.py:54`); grep finds no reader. The mark is on the Python class, not on stored rows.
- **A2** `CanaryCall` inherits `case_derived = True`, while the design-issues section calls `CanaryCall.call`
  non-sensitive (`docs/ledger.md:142`). Stage rows (`RunStarted`, `RunFinished`, `ScoreStarted`, `ScoreFinished`)
  hold no completions but are marked too: the mark follows the log, not the content.
- **A3** Key column `probe` (`docs/ledger.md:7`) doesn't exist; it is `canary`. (`docs/store.md:24` has the same
  stale name.)
- **A4** "Model attestation covers every call" (`docs/ledger.md:82`): it covers every call of every run item.
  Canary calls and judge calls get neither the model check nor the prompt-token check.
- **A5** Pointer `ledger.py:107` for `fingerprint_request` (`docs/ledger.md:70`) is stale; it is at line 115 (107 is
  the prompt-token comment). Symbol pointers (`ledger.py:fingerprint_request`) don't rot.
- **A6** "The response is stored without its `prompt_token_ids`" is up to the runner calling
  `fingerprint_prompt_tokens`; `Call` accepts a response that still has them (checked).
- **A7** "the fingerprint of the wire body": it is over the body's canonical JSON (compact, non-ASCII kept,
  top-level keys sorted, nested order kept), not over the bytes the HTTP client sent.
- **A8** "recompiling with a newer compiler adds a new row, not a new factor": only when the skeleton comes out the
  same. A different skeleton is a new factor and a new row. And "writing the same compilation twice is a no-op"
  holds for byte-identical rows only: `at` is in the bytes, so the same recipe and compiler a second later is a new
  row.
- **A9** Comment `ledger.py:49` ("timestamptz reads back in UTC"): no table stores record times as `timestamptz`
  (`payload` is text, `doc` is jsonb). UTC is still needed so one instant has one spelling in sealed bytes; the
  stated reason is not the current one.
- **A10** Seal (`docs/ledger.md:59`): the first sentence leaves canary calls out, the second adds them. Code
  (`ledger.py:_seal`): sha256 over the canonical JSON `{"canaries": [...], "items": [...], "started": {...}}`, each
  list sorted by row bytes; a score has no `canaries` key.
- **A11** "running exactly like run X means copying X's first row": the row carries X's run id, time and rig. What
  carries over is the trial, `execution`, `canaries` and `verify_at`.

### B. Checks the doc (or `docs/factors.md`) implies but the code doesn't make
- **B1** Which findings go in the finished row? `RunFinished.findings` is written by `finish()`, rows are
  append-only, and `docs/chatddx.md` runs `check_run` *after* `finish()`. But `run.incomplete` and `ledger.seal`
  only fire on a finished run, so `check_run`'s findings can't be in the row they check. The finished row itself
  (`at`, `findings`) is outside the seal.
- **B2** `check_score` has no duplicate check (`check_run` refuses duplicate items) and no `score.incomplete`
  counterpart of `run.incomplete`.
- **B3** `check_run` refuses unplanned canary calls but not duplicate ones, and doesn't warn when planned ones are
  missing from a finished run.
- **B4** The recorded `Execution` is never compared with what happened: `Call.attempts > retries + 1` passes.
- **B5** `ScoreStarted.scorer_code` is never compared with `Scorer.code` (already a design issue in the doc;
  `docs/factors.md:36` says recording it "shows whether what ran is what should have run").
- **B6** A judge call passes if its judge belongs to *any* view of the scorer, not the score item's own view.
  Nothing checks that each of the judge's seeds was called.
- **B7** A score records no execution settings for its judge calls, though judge engines batch like any other.
- **B8** `compare_prompt_tokens(a, b)` matches items by key only; it doesn't check that `a` and `b` are runs of the
  same trial.
- **B9** A response without a `model` passes the model check silently.
- **B10** `check_score` doesn't require the run to be finished or its seal to hold. Whether an open run may be
  scored is unsaid.
- **B11** Nothing checks that a `Compilation`'s recipe compiles to its skeleton.
- **B12** Records accept odd values (all checked):
  - `ToolRun` with both `result` and `error`, or neither;
  - `verify_at` with duplicates or in any order (`("end", "start")` seals differently from `("start", "end")`),
    and kept when no canary set is named;
  - `finished_at` before `started_at`;
  - `ScoreItem.value = NaN` stored as `null`, which reads back as "couldn't be scored".
- **B13** Unknown tools: the code refuses an unknown-name `ToolRun` unless it has an error and no result; the doc says
  "a result from a tool the skeleton doesn't have". For an error, what text went back to the model isn't recorded,
  though "results are stored as sent".

### C. Ambiguities and wording
- **C1** "Ledger" means two things: the package (all records, compilations included) and the Postgres schema
  `ledger` (case-derived tables only; compilations live in `factor.compilation`).
- **C2** "Tier" means two things: a reproducibility tier ("best-effort tier", `docs/ledger.md:26`) and a store
  migration tier ("at tier 1 chatddx_reader", `docs/ledger.md:147`). `docs/factors.md` defines neither.
- **C3** A compilation's digest looks like a component digest (`sha256:…`) but names no component; a `Registry`
  can't resolve it. The human's question at `docs/ledger.md:135`: (1) all components are keyed by digest, but
  `Compilation` is the only *record* keyed by digest, since it has no id of its own; (2) no contradiction with the
  paragraph above once A8 is fixed.
- **C4** "a started row, item rows and a finished row": a run also has canary-call rows.
- **C5** Path convention unstated: `ledger.py:X` in one place, `src/chatddx/ledger/ledger.py:107` in another.
- **C6** Case drift and canary drift sit under `RunStarted`; the `system_fingerprint` bullet sits under the
  case-derived-policy heading.
- **C7** The `Execution` paragraph (`docs/ledger.md:26`) restates `docs/factors.md` "Execution" and drifts from it:
  "Timeouts and retries never change a successful output" vs "decide whether an item gets an answer at all".

### D. Scope: what the doc leans on besides factors
- **D1** Storage paragraphs and every "suggested storage table" line duplicate `docs/store.md` "Layout".
- **D2** The catalog (`docs/ledger.md:135`).
- **D3** Roles, grants and tiers (`chatddx_reader`, `docs/ledger.md:147`): store and clearance.
- **D4** Bundles and the cage (`docs/ledger.md:152`).
- **D5** The other direction: `src/chatddx/factors/base.py:Frozen` keeps `stage` in canonical form, a ledger field.
  It is the only place factors knows about the ledger.

### E. In the code, missing from the doc
- **E1** `ItemKey`, `Turn`, `ToolRun` and `Record` have no entries of their own; `Phase` isn't named.
- **E2** The HMAC option of `fingerprint_request` and `fingerprint_prompt_tokens` (`key=`). Runs fingerprinted
  under different keys always differ.
- **E3** Error types: a log out of order or mixing logs raises pydantic's `ValidationError` (from `Run`/`Score`);
  `check_run`/`check_score` raise `StructuralError`.
- **E4** Finding subjects: the item key (`case.drift`, `attestation.model`, `tools.unanswered`,
  `attestation.prompt_tokens_drift`), the run or score id (`ledger.seal`), the scorer's digest
  (`view.unreachable`), none (`run.incomplete`, `attestation.prompt_tokens`).
- **E5** The case-derived list names `RunItem.call`, `CanaryCall.call` and `JudgeCall.call`, but not the calls in
  `Turn`, tool arguments (inside responses), `ToolRun.result`/`error` or `ScoreItem.detail`.
- **E6** `Call.returned_model`, `RunItem.calls`.

### Adjacent docs (become proposed amendments there, not edits)
- `docs/findings.md`: `tools.unanswered` is listed under "Factors" but comes from `check_run`.
- `docs/store.md:24`: `probe` → `canary`.
- `docs/chatddx.md`: "Seal: the hash over a finished log" (it is over the started and item rows, stored in the
  finished row); "check_run then compares" after `finish()` (B1).

## Decisions
- Decided (1a): the doc leans on `docs/factors.md` and the code only. Storage, catalog, roles, grants and clearance
  leave it; what isn't already elsewhere becomes a proposed amendment there.
- Decided (2b): fix the small, safe gaps in code with tests (B2, B3, B6, B12, A9); the rest go one by one.
- Decided (3a): `case_derived` follows content: only `RunItem` and `ScoreItem` are case-derived.
- Decided (4a): a finished row holds what the runner or scorer saw while working; `check_run`/`check_score` can be
  repeated and their findings aren't stored.
- Decided (5): the ledger doc lists its finding codes inline.
- Decided: C1, C2 and the remaining B items are cleared one by one.
- Decided (C1, option A): a compilation is a component of kind `compilation` that isn't a factor: *provenance*,
  beside canary sets as instruments. It lives in `src/chatddx/factors/request.py`, without `at`; the ledger is the
  run and score logs only, which is also what schema `ledger` holds. No data to keep: migrations `0001`, `0003` and
  `0009` were edited in place, following `e635b97`.
- Decided (C2, option a): "tier" means only the store's integrity tiers. Reproducibility is described in plain
  words: bitwise reproducible, or best-effort. Amendment proposed for `docs/chatddx.md` ("Compromises").
- Decided (B4, option b): `check_run` warns `execution.retries`, `execution.order` and `execution.concurrency`.
  "Order" means send order: an item is sent when its first call starts. With retries, a call's times run from the
  first attempt's start to the last attempt's end. Timeouts stay unchecked.

## Status
Draft: `agents/ledger.md`.

| Item | Outcome |
| --- | --- |
| A1, A2 | Code: per-type `case_derived` (3a). Doc: acting on the mark is up to whoever stores or exports. |
| A3 | Doc; `docs/store.md` amendment (`probe` → `canary`). |
| A4–A8, A10, A11 | Doc corrected. |
| A9 | Code comment fixed. |
| B1 | Decided (4a). Doc; `docs/chatddx.md` amendment. The finished row being outside the seal is an open issue. |
| B2, B3, B6, B12 | Code and tests: `score.incomplete`, `judge.incomplete`, `run.incomplete` for canaries, duplicate checks, record validation. `docs/findings.md`, `docs/chatddx.md` amendments. |
| B4 | Decided (b). Code and tests (`_executed`); draft updated; `docs/findings.md`, `docs/factors.md` (send-order wording), `docs/chatddx.md` amendments. Timeouts stay an open issue. |
| B5, B7–B10, B13 | Open, one by one. Listed in the draft's "Open design issues" meanwhile. |
| B11 | Moved to factors with the compilation; the proposed `docs/factors.md` "Compilation" section states it. Still open. |
| C1 | Decided (A). Code: `Compilation` in factors, `factor.compilation` dropped, the catalog's foreign keys retargeted, the seeder adds compilations as components. Doc: the draft drops its Compilation section. Amendments: `docs/factors.md`, `docs/store.md`, `docs/chatddx.md`. |
| C2 | Decided (a). The draft already avoids "tier"; `docs/chatddx.md` amendment. Also proposed: `docs/store.md` wording for schema `ledger` (only `run_item` and `score_item` are case-derived). |
| C3–C7 | Doc. |
| D1–D3 | Out of the doc; `docs/store.md` (D1) and `docs/catalog.md` (D2) already cover them; D3 → `docs/clearance.md` amendment. |
| D4 | Kept: bundles are a factors concept. Open issue "Records aren't in bundles". |
| D5 | Noted only. |
| E1–E6 | Doc. |

## Flagged: order used as something other than send order
- **O1** A trial's case list is ordered and its order is in the trial's digest (`src/chatddx/factors/trial.py:Trial`,
  `cases`), but the only thing that reads that order is `Execution.schedule` (`case_major@1`, `replicate_major@1`).
  So part of the send order is pinned, though send order is meant to be a recorded setting outside the trial's
  identity (`docs/chatddx.md`, "execution settings are not part of trial identity"). Two trials with the same cases
  in another order are different trials. Open.
- **O2** Not checked: that start-phase canary calls precede the items and end-phase ones follow them.

## Surprises
- `ruff check src` fails on `src/chatddx/inventory/test/test_inventory.py` (import order) at `e635b97`, before
  these changes. Left as is.

## C1: what a compilation is
Investigated at `c2c3b3e`.

### How it got here
- `agents/manifest-reconciliation.md` D7: `request` stopped being a component; its chunk references became a
  `Recipe` inside a `Compilation` *record*, "lineage as `Compilation` record".
- `agents/manifest-triage.md` A5: case-derived tables got their own schema so a read restriction could come later;
  "`compilation` stays out (`Compilation.case_derived` is false)". So `factor.compilation` was placed by
  sensitivity, not by what it is.
- `agents/wip-catalog.md` decision 3 (A2): recipes are skeleton threads; `catalog.edit.compilation` points at the
  compilation that produced the edit's skeleton (`0009-t0-catalog.sql`, FK `(compilation, digest)` →
  `factor.compilation (digest, skeleton)`).

### What it is
`compile_request(recipe, get)` is a pure function of the recipe's chunks and the compiler's code: nothing is
observed. A compilation states "compiler C turns recipe R into skeleton S". It is a derivation, not an event.

It has every trait of a component but one:
- immutable, canonical form with `v`, keyed by the digest of its bytes;
- its references are typed `RefTo` (`request.py:Recipe`), and the catalog walks them (`iter_refs(recipe, "/recipe")`
  in `src/chatddx/store/catalog.py:behind`);
- referenced by digest from outside (`catalog.edit.compilation`);
- the odd one out: `at`.

### Symptoms
- **S1** Placed by sensitivity, not identity. `factor` already holds a non-factor (canary sets, "instruments",
  `docs/factors.md`), so the schema really means "components"; compilation sits there in a table of its own,
  outside the component machinery.
- **S2** Its recipe's references are unchecked: only `skeleton` has a foreign key, and no reference rows exist.
  `src/chatddx/store/test/test_store.py:test_compilations_are_idempotent` stores a recipe naming chunks that don't
  exist.
- **S3** `at` makes the digest an event's: compiling the same recipe again gives a new row. The seeder works around
  it by not writing a compilation when the skeleton is unchanged (`src/chatddx/seed/write.py:record`), which also
  drops a recipe change that leaves the skeleton the same. `catalog.edit.at` already holds when it was adopted.
- **S4** Skeleton → recipe is many-to-one: different recipes can give one skeleton (`docs/factors.md`, "How chunks
  affect each other"), and `at` adds duplicates of the same recipe. `Catalog.language_of` takes any of them
  (`ORDER BY digest LIMIT 1`), not the one the thread's edit names.
- **S5** Not in bundles: a published cage carries the skeleton but not how it was made, though the doc calls the
  compilation "the lineage from the chunks to the frozen request".

### Options
- **A. A component that isn't a factor** (like canary sets): kind `compilation` in `factors/request.py` beside
  `Recipe` and `compile_request`; drop `at`; stored in `factor.component` with reference rows; the catalog's foreign
  key retargeted. The ledger becomes run and score logs only, so C1 dissolves: ledger = logs = schema `ledger`.
- **B. Keep it a record, move it to the ledger**: `ALTER TABLE factor.compilation SET SCHEMA ledger`; grants per
  table instead of per schema (reader may read the non-case-derived ones); reference checks added separately. "Ledger"
  = all records = schema. S3–S5 stay.
- **C. Fold it into the catalog**: the edit holds the recipe and the compiler. Provenance then exists only where a
  thread does, never in bundles, and the catalog stops being "handles only". Not recommended.
- Rejected: the recipe inside the skeleton. Two recipes with the same request would give different skeletons, so
  different trials, against "the compiler's code is not a separate factor" (`docs/factors.md`).
- Later, compatible with A: the recipe as a component of its own, so recipes can be threaded and named directly
  (the G9 pain point in `agents/wip-sample-data.md`); it reopens catalog decision A2.

Take: A.
