# The repl: plan

Plan for bringing the old chatddx shell into remastered. "The old repl" is chatddx-administration/chatddx at
504792c: `src/chatddx/repl/` and its manual `docs/repl.md`. Remastered is this repo at 249548a.
Old permalink base: https://github.com/chatddx-administration/chatddx/blob/504792cbeb09508f2c52912be29a3d8809d2fa1d/

"Decided" means the user said so. "Take" is the agent's recommendation and still open.

## Start with this
The old repl is a thin UI (`src/chatddx/repl/`, ~1,600 lines and ~1,500 of tests) on top of four layers that do
the work: `bench/` (identity, the cell, `Ready`, `Trial`, `save`), `runtime/` (resolving a configuration on a stack,
and the run itself on pydantic-ai and httpx2), `history/` (Django models for trials, runs, messages and scores) and
`scoring/`, ~3,200 lines together. None of those four has a remastered counterpart on the run path.

Remastered has every piece a run is built from: `render`, `Trial`, `Execution`, `prepare_case`, the ledger, the
store, the catalog, the scorers, the facts, the World inventory with `url_of`/`confirm`, and the fake vLLM. What it
lacks is the runner that strings them together (`agents/wip-sample-data.md`, "After parity").

So the plan:
- **Port the UI.** Command table, parsing, completion, prompt, Ctrl-C handling, rendering. These carry over
  nearly as they are.
- **Rebuild what's underneath** on remastered's pieces rather than porting `bench/`, `runtime/`, `history/` and
  `scoring/`.

The order: decisions, runner, scoring pass, reads for a person, the shell, tool rounds.

## Decisions needed before phase 1
### D1. The clearance gate
The sample's vignette source is declared sensitive (`sample-world/inventory.toml`, `[source.sample]`). Sending
case-derived content to an endpoint that isn't cleared is the one hard block, and the runner enforces it
(`docs/chatddx.md`, "Findings and errors"). No clearance pipeline exists yet (`agents/wip-clearance.md`, "Open"). A
runner that follows the rule can't send one sample vignette, not even to the fake.

Options:
- (a) An interim declaration in the World inventory: each endpoint lists the sources it's cleared for, such as
  `cleared_for = ["sample"]`, and the runner refuses anything else. This settles wip-clearance Open 2 as "in the
  inventory, for now". A database grant can replace it later.
- (b) Build the clearance schema first, which settles Open 1–3.
- (c) Declare the sample source not sensitive. That contradicts "treated as if they were".

Take: (a). The gate would also:
- include `confirm` (Open 4);
- refuse a toolset on a sensitive source until tools are cleared (`docs/clearance.md`, "Tools").

Also record where calls went, so the gate can be audited from the ledger: add a defaulted `RunStarted.endpoint`
(name and URL). A defaulted field is additive in canonical form (`docs/ledger.md`, "Canonical form and versions").

### D2. Streaming
The old repl streams the thinking and then the answer, and a batch line's token count climbs while it runs.
`docs/factors.md` ("Rendering") says `stream` and `n` are not sent.

Take: the runner may stream as a transport choice.
- After fingerprinting `render`'s body, it adds `stream: true` and `stream_options.include_usage`.
- It reassembles the chunks into the completion-shaped response that `Call.response` holds:
  - `prompt_token_ids` from the first chunk (`docs/vllm.md`, item 1);
  - `system_fingerprint` from the finish or usage chunk (item 15).
- `Call.request` stays the fingerprint of the rendered body.
- A test pins it: on the fake, the reassembled stream equals the completion it gives unstreamed.

This needs a proposed amendment to `docs/factors.md`, "Rendering".

### D3. Seeds
The old repl:
- drew seeds below 100,000;
- ran unseeded with `seed none`;
- refused a seed on a greedy cell.

In remastered:
- a trial has at least one seed (`docs/factors.md`, "Seeds are explicit");
- `render` sends it unless the skeleton is greedy;
- greedy trials still hash their seeds (`docs/chatddx.md`, "Compromises").

So "unseeded and not greedy" can't be expressed.

Take:
- Drop `seed none`.
- Keep the short draw for a readable prompt, and accept seeds up to 2³¹−1 (`factors/trial.py:suggest_seeds`).
- On a greedy skeleton, give a notice ("greedy: the seed isn't sent") instead of a refusal.

### D4. Dependencies
Remastered keeps its dependencies few: `cli.py` is argparse, and `web_search` was ported to urllib. The old repl leans
on `rich` for tables, colour, the batch's live line and, in tests, `Console(record=True).export_text()`.

Take:
- Add `rich`, imported only under `chatddx.repl`.
- No `typer`: `chatddx repl` is an argparse subcommand like the rest.
- No pydantic-ai or httpx for the runner. pydantic-ai builds requests from its own message model, so the wire body
  wouldn't be `render`'s and `Call.request` would fingerprint something that wasn't sent.
