# Inventory and init-data: work in progress

Maps the old chatddx inventory onto factors, catalog and identity, for `chatddx init-data USER --giftbag`.
The old repo is chatddx-administration/chatddx at 7893656. Its seed is `src/chatddx/data/inventory.toml`, its
giftbag is `giftbag-inventory.toml`, and its parser is `src/chatddx/repo/parsers/inventory.py`.
Permalink base: https://github.com/chatddx-administration/chatddx/blob/7893656c143154e0421af0a85298177c347502e3/

The plan is to hand-edit the old TOML into the new shape. No adapter will be built. Nothing here is decided yet.
"Take" marks the agent's recommendation.

## What the old command did
- `init-data USER [--inventory P] [--with-giftbag] [--giftbag-inventory P]` (`src/chatddx/core/provisioning.py`).
- It parses `inventory.toml` with owner `archive` and commits it. Each record is reported as "created" or
  "validated", keyed by name. USER then becomes a collaborator on every archive head.
- `--with-giftbag` parses `giftbag-inventory.toml` (slices, configurations, cases) with owner USER. It commits that as
  USER's own branches.
- Parser features:
  - file-level `extends`, where the extending file wins;
  - record-level `extends` and `partial`;
  - `*_path` keys load `.toml`, `.json` and `.txt` files;
  - a case without `vignette` loads `cases/<name>.txt`, decoded as UTF-8 with trailing newlines stripped.

## Old → new

| old entity | records | new home | lost or open |
|---|---|---|---|
| machine, os | 3, 3 | `LocalEngine.hardware`, `.runtime` (inline) | ids, CPU, RAM, location, GPU uuid, kernel |
| llm | 2 | `ModelArtifact` + per-model chunks | file hashes missing; `facts`, `specs`, `profile` have no field |
| serving | 5 | `LocalEngine.argv`, `.env`, `.runtime` | `args`/`performance` split; host, port |
| stack | 5 | nothing: endpoints are World facts | `endpoint`, `served_name`, `api`, `max_jobs` |
| client | 2 | `Code` on `RunStarted`, written per run | not seeded |
| tool, toolset | 1, 1 | nothing | generation tools aren't modelled |
| instruction | 2 | `chunk.instructions` + `chunk.prompt` | handlebars templating |
| output | 4 | `chunk.output` (schema, guidance) + scorer views | JSONPath views |
| coercion | 5 | `chunk.output` contract | `auto`, `tool_description` |
| reasoning | 9 | `chunk.reasoning` | per model; `default`, `on`, `off`, `xhigh` |
| sampling | 5 | `chunk.sampling` | per model; `defaults` |
| configuration | 7 | `Recipe` → skeleton thread + `Compilation` | becomes model-specific |
| case | 99 | `case` + family (tags) + `expectation` | `language`; the vignette text itself |
| scorer | 4 | `scorer` (one per output shape) | `metrics`; entry points |

## Take: shape of the new TOML
Tables are kinds, keys are component fields 1:1, references are record names, and the catalog keys (`tags`,
`description`) are peeled off before validation. Each record then validates directly against its pydantic component,
so the parser stays small and the factors stay the single source of rules:

```toml
extends = ["inventory/chunks.toml", "inventory/recipes.toml", "inventory/cases.toml", "inventory/scorers.toml"]

[prompt.case]
segments = [{ slot = "case" }, { slot = "appendices" }]

[output.management-plan]
contract = { kind = "native" }
json_schema_path = "schemas/management_plan_v1.json"
guidance = "Fill in the management plan for the case."

[sampling.greedy]
temperature = 0

[recipe.plan]                  # a skeleton thread named "plan"
prompt = "case"
output = "management-plan"
sampling = "generation-config"
tags = ["ddx"]

[case.Dutchfall11w]
source = "chatddx"             # id defaults to the record name; the fingerprint is read from the source
tags = ["dutch-fall", "lang:en"]
expectation.diagnosis.pattern = "aaa | (abdominal | aortic) & aneurysm | dissection"
```

## Open

