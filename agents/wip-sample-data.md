# Sample data: work in progress

"The sample data" is the old chatddx inventory: chatddx-administration/chatddx at 7893656, with
`src/chatddx/data/inventory.toml`, `giftbag-inventory.toml` and the vignettes in `src/chatddx/data/cases/`. It is
hand-edited into remastered's shape, with no adapter, and spreads over remastered's channels: factors, cases,
catalog, and the World inventory (`docs/chatddx.md`, glossary). The word "inventory" keeps its glossary meaning.
Permalink base: https://github.com/chatddx-administration/chatddx/blob/7893656c143154e0421af0a85298177c347502e3/

"Decided" means the user said so. "Take" is the agent's recommendation and still open. Each gap is tagged
[maybe fix remastered]: the sample data needs something remastered may lack.

## Decided
- Seed now: chunks, recipes (as compiled skeleton threads), cases with their families, an expectation schema and
  expectations.
- Deferred: engines and models, scorers, the `init-data` command, the compiler's `Code` (old C8), scorings (old D9),
  which come from a separate runtime-data pipe.
- Base the sample data on 7893656. Quirks found while tweaking it, such as 13d317d's rename and the
  `DutchFall10w`/`Dutchfall*` spelling, are welcome stress tests.
- `max_tokens` is dropped from sampling.
- Instructions: the hand edit changes the mechanism but keeps the spirit; remastered must support the same things
  (G3).
- The vignette files are a fake-sensitive source (G11). Fingerprints are over the raw bytes.
- The expectation schema is hand-written into the new sample data.
- The `# guessed` markers on targets are dropped on import, and the data is treated as live.

## Old → new

| old entity | records | new home | lost or open |
|---|---|---|---|
| machine, os | 3, 3 | `LocalEngine.hardware`, `.runtime` (inline) | deferred |
| llm | 2 | `ModelArtifact`; its facts have no home (G1) | deferred, except G1 |
| serving, stack | 5, 5 | `LocalEngine` + World inventory | deferred |
| client | 2 | `Code` on `RunStarted`, written per run | dropped |
| tool, toolset | 1, 1 | nothing yet (G8) | |
| instruction | 2 | `chunk.instructions` + `chunk.prompt` | G3 |
| output | 4 | `chunk.output`; views go to scorers | G4, G6, G7 |
| coercion | 5 | `chunk.output` contract | G4, G5 |
| reasoning | 9 | `chunk.reasoning` | G1, G2 |
| sampling | 5 | `chunk.sampling` | G1 |
| configuration | 7 | `Recipe` → skeleton thread + `Compilation` | G9 |
| case | 99 | `case` + family (tags) + `expectation` | G11–G14 |
| scorer | 4 | `scorer` | deferred |

## Take: TOML shape for the factor channel
Tables are kinds, keys are component fields 1:1, references are record names, and the catalog keys (`tags`,
`description`) are peeled off before validation:

```toml
[prompt.case]
segments = [{ slot = "case" }, { slot = "appendices" }]

[output.management-plan]
contract = { kind = "native" }
json_schema_path = "schemas/management_plan_v1.json"
guidance = "Fill in the management plan for the case."

[recipe.plan]
prompt = "case"
output = "management-plan"
sampling = "generation-config"
tags = ["ddx"]
```

## Gaps

