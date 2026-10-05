# Sample data: work in progress

"The sample data" is the old chatddx inventory: chatddx-administration/chatddx at 7893656, with
`src/chatddx/data/inventory.toml`, `giftbag-inventory.toml` and the vignettes in `src/chatddx/data/cases/`. It is
hand-edited into remastered's shape, with no adapter, and spreads over remastered's channels: factors, cases,
catalog, and the World inventory (`docs/chatddx.md`, glossary). The word "inventory" keeps its glossary meaning.
The vignettes are copied into `sample-world/` (see "The sample data now").
Permalink base: https://github.com/chatddx-administration/chatddx/blob/7893656c143154e0421af0a85298177c347502e3/

"Decided" means the user said so. "Take" is the agent's recommendation and still open.

This file also holds what `docs/sample-data.md` said, so that doc can go. The gaps found while porting (G1–G18) are
all fixed but one. Each is condensed to where its result is described now and what is still open. The full
write-ups are in git history: `git show 79ec890:agents/wip-sample-data.md`.

## Start with this
What's left is the parked work: engines and models, then scorers, then free-text expectations (see "Parked").
Settle these first.

### Decisions (the user's)
1. **Which models `from_facts` writes for.** Today it's every model the facts know
   (`src/chatddx/seed/plan.py:plan_factors`), not the models of the engines that will run the chunks. Once engines
   are seeded, the two lists can differ. Take: write for the seeded engines' models and report a model without
   facts, so facts about a model nobody runs are just unused (`docs/facts.md`, "Open design issues").
