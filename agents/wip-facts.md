# Facts: work in progress

Superseded by `agents/facts.md`, which covers the current state and what's still open; kept for its history.

Material for a future `docs/facts.md`. "Decided" means the user said so; "Take" is the agent's recommendation and
still open. Code: `src/chatddx/facts/facts.py`, tests and a ported sample in `src/chatddx/facts/test/`.

## Scope
Facts are knowledge about models: how a reasoning level is expressed (or refused), which sampling is recommended
per level, which output contracts work and with what caveats, and descriptive specs. They are not factors: they
never reach the wire themselves, they produce or check the literal chunks that do. So no digest depends on them,
and correcting a guessed fact changes nothing stored (`docs/factors.md`, "Not factors").

## Decisions
- Decided: facts are typed in code (pydantic, `Facts`, `ModelFacts`) and written by ops/developers in TOML
  (`[model."<name>"]` tables), loaded explicitly with `Facts.load(*paths)` and passed to whoever needs them. A
  model may have facts in only one file. History is git's.
- Decided: facts apply at authoring. `ModelFacts.reasoning_chunk(effort, budget=)` and
  `ModelFacts.sampling_chunk(effort, **overrides)` write literal `Reasoning` and `Sampling` chunks, so a skeleton
  still means exactly what it sends. They raise `Refused` with the fact's reason.
- Decided: facts are keyed by model name, the `ModelArtifact.repo` for local engines and `RemoteEngine.model` for
  remote ones (`model_name`, `Facts.about`).

## Shape (`ModelFacts`)
- Specs: `family`, `parameters_b`, `active_parameters_b`, `quantization`, `context_length`, `licence`.
- `reasoning`:
  - `default`: the level a model uses when nothing is asked.
  - The levels `off, on, minimal, low, medium, high, xhigh` (the old intents, kept on the user's word that they
    were good). Each is one of:
    - writes: `{ effort = … }` and/or `{ chat_template_kwargs = … }`, the `Reasoning` chunk's fields;
    - a collapse into another level (`low = "on"`);
    - a refusal: `{ refused = "why" }`.
  - `budget`: `"thinking_token_budget"` or a refusal.
  - Collapses must land on writes or a refusal, and may not go round; `default` is checked like a collapse.
- `sampling`:
  - `recommended`: a table per level.
  - `generation_config`: what the server fills in when a chunk leaves a field out. Descriptive only.
- `output`: `default` contract, and per contract (`native`, `tool`, `text`) either `{ note = … }` or a refusal.

## Lints (`lint(registry, digests, facts=)`)
With facts given, trials and judges also get:
- `facts.missing` (info): no facts about the engine's model, so model-level checks were skipped.
- `facts.reasoning_unmatched`: the skeleton's `reasoning_effort` and `chat_template_kwargs` equal none of the
  model's levels. This catches a skeleton written for one model and paired with another, e.g. `enable_thinking`
  sent to gpt-oss, or `reasoning_effort = "none"` to gpt-oss, whose `off` is refused.
- `facts.budget_refused`: `thinking_token_budget` on a model whose budget is refused.
- `facts.output_refused` (warning) and `facts.output_note` (info): the contract's fact.
- `vllm.grammar_before_reasoning` gets sharper. When the skeleton leaves reasoning to the model, the model's
  `default` decides: a warning if it reasons, silence if it's `off`, and `info` when unknown. An engine with
  `--default-chat-template-kwargs` changes the default, so it stays `info`.

Without facts, linting is unchanged.

## From the old `llms.toml`
`src/chatddx/data/sample/facts.toml` (the sample data's, since init-data) ports the two sample models by hand:
- `specs` moved to the top level, and `coercion` became `output`, with `prompted` mapped to `text`.
- Reasoning writes use the chunk's names (`effort`, not `reasoning_effort`).

Left behind:
- `snapshot`, `source`: these are World facts (where the files are), for the inventory.
- `tags`: catalog.
- `profile`: pydantic-ai only.
- `needs`: vLLM requirements, now G10's runtime lints.
- gpt-oss's tool note, about `tool_choice = "required"`. Remastered sends `required` since G8 (a `tool`
  contract with a toolset), so the note may come back, once checked against vLLM 0.24 (see Open).

## Open
- **Where the sample's facts live.** Decided with init-data: `src/chatddx/data/sample/facts.toml`, which the tests
  load too.
- **`xhigh`.** It is a level, but neither `Reasoning.effort` nor `Writes.effort` can express it. Adding it to both
  Literals is additive, if a model that supports it shows up.
- **Named and `required` `tool_choice` on harmony** (gpt-oss) are unverified. vLLM 0.24's gpt-oss tool parser
  accepts both, so the JSON-array grammar applies; whether harmony's output survives it isn't known.
- **Facts for engines vs models.** Some old facts were really about model × runtime (harmony and
  `response_format`). They live on the model for now; a runtime-keyed section can be added when a second runtime
  appears.
- **Defaults from argv.** The engine's `--default-chat-template-kwargs` and `--generation-config` change defaults.
  Facts describe the model's own defaults, and only the reasoning-default check accounts for the override.