### Request
- **G1. Model facts.** [maybe fix remastered] (old C1)
  - The old `llm.facts` held the knowledge that turned model-neutral intent into a request body:
    - recommended sampling per reasoning mode;
    - how each reasoning level is expressed, or that it's refused;
    - the default output mode (`coercion.auto`);
    - which server feature a mode needs (`reasoning_parser`, `tool_call_parser`);
    - what the server fills in for omitted fields (`sampling.generation_config`).
  - Remastered has no home for any of it. Without facts, chunks are literal values someone must look up by hand per
    model, and nothing can check them.
  - A skeleton is engine-agnostic, but facts are per model. So facts apply either when authoring (a helper or the
    portal picks a model and writes literal chunks) or at trial time, when skeleton and engine meet (G10).
  - Candidate homes:
    - a component kind that references a `model` (facts then get a digest and a thread);
    - the World inventory (mutable, ops-authored);
    - code, like the vLLM clamp lint.
  - **Fixed** (user's choices: typed in code with values in TOML, applied at authoring, keyed by model name). See
    `agents/wip-facts.md`.
    - The package is `chatddx.facts`. `ModelFacts.reasoning_chunk` and `sampling_chunk` write literal chunks,
      and `lint(…, facts=)` checks pairs.
    - The sample's two models are ported in `src/chatddx/facts/test/sample.toml`.
- **G2. Reasoning levels.** [maybe fix remastered] (old C2)
  - The old intent levels were `default, off, on, minimal, low, medium, high, xhigh` and `budget`. They were
    translated per model, and a model could refuse a level with a reason.
  - `chunk.reasoning` holds only literal body values (`effort`, `thinking_token_budget`, `chat_template_kwargs`).
  - The same tension as G1: a level needs a model to become a body, and a skeleton has none.
  - **Fixed with G1.**
    - The old levels are kept as facts per model: writes, collapses, refusals, a default and a budget.
    - They become literal `Reasoning` chunks at authoring (`ModelFacts.reasoning_chunk`).
    - `facts.reasoning_unmatched` catches a skeleton whose reasoning belongs to another model.
    - Open: `xhigh` has no `Reasoning.effort` value yet (`agents/wip-facts.md`).
- **G3. Placing chunk-supplied text.** [maybe fix remastered] (old C4)
  - The only composition `compile_request` knows is output guidance appended to the instructions after a blank line
    (`src/chatddx/factors/request.py:compile_request`).
  - The old instruction placed output guidance, the schema prompt and tool guidance anywhere in the system or user
    message, conditionally (`{{#if …}}`).
  - Remastered needs a mechanism with the same reach. One candidate is compile-time slots: placeholders that
    `compile_request` fills from the recipe's chunks. They would sit alongside the send-time slots that `render`
    fills.
  - **Fixed** by extending G4's inserts.
    - `output_guidance` may be inserted in `Instructions.text` or in `Prompt.segments`, not both
      (`INSERTS_BY_KIND`). `schema` stays in the output's guidance.
    - An insert's `before` and `after` text appear only when its fill isn't empty. This replaces the old
      `{{#if x}}\n\n{{x}}{{/if}}` and mirrors `AppendixLayout`.
    - Guidance that isn't inserted anywhere is still appended to the instructions, so existing recipes compile to
      the same text. Text-only instructions stay plain strings and keep their digests.
    - Compilation merges adjacent text in messages, so splitting a chunk's text differently doesn't change a skeleton.
      The one digest change: a prompt that already had adjacent or empty strings now compiles to merged segments.
    - Sample data:
      - `instruction.ddx` (a system message made only of slots, user `{{case}}`) needs no instructions chunk: the
        prompt is `[{ slot = "case" }]` and the guidance becomes the system message. Its `tool_guidance` part waits
        for G8, which adds a `tool_guidance` insert.
      - `instruction.challenge-coercion` becomes `text = "DON'T FOLLOW …!!"`, with the guidance (schema shown, for
        the prompted variant) appended by default.
    - Proposed amendments: `docs/chatddx.md` (Insert, Segment's colon) and `docs/factors.md` (Request, Instructions,
      Prompt, Output).
    - Tests: `test_output_guidance_goes_where_it_is_inserted`, `test_inserts_are_checked_per_chunk`.
- **G4. Showing the schema in the prompt.** [maybe fix remastered] (old C5)
  - "native (shown)" sends `response_format` and also shows the schema as text. "prompted" shows it and enforces
    nothing.
  - Neither can be expressed except by pasting the schema into `guidance` by hand, which drifts from `json_schema`.
    The old code rendered it with `json.dumps(indent=2, ensure_ascii=False)` into `{{schema}}`.
  - **Fixed** with option "placeholder in guidance" (user's choice over a `show_schema` flag).
    - `Output.guidance` is text, or segments of text and `Insert(insert="schema")`. `compile_request` fills the
      insert with the output's schema as `json.dumps(indent=2, ensure_ascii=False)` (`Output.guidance_text`).
    - The schema must exist (`Output.output_schema` covers the text contract's own schema) and may be inserted once.
      Adjacent text is merged, so text-only guidance stays a plain string and keeps its digest.
    - "native (shown)" is a native output whose guidance inserts the schema. "prompted" is a text output with
      `json_schema` whose guidance inserts it.
    - The old composition was checked against pydantic-ai 2.41.0's `TemplateStr`. It doesn't HTML-escape, and the
      sample's guidance below compiles to the identical system text:
      ```toml
      guidance = [
        """Fill in the management plan for the case.

      Answer with a JSON object that matches this JSON Schema, and nothing else:

      """,
        { insert = "schema" },
      ]
      ```
    - "Insert" is the new compile-time counterpart of "slot". G3 extended it to the instructions and prompt.
    - Proposed amendments: `docs/chatddx.md` (glossary: Segment, Insert) and `docs/factors.md` (Request, Output).
    - Tests: `test_outputs_show_their_schema`, `test_schema_inserts_are_checked`.
- **G5. The tool contract has no description.** [maybe fix remastered] (old C5)
  - `ToolOutput` holds only a name, and `compile_request` emits the function without a description. The old
    `coercion.tool` had `tool_description` and the name `final_result`.
  - A defaulted `description` field is additive, so stored digests keep their values.
  - **Fixed.**
    - `ToolOutput.description` is optional and non-empty when given. `compile_request` emits it between the
      function's `name` and `parameters`.
    - A skeleton's tool contract must match its body's description, as it already had to match the name.
    - Tool contracts without a description compile, digest and validate as before.
    - Sample data: `coercion.tool` →
      `contract = { kind = "tool", name = "final_result", description = "Give your answer by calling this tool, with
      the answer as its arguments." }`.
    - Proposed amendments: `docs/factors.md` (Output, Skeleton).
    - Tests: `test_tool_contracts_carry_a_description` and the description case in
      `test_skeleton_structure_is_enforced`.
- **G6. Views.** [maybe fix remastered] (old C6)
  - `View.output` and `View.expectation` are JSON Pointers. They can't express `[*]`
    (`$.diagnoses[*].diagnosis`) or filters (`$.diagnoses[?(@.critical)].diagnosis`).
  - Free-text outputs were read through parsers (`lines`, `whole`).
  - The rest of the scorer is deferred, but the sample data's outputs can't be described without this.
- **G7. JSON key order is lost in storage.** [maybe fix remastered] (confirmed)
  - `canonical_bytes` sorts keys, and a component read back (`Registry.add_raw`, `Store.get`, `Bundle.load`) keeps
    that order. A skeleton built in memory sends its schema properties as authored (`diagnosis, probability,
    critical`). The same skeleton, same digest, loaded from its canonical bytes sends them alphabetically
    (`critical, diagnosis, probability`).
  - Property order steers what a constrained model generates first, and it is what a shown schema (G4) reads like.
  - `fingerprint_request` hashes sorted keys too, so the ledger can't see the difference.
  - The old code kept order on purpose, for output schemas and tool parameters. They were stored as text
    (`src/chatddx/repo/families/django.py:OrderedJSONField`), and their identity hashed ordered pairs, so a
    reordered schema got a new identity (`src/chatddx/repo/families/canonical.py:ordered`).
  - Affected: `Output.json_schema`, `TextOutput.json_schema`, `Skeleton.body`, `Passthrough.body`,
    `Canary.body` and `messages`, and tool parameters once G8 exists.
  - **Fixed.** `canonical_bytes` no longer sorts. Field names are sorted by `Frozen`'s serializer and
    `canonical_doc`. Settings are sorted by the `Settings` type:
    - the top level of `Skeleton.body`, `Passthrough.body` and `Canary.body`;
    - `chat_template_kwargs`, `View.params`, `Scorer.params` and `LocalEngine.env`.

    All other JSON keeps its order. `fingerprint_request` sorts only the body's top level, and the seal doc is
    sorted.
    - Bytes written before the fix (all keys sorted) re-serialize unchanged, so stored digests and seals stay valid.
    - Their order is already lost, though. Compiling the same recipe now gives a new skeleton digest, and its request
      fingerprints differ from the old ones.
    - Proposed amendments: `docs/chatddx.md` (glossary), `docs/ledger.md` (request fingerprint), `docs/store.md`
      (`doc` is jsonb and reorders keys).
    - Tests: `test_json_data_keeps_its_order`, `test_settings_carry_no_order` and
      `test_bytes_with_sorted_keys_keep_their_digest` in `src/chatddx/factors/test/test_factors.py`; the schema-order
      assertions in `test_components_roundtrip` (`src/chatddx/store/test/test_store.py`) and
      `test_request_fingerprint_is_over_canonical_bytes` (`src/chatddx/ledger/test/test_ledger.py`).
    - Alternative not taken: sort everything except fields marked ordered, as the old code did. That needs a
      marker everywhere a schema can sit, including inside free-form bodies (e.g. a passthrough `guided_json`), and
      a missed marker silently reorders again.
- **G8. Tools.** [maybe fix remastered] (old C7)
  - The old `tool` and `toolset` entities had a name, a description, a parameter schema, a Python implementation
    and toolset guidance (`plan-web` uses `web_search`).
  - Remastered lacks all of these:
    - a component for a tool or toolset;
    - pinning of the implementation's code;
    - a `tool_guidance` insert: the mechanism exists since G3, but nothing fills it yet;
    - room in the body: `tools` and `tool_choice` are reserved for the tool contract;
    - a multi-turn loop: `render` builds one request;
    - ledger rows for tool calls and results: a run item holds one `Call`;
    - a clearance rule: a tool such as `web_search` sends case-derived text to a third party, which falls under the
      hard block.
- **G9. Variations.** [maybe fix remastered] (old C9)
  - The sample data defines `plan-shown`, `plan-prompted`, `diagnoses-tool`, `coercion.prompted` and
    `sampling.recommended-4k` as "X with Y replaced".
  - Remastered can't express that:
    - a recipe exists only inside a `Compilation` record, not as something authored or named on its own;
    - a chunk can't be derived from another with one field overridden;
    - forks copy whole skeletons.
  - Semantics differ too. Old `extends` was live: a child followed its parent's edits. A fork is a snapshot, and
    nothing propagates (`agents/wip-catalog.md`, decision 4); `Catalog.behind` only reports.
  - **Fixed** (user's choice: a proposal to re-apply when the origin moves). A variation stays a fork, as the docs
    say, and the catalog now knows what it varies:
    - `Catalog.recipe(thread)` reads a configuration's recipe.
    - `Catalog.variation(thread)` gives the base (`forked_from`, or the latest `based_on`), the origin's head,
      `moved`, and `varies`: recipe parts for skeleton threads, top-level fields for other kinds.
    - `Catalog.proposal(thread)` gives the origin's head with the fork's changes on top.
    - Accepting is `Catalog.edit(fork, …, based_on=origin_head)`. The new `catalog.edit.based_on` column
      (migrations 0015 t0, 0016 t2) records it, so the same proposal isn't made twice.
    - Sample data, when the loader exists (deferred command): a record-level `extends` becomes a fork with
      `forked_from`. `plan-shown`, `plan-prompted` and `diagnoses-tool` vary `output`. `coercion.prompted`
      vs `native-shown` is an output chunk fork that changes `contract`. `sampling.recommended-4k` is gone with
      `max_tokens` (old C3).
    - Chunk derivation needs no helper: an author changes a field and saves the result as a fork. The catalog then
      sees the field as varied.
    - Proposed amendments: `docs/catalog.md`, `docs/store.md`, `docs/chatddx.md`.
    - Tests: `test_variations_are_reapplied_on_request`, `test_chunk_variations`.
- **G10. Skeleton × engine compatibility.** [maybe fix remastered]
  - The old facts refused combinations: gpt-oss can't turn reasoning off, harmony doesn't render `response_format`,
    vLLM ignores `tool_choice = required` for gpt-oss, and native/tool modes need parsers in the engine's argv.
  - `lint.py` knows only the temperature clamp, and nothing checks a trial's skeleton against its engine. Depends
    on G1.
  - Found during G15:
    - On vLLM 0.24, a `tool` contract is only constrained and parsed when the engine's argv has
      `--enable-auto-tool-choice` and `--tool-call-parser` (`docs/vllm.md`, proposed assumption 5). A local
      engine's argv is enough to lint this, without facts.
    - OpenAI's API is reported to reject function parameters whose root isn't `type: object` (not verified here),
      and other OpenAI-compatible remotes may too. vLLM 0.24 accepts any root. That's a candidate lint for tool
      contracts on `engine.remote`, once the remote's behavior is known.
  - **Partly fixed**: the checks that need only the engine's declared runtime and argv, not model facts.
    - Trials and judges both pair a skeleton with an engine, so `lint.py:_pair` applies to both. Judges were
      unlinted before, so they now also get `vllm.temperature_clamped`.
    - On vLLM 0.24:
      - `vllm.tool_unconstrained`: a `tool` contract without `--enable-auto-tool-choice` and `--tool-call-parser`.
      - `vllm.thinking_budget_refused`: `thinking_token_budget` without `--reasoning-parser` or
        `--reasoning-config`; the request gets a 400.
      - `vllm.grammar_before_reasoning`: a grammar (`native`, or `tool` with parsers) without `--reasoning-parser`.
        It's a warning if the skeleton asks to reason, `info` if it leaves that to the model, and nothing if it
        turns reasoning off. The old pelle comment described exactly this.
    - Elsewhere (remote engines and other vLLM versions), `schema.ref_unverified` fires when a `native` or `tool`
      schema has `$ref`, and suggests `inline_refs@1` (G16).
    - Flags are read as vLLM reads them, `_` and `-` alike (`engine.py:flag_names`). This also fixed a bypass:
      `LocalEngine` rejected `--served-model-name` but let `--served_model_name` and `--chat_template` through.
    - Evidence: `docs/vllm.md` proposed assumptions 6–8.
    - Done with G1: `facts.reasoning_unmatched`, `facts.budget_refused`, `facts.output_refused` and
      `facts.output_note`, plus the model's default reasoning for `vllm.grammar_before_reasoning`. The rest of
      this bullet is kept for history.
    - Waits for G1: the model-level refusals (gpt-oss can't turn reasoning off, harmony doesn't render
      `response_format`, gpt-oss and `tool_choice = required`) and whether a model reasons by default. That default
      is what would turn the `info` into a warning or silence it. Also waiting: the object-root rule for remotes,
      until verified.
    - Open, not fixed: vLLM's `--config FILE` pulls arguments from a YAML file the digest doesn't cover, including
      owned flags. Options: forbid `--config` in argv, or lint it.
    - Proposed amendments: `docs/factors.md` (Linting, Local engine), `docs/chatddx.md` (Findings), `docs/vllm.md`
      (6–8).
    - Tests: `test_skeleton_and_engine_compatibility`, and the underscore case in
      `test_engine_argv_cannot_override_manifest`.
- **G15. Sent schemas are not prepared.** [maybe fix remastered] (found while doing G4)
  - The old code sent an inlined copy of the schema, with `$ref` resolved and `$defs` dropped
    (`src/chatddx/runtime/resolution.py:inlined`). It refused a schema whose top level isn't an object. Only the
    shown schema was the authored one.
  - Remastered sends and shows the authored schema as is. `management_plan_v1.json` uses `$defs` and `$ref`.
  - Open: whether vLLM 0.24's structured-output backends and the tool-call path take `$ref`, and whether inlining
    belongs to the output chunk, to compilation, or to a lint.
  - **Closed, no change.** vLLM 0.24 constrains `$defs`/`$ref` schemas as written, on both the `response_format` and
    the named-tool path.
    - xgrammar 0.2.1 and 0.2.8, both ends of vLLM 0.24's pinned range, compile `management_plan_v1.json` to the same
      grammar as the old inlined copy, up to rule names. So the default `auto` backend keeps xgrammar.
    - The evidence and permalinks are in the proposed amendment to `docs/vllm.md` (assumptions 4 and 5), so the fake
      vLLM pins them.
    - The old code doesn't record why it inlined; the target stack doesn't need it. Remastered sends what it shows.
    - Moved to G10: the object-root rule (remote OpenAI-compatible APIs reject non-object function parameters) and
      the tool-parser requirement (assumption 5).
- **G16. An inline path for engines that don't resolve `$ref`.** [maybe fix remastered] (follows G15)
  - The old code inlined because pydantic wasn't cooperative. Remastered uses no pydantic-ai, but it must not
    depend on vLLM. Some engines resolve local refs, some reject them, and some ignore what they don't understand,
    which leaves part of the answer unconstrained without an error. The remote Gemma engine's server software is
    unknown.
  - **Fixed** (user's choice: a separate gap, compile-time and opt-in).
    - `Output.schema_ops` is a tuple of named, versioned ops, like `NormalizeOp`. It's empty by default, so
      existing digests are kept. The first op is `inline_refs@1`.
    - `compile_request` applies the ops to the one schema the skeleton sends (`response_format` or the tool's
      `parameters`) and shows (the `schema` insert). A text contract's schema in the skeleton is the rewritten one
      too.
    - `Output.authored_schema` is the schema as written; `Output.output_schema` is the one after the ops.
    - `inline_refs@1`:
      - resolves local refs (RFC 6901: `~0`/`~1`, percent-decoding, array indices) and drops `$defs`/`definitions`;
      - lets keywords beside a `$ref` override the target's;
      - refuses refs outside the schema, refs to nothing, and recursion (a `ValidationError` on the output chunk).
    - On the sample's `management_plan_v1.json` it gives exactly the old code's inlined schema, key order included.
    - Not done: deciding per engine. That's a G10 lint: `$ref` in a skeleton bound to an engine not known to
      resolve refs, fed by G1 facts or what's learned about the remote engine.
    - Proposed amendments: `docs/chatddx.md` (glossary: Schema op) and `docs/factors.md` (Output).
    - Tests: `test_schema_ops_inline_refs`, `test_schema_ops_are_checked`.

### Cases
- **G11. Vignette sources.** [maybe fix remastered] (old D1)
  - `SourceCase.source` is a free string. Nothing defines what a source is or where it is (e.g. a directory of
    `.txt` files), and nothing fetches from one or lists it (id → fingerprint) for an importer.
  - `prepare_case` is missing (`docs/factors.md`, "Misc").
  - Nothing marks a source sensitive, so a fake-sensitive source can't be declared; `docs/clearance.md` is a stub.
  - Tests need a source too: the old tests' vignettes were inline strings (`test-cases.toml`).
  - **Fixed** (it follows the glossary: the inventory says where vignette sources are; D1 fake-sensitive, D2 raw
    bytes).
    - `chatddx.inventory` loads `[source.<name>]` tables (`kind = "directory"`, `path` relative to the inventory
      file, `suffix = ".txt"`, `sensitive = true` by default). It refuses any other table for now.
    - `Source` (`DirectorySource`, `MemorySource` for tests) has `ids()`, `fetch(id) -> bytes` and `cases()`, a
      `CaseInput` per vignette with no appendices. A directory source's ids are file stems, and ids that would
      leave the directory are refused.
    - `prepare_case(case, raw, get, layout, normalization, key=)` (`factors/cases.py`) is the old prepare_case
      without the IO. It fingerprints the raw bytes, reports `case.drift` on a mismatch (a warning: the run goes on
      with what was read), decodes strict UTF-8, cleans, and joins the appendices. It returns the fills and the
      observed fingerprint for `RunItem.vignette`.
    - `check_run` reports `case.drift` for recorded items.
    - Sensitivity is declared, not enforced: the hard block waits for clearance (`agents/wip-clearance.md`).
    - The sample vignettes stay out of this repo, being fake-sensitive. The sample inventory names them where they
      are, e.g. `[source.sample] path = "<old checkout>/src/chatddx/data/cases"`, so their ids are the old case
      names, `DutchFall10w` included.
    - Proposed amendments: `docs/chatddx.md` (per-item steps, drift, Findings, Inventory), `docs/factors.md`
      (Case, Misc), `docs/ledger.md` (RunStarted), `docs/clearance.md`.
    - Tests: `src/chatddx/inventory/test/test_inventory.py`, `test_prepare_case`, and the drifted item in
      `test_run_and_score_checks`.
- **G12. Case language.** [maybe fix remastered] (old D5)
  - `language` (79 en, 20 sv) has no home. A case's family carries only name, description, tags, owner,
    collaborators and deleted.
  - **Fixed** as a typed catalog entry, not the tag convention first suggested (`lang:sv`), so results can be split
    by language reliably.
    - In the old code the field was descriptive only: the portal displayed it, tracked changes to it and filtered
      by it. It never reached a prompt or a scorer, so it's catalog, not a factor.
    - `EntryField.LANGUAGE` holds a language tag (`^[a-z]{2,3}(-[A-Za-z0-9]{1,8})*$`, e.g. `sv`, `pt-BR`). It can
      be replaced but not removed, and `About.language` folds the latest.
    - Migration `0017-t2-catalog-language` mirrors the field and pattern. It also gives 0011's shape check a name
      (`entry_shape_check`), and was checked on an already migrated database.
    - Sample data, when the loader exists: a `language` entry on each case's family (79 `en`, 20 `sv`).
    - Proposed amendments: `docs/catalog.md`, `docs/store.md`, `docs/factors.md`.
    - Tests: `test_cases_have_a_language`, and the language cases in `test_entry_shapes`.
    - This covers the case side of the one-language ambition only; the request side is G18.
- **G13. Repairs.** [maybe fix remastered] (old D3, D4)
  - 13d317d holds both repair cases: a renamed vignette file (`DutchFall10w.txt` → `Dutchfall10w.txt`) and changed
    content (comments appended to `Dutchfall11w.txt` and `casesfromedn1.txt`).
  - `Catalog.adopt` refuses both, and the repair helpers and the API to append bindings aren't implemented
    (`docs/catalog.md`, "Cases").
  - **Fixed**, following `agents/wip-catalog.md` decision 7.
    - `Catalog.survey(source, listing)` sorts a source's vignettes into unchanged, changed, renamed, new and gone.
    - `Catalog.repair(family, by, id=… | vignette=…)` appends a binding, re-binds appendix threads, builds new
      cases, and on a rename re-keys expectation threads.
    - `Catalog.behind` reports a case whose family has a newer binding (`Behind.binding`).
    - Migration `0018-t2-catalog-bindings` makes consecutive bindings keep the id or the vignette.
    - Stress test: adopting the 99 vignettes at 7893656 and surveying the 13d317d checkout found exactly
      `Dutchfall11w` and `casesfromedn1` changed (the appended comments) and `DutchFall10w` → `Dutchfall10w`
      renamed. After three repairs all 99 were unchanged. This ran in a rolled-back transaction.
    - Still open:
      - Two identical vignettes under different ids: `survey` calls the second new, while `adopt` refuses it as a
        rename (`agents/wip-catalog.md`, known gaps).
      - Trials aren't re-keyed by any repair; `behind` proposes the new cases to their owners.
      - A repair rebuilds only the cases at the family's current binding, not older ones.
    - Proposed amendments: `docs/catalog.md` (Cases), `docs/store.md` (0018, Store API), `docs/factors.md`
      (Misc).
    - Tests: `test_renamed_vignettes_are_repaired`, `test_changed_vignettes_are_repaired`.
- **G14. Expectation data isn't validated.** [maybe fix remastered]
  - By design the factors don't check `Expectation.data` against its schema; the scorer does. Hand-edited sample
    data has no check before it lands, and scorers are deferred.

### Catalog
- **G17. Configurations are named, but nothing names them.** [maybe fix remastered] (found after `docs/catalog.md`
  and `docs/chatddx.md` became "a configuration is a *named* set of pinned factors")
  - `Catalog.create(digest, by, compilation=, forked_from=)` takes no name, and nothing ever requires a `name`
    entry on a thread. A configuration's skeleton thread, or a saved variation (a fork), can stay unnamed forever,
    and `heads(kind)` lists them next to named ones. Only removing a name is already ruled out
    (`Entry._shape`).
  - The docs have their own tension: "a variation is a configuration" (so, named), while "any digest can be run
    without a thread", so a variation being tried has no name until it's saved.
  - Options:
    - (a) `Catalog.create(…, name=)`, required for the kinds that stand for configurations (`skeleton`, perhaps
      `trial`): the thread, its first edit and its name are written in one transaction.
    - (b) The same, required for every thread kind, since threads are where names attach for everything but cases.
    - (c) Leave creation open, and have the portal, listings and `heads` treat unnamed threads as drafts.
  - The database can't require a name entry, just as it can't require a thread's first edit (`docs/store.md`,
    "Known gaps"); Python would enforce it.
  - Also to decide: what an unsaved variation is called in the docs, if "variation" now implies a name.

### Language
- **G18. One language per trial.** [maybe fix remastered] (follows G12)
  - The ambition, from the old `docs/backlog/language-slice.md` (chatddx-administration/chatddx, branch
    `new-datamodel`, never built): a trial runs in one language throughout. Its cases, the instructions, the
    guidance, every other text a chunk brings, and the expectations it's scored against are all Swedish or all
    English, never mixed. The research is Swedish, and what holds in English may not hold in Swedish.
  - The backlog's design was gettext-style translation files:
    - texts are written once in a source language, and a language variation is a catalog of translations
      (source text → translation);
    - a missing translation refuses the run (nothing is guessed, nothing mixes);
    - a catalog is versioned content, and a run names the catalog it read;
    - a translated case is a case of its own, with its own targets, that names what it translates
      (`translation_of`);
    - languages are compared by varying the language over paired cases.
  - It rejected parallel per-language variations (`ddx-sv`): nothing holds them to one another or to completeness.
  - **Not like Facts.** Facts write literal chunks before compilation and leave no record, by design, so that a
    corrected fact changes nothing stored. Translations need the opposite:
    - provenance, to find what to retranslate when a source text changes and to pair languages;
    - versioning as content, since they change what the model reads;
    - keys by source text per language, not by model.
  - **Take: translations as a recipe part, applied at compile time.**
    - A translations chunk with a source language, a target language, and source text → translation entries. The
      recipe references it as an optional part.
    - `compile_request` renders every chunk-supplied text through it, the way inserts are filled. A missing
      translation fails the compilation.
    - The skeleton stays literal translated text, and the `Compilation` records which translations produced it.
    - An edited translation makes the skeleton threads that used it behind (`Catalog.behind` already looks through
      recipes), so retranslation is a proposal.
  - **The two sides.**
    - The case side stays G12's `language` entry on the family: a vignette's language can only be labelled, not
      produced. Expectations follow their case.
    - The request side is the translations above. Labelling chunk or skeleton threads with `language` entries
      would be the rejected parallel variations.
  - Open, whichever way it's built:
    - **Texts compile doesn't own yet.** Few-shot examples could join the translations. The schema is harder: its
      descriptions are English the model reads (and sees when shown, G4), but translating property names or enum
      values (`high`, `Lab`) changes the answer's structure and breaks scoring.
    - **Per-case texts.** Appendices and expectation data are written per case by clinicians, so they're in the
      case's language by authorship, unchecked.
    - **A skeleton's language.** A same-language check needs it: either read from the compilation (a check that
      needs the store, unlike today's registry-only lints), or written as a `language` entry when a compiled
      skeleton thread is saved.
    - **Pairing translated cases.** A translated vignette is its own family; `translation_of` needs a
      family-to-family link the catalog doesn't have.
    - **Warning or refusal.** The backlog refused mixed runs; remastered's rule is "warnings, not crashes".
      Language mixing could become a second hard block next to clearance, or stay a lint.
    - **The source language of untranslated recipes.** A recipe without translations has the language its chunks
      were written in, which nothing declares today.

## Parked with the deferred work

### Engines and models
- Model file hashes (`ModelArtifact.files`) and the chat-template digest need an import script on the hosts.
- Hardware:
  - compute capability is missing from the old data;
  - `gpu_count` is missing (`docs/factors.md`);
  - CPU, RAM, location, GPU uuid and machine id have no home.
- Runtime:
  - Is `Runtime.closure` the vLLM package or the container toplevel?
  - malborg says vLLM 0.13.0 (guessed), but the docs say both hosts run 0.24.0.
- No split between args in the digest and recorded-only args (`performance`). `max-num-seqs` affects batching.
- Endpoints:
  - nothing maps an engine digest to a URL, and the World inventory has no code;
  - the hosts' served names must become the engine digest;
  - per-endpoint capacity (`max_jobs`), credentials and the API kind have no home.
- Fake vLLM.
- Model specs (family, size, quantization, context length, licence).

### Scorers
- Scoring code: the pattern matcher, `reciprocal_rank`, `first_mention` and `mentions`.
- Free-text parsing (G6).
- Aggregation (`mean`, `stderr`).
- A check that views agree with the output schema (old `prove`).
- One scorer per output shape (`plan`, `diagnoses`, `free-text`, `raw`), with the old view names as labels.

### The command
- Loaders per channel: a factor TOML loader with name references, a case importer from a source, a catalog writer,
  and the World inventory.
- Name lookup: no lookup by (kind, name, owner) in the catalog.
- Re-runs: re-seeding without duplicate threads (created / validated / updated).
- People: the `archive` person, and creating USER or requiring `chatddx person add` first.
- Sharing:
  - collaborator entries on archive threads;
  - the giftbag as forks;
  - families aren't per owner.
- The compiler's `Code` for `Compilation` records (old C8).
- `wipe-data`, which tier 2 rules out as a DELETE.