2. **Where an engine's location lives.** The inventory knows only `[source.<name>]` tables
   (`src/chatddx/inventory/inventory.py`). The runner needs engine digest → URL, and the start-up script needs the
   paths of the model files and the chat template (`docs/factors.md`, "The inventory doesn't locate local engines
   yet, and runs don't record where calls went"). Every endpoint item under "Engines and models" waits on this.
3. **Where scorer code lives, and what it must do.** This is tool code's problem too: `web_search` has no home
   (G8). The options are a `Code` pin with an `entry_point`, as today, or packaging entry points, which tools and
   scorers should adopt together (`docs/factors.md`, "Finding code through packaging entry points"). The scorer
   interface, meaning its arguments and return value, is unspecified (`docs/factors.md`, "Smaller issues").
   Everything under "Scorers" waits on this.
4. **Where model hashes, revisions and chat-template digests come from.** They need the hosts: an import script
   (`docs/chatddx.md`) or values the user supplies. An agent in a cloud container can't reach the hosts. Should the
   sample seed engines with values marked as placeholders until then?

The early work is done: the vignettes are in, `init-data` lints what it lands, and the fake vLLM is ported (see
"The fake vLLM").

## Decided
- Seed now: chunks, recipes (as compiled skeleton threads), cases with their families, an expectation schema and
  expectations.
- Deferred: engines and models, scorers, scorings (old D9), which come from a separate runtime-data pipe. The
  `init-data` command and the compiler's `Code` (old C8) are done.
- Base the sample data on 7893656. Quirks found while tweaking it, such as 13d317d's rename and the
  `DutchFall10w`/`Dutchfall*` spelling, are welcome stress tests.
- Olof's comments on the cases (13d317d, 504792c) will be the foundation for a free-text expectation scored by
  judges. The sample stays on 7893656 until that work starts (see "Parked", "Free-text expectations").
- `max_tokens` is dropped from sampling.
- Instructions: the hand edit changes the mechanism but keeps the spirit; remastered must support the same things
  (G3).
- The vignette files are a fake-sensitive source (G11). Fingerprints are over the raw bytes.
- The expectation schema is hand-written into the new sample data.
- The `# guessed` markers on targets are dropped on import, and the data is treated as live.
- The sample data lives in the repo (`src/chatddx/data/sample/`), with the vignettes named by the World inventory.
- The 99 vignettes are copied into the repo and kept under VCS. They aren't sensitive, but are treated as if they
  were.
- Model-dependent chunks come per model, from the facts.
- Re-runs are created, validated or updated, keyed by kind, name and the archive as owner.
- The giftbag is forks.
- Flags were renamed after first use: the old `--inventory` (what to seed) is `--data`, the World inventory is
  `--world` so "inventory" isn't read in its old sense, and `--vignettes DIR` replaces the World file with a plain
  directory.

## The sample data now
`src/chatddx/data/sample/` holds:
- `factors.toml`: chunks and recipes, from the old slices and configurations. Its header comment gives the format.
  Tables are kinds, keys are component fields, and references name records. On top of that, `json_schema_path`
  loads a JSON file, `from_facts` writes a chunk per model, `fork_of` seeds a record as a fork, and `tags` and
  `description` are catalog entries.
- `facts.toml`: the two sample models' facts (`docs/facts.md`, "The sample's facts").
- `cases.toml`: the 99 cases, each with its tags, language and targets, without the `# guessed` markers.
- `schemas/`: the two output schemas and the hand-written `targets.json`.

The vignettes aren't in it: they're a fake-sensitive source, so they stay out of the package. `sample-world/` holds
them as a World would:
- `inventory.toml` is the sample World inventory. Its `[source.sample]` table locates `vignettes/` and declares it
  sensitive.
- `vignettes/` has the 99 files of 7893656's `src/chatddx/data/cases`, byte for byte (their git blob hashes match the
  old repo's). 30 of them mix CRLF and LF line endings, so `.gitattributes` marks the directory `-text` to keep git
  from converting them.
- `src/chatddx/store/test/test_seed.py:test_the_sample_world_holds_the_vignettes_at_7893656` pins their ids, checks
  that they decode as UTF-8, and pins one fingerprint over all their fingerprints, so leaving 7893656 takes a
  deliberate edit.

`init-data` still needs the source named: there's no default for `--world` or `--vignettes`.

### What changed in the hand edit
- `instruction.ddx` and `instruction.bare` collapse into `prompt.case`; bare is the same prompt with `output.raw`.
- `reasoning.default` is no chunk: a recipe without a reasoning chunk leaves reasoning to the model. No recipe uses
  one, so the reasoning chunks are seeded as threads of their own.
- `sampling.recommended-4k` is gone with `max_tokens`.
- `toolset.web`, `tool.web_search` and `configuration.plan-web` wait for tool code (G8).
- `coercion.*` is folded into outputs (G4, G5), and `extends` became `fork_of` (G9).
- `dont_miss`, a target kind in the old schema (`src/chatddx/repo/entities/case/pydantic.py:22`) and in
  `docs/clinical-input.md`, isn't in `targets.json`. No case had one.

### init-data
`chatddx init-data USER (--vignettes DIR | --world FILE) [--source NAME] [--giftbag] [--data DIR] [--facts PATH …]`
(`src/chatddx/cli.py`) connects as `DB_USER` and seeds in one transaction for the `archive` person, created if
missing. It shares everything with USER, who must exist (`chatddx person add`). It prints one line per record:
created, validated, updated, skipped, missing, needs repair, forked or kept. It ends with the lints' findings.
- **Inputs.** `--data` is what gets seeded, the package's sample data by default. The vignettes come from
  `--vignettes`, a directory of `<id>.txt` files such as `sample-world/vignettes`, or from `--world`, a World
  inventory whose `[source.<name>]` table locates them, such as `sample-world/inventory.toml`. `--source` names the
  source (`sample`), which the cases are keyed by. A path that holds none of the sample's cases is refused before
  anything is written. `--facts` names the facts files, the data directory's `facts.toml` by default.
- **Planning.** `chatddx.seed.plan_factors` is pure. It plans 38 records: 12 recipes (6 configurations × 2 models),
  12 reasoning chunks, 5 sampling chunks, 7 outputs, a prompt and an expectation schema. It skips 4 reasoning chunks
  the facts refuse for gpt-oss.
- **Chunks and recipes.** Each record becomes a thread named after it and owned by the archive. Recipes are
  compiled, and their compilations are recorded. A `from_facts` chunk is written from each model's facts, so the
  recipes using it come once per model, named `plan (Qwen/Qwen3-8B-AWQ)`. A refused combination is skipped and
  reported. A `fork_of` record is seeded as a fork, so the catalog knows what it varies.
- **Cases.** Each case is read from the source and adopted into a family named by its id, with its tags and
  language. Each case gets an expectation thread.
- **Re-runs.** A record is matched by kind, name and the archive as owner (`Catalog.find`). It's validated when
  unchanged and gets a new edit when changed, so nothing is duplicated. A vignette that is missing or has changed at
  the source is reported, and a changed one needs a repair (`docs/catalog.md`, "Families and repairs").
- **Sharing.** USER becomes a collaborator on every archive thread and family.
- **`--giftbag`.** USER also gets their own fork of every chunk, skeleton and expectation thread, so the catalog's
  variations and proposals follow the archive when it moves. Expectation schemas stay the archive's. Existing forks
  are kept (`src/chatddx/seed/write.py:_gifted`).
- **Lints.** Every component that got a thread or a family this run, validated or not, is linted
  (`src/chatddx/seed/write.py:_Seeder.lint`): `src/chatddx/factors/lint.py:lint`, with the catalog's languages and
  the facts' `reasons`, and `src/chatddx/facts/lint.py:lint`, with the facts the plan was made with (`Plan.facts`).
  A missing case, or one that needs repair, isn't linted. Each finding is a line, `[lint warning] expectation
  Dutchfall11w: expectation.invalid: at the root: 'diagnosis' is a required property`, naming every record that
  shares the digest, and a last line counts them: `[lint] 0 findings in 230 components` for the sample. The
  findings are printed, not stored, and don't stop the seeding. Today only the expectation schema and expectation
  lints have anything to check. The vLLM, facts and language lints will apply once trials and judges are seeded.
- **Compiler.** Compilations record the running code as their compiler (`src/chatddx/core/rig.py:rig`): the version
  from the package, and the revision from `CHATDDX_REVISION` when it's set.

The old checkout's vignettes were checked in a scratch database:
- the first run created 236 records and 136 giftbag forks;
- a re-run validated all 236 and kept every fork;
- against the 13d317d checkout, 230 were validated, the renamed `DutchFall10w` was missing, and `Dutchfall11w` and
  `casesfromedn1` needed repair. That matches G13's survey;
- against 504792c, the head of `new-datamodel` on 2026-10-05, 228 were validated: `Dutchfall1w` needs repair too;
- through `--world sample-world/inventory.toml`, on a database seeded from the 7893656 checkout, all 236 were
  validated and every fork kept.

Tests: `src/chatddx/store/test/test_seed.py`.

Still open:
- A renamed vignette shows as missing. `Catalog.survey` could name the rename, and a `repair` command could apply it.
- `wipe-data`, which tier 2 rules out as a DELETE; deletion is a `deleted` entry.
- Tags and collaborators are only ever added on a re-run, never removed.
- The seeded chunks have no language, so a trial's request language is unknown (G18).

## The fake vLLM
`src/chatddx/fake_vllm/` is the old fake (`src/chatddx/dev/fake_vllm.py` at 7893656), ported to vLLM 0.24 and to
how remastered names engines. `chatddx fake-vllm [--delay S] [--runaway] MODEL [vllm serve's flags]` serves it.
Items are `docs/vllm.md`'s; 13–20 are among its proposed amendments.
- **`served.py`**: what `vllm serve MODEL` with its flags sets up (`Served.of`). Flags are read as vLLM reads them:
  `_` for `-`, the last one wins, `--config=FILE` is ignored, and `--config FILE` is refused because the fake can't
  read YAML. It reads `--served-model-name` (so an engine is served by its digest), `--max-model-len`,
  `--reasoning-parser`, `--reasoning-config`, `--enable-auto-tool-choice`, `--tool-call-parser`,
  `--default-chat-template-kwargs`, `--fingerprint-mode`/`--fingerprint-value`, `--host` and `--port`. It won't
  start where vLLM won't (`--enable-auto-tool-choice` without a parser). How the model behaves comes from its name:
  Qwen3, gpt-oss (Harmony), Mistral, or none of these.
- **`chat.py`**: one request in, one response or stream out. `accept` refuses what vLLM 0.24 refuses, with its
  status, type and message: an unknown model (404), a bad `tool_choice`, tools without a parser (items 5 and 19),
  Harmony's efforts (item 17), and a thinking budget without reasoning set up (item 7). `respond` decides:
  - whether the model reasons: Qwen3 unless `enable_thinking` is false, which `reasoning_effort: none` and the
    server's default kwargs also set (item 16); gpt-oss always; and no model when a grammar holds the answer from
    its first token without `--reasoning-parser` (item 6);
  - where the reasoning goes: separated, left out (`include_reasoning: false`), or in the content without a parser;
  - what it answers: a named tool's call (finishing `stop`); under `required`, each toolset tool once and then the
    answer tool, which the compiler offers last; under `auto`, each tool once and then text, but nothing called when
    a `response_format` holds the answer (item 10); a document for a `response_format` or a schema shown in the
    system message; else three fake diagnoses, rotated by the seed unless the temperature is exactly 0 (items 2, 3),
    with the clamp logged as vLLM logs it.
  `completion` and `stream` give `prompt_token_ids` and `token_ids` only when asked (item 1), the served name as
  `model`, and `system_fingerprint` (item 15). A token is a word, and its id is a CRC of it, so the ids change with
  the prompt.
- **`server.py`**: `/v1/chat/completions`, `/v1/models` (item 14), `/version` (`0.24.0+fake`) and `/health`, with a
  delay between streamed tokens. A client that hangs up mid-stream is heard from the socket and logged.
- **Tests**: `src/chatddx/fake_vllm/test/`. They pin each behaviour above. They also render every sample skeleton
  for both sample models, served as the old inventory served them, and check each answer against its contract and
  schema, and they run a tool round through `next_request`.
- **Checked against vLLM v0.24.0's source.** This found that item 5 is imprecise: `required` and named tool choices
  need both tool flags, like `auto`. `docs/vllm.md` has proposed amendments for that and for items 13–20.

Left behind:
- the httpx transport (`FakeTransport`): remastered has no HTTP client yet, and the runner will choose one;
- `chatddx samples` and `src/chatddx/dev/samples/` (`typical`, `broken`, `rich`): runs for the old history, which wait
  for the runner.

Not faked yet:
- abbreviated flags aren't expanded, and bare arguments aren't refused: the fake can't tell a flag that takes a
  value from one that doesn't;
- `n` > 1, `stop`, `logprobs`, `echo`, `structured_outputs`, `chat_template`, `return_prompt_text` and
  `prompt_logprobs` are ignored, and so are the sampling settings beyond the temperature;
- a prompt longer than the context, or a `max_tokens` past it, isn't refused;
- the answer's JSON doesn't vary with the seed;
- unchecked against vLLM: gpt-oss without any parser answers `analysis…assistantfinal…`, taking Harmony's markers to
  be special tokens skipped in detokenizing; and Harmony with a tool parser but no reasoning parser still separates
  the reasoning;
- nothing yet runs the factors' `vllm.*` lints and the fake on the same cases, which would catch the two drifting
  apart.

## Old → new

| old entity | records | new home | open |
|---|---|---|---|
| machine, os | 3, 3 | `LocalEngine.hardware`, `.runtime` (inline) | deferred |
| llm | 2 | `ModelArtifact`; its facts in `facts.toml` (G1) | deferred, except the facts |
| serving, stack | 5, 5 | `LocalEngine` + World inventory | deferred |
| client | 2 | `Code` on `RunStarted`, written per run | dropped |
| tool, toolset | 1, 1 | `tool` + `chunk.toolset` (G8) | the loop, clearance, tool code |
| instruction | 2 | `chunk.instructions` + `chunk.prompt` (G3) | |
| output | 4 | `chunk.output`; views go to scorers (G4, G6, G7) | scorers |
| coercion | 5 | `chunk.output` contract (G4, G5) | |
| reasoning | 9 | `chunk.reasoning`, written from the facts (G1, G2) | |
| sampling | 5 | `chunk.sampling`, `recommended` written from the facts (G1) | |
| configuration | 7 | `Recipe` → skeleton thread + `Compilation` (G9) | `plan-web` (G8) |
| case | 99 | `case` + family (tags, language) + `expectation` (G11–G14) | free-text expectations |
| scorer | 4 | `scorer` | deferred |

## Gaps
Each gap was tagged [maybe fix remastered]: the sample data needed something remastered lacked.

### Request
- **G1. Model facts.** Fixed: `chatddx.facts`, typed in code with values in TOML, applied when chunks are written
  and keyed by model name (`docs/facts.md`).
- **G2. Reasoning levels.** Fixed with G1: the old levels are facts per model, with writes, collapses, refusals, a
  default and a budget (`docs/facts.md`, "Reasoning"). Open: `xhigh` can't be written (`docs/facts.md`).
- **G3. Placing chunk-supplied text.** Fixed: the `output_guidance` and `tool_guidance` inserts, in the
  instructions or the prompt (`docs/factors.md`, "How chunks affect each other"). In the sample, `instruction.ddx`
  became `prompt.case`, with the guidance as the system message.
- **G4. Showing the schema in the prompt.** Fixed: the `schema` insert in an output's guidance (`docs/factors.md`,
  "Output"). "native (shown)" and "prompted" are outputs in `factors.toml`. The old composition was checked against
  pydantic-ai 2.41.0's `TemplateStr`, and the sample's guidance compiles to the identical system text.
- **G5. The tool contract has no description.** Fixed: `ToolOutput.description` (`docs/factors.md`, "Output").
- **G6. Views.** Fixed: an RFC 9535 subset beside JSON Pointers, and a `lines@1` split (`docs/factors.md`, "View").
  The old views, translated for the scorers to come:
  - differential `$.diagnoses[*].diagnosis`;
  - warning `$.acute_warning`;
  - disposition `$.management.disposition`;
  - critical `$.diagnoses[?@.critical == true].diagnosis`, since the old `[?(@.critical)]` meant "is true" and
    RFC 9535's `[?@.critical]` means "exists";
  - diagnoses `$.diagnoses[*]`;
  - free text: the empty selector with `lines@1` (old `lines`), or no split (old `whole`).

  Unlike the old `read`, nulls are kept.
- **G7. JSON key order was lost in storage.** Fixed: only field names and settings are sorted (`docs/factors.md`,
  "Canonical form and digest"). Bytes written before the fix keep their digests, but recompiling the same recipe now
  gives a new skeleton digest.
- **G8. Tools.** Fixed in shape (`docs/factors.md`, "Tool", "Toolset", "Tool rounds"; `docs/ledger.md`, "Turn and
  ToolRun"; `docs/clearance.md`, "Tools"). Still open:
  - the loop itself, and where tools run, are the runner's;
  - `web_search`'s code (the old `chatddx.runtime.tools.web_search`) has no home, so `toolset.web`,
    `tool.web_search` and `plan-web` aren't in the sample (see "Start with this", 3);
  - `schema.ref_unverified` doesn't look at toolset tools' parameters;
  - gpt-oss with `required` (`docs/facts.md`);
  - a turn after a response that already called the answer tool isn't rejected.
- **G9. Variations.** Fixed: a variation is a fork, and the catalog proposes re-applying it when its origin moves
  (`docs/catalog.md`, "Configurations and variations"). The sample's `extends` became `fork_of`.
- **G10. Skeleton × engine compatibility.** Fixed: the runtime lints (`docs/factors.md`, "Lints"; `docs/vllm.md`,
  items 5–8), the facts' checks (`docs/facts.md`, "Checking pairs"), and `--config` forbidden in argv. Still open:
  OpenAI's API is reported to reject function parameters whose root isn't `type: object`. That's unverified, and a
  candidate lint for tool contracts on `engine.remote`.
- **G15. Sent schemas are not prepared.** Closed, no change: vLLM 0.24 constrains `$defs` and `$ref` as written
  (`docs/vllm.md`, item 4).
- **G16. An inline path for engines that don't resolve `$ref`.** Fixed: `Output.schema_ops` with `inline_refs@1`
  (`docs/factors.md`, "Output"). On `management_plan_v1.json` it gives exactly the old code's inlined schema. Which
  engines need it is only linted (`schema.ref_unverified`).

### Cases
- **G11. Vignette sources.** Fixed: `chatddx.inventory`'s `[source.<name>]` tables and `prepare_case`
  (`docs/factors.md`, "Preparing a case"). Sensitivity is declared, not enforced (`agents/wip-clearance.md`).
- **G12. Case language.** Fixed: a `language` entry on each family (`docs/catalog.md`, "Languages"). The sample has
  79 `en` and 20 `sv`.
- **G13. Repairs.** Fixed: `Catalog.survey` and `Catalog.repair` (`docs/catalog.md`, "Families and repairs"). On the
  13d317d checkout, the survey found exactly the two changed vignettes and the rename. Still open:
  - trials aren't re-keyed by a repair; `behind` proposes the new cases to their owners;
  - a repair rebuilds only the cases at the family's current binding, not older ones.
- **G14. Expectation data isn't validated.** Fixed with lints (`docs/factors.md`, "Lints"). The hand-written targets
  schema passes all 99 cases' targets, and `init-data` lints what it lands (see "init-data"). Still open:
  - `Output.json_schema` goes to the engine unchecked, and the same validator could give it an
    `output.schema_invalid`.

### Catalog
- **G17. Configurations are named, but nothing names them.** Fixed the other way round: names are optional, and an
  unnamed thing is shown by a title (`docs/catalog.md`, "Names", "Titles"). Still open:
  - titles are rebuilt from names that change, so an export for publication should freeze them;
  - "every configuration using output X" needs a reverse query that isn't written yet.

### Language
- **G18. One language per trial.** Fixed, with the language implicit in what's sent: a translations chunk, languages
  kept on families and digests, and `language.mixed`/`language.unknown` (`docs/factors.md`, "Translations";
  `docs/catalog.md`, "Languages"). Also decided: a model that answers in another language than it was prompted in
  loses the score, and mixing is a warning, not a hard block.
  - In the sample, the seeded chunks have no language, so every trial's request language is unknown
    (`language.unknown`). Giving them `en` (`Catalog.language`) would make the 20 Swedish cases' trials
    `language.mixed` until a Swedish translations chunk exists.
  - Still open: few-shot assistant examples that are JSON answers are translated as one text, so the translator
    edits the JSON by hand.
  - Side effects of the implicit language, which no doc covers yet:
    - **Not everything the model reads is ours.** Chat templates add English: default system prompts, harmony's
      channel instructions, tool-call scaffolding. Reasoning traces are often English whatever the prompt.
    - **Structure stays English.** Property names, enum values and tool names aren't translated. That keeps scoring
      language-neutral.
    - **Budgets aren't neutral.** The same text takes different numbers of tokens in Swedish and English, which
      confounds a comparison between languages.
    - **Scorers that need the language** (stemming, synonyms, ontologies) should get it from the expectation data
      or their resources, which are factors, not from the catalog at scoring time.
    - **Languages can be wrong, and they change.** Nothing checks one against the text, and analyses split by
      language should freeze them at export.

## Parked
The deferred work. Settle "Start with this" first.

### Engines and models
- Model file hashes (`ModelArtifact.files`) and the chat-template digest need an import script on the hosts.
- Hardware:
  - compute capability is missing from the old data;
  - `gpu_count` is missing (`docs/factors.md`, "Smaller issues");
  - CPU, RAM, location, GPU uuid and machine id have no home.
- Runtime:
  - Is `Runtime.closure` the vLLM package or the container toplevel?
  - malborg says vLLM 0.13.0 (guessed), but the docs say both hosts run 0.24.0.
- No split between args in the digest and recorded-only args (`performance`). `max-num-seqs` affects batching.
- Endpoints:
  - nothing maps an engine digest to a URL, and the World inventory locates only vignette sources;
  - the hosts' served names must become the engine digest;
  - per-endpoint capacity (`max_jobs`), credentials and the API kind have no home.
- The fake vLLM is in (see "The fake vLLM"). A start-up script can run it with vLLM's command line.
- Model specs (family, size, quantization, context length, licence) are held by the facts, descriptive only
  (`docs/facts.md`).

### Scorers
- Scoring code: the pattern matcher, `reciprocal_rank`, `first_mention` and `mentions`. The old code is at 7893656
  in `src/chatddx/scoring/scorers/patterns.py`, with `metrics.py`, `score.py` and tests in `src/chatddx/scoring/`.
- Free-text parsing beyond `lines@1` (G6).
- Aggregation (`mean`, `stderr`).
- A check that views agree with the output schema (old `prove`): reachability is done (G6); item types are open.
- One scorer per output shape (`plan`, `diagnoses`, `free-text`, `raw`), with the old view names as labels.
- Scorings (old D9).

### Free-text expectations
Decided: Olof's comments on the cases will be the foundation for a free-text expectation that judges score. The
sample stays on 7893656 until this starts.
- **What's there.** Olof, a clinician, added comments to the end of three vignettes in the old repo after 7893656:
  `Dutchfall11w` and `casesfromedn1` in 13d317d (2026-09-28), and `Dutchfall1w` in 504792c (2026-10-04). Each is a
  block headed "Olof comments" or "Olof kommenterar", in Swedish even for an English vignette: their reading of the
  case, what to do, and the disposition. `Dutchfall11w` and `casesfromedn1` end in a "Chatddx:" block too, the short
  answer they'd want ChatDDx to give. 13d317d also renamed `DutchFall10w` to `Dutchfall10w`, with the same bytes.
- **They can't stay in the vignette.** A vignette is sent whole, so the comments would reach the model as part of
  the case. Appendices are sent too (`docs/factors.md`, "Appendix"), so they're no home either. They're expectation
  data, and the design already has judges read free-text notes in expectations (`docs/factors.md`, "View",
  "Judge").
- **Cutting them off doesn't restore 7893656.** In `Dutchfall11w` and `Dutchfall1w` the edit also turned the old
  last line's LF into CRLF. Only `casesfromedn1` is a pure append. Unless the old bytes are restored exactly, those
  cases need a repair (`docs/catalog.md`, "Families and repairs").
- **What it needs:**
  - an expectation schema for free text beside `targets.json`, whose `additionalProperties: false` leaves no room.
    Open: one text, or the comment and the "Chatddx:" answer kept apart;
  - a judge-purpose prompt with `completion` and `expectation`, and maybe `vignette`; a judge, which needs an
    engine; and a scorer whose view names the judge. So it waits on engines (decision 2) and scorers (decision 3);
  - a language for the notes. They're `sv`, while 79 of the cases are `en`, so a judge would read Swedish notes
    about an English answer (G18);
  - somewhere for clinicians to write them other than the vignette files. Expectations are written in the portal
    (`docs/factors.md`, "Expectation"). Take: until there is one, a file in the sample data beside `cases.toml`,
    keyed by case id.
- **They can disagree with the targets.** On `Dutchfall11w`, Olof leads with shock, most likely sepsis, and uses
  ultrasound (RUSH) to rule out other causes, with blood cultures and broad empirical antibiotics. The targets,
  guessed before their markers were dropped, have AAA or dissection as the diagnosis. `Dutchfall1w` and
  `casesfromedn1` roughly agree with theirs. Open: whether the targets get revised from the notes.
