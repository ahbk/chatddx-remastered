# Facts

To ask a model something, the rig needs to know things about it that a request doesn't say: how each reasoning
level is expressed for this model, or that it's refused; which sampling its makers recommend for each level; which
output contracts work with it and with what caveats. Facts are that knowledge, written by ops and developers, one
model at a time.

Facts are not factors (`docs/factors.md`, "Not factors"). They never reach the wire themselves: they write literal
chunks before compilation, and they check chunks that are already written. No component refers to them, so no digest
depends on them, and correcting a guessed fact changes nothing stored. A skeleton still means exactly what it sends.

Code paths are relative to `src/chatddx/facts/` unless they start with `src/` or `docs/`. The package builds on
`chatddx.factors` and imports no other chatddx package; `chatddx.factors` doesn't import it.

## Where facts come from
Facts are typed in code (`facts.py:Facts`, `facts.py:ModelFacts`) and written in TOML, one `[model."<name>"]` table
per model. `Facts.load(*paths)` reads them. A file may hold only `model` tables, and a model may have facts in only
one file. Nothing is stored in the database: whoever needs facts loads them and passes them on. Their history is
git's.

Facts are keyed by model name: the `ModelArtifact.repo` behind a local engine, or a remote engine's `model`
(`facts.py:model_name`). `Facts.about(engine, registry)` gives an engine's model facts, or nothing when there are
none.

The only facts file today is the sample data's (see "The sample's facts").

## What a model's facts hold
Every part is optional. A missing part means "unknown", not "no".

