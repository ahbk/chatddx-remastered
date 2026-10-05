# Ledger

The factors (`docs/factors.md`) say what an experiment should be. The ledger says what happened: which requests a
run sent and what came back, and how a score graded them. It is written down as *records*, rows that are added and
never changed, and the ledger is all of them.

How a skeleton was compiled is not in the ledger. Compiling is deterministic, so nothing about it is observed; a
compilation is provenance, stored as a component (`docs/factors.md`, "Compilation").

Records are not factors. A factor is chosen in advance and pinned by digest; a record is written when something
happens. Records hold the plan of a run or a score, which is its *recorded* factors, what the rig did, and what the
world answered, which is the evidence of the *observed* factors (`docs/factors.md`, "What a factor is"; see "What a
record holds"). They refer to components by digest and to each other by id.

Code paths are relative to `src/chatddx/ledger/` unless they start with `src/` or `docs/`. The package builds on
`chatddx.factors` and imports no other chatddx package.

## How records work
- defined in: `ledger.py:Record`

### Logs and stages
Every record belongs to a *log*: a run or a score. A log has
- a started row, written first, saying what is about to happen;
- item rows, one for each thing that happened (a run also has canary-call rows);
- a finished row, written last, holding the seal.

The started and finished rows are the log's *stages*. A new kind of stage would be a new row type in the same log,
not a new log.

Records are never changed or removed: a log only grows, by adding rows.

### What a record holds
A record holds three kinds of content.

| Content | What it is | Where | Examples |
| --- | --- | --- | --- |
| Plan | What the run or the score is set to do, written before it starts: its recorded factors. | Started rows | `trial`, `execution`, `canaries`, `verify_at`, `rig`, `scorer_code` |
| Conduct | What the rig itself did. | Item rows | the request sent (`Call.request`, a fingerprint), when, how many attempts, the tool results sent back, a score item's value and detail |
| Observation | What the world answered: the evidence of the observed factors. | Item rows | the response, the model name it gives, the prompt-token fingerprint, the vignette fingerprint read at the source |

A run item holds all three: its key comes from the plan; its request fingerprint, times and attempts are conduct
(the times also show how fast the engine answered); its response and fingerprints are observations. Finished rows
add conclusions drawn from the rest: the seal and the findings.

Each check compares two of these, or one of them with the pinned factors:

| Compares | Findings |
| --- | --- |
| Plan and conduct | `run.incomplete`, `score.incomplete`, `execution.retries`, `execution.order`, `execution.concurrency` |
| Pinned factors and plan | `score.scorer_code` |
| Pinned factors and conduct | `judge.incomplete` |
| Pinned factors and observations | `attestation.model`, `case.drift`, `tools.unanswered` |
| Pinned factors with each other | `view.unreachable` (a scorer's view and the run's skeleton) |
| Observations that should be there | `attestation.prompt_tokens` |
| Observations of two runs | `attestation.prompt_tokens_drift` |
| Rows and their seal | `ledger.seal` |

### Canonical form and versions
A record is written out in canonical form, the same way as a component (`docs/factors.md`, "Canonical form and
digest"): field names sorted, fields equal to their default left out, JSON data such as a response keeping its key
order, and a `v` field holding the record type's schema version. A log row's `stage` is always kept, as a
component's `kind` is, since it tells the stages apart.

The schema version (`Record.schema_version`, 1 for every type today) follows the components' rule: adding a field
with a backward-compatible default needs no new version; changing a field's meaning or default does.
`Record.parse` reads canonical bytes back, and refuses (`StructuralError`) another version or anything that isn't a
JSON object.

### Times
Every time in a record is timezone-aware and kept in UTC: a time given as 02:00+02:00 is stored as 00:00Z. The
offset is part of the canonical bytes, so without this the same instant would seal differently depending on the
writer's time zone.

Nothing finishes before it starts: a call, a tool run, or a log whose finished stage is earlier than its started
stage is refused.

### Seal
A log's seal (`Run.seal`, `Score.seal`) is `sha256:` followed by the SHA-256 of one JSON document holding the log's
canonical started row and its canonical item rows, plus, for a run, its canary calls. The item rows and the canary
calls are each sorted by their canonical bytes, so the seal doesn't depend on the order storage returns them in.
And since a canonical row leaves out fields equal to their default, adding a defaulted field keeps old seals valid.

`finish()` computes the seal and puts it in the finished row. The finished row itself, with its time and findings,
is outside the seal. A check recomputes the seal from the rows and reports `ledger.seal` when it differs: rows were
added, removed or changed after the log was finished.

### Case-derived records
Content produced from a vignette is *case-derived*. Each record type declares whether it can hold any
(`Record.case_derived`); a new type is case-derived unless it says otherwise. Two types do:
- run items (`RunItem`): their responses hold the completions and the arguments the model passed to tools, and their
  tool rounds hold what the tools returned (`ToolRun.result`, `ToolRun.error`);
- score items (`ScoreItem`): their judge responses grade completions and may quote them, and `detail` holds whatever
  the scorer wrote.

The other types hold none. Started and finished rows hold references, settings, times, seals and findings written
by code. Canary calls answer canaries, which are non-sensitive by definition (`docs/factors.md`, "Canary sets").

Request bodies and prompt-token ids would also carry the case's text, so neither is stored: only their
fingerprints are (see "Call").

The mark says what a record may hold. Acting on it, by restricting who reads such records or where they go, is up to
whatever stores or exports them.

### Errors and findings
As with components (`docs/factors.md`, "Errors and findings"), problems come in two strengths.
- An *error* stops the work.
  - A record or log that breaks its own rules can't be built: pydantic raises `ValidationError`. Examples are a log
    whose stages are out of order or whose rows belong to another log, a time that finishes before it starts, and a
    tool run with both a result and an error.
  - A problem that takes components to see makes `check_run` or `check_score` raise `StructuralError`. Examples are
    run items the trial doesn't contain, unplanned canary calls, and a score of another run.
- A *finding* is a warning about something observed that doesn't match what was declared. Findings never stop the
  work (see "Findings").

## Runs
A run is one execution of a trial (`docs/factors.md`, "Trial"). Its log is written one row at a time as things
happen: `RunStarted`, then a `RunItem` for each (case, replicate) and a `CanaryCall` for each planned probe, in
whatever order they happen, then `RunFinished`.

### RunStarted
- principal author: none; written by the runner
- defined in: `ledger.py:RunStarted`

`RunStarted` opens a run. It holds
- the run's id (`run`) and the time (`at`);
- the version of the rig's code (`rig`, a `Code`);
- the trial;
- the execution settings (`execution`, see `docs/factors.md`, "Execution"), the defaults when left out;
- optionally a canary set (`canaries`), and the phases at which its canaries are sent (`verify_at`): `start`,
  `end`, or both, the default.

It is written before the first request and never changes. It holds the run's recorded factors, but it is not part of
the trial's digest. To run a trial again under the same conditions, write a new started row with a new id and time
and the same `trial`, `execution`, `canaries` and `verify_at`.

The execution settings matter most on engines that aren't batch invariant (`docs/factors.md`, "Local engine"). On a
batch-invariant engine, order and concurrency don't change the outputs, so a re-run may be bitwise identical;
otherwise reproducing a run is best-effort. The calls show whether the settings were followed (see "Run").

`verify_at` is a set of phases: duplicates are refused, and the phases are kept in run order (`start` before
`end`), so two started rows that plan the same probes seal the same. It needs at least one phase, and only means
something with a canary set: without one it must stay at its default.

### RunItem
- principal author: none; written by the runner
- defined in: `ledger.py:RunItem`, `ledger.py:ItemKey`

A run item records one request of the trial and what came back. It holds
- the run's id;
- its key (`ItemKey`): the case and the replicate, the seed's position among the trial's seeds. The engine and the
  seed aren't repeated, since the trial has them;
- the fingerprint of the vignette as it was read from its source (`vignette`), to detect drift (`docs/factors.md`,
  "Preparing a case");
- the first `Call`;
- the tool rounds that followed, if any (`turns`).

`RunItem.calls` lists the first call and then each round's call.

### Turn and ToolRun
- principal author: none; written by the runner as part of a run item
- defined in: `ledger.py:Turn`, `ledger.py:ToolRun`

When the skeleton offers tools and a response calls some, the runner runs them and sends another request
(`docs/factors.md`, "Tool rounds"). Each such round is a `Turn`: the tools run for the previous response's calls, at
least one, and the next `Call`.

A `ToolRun` holds the id of the tool call it answers, the tool's name, the start and end times, and exactly one of
- `result`: the text sent back to the model. It is stored as sent, since it shaped the next request;
- `error`: why the tool gave no result. A call that names no tool of the skeleton is answered with an error.

The arguments aren't repeated: they are in the previous call's response.

### CanaryCall
- principal author: none; written by the runner
- defined in: `ledger.py:CanaryCall`

A canary call records one canary of the run's set, sent at one phase. It holds the run's id, the phase (`start` or
`end`), the canary's position in the set, and a `Call`.

### RunFinished
- principal author: none; written by the runner
- defined in: `ledger.py:RunFinished`, `ledger.py:Run.finish`

`RunFinished` closes the log with the run's id, the time, findings and the seal. `Run.finish(at, findings)` builds
it from the rows written so far.

Its findings are those the runner saw while running, such as `case.drift` when a vignette was read
(`docs/factors.md`, "Preparing a case"). `check_run` can be repeated at any time from the stored rows, so its
findings aren't stored; some of them (`run.incomplete`, `ledger.seal`) need the finished row to exist anyway.

### Run
- principal author: none; assembled from stored rows
- defined in: `ledger.py:Run`, `ledger.py:check_run`, `ledger.py:compare_prompt_tokens`

`Run` puts a run's log back together from its rows: its stages (the started row, then the finished row if there is
one), its items and its canary calls. It refuses stages out of order, a finished stage earlier than the started one,
and rows of another run.

`check_run(run, registry)` compares the run with what its started row declared. It raises `StructuralError` for
- an item the trial doesn't contain, or two items with the same key;
- a canary call outside the plan: any canary call when no set is named, a phase not in `verify_at`, or a position
  not in the set; and two canary calls for the same phase and position;
- tool rounds that don't fit the skeleton:
  - rounds on a skeleton without tools;
  - more rounds than the skeleton's `max_rounds`;
  - a round whose tool runs don't answer exactly the previous response's tool calls, by id and name. Calls to the
    answer function of a `tool` contract are left out, since they end the item;
  - a tool run for a tool the skeleton doesn't have, unless it is an error.

It warns (see "Findings") when
- a finished run lacks items of the trial, or planned canary calls (`run.incomplete`);
- an item's vignette fingerprint differs from its case's (`case.drift`);
- an item's last response still calls tools, because the rounds ran out or the run stopped (`tools.unanswered`);
- a call returned another model name than declared: the engine's digest for a local engine, the requested model for
  a remote one, or none at all (`attestation.model`). Every call of every item that got a response is checked;
- an item has a call without a prompt-token fingerprint (`attestation.prompt_tokens`);
- the rows no longer match the seal (`ledger.seal`);
- the calls don't follow the execution settings (`execution.*`):
  - a call took more attempts than `retries` allows (`execution.retries`);
  - items were sent out of order: an item's first call started before that of an item `Execution.schedule` puts
    ahead of it (`execution.order`);
  - more calls were in flight at once than `concurrency` allows, canary calls included (`execution.concurrency`).

Canary calls get neither the model check nor the prompt-token check. Timeouts aren't checked (see "Open design
issues").

`compare_prompt_tokens(a, b, registry)` compares two runs item by item, matching items by key. It warns
(`attestation.prompt_tokens_drift`) where both first calls have a prompt-token fingerprint and the two differ: the
engine read different tokens for the same item. Later calls aren't compared, since they depend on what the tools
returned. It refuses (`StructuralError`) two runs whose trials build their prompts differently: another skeleton, engine
or text cleanup. The runs may be of different trials otherwise, since cases are matched by key and the seed doesn't
shape the prompt.

## Call
- principal author: none; written by the runner or the scorer as part of another record
- defined in: `ledger.py:Call`, `ledger.py:fingerprint_request`, `ledger.py:fingerprint_prompt_tokens`

A call records one exchange with an engine. Run items, tool rounds, canary calls and judge calls all hold calls. A
call holds
- `request`: the fingerprint of the request body;
- `started_at` and `finished_at`. With retries, they run from the first attempt's start to the last attempt's
  end;
- `status`: the HTTP status, if a response came;
- `attempts`: how many times the request was sent, 1 by default and more with retries (`docs/factors.md`,
  "Execution");
- `error`, if it failed;
- `response`: the response body, parsed, without its prompt-token ids;
- `prompt_tokens`: the fingerprint of those ids.

The request body is never stored, since it holds the case's text (`docs/factors.md`, "Rendering").
`fingerprint_request(body)` fingerprints its canonical JSON: compact, non-ASCII characters kept, top-level keys
sorted and nested key order kept. So the fingerprint doesn't depend on how a client spells the JSON it sends, or on
the order of the body's settings. Two requests whose schemas list properties in a different order do get different
fingerprints, since property order is part of what the model reads.

`render` asks the engine for the prompt's token ids (`return_token_ids`): the tokens the engine actually read after
applying its chat template. They encode the case's text too, so `fingerprint_prompt_tokens(response)` takes them
out of the response and returns their fingerprint, which goes in `prompt_tokens`. Comparing these fingerprints
shows whether the engine read the same tokens, without storing the text (`compare_prompt_tokens`). `Call` itself
doesn't refuse a response that still holds `prompt_token_ids`: taking them out is up to whoever builds the call.

Both helpers take an optional key and then give HMAC fingerprints, as `Fingerprint.of` does (`docs/factors.md`,
"Fingerprints"). Fingerprints made with different keys never match.

`Call.returned_model` reads the model name from the response. `Call.system_fingerprint` reads the engine's
`system_fingerprint`, which nothing compares yet.

## Scores
A score grades one run with one scoring (`docs/factors.md`, "Scoring"). Its log is written the way a run's is:
`ScoreStarted`, then a `ScoreItem` for each (run item, view), then `ScoreFinished`. A score has no canary calls.

### ScoreStarted
- principal author: none; written by the scorer
- defined in: `ledger.py:ScoreStarted`

`ScoreStarted` holds the score's id, the id of the run it grades, the time, the version of the rig's code (`rig`),
the scorer code that actually runs (`scorer_code`), the scoring, and the execution settings of its judge calls
(`execution`, the defaults when left out), which are checked the way a run's are (see "Score").

`scorer_code` is a recorded factor. Set beside the code the scorer pins (`Scorer.code`), it shows whether what ran
is what should have run, and `check_score` compares the two (see "Score").

### ScoreItem
- principal author: none; written by the scorer
- defined in: `ledger.py:ScoreItem`

A score item holds the score's id, a run item's key, a view's position among the scorer's views, the value,
optional `detail`, and the judge calls made for it. The value is a finite number, or empty (`null`) when the item
couldn't be scored. What the value means and what `detail` holds are up to the scorer's code (`docs/factors.md`,
"View").

### JudgeCall
- principal author: none; written by the scorer as part of a score item
- defined in: `ledger.py:JudgeCall`

A judge call records one request to a judge: the judge, the position of the seed used among the judge's seeds, and
the `Call`. A view with a judge sends one request per seed (`docs/factors.md`, "Judge").

### ScoreFinished
- principal author: none; written by the scorer
- defined in: `ledger.py:ScoreFinished`, `ledger.py:Score.finish`

`ScoreFinished` closes the log with the score's id, the time, findings and the seal, as `RunFinished` does for a run.
Judge calls live inside score items, so the seal covers them.

### Score
- principal author: none; assembled from stored rows
- defined in: `ledger.py:Score`, `ledger.py:check_score`

`Score` puts a score's log back together the way `Run` does, and refuses the same things.

`check_score(score, run, registry)` compares the score with its scoring and its run. It raises `StructuralError`
for
- a score of another run;
- a view position the scorer doesn't have;
- an item that isn't in the run;
- two items for the same run item and view;
- a judge call whose judge isn't the one its item's view names (so any judge call on a view without a judge), a
  seed position the judge doesn't have, or two judge calls for the same seed in one item.

It warns when
- a view's output selector can't pick anything from answers that follow the run skeleton's output schema
  (`view.unreachable`). Free text (a `text` contract without a schema) is reachable only by a selector that picks
  the whole answer (`""` or `$`);
- an item has a value, but its view's judge was called with fewer seeds than the judge has (`judge.incomplete`). An
  item without a value may have stopped before calling the judge, so it isn't flagged;
- a finished score lacks an item for some run item and view (`score.incomplete`);
- the scorer code that ran isn't the code the scorer pins (`score.scorer_code`): the distribution and the version
  must match, and the revision too when the scorer pins one;
- the rows no longer match the seal (`ledger.seal`);
- the judge calls don't follow the score's execution settings, as for a run (`execution.retries`,
  `execution.order`, `execution.concurrency`). A run item counts as sent when its first judge call starts; items
  without judge calls are left out of the order.

## Findings
A finding (`docs/factors.md`, "Errors and findings") has a level, a code, a message and, optionally, a subject. All
of the ledger's findings are warnings.

| Code | From | Subject | Meaning |
| --- | --- | --- | --- |
| `run.incomplete` | `check_run` | none | A finished run lacks some of its trial's items, or some planned canary calls. One finding for each, with the count. |
| `case.drift` | `check_run` | item key | The vignette read at the source differs from the case's fingerprint. `prepare_case` reports the same code when the vignette is read. |
| `tools.unanswered` | `check_run` | item key | An item's last response still calls tools, after its rounds ran out or the run stopped. |
| `attestation.model` | `check_run` | item key | A call's response gave another model name than declared, or none. |
| `execution.retries` | `check_run`, `check_score` | none | Some calls took more attempts than the run's or the score's `retries` allows. One finding, with the count. |
| `execution.order` | `check_run`, `check_score` | none | Some items were sent before items the order schedules ahead of them. One finding, with the count. |
| `execution.concurrency` | `check_run`, `check_score` | none | More calls were in flight at once than `concurrency` allows. One finding, with the peak. |
| `attestation.prompt_tokens` | `check_run` | none | Some items have a call without a prompt-token fingerprint, for example because the engine didn't return token ids. One finding, with the count. |
| `attestation.prompt_tokens_drift` | `compare_prompt_tokens` | item key | The engine read different prompt tokens for the same item in two runs. |
| `ledger.seal` | `check_run`, `check_score` | run or score id | The rows no longer match the seal in the finished row. |
| `view.unreachable` | `check_score` | scorer digest | A view's output selector reaches nothing in the run's output schema. A lint reports the same code for a view's expectation selector (`docs/factors.md`, "Lints"). |
| `judge.incomplete` | `check_score` | item key | A scored item's judge was called with fewer seeds than the judge has. |
| `score.incomplete` | `check_score` | none | A finished score lacks items for some pairs of run item and view. |
| `score.scorer_code` | `check_score` | score id | The scorer code that ran isn't the code the scorer pins. |

## Terms

| Term | Meaning |
| --- | --- |
| Record | A row written when something happens: what happened, not what was planned. |
| Ledger | All records: the run and score logs. |
| Log | A run or a score: a started row, item rows and a finished row, only ever added to. |
| Stage | A log's started or finished row. |
| Item key | (case, replicate): which request of a run an item is. |
| Call | One exchange with an engine. |
| Tool round | Running the tools a response called, and sending the next request. |
| Seal | The hash over a log's started row and item rows, kept in its finished row. |
| Case-derived | Produced from a vignette: completions, tool arguments and results, judge responses. |
| Attestation | What the engine says about itself: the model name it returned and the prompt tokens it read. |

## Open design issues

### Recorded settings aren't compared with what happened
Some records state what should happen and others what did, but nothing compares them:
- `timeout_s` and the calls: a call's times span all its attempts, so an attempt that ran past the timeout can't
  be told from them;
- a call's request fingerprint and the request it should have been. A request can be rebuilt from stored data and
  the vignette, and a judge request from the scorer's parse of the answer (`docs/factors.md`, "Judge"), but nothing
  does it.

### Smaller issues
- **Canary drift isn't checked.** Nothing compares canary outputs between phases or between runs.
- **Canary phases aren't checked against the items.** Nothing checks that start-phase canary calls come before the
  items and end-phase ones after them.
- **The tool code that ran isn't recorded.** A tool pins its code like a scorer (`docs/factors.md`, "Tool"), but a
  `ToolRun` doesn't say which code ran, so nothing like `score.scorer_code` is possible for tools.
- **Only run items are attested.** Canary calls and judge calls get no model check and no prompt-token check.
- **`system_fingerprint` is kept but never compared** between calls or runs.
- **A score may grade an unfinished run.** `check_score` doesn't ask for the run to be finished or its seal to hold.
- **Missing expectations aren't flagged.** `check_score` doesn't warn when a run item's case has no expectation in
  the scoring.
- **The finished row is outside the seal.** Its time and findings could change without `ledger.seal` noticing.
- **What a failed tool told the model isn't recorded.** A `ToolRun` with an error doesn't hold the text sent back in
  its place.
- **`Call` accepts `prompt_token_ids`.** A response is meant to be stored without them, but nothing refuses one that
  still has them.
- **Calls don't record where they went.** A call has no endpoint, so the ledger can't show which one received
  case-derived content (`docs/factors.md`, "The inventory doesn't locate local engines yet, and runs don't record
  where calls went").
- **Records aren't in bundles.** A `Bundle` (`docs/factors.md`, "Registries and bundles") carries components only;
  how records travel with the factors they reference, for example for publication, isn't specified.

## Proposed amendments