- HTTP is stdlib, as `inventory/serving.py:served` and the fake server already are.

### D5. Turning a response into an answer
Right after a run, the repl shows `valid`/`invalid`, the views and the scores.
`scorers/scorer.py:score` takes an `answer: JsonValue`, but nothing makes one from a response: parsing is open
(`agents/wip-sample-data.md`, "Parked", "Scorers"), and `docs/chatddx.md` puts it in the scorer's code.

Take: one versioned function, `chatddx.scorers.answer` (`answer@1`). It reads the response by the skeleton's
contract:
- native: the content as JSON;
- tool: the answer tool's arguments;
- text: the content, or the JSON in it when the output has a schema.

It returns:
- the value;
- whether it holds to `Skeleton.output_schema`, and why not.

Scorer entry points call it, so the scorer's `Code` pins it. The repl calls the same function to display a run.
The old runaway salvage (`runtime/run.py:Run.salvaged`, which closes a truncated structure) belongs here too, as
best-effort.

### D6. Vocabulary
An old "stack" (`qwen3-8b-awq@pelle`) is an engine plus where it's served. Remastered splits that into:
- an engine, which is a factor;
- a World-inventory endpoint named `<server>@<host>` (`qwen3-8b@pelle`).

Take:
- `endpoints` replaces `stacks`, and `on ENDPOINT` replaces `on STACK`.
- "Configuration" stays: a configuration is a skeleton thread (`docs/catalog.md`).
- Slices become recipe parts (`factors/request.py:Recipe`): `instructions`, `few_shot`, `prompt`, `output`,
  `sampling`, `reasoning`, `passthrough`, `translations`, `toolset`.
- `coercion` is gone, folded into outputs (G4, G5).

### D7. The repl's trials in the catalog
Each `run` and `batch` makes a `Trial`. Trial is a thread kind, but a thread per ad-hoc trial would flood the
catalog.

Take:
- Store the trial (`Store.add`), but don't thread it.
- Give the run an `owner` entry: "a run's owner should be whoever started it", a rule left to the runner
  (`docs/catalog.md`, "Owners").

### D8. Which expectation a case is scored against
A case can have several expectation threads, such as the archive's and the user's giftbag fork. Take:
1. the user's own live thread;
2. otherwise the archive's;
3. otherwise refuse, naming them.

## How the old pieces map
| old (504792c) | remastered | notes |
|---|---|---|
| identity (`core/models.py:IdentityModel`) | `identity.person`, `People.find(login)` | `init-data` adds it |
| configuration, 6 slices (`repo/entities/configuration`) | skeleton thread; parts from `Catalog.recipe` | model-dependent chunks are per model: `plan (Qwen/Qwen3-8B-AWQ)` |
| `set` a slice (`bench/cell.py:Cell.set`) | swap a recipe part, then `compile_request` to an unsaved skeleton | shown by `Catalog.title_of` |
| `save` (`bench/bench.py:Bench.save`) | fork of the skeleton thread, or a new thread, plus name and owner entries | no "yours now too": digests aren't owned |
| "save it as your own first" (`NotOwn`) | dropped: any digest can be run without a thread | |
| stack (`repo/entities/stack`) | engine plus World endpoint (`url_of`, `confirm`) | D6 |
| resolution and refusals (`runtime/resolution.py`) | the compiled skeleton, plus `factors.lint` and `facts.lint` on a provisional trial | warnings, never refusals (`docs/chatddx.md`, "Compromises") |
| case, tags (`repo/entities/case`) | family (name, tags, language), its case, the vignette from the source | |
| targets | expectation thread per case | D8 |
| trial: cell × case × seed (`bench.py:Trial`) | `Trial`: skeleton, engine, cases, cleanup, seeds | `run` is 1 case and 1 seed; `batch` is n cases and 1 seed |
| run, conversation, messages (`history/`) | `RunStarted`, `RunItem` (`Call`, `Turn`s), `RunFinished` | requests kept only as fingerprints |
| client and build | `RunStarted.rig` (`core/rig.py:rig`) | |
| `Run` on pydantic-ai (`runtime/run.py`) | new `chatddx.runner` | D2, D4 |
| `Sending`, `Outcome` (`bench/sending.py`, `outcome.py`) | runner events, `Call.error` | |
| scorer, one view each (`scoring/score.py`) | scorer with views and labels (`Catalog.labels`), one per output shape | batch columns become view labels |
| score (`ScoreModel`) | `ScoreStarted`, `ScoreItem`, `ScoreFinished` under a `Scoring` | built per run (phase 2) |
| mean and stderr (`scoring/metrics.py`) | `scorers/aggregate.py` | display only: analysis, not a score |
| per-identity secrets | endpoint `credential`, only a name | refused for now (see "Left out") |