### Reasoning
`reasoning` (`facts.py:ReasoningFacts`) has an entry for each level: `off`, `on`, `minimal`, `low`, `medium`,
`high` and `xhigh` (`facts.py:Intent`, the old chatddx's intents). Each entry is one of:
- *writes*: what the `Reasoning` chunk sets, `effort` and/or `chat_template_kwargs` (`facts.py:Writes`);
- a *collapse* into another level, e.g. `low = "on"`: asking for `low` is asking for `on`;
- a *refusal* with a reason, e.g. `off = { refused = "always reasons: …" }` (`facts.py:Refusal`).

A level *lands* where its collapses end: on writes, on a refusal, or on nothing when the level it reaches has no
entry (`ReasoningFacts.land`). Loading refuses collapses that go round, and collapses that land on nothing.

`default` names the level the model uses when a request says nothing about reasoning. It is checked like a
collapse. `budget` is `"thinking_token_budget"` when the model takes a thinking budget, or a refusal.

### Sampling
`sampling.recommended` holds, per level, the sampling its makers recommend (`facts.py:SamplingValues`, the
`Sampling` chunk's sampling fields). It's looked up by the level a request lands on, so `recommended.on` serves every
level that collapses into `on`.

`sampling.generation_config` is what the server fills in when a chunk leaves a field out. It's descriptive only.

### Output
`output` holds a note or a refusal per contract, `native`, `tool` and `text` (`facts.py:ContractFact`). Its
`default`, the contract the model is usually asked for, is descriptive only.

### Specs
`family`, `parameters_b`, `active_parameters_b`, `quantization`, `context_length` and `licence` are descriptive
only.

## Writing chunks
Facts apply when a chunk is written. `ModelFacts.reasoning_chunk(effort, budget=)` and
`ModelFacts.sampling_chunk(effort, **overrides)` write literal `Reasoning` and `Sampling` chunks for one model, where
the effort is a level or `"default"`. Overrides replace recommended values.

Both raise `Refused`, with the fact's reason when there is one, when:
- no default is known, or the level lands on a refusal or on nothing;
- a budget is asked for and the model's budget is refused or unknown (`reasoning_chunk`);
- nothing is recommended for the level the effort lands on (`sampling_chunk`).

The seeder is the only caller today (`docs/sample-data.md`). A `from_facts` record in a factors file is written once
per model the facts know, named `<record> (<model>)`, and a recipe that uses it comes once per model too
(`src/chatddx/seed/plan.py:_component`, `src/chatddx/seed/plan.py:plan_factors`). A refused chunk, and the recipes
that need it, are skipped and reported. `chatddx init-data --facts PATH …` names the facts files, by default the
data directory's `facts.toml` (`src/chatddx/cli.py`).

A collapsed level writes the same chunk as the level it lands on, so seeding gives them one digest under several
names. The sample's Qwen3 has six reasoning threads for `enable_thinking = true`.

## Checking pairs
A trial or a judge pairs a skeleton with an engine, whose model may have facts. `lint.py:lint(registry, facts,
digests)` checks those pairs:
- `facts.missing` (info): there are no facts about the engine's model, so the model-level checks were skipped.
- `facts.reasoning_unmatched`: the skeleton's `reasoning_effort` and `chat_template_kwargs` match none of the model's
  level writes. This catches a skeleton written for one model and paired with another: `enable_thinking` sent to
  gpt-oss, or `reasoning_effort = "none"` sent to gpt-oss, whose `off` is refused. A skeleton that sets neither
  isn't checked.
- `facts.budget_refused`: `thinking_token_budget` on a model whose budget is refused.
- `facts.output_refused`: the facts refuse the skeleton's contract.
- `facts.output_note` (info): the facts have a note on the skeleton's contract.

Facts also feed one of the factors' own lints (`docs/factors.md`, "Lints"). `lint.py:reasons(facts, registry)` is
passed as `reasons=` to `src/chatddx/factors/lint.py:lint` and says whether an engine's model reasons by default:
`ModelFacts.reasons_by_default` is true when the model's `default` lands on a level other than `off`.
`vllm.grammar_before_reasoning` uses it when a skeleton leaves reasoning to the model. It reports a warning if the
model reasons, nothing if it doesn't, and info when that's unknown. An engine with `--default-chat-template-kwargs`
changes the model's default, so it stays at info.

Without facts, linting is unchanged.

## The sample's facts
`src/chatddx/data/sample/facts.toml` holds the facts about the old chatddx's two models, `Qwen/Qwen3-8B-AWQ` and
`openai/gpt-oss-20b`. They were ported by hand from its `src/chatddx/data/inventory/llms.toml` at 7893656, and the
tests load them too:
- the specs moved to the top level;
- `coercion` became `output`, with `prompted` mapped to `text`;
- the reasoning writes use the chunk's field names (`effort`, not `reasoning_effort`).

Left behind:
- `snapshot` and `source` say where the files are, which is the World inventory's business;
- `tags` belong to the catalog;
- `profile` was for pydantic-ai only;
- `needs` listed vLLM requirements, which are now the factors' `vllm.*` lints;
- gpt-oss's note on `tool_choice = "required"`. A `tool` contract with a toolset sends `required`
  (`docs/factors.md`, "Recipe and compilation"), so the note may come back once it's checked (see "Open design
  issues").

## Open design issues
- **Nothing outside the tests runs the checks.** `lint.py:lint` and `lint.py:reasons` have no caller outside the
  tests, and neither does `src/chatddx/factors/lint.py:lint`. `init-data` lints nothing it lands. It seeds no trials
  or judges yet, so it would get no `facts.*` finding anyway.
- **Which models get seeded.** A `from_facts` record is written for every model the facts know, not for the models of
  the engines that will run it. Once engines are seeded, the two lists can differ.
- **`xhigh` can't be written.** It's a level, but neither `Reasoning.effort` nor `Writes.effort` has the value, so a
  model can only collapse or refuse it. Adding it to both is additive, if a model that supports it shows up.
- **`tool_choice` on harmony.** Named and `required` tool choices are unverified for gpt-oss. vLLM 0.24's gpt-oss
  tool parser accepts both, so the JSON-array grammar applies; whether harmony's output survives it isn't known.
- **A model or a model on a runtime.** Some facts are really about a model on a runtime (harmony and
  `response_format`). They live on the model for now; a runtime-keyed section can be added when a second runtime
  appears.
- **Defaults from argv.** An engine's `--default-chat-template-kwargs` and `--generation-config` change the model's
  defaults. Facts describe the model's own defaults, and only `vllm.grammar_before_reasoning` accounts for the
  override.

### Smaller issues
- **A budget with reasoning off is written.** Qwen3's `off` with a budget gives `enable_thinking = false` together
  with a `thinking_token_budget`.
- **Unread recommendations.** A `sampling.recommended` entry for a level that collapses is never read, and nothing
  reports it.
- **An unknown budget passes the check.** `facts.budget_refused` says nothing when the budget is unknown, while
  `reasoning_chunk` refuses to write it.
- **A refused default is accepted.** A `default` that lands on a refusal loads, and `reasons_by_default` then says
  unknown.
