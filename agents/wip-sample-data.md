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
The endgame is parity with the old `init-data`. Everything is in but the real engines: the configurations and cases,
the tool and scorers, and the two fake engines with their endpoints (see "Engines and endpoints"). What's left, in
order:

### 1. Import the real engines from their hosts' reports (decision 4A)
The old `qwen3-8b-awq@pelle`, `qwen3-8b-awq@malborg` and `gpt-oss-20b@malborg` get seeded from values read on the
hosts, not guessed. Most of the old data for them was guessed, malborg's vLLM 0.13.0 among it, and a guess in an
engine lands in its digest, which is its served name and the key of every trial. Today pelle serves `qwen3-8b` and
malborg `gpt-oss-20b` (Kompismoln/org f05f153, `hosts/*/configuration.nix`); malborg no longer serves Qwen.

The hosts run vLLM through o11n's `o11n.vllm` (Kompismoln/o11n, `nixos/vllm.nix`), each server in a NixOS
container, and its report endpoint (`o11n.vllm.report`, `nixos/vllm-report.py`) gives what an import needs. That
replaced the planned `import-engine` run on a host, which would have read `nvidia-smi` and `/proc` itself.

1. **Settled by o11n and org** (the user's, through o11n 2a874df and org 6528f10):
   - **Which chat template each engine runs**: the file `chatTemplate` names, now required. vLLM then reads it for
     every request, and the report hashes it and compares it with the model's own. Pelle's is `tokenizer_config.json`'s
     template (same text, plus a trailing newline); malborg's is the repo's own `chat_template.jinja`, which vLLM
     doesn't read for gpt-oss (it renders with Harmony), so its hash pins nothing.
   - **Malborg's Harmony date**: `VLLM_SYSTEM_START_DATE = "2026-10-07"`, in the engine's env and digest.
   - **The report endpoints**: enabled on both hosts, at `pelle.km:12008` and `malborg.km:12008`.
   - **Where the real World lives**: `world/`. `import-engine` writes `world/factors.toml` (`[model]`,
     `[local_engine]`) and `world/endpoints.toml` (`[endpoint]`, `[host]`); `world/inventory.toml` is hand-written:
     the sample's vignettes as `[source.sample]`, and `include = ["endpoints.toml"]`
     (`src/chatddx/inventory/inventory.py:Inventory.load`). `init-data --world world/inventory.toml --factors
     world/factors.toml` seeds the sample with them, and `src/chatddx/store/test/test_seed.py:test_init_data_command`
     checks that it binds each endpoint.
   - **What a model's files are**: every file of the snapshot at the pinned commit, as the report hashes them, with a
     snapshot that holds only what vLLM needs. A list of only what vLLM reads would have to follow vLLM's loaders per
     version and model, and missing a file means missing a change: with `--generation-config auto` (the default)
     `generation_config.json` sets the sampling of every request that doesn't
     (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/config/model.py#L1400-L1470). gpt-oss's repo also has
     `original/` and `metal/`, the weights in other formats, which vLLM never reads: it takes the root's
     `*.safetensors` listed in the index
     (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/model_executor/model_loader/default_loader.py#L208-L232).
     Malborg's cache was downloaded again without them (52d728b): the other files' hashes are the same, and the model's
     digest went from `c642e8` to `1c249b`, the engine's from `9a5ca7` to `598792`.
2. **Still open:**
   - **Decision 1**, which models `from_facts` writes chunks for. Today it's every model the facts know
     (`src/chatddx/seed/plan.py:plan_factors`); the real engines serve the same two models as the fakes, so nothing
     changes yet. Take: the seeded engines' models, reporting a model without facts (`docs/facts.md`, "Open design
     issues").
3. **Done: `chatddx import-engine REPORT … [--server NAME …] --factors FILE --endpoints FILE`**
   (`src/chatddx/inventory/report.py`). It reads each host's report from its URL or a file and writes the
   `[model]` and `[local_engine]` tables to `--factors` and the `[endpoint]` and `[host]` tables to `--endpoints`,
   each named `<server>@<host>`. The digests are the seeder's, and its findings go to stderr. It:
   - refuses what an engine can't record: a server on several GPUs (`Hardware` has no count), a model that isn't a
     Hugging Face repo at a revision, a part the report couldn't gather, extra arguments setting `--host` or `--port`,
     and whatever `LocalEngine` refuses;
   - drops the env o11n sets to find the model, the GPU and CUDA (`HOST_ENV`), and keeps the rest, such as
     `VLLM_USE_FLASHINFER_SAMPLER`;
   - takes `max_jobs` from `--max-num-seqs`, the URL's host from the report's URL (or `--endpoint-host`), and keeps
     the model by repo ID, as o11n passes it with `--revision`. `start_up` now gives `--revision` too;
   - runs the model and engine lints and `check_chat_template` on the reported text, and reports a template vLLM
     doesn't read, a missing `VLLM_SYSTEM_START_DATE` there, a template that isn't the model's own, and a server
     that isn't running, runs other arguments, reports another vLLM version or doesn't answer under the digest.
4. **Imported** (chatddx-remastered 5760e10), and every value checks out: the digests recompute, the lints are clean,
   and `start_up` gives o11n's command line. But the runtime root was named after its server and built with the host's
   package set, so the closures differed between the hosts for the same packages and would have moved with every
   update of o11n's nixpkgs. Fixed in o11n b84e960 (`vllm-runtime`, from the container's package set) and imported
   again (973dd61): both hosts now report the same runtime root.
5. **Seeded and checked** (52d728b). `init-data alice --world world/inventory.toml --factors world/factors.toml` creates
   the two models and engines, their lints are clean, and each World endpoint serves the engine it names:
   `qwen3-8b@pelle` `sha256:a6845f87…`, `gpt-oss-20b@malborg` `sha256:598792a2…`.
6. **Serve them under their digests.** Put each engine's digest first in its server's `servedModelNames` (responses
   carry the first name), as `import-engine`'s `endpoint.served_name` says. The served names aren't part of the
   digest, so this doesn't change it. Then `confirm` passes.
7. **Optionally, check against HuggingFace** (4B as a check, not a source). With `huggingface.co` allowed in the
   environment's network policy, the revisions and the weights' SHA-256s can be compared with the hub's.

### 2. Then the small parity items
- The old `init-data` created USER when missing; remastered requires `chatddx person add`. Take: keep it explicit,
  since people have roles and passwords now.
- `wipe-data`: tier 2 forbids DELETE, so removing a user's records means `deleted` entries and dropping them as
  collaborators. Its semantics are to decide.
- The old target format's `dont_miss`, `warning = false` and `text`, which no case used. `text`, a target in plain
  words "for people or LLM judges", meets the free-text expectations (see "Parked").

### After parity
The free-text expectations (see "Parked"), and the runner, which the fake vLLM, the endpoints and `confirm` are
ready for.

## Decided
- Seed now: chunks, recipes (as compiled skeleton threads), cases with their families, an expectation schema and
  expectations.
- Deferred: scorings (old D9), which come from a separate runtime-data pipe. The `init-data` command and the
  compiler's `Code` (old C8) are done. Engines and models were deferred too, until their locations and values were
  decided, and so were scorers, until it was decided where their code lives.
- Tools and scorers pin chatddx's own code, as compilations record their compiler: `Code` from `rig()` and an
  `entry_point` in chatddx. Packaging entry points can come later, for tools and scorers together.
- Engine locations live in the World inventory (decision 2A). An `[endpoint.<name>]` binds an engine by digest, the
  name it's served under, with its URL, `max_jobs` and credential; a `[host.<name>]` says where it keeps what
  engines pin, by model artifact digest and chat-template hash. A remote engine keeps `base_url` in its digest, and
  its endpoint adds only the credential and capacity.
- Engine values come from the hosts (decision 4A), with the fake engines first, whose values are ours (4D). No
  placeholders.
- The scorer interface, version 1, is the old one fed through remastered's views: the entry point takes a view's
  metric, the items its output selector picks (none without an answer), the items its expectation selector picks,
  and the params, and gives a value and a detail.
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

`factors.toml` also holds the old tool, toolset, `plan-web` and scorers (see "Tools and scorers"), and the old fake
stacks' models and engines (see "Engines and endpoints").

The vignettes aren't in it: they're a fake-sensitive source, so they stay out of the package. `sample-world/` holds
them as a World would:
- `inventory.toml` is the sample World inventory. Its `[source.sample]` table locates `vignettes/` and declares it
  sensitive, and it has the fake engines' endpoints and host (see "Engines and endpoints").
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
- `configuration.plan-web` is `recipe.plan-web`, a fork of `plan` with `toolset.web`. Its tool's code is the old
  `chatddx.runtime.tools.web_search`, ported to the standard library as `chatddx.tools.web_search`.
- The four old scorers are views of four scorers, one per output shape (see "Tools and scorers").
- `coercion.*` is folded into outputs (G4, G5), and `extends` became `fork_of` (G9).
- `dont_miss`, a target kind in the old schema (`src/chatddx/repo/entities/case/pydantic.py:22`) and in
  `docs/clinical-input.md`, isn't in `targets.json`. No case had one.

### init-data
`chatddx init-data USER (--vignettes DIR | --world FILE) [--source NAME] [--giftbag] [--data DIR] [--factors FILE …] [--facts PATH …]`
(`src/chatddx/cli.py`) connects as `DB_USER` and seeds in one transaction for the `archive` person, created if
missing. It shares everything with USER, who must exist (`chatddx person add`). It prints one line per record:
created, validated, updated, skipped, missing, needs repair, forked or kept. It ends with the lints' findings.
- **Inputs.** `--data` is what gets seeded, the package's sample data by default, and `--factors` more factors
  files planned with its own, such as `world/factors.toml`; a name may be defined only once per table. The
  vignettes come from `--vignettes`, a directory of `<id>.txt` files such as `sample-world/vignettes`, or from
  `--world`, a World inventory whose `[source.<name>]` table locates them, such as `sample-world/inventory.toml`.
  `--source` names the source (`sample`), which the cases are keyed by. A path that holds none of the sample's cases is refused before
  anything is written. `--facts` names the facts files, the data directory's `facts.toml` by default.
- **Planning.** `chatddx.seed.plan_factors` is pure. It plans 50 records: 14 recipes (7 configurations × 2 models),
  12 reasoning chunks, 5 sampling chunks, 7 outputs, a prompt, a tool, a toolset, an expectation schema, 4
  scorers, 2 models and 2 engines. It skips 4 reasoning chunks the facts refuse for gpt-oss.
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
- **`--giftbag`.** USER also gets their own fork of every chunk, tool, skeleton and expectation thread, so the
  catalog's variations and proposals follow the archive when it moves. Expectation schemas and scorers stay the
  archive's. Existing forks are kept (`src/chatddx/seed/write.py:_gifted`).
- **Lints.** Every component that got a thread or a family this run, validated or not, is linted
  (`src/chatddx/seed/write.py:_Seeder.lint`): `src/chatddx/factors/lint.py:lint`, with the catalog's languages and
  the facts' `reasons`, and `src/chatddx/facts/lint.py:lint`, with the facts the plan was made with (`Plan.facts`).
  A missing case, or one that needs repair, isn't linted. Each finding is a line, `[lint warning] expectation
  Dutchfall11w: expectation.invalid: at the root: 'diagnosis' is a required property`, naming every record that
  shares the digest, and a last line counts them: `[lint] 0 findings in 230 components` for the sample. The
  findings are printed, not stored, and don't stop the seeding. Today the expectation schema, expectation and
  scorer, model and engine lints have something to check: the fake models get `model.revision` and the fake
  engines `engine.closure`, and without `CHATDDX_REVISION` each scorer gets `scorer.revision`, so the sample's
  count is 4, or 8 without a revision. The vLLM, facts and language lints will apply once trials and judges are seeded.
- **Compiler.** Compilations record the running code as their compiler (`src/chatddx/core/rig.py:rig`): the version
  from the package, and the revision from `CHATDDX_REVISION` when it's set.

The old checkout's vignettes were checked in a scratch database:
- the first run created 236 records and 136 giftbag forks;
- a re-run validated all 236 and kept every fork;
- against the 13d317d checkout, 230 were validated, the renamed `DutchFall10w` was missing, and `Dutchfall11w` and
  `casesfromedn1` needed repair. That matches G13's survey;
- against 504792c, the head of `new-datamodel` on 2026-10-05, 228 were validated: `Dutchfall1w` needs repair too;
- through `--world sample-world/inventory.toml`, on a database seeded from the 7893656 checkout, all 236 were
  validated and every fork kept;
- once the tools and scorers were added, the same database got the tool, the toolset, 4 scorers and the 2 `plan-web`
  skeletons created, with the tool, toolset and skeletons forked for alice, and a re-run validated everything;
- once the fake engines were added, it got their 2 models and 2 engines created, and both sample endpoints reported
  the engine they serve.

Tests: `src/chatddx/store/test/test_seed.py`.

Still open:
- A renamed vignette shows as missing. `Catalog.survey` could name the rename, and a `repair` command could apply it.
- `wipe-data`, which tier 2 rules out as a DELETE; deletion is a `deleted` entry.
- Tags and collaborators are only ever added on a re-run, never removed.
- The seeded chunks have no language, so a trial's request language is unknown (G18).

## Engines and endpoints
Decisions 2A and 4D, done:
- **The inventory** (`src/chatddx/inventory/inventory.py`) takes `[endpoint.<name>]` and `[host.<name>]` beside
  `[source.<name>]`. A host's locations are its own, given as its start-up script reads them, so they aren't resolved
  against the inventory file.
- **`src/chatddx/inventory/serving.py`**:
  - `start_up` joins an endpoint with its engine and its host into what the start-up script runs: vLLM's arguments
    (the model's location and revision, the digest as `--served-model-name`, the template, the host's bind address
    and the URL's port, then the engine's argv), the closure and the env. It refuses a host that doesn't locate the model or the
    template, a remote engine, and an engine whose argv sets `--host` or `--port`, which the endpoint decides;
  - `url_of` gives an endpoint's URL, a remote engine's `base_url`;
  - `confirm` checks that the endpoint's `/v1/models` lists the name its engine is served under, the digest of a
    local engine and the model of a remote one, before case-derived content goes out.
- **The seeder** takes `model`, `local_engine` and `remote_engine` tables, and resolves every reference by name in
  one place (`REFERENCES` in `src/chatddx/seed/plan.py`). Engines aren't gifted, as the old giftbag gave no stacks.
- **The fake engines** (`factors.toml`): the old fake stacks' models and flags. Every value is ours and says what it
  is: the real repo, so its facts apply, with revision `fake` and one empty file for no weights; no GPU; the runtime
  `0.24.0+fake` run by `chatddx fake-vllm` rather than a Nix closure; an empty chat template. So `init-data`'s lints
  give each fake model `model.revision` and each fake engine `engine.closure`, four warnings on every run.
- **The sample World** (`sample-world/inventory.toml`) has an endpoint per fake engine, `127.0.0.1:12099` and
  `:12100` (vLLM serves one model a process), and the fake host, which keeps the models by repo name and the empty
  template at `/dev/null`. An engine change means a new digest there; a test catches the drift.
- **init-data** with `--world` ends with a line per endpoint: the URL and the seeded engine it serves, or why it
  can't be started or doesn't serve a seeded engine (`src/chatddx/seed/world.py`).
- **Tests**: `src/chatddx/inventory/test/test_serving.py` starts the fake vLLM from a sample endpoint's start-up and
  confirms it serves its engine's digest, and that another endpoint pointing there doesn't. Started by hand with
  `chatddx fake-vllm` and those arguments, it confirms too.

## Tools and scorers
Where tool and scorer code lives is settled (see "Decided"):
- **Code.** A `tool` or `scorer` record without a `code` pins the running chatddx, the `Code` compilations record as
  their compiler (`src/chatddx/seed/plan.py:plan_factors`). `src/chatddx/core/rig.py:entry` finds an entry point's
  function, and runs only the running chatddx: other pinned code is refused, not run unpinned. Planning refuses an
  entry point that names nothing, and a view whose metric the scorer's code doesn't know (`Metrics.names`). Since the
  code is part of a tool's and a scorer's digest, a new version or `CHATDDX_REVISION` makes new ones, and the
  toolset and `plan-web` skeletons with them.
- **Scorer interface, version 1** (`src/chatddx/scorers/scorer.py`). An entry point is a `ScoreFunction`: it takes a
  view's `metric`, the items its output selector picks (`None` without an answer), the items its expectation
  selector picks, and the scorer's params under the view's. It returns `Scored(value, detail)`, what a `ScoreItem`
  keeps. `score(scorer, run, answer, data)` applies every view; `function(scorer)` finds and checks the entry point.
- **The pattern scorers** (`src/chatddx/scorers/patterns.py`): the old matcher and `reciprocal_rank`,
  `first_mention` and `mentions`, unchanged in what they find, behind `score`, a `Metrics` of the three. A target is
  the expectation selector's one item: a target object with a `pattern`, a pattern, or `false` (nothing expected).
  No target, or one that doesn't read, gives no value, with the reason in the detail. A null output item names
  nothing, so a plan's `acute_warning: null` is no warning. The old aggregates are in `aggregate.py`.
- **The sample's scorers.** The old scorers each read a named view of whichever output had it. Here selectors bind a
  scorer to a shape, so there are four, with the old view names as catalog labels (`Catalog.label`):
  - `plan`: `differential` (`reciprocal_rank`), `warning` and `disposition` (`mentions`);
  - `diagnoses`: `differential`;
  - `free-text`: `text` (`first_mention`, the whole text) and `differential` (lines);
  - `raw`: `text`. No sample configuration uses `output.raw`, as in the old inventory.
  The old `critical` view had no scorer, so it has no view.
- **The tool.** `chatddx.tools.web_search` is the old tool on `urllib`, with the old record's name, description and
  parameters, in `toolset.web` with its guidance. It sends the model's query to DuckDuckGo, a third party, so the
  clearance hard block must cover it (`docs/clearance.md`, "Tools") when the runner runs tools.
- **init-data** writes the labels, gives USER a fork of the tool as the old giftbag did (scorers stay the
  archive's), and lints the scorers on landing.
- **Tests**: `src/chatddx/scorers/test/`, `src/chatddx/tools/test/`, and `src/chatddx/store/test/test_seed.py`. One
  test builds an answer of each sample configuration's shape and checks that every view of the scorer reading it
  picks something.

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
| machine, os | 3, 3 | `LocalEngine.hardware`, `.runtime` (inline) | fake done; pelle, malborg by the import |
| llm | 2 | `ModelArtifact` (fakes seeded); its facts in `facts.toml` (G1) | the real artifacts, by the import |
| serving, stack | 5, 5 | `LocalEngine` + World inventory endpoint and host | fake done; pelle, malborg by the import |
| client | 2 | `Code` on `RunStarted`, written per run | dropped |
| tool, toolset | 1, 1 | `tool` + `chunk.toolset` (G8), code in `chatddx.tools` | the loop, clearance |
| instruction | 2 | `chunk.instructions` + `chunk.prompt` (G3) | |
| output | 4 | `chunk.output`; views go to scorers (G4, G6, G7) | |
| coercion | 5 | `chunk.output` contract (G4, G5) | |
| reasoning | 9 | `chunk.reasoning`, written from the facts (G1, G2) | |
| sampling | 5 | `chunk.sampling`, `recommended` written from the facts (G1) | |
| configuration | 7 | `Recipe` → skeleton thread + `Compilation` (G9) | |
| case | 99 | `case` + family (tags, language) + `expectation` (G11–G14) | free-text expectations |
| scorer | 4 | views of 4 `scorer`s, one per output shape, code in `chatddx.scorers` | aggregates, scorings |

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
  - `plan-web` can't call its tool on vLLM 0.24: a native contract with tools sends `tool_choice: auto` beside a
    `response_format`, which then constrains the whole answer (`docs/vllm.md`, item 10). `vllm.native_tools_uncallable`
    will flag it once it's paired with a vLLM engine, and the fake vLLM answers it by the schema, calling nothing;
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
The fake engines are seeded and located (see "Engines and endpoints"); the real ones wait on the import (see "Start
with this"). Still open:
- Hardware:
  - `gpu_count` is missing (`docs/factors.md`, "Smaller issues");
  - CPU, RAM, location, GPU uuid and machine id have no home.
- Runtime: `Runtime.closure` is o11n's runtime root for the real engines (see "Start with this"), and the fakes'
  `chatddx fake-vllm`.
- No split between args in the digest and recorded-only args (`performance`). `max-num-seqs` affects batching. An
  endpoint now decides `--host` and `--port`; the rest of the old `performance` args would land in `argv`.
- Endpoints:
  - a credential is only a name: nothing stores or passes secrets yet;
  - `Call` records no URL, so the ledger can't show which endpoint got case-derived content (the runner's);
  - whether `confirm` is part of the clearance hard block (`agents/wip-clearance.md`, "Open", 4).
- Model specs (family, size, quantization, context length, licence) are held by the facts, descriptive only
  (`docs/facts.md`).

### Scorers
The scorers are seeded (see "Tools and scorers"). Still open:
- parsing a response into the answer a scorer reads: the JSON content, the answer tool's arguments, or the text.
  `docs/factors.md` puts it in the scorer's code, which version 1 doesn't do yet;
- free-text parsing beyond `lines@1` (G6);
- the old scorers' aggregates (`metrics = ["mean", "stderr"]`): the functions are ported
  (`src/chatddx/scorers/aggregate.py`), but no factor says which apply. Summing up over runs is analysis, which
  doesn't change a score;
- views with a judge: `score` refuses them until judges can run;
- a check that views agree with the output schema (old `prove`): reachability is done (G6); item types are open;
- scorings (old D9).

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
    engine; and a scorer whose view names the judge. So it waits on a judge engine and a way to run judges;
  - a language for the notes. They're `sv`, while 79 of the cases are `en`, so a judge would read Swedish notes
    about an English answer (G18);
  - somewhere for clinicians to write them other than the vignette files. Expectations are written in the portal
    (`docs/factors.md`, "Expectation"). Take: until there is one, a file in the sample data beside `cases.toml`,
    keyed by case id.
- **They can disagree with the targets.** On `Dutchfall11w`, Olof leads with shock, most likely sepsis, and uses
  ultrasound (RUSH) to rule out other causes, with blood cultures and broad empirical antibiotics. The targets,
  guessed before their markers were dropped, have AAA or dissection as the diagnosis. `Dutchfall1w` and
  `casesfromedn1` roughly agree with theirs. Open: whether the targets get revised from the notes.