## Commands
| command | backed by | what changes |
|---|---|---|
| `help`, `quit`, `exit` | ported | |
| `configurations` | visible skeleton threads, `Catalog.recipe`, `title_of` for each part | columns are recipe parts |
| `endpoints` (was `stacks`) | World endpoints, `title_of(engine)`, URL | D6 |
| `cases` | visible families and their tags | |
| `use CONFIGURATION` | skeleton thread by name: own, shared or `OWNER/NAME` | warns when its per-model chunks are for another model than the endpoint's |
| `on ENDPOINT` | inventory endpoint, then its engine | |
| `cell CONFIGURATION ENDPOINT` | both | |
| `set PART CHUNK` | swap a recipe part and recompile; `none` empties an optional part | a per-model chunk is picked by the engine's model |
| `show` | parts and their descriptions, the request rendered with placeholder fills, the provisional trial's lints, each scorer's reach and coverage | lints replace refusals |
| `show tag TAG...` | as `show`, with coverage over the tagged cases | |
| `show KIND [NAME]` | the component, `Catalog.about`, and the runs reaching it | remastered's kinds |
| `reasoning` | `Facts`: each model × intent (`ReasoningFacts.realized`, refusals) | read from `facts.toml`, not from stacks |
| `seed [SEED]` | the trial's seeds | D3 |
| `run CASE [SEED]` | runner on a trial of one case and one seed, then the scoring pass | |
| `batch TAG...` | runner on a trial of the tagged cases and one seed | order is the schedule: cases by digest |
| `save NAME` | fork, plus name and owner entries | |
| `runs [COUNT]` | runs the person owns (catalog entries), newest first | |
| `replay [RUN]` | `Store.run`, its responses and the request re-rendered from the trial and source | the request isn't stored |
| `score [RUN]` | scores runs with no score under the scoring as it would be built now | |
| `scorers` | visible scorers, their views and labels, and `reaches` against the cell's output schema | |

## Phases
### 1. Runner: `src/chatddx/runner/`
- **The model name an engine answers to**, one method on the engine. Today `ledger/run.py:check_run` and every
  runner choose it themselves (`docs/factors.md`, "Smaller issues"; `agents/manifest-triage.md`, C4).
- **`http.py`**: POST `/v1/chat/completions` over the stdlib, returning JSON or an SSE stream, with a timeout.
  `assemble(chunks)` gives the completion-shaped response (D2).
- **`gate.py`**: D1 and `confirm`, before the first case-derived request of a run.
- **`send.py`**: render a request, send it and make a `Call`:
  - it uses `fingerprint_request` and `fingerprint_prompt_tokens`;
  - the runaway guard of `runtime/run.py` (`RUNAWAY = 100` whitespace tokens) goes in `Call.error`;
  - a `KeyboardInterrupt` mid-stream is recorded as `stopped`.
- **`run.py`** runs a trial:
  1. `Store.add` the trial's closure.
  2. Append `RunStarted` (`rig()`, `execution`, `tool_code`), and write the run's owner entry.
  3. For each item of `Execution.schedule`:
     - `Source.fetch`, then `prepare_case`, keeping its findings;
     - `render(skeleton, model=…, seed=trial.seeds[r], fills=…)`;
     - send, then append the `RunItem`.
  4. Append `RunFinished` with the findings, then `check_run`.
- **Events** for a UI: an item starting, reasoning text, content text, a tool call, usage, an item ending (its
  `RunItem`) and a finding. They're synchronous: `Store` has no async API (`docs/store.md`, "Known gaps"), and a
  batch's concurrency stays 1.