### A. Scope and terms
- A1. **"Inventory" already means something else.** `docs/chatddx.md` defines it as ops-authored TOML about the World
  (hosts, GPUs, endpoints, model paths, chat templates, closures, vignette sources), mutable and not
  content-addressed. The old inventory mixes three things: World facts (machines, endpoints, host/port, snapshot
  paths), factors (chunks, cases, scorers) and catalog data (names, tags, owners). What init-data seeds is the second
  and third. Should the seed file keep the name "inventory"?
- A2. **What to seed now.** Take: chunks, recipes (as compiled skeleton threads), cases with their families,
  an expectation schema, expectations and scorers. Defer models and engines, which need World data (B1, B2).
  Drop clients, tools and toolsets (C7).

### B. Models and engines (only if seeded now)
- B1. `ModelArtifact.files` needs the sha256 of every model file. The old file has only a snapshot path and
  `repo@rev`, both marked `# guessed`. This needs an import script run on the hosts.
- B2. `LocalEngine.chat_template` (path and sha256) is required. The old file has nothing for it.
- B3. `Hardware.compute_capability` isn't in the old file. `docs/chatddx.md` gives (8, 6) for the 3070 and
  (12, 0) for the 5090. `driver` sits on `os.*.specs.nvidia_driver`, which is guessed.
- B4. Runtime:
  - malborg's two servings say vLLM 0.13.0 (guessed), but the docs say both hosts run 0.24.0.
  - What is `Runtime.closure`: the vLLM package (`serving.engine`, e.g. `…-python3.13-vllm-0.24.0`) or the
    container's toplevel (`os.vllm-qwen3`)? The test sample uses `…-vllm-container`.
- B5. The new engine has only `argv`, and all of it is in the digest. The old `performance` block isn't
  fingerprinted:
  - `host` and `port` are locations, so they belong to the World inventory.
  - `gpu-memory-utilization`, `max-num-seqs`, `enable-prefix-caching` and `async-scheduling` could go into argv or be
    dropped. `max-num-seqs` changes batching, which changes outputs on engines that aren't batch invariant.
- B6. `LocalEngine` has no URL (`docs/factors.md`, "Local engines have no endpoint"), so `stack.endpoint` has no home.
  `--served-model-name` is owned by the start-up script and set to the engine digest. The hosts' current
  `Qwen/Qwen3-8B-AWQ` and `openai/gpt-oss-20b` names would then fail `attestation.model`.
- B7. The fake stacks: remastered has no fake vLLM yet. Should they be a `LocalEngine` with made-up hardware, an
  `engine.remote` on localhost, or skipped?

### C. Request chunks and configurations
- C1. **Model-agnostic → model-specific.** The old configurations resolve `sampling.recommended`, `reasoning.default`
  and `coercion.auto` per LLM at run time, from `llm.facts` (`src/chatddx/runtime/resolution.py`). The new chunks are
  literal body values, so `plan` on qwen3 and on gpt-oss become two skeletons. Options:
  - (a) Per-model chunks and recipes: `sampling.qwen3-thinking` (0.6, 0.95, 20, presence 1.5),
    `sampling.qwen3-no-thinking` (0.7, 0.8, 20, 1.5), `sampling.gpt-oss` (1.0, 1.0), with recipes such as
    `plan@qwen3` and `plan@gpt-oss`.
  - (b) Model-neutral only: `generation-config` and `recommended` become an empty `Sampling` (the server's
    generation config applies), and `default` reasoning means no reasoning chunk.
  - Take: (b) for the archive, plus the few explicit per-model chunks people actually compare.
- C2. **Reasoning vocabulary.** Old efforts are `default, off, on, minimal, low, medium, high, xhigh` plus `budget`.
  New fields are `effort ∈ {none, minimal, low, medium, high}`, `thinking_token_budget` and `chat_template_kwargs`.
  - qwen3 on/off is `chat_template_kwargs.enable_thinking`, and qwen3 maps every effort to "on".
  - gpt-oss uses `effort`, refuses off and minimal, and has no `xhigh`.
  - Open: does `default` mean "send nothing" or "send the model's documented default"? Which of the 9 records
    survive?
