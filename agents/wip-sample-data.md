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
- **G2. Reasoning levels.** [maybe fix remastered] (old C2)
  - The old intent levels were `default, off, on, minimal, low, medium, high, xhigh` and `budget`. They were
    translated per model, and a model could refuse a level with a reason.
  - `chunk.reasoning` holds only literal body values (`effort`, `thinking_token_budget`, `chat_template_kwargs`).
  - The same tension as G1: a level needs a model to become a body, and a skeleton has none.
- **G3. Placing chunk-supplied text.** [maybe fix remastered] (old C4)
  - The only composition `compile_request` knows is output guidance appended to the instructions after a blank line
    (`src/chatddx/factors/request.py:compile_request`).
  - The old instruction placed output guidance, the schema prompt and tool guidance anywhere in the system or user
    message, conditionally (`{{#if …}}`).
  - Remastered needs a mechanism with the same reach. One candidate is compile-time slots: placeholders that
    `compile_request` fills from the recipe's chunks. They would sit alongside the send-time slots that `render`
    fills.
- **G4. Showing the schema in the prompt.** [maybe fix remastered] (old C5)
  - "native (shown)" sends `response_format` and also shows the schema as text. "prompted" shows it and enforces
    nothing.
  - Neither can be expressed except by pasting the schema into `guidance` by hand, which drifts from `json_schema`.
    The old code rendered it with `json.dumps(indent=2, ensure_ascii=False)` into `{{schema}}`.
  - It probably rides on G3.
- **G5. The tool contract has no description.** [maybe fix remastered] (old C5)
  - `ToolOutput` holds only a name, and `compile_request` emits the function without a description. The old
    `coercion.tool` had `tool_description` and the name `final_result`.
  - A defaulted `description` field is additive, so stored digests keep their values.
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
    - a place for tool guidance (G3);
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
- **G10. Skeleton × engine compatibility.** [maybe fix remastered]
  - The old facts refused combinations: gpt-oss can't turn reasoning off, harmony doesn't render `response_format`,
    vLLM ignores `tool_choice = required` for gpt-oss, and native/tool modes need parsers in the engine's argv.
  - `lint.py` knows only the temperature clamp, and nothing checks a trial's skeleton against its engine. Depends
    on G1.

### Cases
- **G11. Vignette sources.** [maybe fix remastered] (old D1)
  - `SourceCase.source` is a free string. Nothing defines what a source is or where it is (e.g. a directory of
    `.txt` files), and nothing fetches from one or lists it (id → fingerprint) for an importer.
  - `prepare_case` is missing (`docs/factors.md`, "Misc").
  - Nothing marks a source sensitive, so a fake-sensitive source can't be declared; `docs/clearance.md` is a stub.
  - Tests need a source too: the old tests' vignettes were inline strings (`test-cases.toml`).
- **G12. Case language.** [maybe fix remastered] (old D5)
  - `language` (79 en, 20 sv) has no home. A case's family carries only name, description, tags, owner,
    collaborators and deleted.
- **G13. Repairs.** [maybe fix remastered] (old D3, D4)
  - 13d317d holds both repair cases: a renamed vignette file (`DutchFall10w.txt` → `Dutchfall10w.txt`) and changed
    content (comments appended to `Dutchfall11w.txt` and `casesfromedn1.txt`).
  - `Catalog.adopt` refuses both, and the repair helpers and the API to append bindings aren't implemented
    (`docs/catalog.md`, "Cases").
- **G14. Expectation data isn't validated.** [maybe fix remastered]
  - By design the factors don't check `Expectation.data` against its schema; the scorer does. Hand-edited sample
    data has no check before it lands, and scorers are deferred.

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