- **Tests** in `src/chatddx/runner/test/`. They:
  - start the fake in a thread on port 0, as `inventory/test/test_serving.py` does;
  - point a temporary World inventory at it;
  - share a seeded template database, like `store/test/conftest.py:migrated` with `seed` run once.

  They cover:
  - streamed matching unstreamed;
  - the seed being sent unless greedy;
  - prompt tokens being fingerprinted, not stored;
  - runaway (the fake's `--runaway`);
  - a stop mid-stream;
  - `case.drift`;
  - the gate's refusal;
  - `confirm` refusing an endpoint that serves another digest;
  - a clean `check_run`.

### 2. Scoring pass
- **`chatddx.scorers.answer`** (D5).
- **Which scorers apply**: the visible scorer threads whose views reach `Skeleton.output_schema`
  (`factors/select.py:reaches`, the same check as `view.unreachable`).
- **Per run and applicable scorer**:
  1. Build a `Scoring` of the scorer and the expectations of the run's cases (D8), and `Store.add` it.
  2. Append `ScoreStarted`, then a `ScoreItem` per item and view via `scorers/scorer.py:score`, then
     `ScoreFinished`.
  3. Run `check_score`, and write the score's owner entry.
- **Outstanding runs**: runs the person owns with no score under the `Scoring` digest that would be built now. A
  changed expectation makes a new digest, so `score` catches up, as the old one did.
- **Judged views** stay refused (`score` raises `NotImplementedError`) and are shown as "not yet".
- **Tests**: each sample output shape against its scorer; a changed expectation makes a run outstanding.

### 3. Reads for a person
This closes `docs/store.md`'s known gap "Runs and scores can't be found". The store gets:
- live threads of a kind that the person owns or collaborates on, by name and as `OWNER/NAME`;
- the visible families with their tags and language;
- the runs a person owns, newest first, also by UUID prefix;
- the runs of a trial (for "run N of trial X") and the scores of a run;
- the runs whose trial reaches a digest, through the factor reference rows (for `show KIND NAME`).

A person-scoped layer, the old `Bench`'s role, goes in `src/chatddx/repl/bench.py`. It holds the cell, names,
completions and lookups, and nothing in it is UI. Tests go in `store/test/` and `repl/test/`.

### 4. The shell: `src/chatddx/repl/`
- **Entry**: `chatddx repl LOGIN --world FILE [--history PATH]`, a subcommand in `src/chatddx/cli.py`. `--world`
  locates the vignettes and endpoints, as for `init-data`.
- **Modules**, after the old ones:
  - `commands.py`: the `Command` table, `handle` and `complete`, nearly verbatim;
  - `shell.py`: the prompt and the completion cache;
  - `render.py`: on rich, the streamed transcript, the batch's columns and live line, views, scores and validity;
  - `choosing.py`, `listing.py`, `inspecting.py`, `running.py`, `reviewing.py` and `scoring.py`.
- **Ctrl-C** keeps the old semantics (`running.py:_Stopping`, `_held`), made simpler by a synchronous runner:
  - in a batch, the first press sets a flag that is checked between items;
  - the second raises into the send, which records the item as stopped.
- **In three steps**:
  - 4a, inspecting: `help`, `configurations`, `endpoints`, `cases`, `use`, `on`, `cell`, `set`, `save`, `show`,
    `reasoning`, `scorers` and `seed`. It needs only phase 3, so it can be built alongside phases 1–2.
  - 4b: `run` and `batch`.
  - 4c: `runs`, `replay` and `score`.
- **Tests**: the old tests' scenarios, rewritten for the new names and columns. They use a `say(*lines) -> text`
  fixture over `Console(record=True, width=200)`, as the old conftest's `say_to` did.

### 5. Tool rounds
- The `next_request` loop, with `max_rounds`, `Turn` and `ToolRun`.
- Tools are found by `core/rig.py:entry`, and their code goes in `RunStarted.tool_code`.
- D1 gates tools.

It comes last: `plan-web` can't call its tool on vLLM 0.24 anyway (G8; `docs/vllm.md`, item 10).

### 6. Docs
Agents propose and humans write `docs/*` (`AGENTS.md`, "Docs"). The manual's draft stays in `agents/` until it's
adopted. Proposals:
- a new `docs/repl.md`, the old manual rewritten;
- `docs/factors.md`, "Rendering": streaming (D2);
- `docs/catalog.md`, "Owners": the runner writes a run's owner (D7);
- `docs/store.md`, "Known gaps": the listings of phase 3;
- `docs/ledger.md`, `RunStarted`, and `docs/clearance.md`: the endpoint and the interim gate (D1).

## Left out
- **The portal's own code**: the worker, `bench/plan.py`'s crossing and `bench/held.py`.
- **`chatddx samples`**, the old typical, broken and rich runs (`agents/wip-sample-data.md`, "Left behind").
  It's small once the runner exists.
- **Endpoints with a credential**, refused until secrets have a home. The sample World and the real one have none.
- **Judges** for the free-text notes: `score` shows judged views as "not yet".
- **The injected transport** (`FakeTransport`): tests run the real fake server instead.

## Risks and smaller open points
- **A configuration on another model's endpoint.** The old `show` said what each slice became on the stack.
  Here, model-dependent chunks are fixed at seed time, so such a mismatch shows only through
  `facts.sampling_unmatched` and the `vllm.*` lints. `use`, `on` and `cell` should say so straight away.
- **The same vignette under two names.** The old repl ran it once. Here two source ids make two cases, so both run.
  Take: deduplicate a batch's cases by vignette fingerprint, and say which were merged.
- **Batch order** is by case digest, since `Trial` sorts its cases, not by name.
- **`return_token_ids` on remote engines** is still undecided (`agents/manifest-triage.md`, D). The fake and
  vLLM accept it.