- C3. **Sampling.**
  - The `defaults` key goes away.
  - `max_tokens` becomes `max_output_tokens`. Which wire key should it use: `max_completion_tokens` (the default)
    or `max_tokens`?
  - `recommended-4k` is whatever `recommended` becomes, plus 4096.
  - `fixed` sets presence and frequency penalties of 0. These are sent explicitly, since 0 ≠ None.
- C4. **Instructions.** There is no templating now. `instruction.ddx`'s system message is made only of slots
  (`output_guidance`, `tool_guidance`, `schema_prompt`), so it becomes "no instructions chunk": `compile_request`
  appends the output guidance itself. The user message `{{case}}` becomes a prompt with the `case` slot.
  - Should the prompt also carry the `appendices` slot? No appendices exist yet, but adding the slot later changes
    every skeleton digest. Take: include it.
  - `instruction.bare` (tests) then collapses into the same digest as `ddx`.
- C5. **Coercion folds into the output's contract.** Each output × coercion pair becomes its own output chunk:
  - `native` → `{kind = "native"}` + `json_schema`.
  - `native-shown` and `prompted` show the schema through a `{{schema}}` template, which no longer exists. The
    schema text has to be written out into `guidance`. The old code rendered it with
    `json.dumps(indent=2, ensure_ascii=False)`. `prompted` → `{kind = "text", json_schema = …}`.
  - `tool` → `{kind = "tool", name = "final_result"}` (the old name). `tool_description` has nowhere to go:
    `ToolOutput` has no description and `compile_request` emits none. Options: drop it, fold it into the guidance,
    or add a defaulted `description` to `ToolOutput`. A defaulted field is additive, so stored digests survive.
  - `auto` disappears. `free-text` is `{kind = "text"}`.
  - Heads-up: native `response_format` is now always named `output` with `strict: true`.
- C6. **Views move to the scorer, and JSONPath becomes a JSON Pointer.** RFC 6901 can't express
  `$.diagnoses[*].diagnosis` or `$.diagnoses[?(@.critical)].diagnosis`. Options:
  - point at `/diagnoses` and put the rest in `View.params`, e.g. `{each = "/diagnosis", where = "/critical"}`;
  - let the metric's code know the shape.
  - Free-text `lines` and `whole` would become `output = ""` with `params.parse`.
- C7. **Tools.** `toolset.web`, `configuration.plan-web` and the tests' `sentinel` can't be represented. `tools` is
  an output key that passthrough may not set, `render` is single-turn, and the Python implementations don't exist
  here. Take: drop `plan-web` for now.
- C8. A configuration becomes a recipe, compiled into a skeleton thread whose edit names a `Compilation`. That record
  needs `compiler: Code`. Take: the installed `chatddx` distribution's version, with the git revision if one can
  be found, else `None`.
- C9. **Record-level `extends`.** It has 6 uses: `plan-shown`, `plan-prompted`, `plan-web`, `diagnoses-tool`,
  `coercion.prompted` and `sampling.recommended-4k`. `partial` has 0 uses. Take: keep file-level `extends`, which
  the archive/giftbag split needs. Flatten the record-level uses by hand.

### D. Cases and expectations
- D1. **Vignettes are never stored.** A case is `SourceCase(source, id)` plus the vignette's fingerprint, so
  init-data must read the 99 `.txt` files to fingerprint them. Open:
  - where those files live for remastered (copied into this repo, or a path option such as `--source NAME=DIR`);
  - the source name: one source (`chatddx`), or one per collection (`dutch-fall`, `edn`, `openxddx`). Ids would be
    the file stems.
- D2. **Fingerprint bytes.** Take: the raw file bytes, as `docs/factors.md` says ("raw, as fetched"). The old parser
  stripped trailing newlines; cleanup is now the trial's `normalization` (`strip@1`).
- D3. **The old branch head is 13d317d, one commit past 7893656.**
  - It renames `DutchFall10w.txt` to `Dutchfall10w.txt`, while `cases.toml` still says `[case.DutchFall10w]`.
  - It appends clinician comments ("Olof comments", "Chatddx:") to `Dutchfall11w.txt` and `casesfromedn1.txt`.
    Left in place, those comments would be sent to the LLM as part of the vignette and change its fingerprint.
    They read as expected answers (expectation data), not vignette or appendix text.
- D4. **Case ids.**
  - At 7893656 `DutchFall10w` is the only case with a capital F. The id becomes the family's binding, and a later
    rename needs the unimplemented repair helpers, so settle it now.
  - `openxddx-case_43` is absent. Is that intended?
- D5. **`language`** (79 en, 20 sv) has no field: it isn't a factor, and the catalog has only name, description,
  tag, owner, collaborator and deleted. Take: a tag such as `lang:sv`.
- D6. Tags (`dutch-fall`, `edn`, `openxddx`) become tag entries on the case's family. Families are global, one per
  vignette and not per owner. See F3.
- D7. **Expectation schema.** Targets become `Expectation.data`, but no `ExpectationSchema` exists yet. It must cover
  `diagnosis`, `warning`, `disposition` and `dont_miss`, each `{text?, pattern?}`, with `warning = false` allowed. Its
  digest is in every expectation and in every scorer's `consumes`.
- D8. **Guessed targets.** 198 of the 297 target patterns carry `# guessed`: every `warning` and `disposition`, none
  of `diagnosis`. Expectations are clinician-authored and feed scores. TOML comments are invisible to a parser, so
  the marker would be lost on import. Options:
  - import all of them;
  - import `diagnosis` only;
  - import all, with a `guessed` tag on the affected expectation threads.
- D9. Should init-data seed `scoring` components (a scorer plus expectations), e.g. one per collection, or leave
  that to run time? Take: leave it.

### E. Scorers
- E1. An old scorer is one entry point, one view name and one target kind. The views were defined per output. A new
  scorer holds output pointers, so scorers become per output shape. Take:
  - `plan`: `reciprocal_rank` on differential, `mentions` on warning, `mentions` on disposition;
  - `diagnoses`: `reciprocal_rank`;
  - `free-text`: `reciprocal_rank` on lines, `first_mention` on whole;
  - `raw`: `first_mention`.
  - The old view names become view labels (`catalog.label`).
- E2. `Scorer.code` must pin code that doesn't exist here yet. The metrics would be free strings
  (`reciprocal_rank`, `first_mention`, `mentions`). `revision = None` triggers the `scorer.revision` lint.
- E3. `metrics = ["mean", "stderr"]` is aggregation, which remastered hasn't named. Drop it, or keep it in
  `params`?
- E4. The `critical` view is defined but no scorer reads it. Drop it?

### F. The command
- F1. **People.** `archive` must be an `identity.person`. Create it on demand, and with what name and roles? Should
  the USER be created if missing, as the old `ensure_identity` did, or must `chatddx person add` come first?
- F2. **Sharing.** The archive gets owner entries on its threads, and USER gets collaborator entries on each of
  them, as before. Should USER also get collaborator entries on case families?
- F3. **Giftbag.** Take: forks of the archive's threads (`forked_from` = the archive's head edit), owned by USER,
  with the same names. Cases have no threads and families aren't per owner. Does the giftbag's "cases" then mean
  forks of the expectation threads, or nothing?
- F4. **Re-runs.** The catalog is append-only and names aren't unique, so a naive re-run duplicates threads.
  Take: init-data keys threads by (kind, name, owner) itself:
  - the same digest is a no-op ("validated");
  - a different digest appends an edit ("updated"), as the old `test-later-inventory.toml` expects.
- F5. **Flags.** You asked for `--giftbag`; the old command had `--with-giftbag` and `--giftbag-inventory PATH`.
  Take: `init-data USER [--inventory PATH] [--giftbag] [--giftbag-inventory PATH]`.
- F6. **Location.** Take: `src/chatddx/data/` with `settings.INVENTORY_PATH`, as in the old repo.
- F7. `wipe-data` can't exist as it was, because tier 2 refuses DELETE. Its counterpart would be `deleted` entries.
  Out of scope unless asked.
