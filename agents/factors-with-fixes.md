# Factors

A case vignette can be fed to an LLM in a myriad of ways: Instructions, sampling parameters, GPU, the weights and many more choices affect the final output. It's practically impossible to tweak each aspect individually, so we need to group them in meaningful and manageable categories, we call these categories factors.

There's a catch-22 to this because we need to know *how* some aspect affects output in order to group them effectively, but the *how* is what our research is all about, so we start with a flexible system of factors based on reasonable guesses and then refine iteratively.

This document describes each factor as they exist today, along with the mechanics of how factors interact when combined.

The factors provide the basic building blocks for chatddx's rig: An environment that can be provably reconstructed for scientific rigor.

## Not factors
Simply stated, if it doesn't affect output or scoring, it's not a factor, some examples follow:
- records and observations: These are specified in `ledger.md`.
- people, roles, authentication and authorization: These are specified in `identity.md`.
- names, labels, language, tags, descriptions, authors, owners, collaborators, version history: These are specified in the `catalog.md`.
- sensitivity and vetting parameters: These are specified in `clearance.md`.
- knowledge about models (reasoning levels, recommended sampling, output caveats, specs):
  these are model facts (`src/chatddx/facts/facts.py`, material in `agents/wip-facts.md` until a `facts.md`
  exists). They produce and check literal chunks at authoring, and no digest depends on them.

## In depth
In the code, a factor is immutable and identified by its digest. They reference each other by this digest, forming a graph. Each reference is typed (`Annotated[Digest, RefTo(kind, …)]`). From these types, `Registry.check` derives the graph and checks that every reference exists and has an allowed kind. Rules that span components, such as "a trial must use a generation skeleton", are `cross_check` hooks run by the same check.

The kinds are `model`, `engine.local`, `engine.remote`, the nine `chunk.*` kinds, `skeleton`, `appendix`, `case`, `trial`, `expectation_schema`, `expectation`, `scorer`, `judge`, `scoring`, `canary_set` and `tool`.

The suggested storage is one table for all kinds, `factor.component` (`digest`, `kind`, `v`, `canonical`, `doc`), plus `factor.component_ref` (`src`, `path`, `dst`, `kinds`), which holds one row per typed reference so that every reference gets a foreign key, including references inside lists and references that allow several kinds.

Note on foreign keys: `Store.add` writes one `factor.component_ref` row per `Component.refs()` site, and the foreign key sits on that table. The allowed kinds are stored in the row's kinds column and checked by a tier-2 trigger (see `docs/store.md` for postgres' integrity tiers).

Note on `x-ref`: `RefTo` puts `x-ref`: [allowed kinds] on each reference field when Pydantic generates a JSON Schema for a component. Nothing is consuming it right now and it may be dead weight if the portal doesn't need it.

## Index

### Case
- principal author: Clinicians
- defined in: `cases.py:CaseInput`

A case (kind `case`) names a vignette in the predefined source by source name and id (`SourceCase`), records the fingerprint of that vignette, and lists the appendices to append, in order.

An import script supplies the vignette fingerprint, taken over the raw vignette as fetched:
Sources are declared in the inventory, and `Source.cases()` builds a case, with no appendices,
for every vignette a source lists.

The vignette itself is sensitive by default, unstructured and not edited or stored: only its fingerprint is. The case's digest covers all three parts (source reference, fingerprint and appendix list), so the vignette is not its sole contributor, and the same source case with different appendices is a different case. Expectations are keyed by this digest, which lets appendices change the correct answer.

A changed vignette at the source has a new fingerprint. It therefore needs a new case, new appendices bound to the new fingerprint, and new expectations; whether a user or an automatic step re-binds them is open. Text cleanup is not part of the case: a trial chooses it (see "Trial").

`prepare_case(case, raw, get, layout, normalization)` (`cases.py:prepare_case`) turns a case and its raw vignette
into the slot fills for a request: the vignette cleaned by the trial's text-cleanup steps, and the appendices joined
by the skeleton's layout. It fingerprints the raw vignette and reports `case.drift` when that fingerprint differs
from the case's. A vignette fingerprinted with an HMAC key needs the same key id, or it raises.

### Appendix
- principal author: Clinicians
- defined in: `cases.py:Appendix`

An appendix (kind `appendix`) is a block of plain text, written in the portal and attached to one source case and one vignette fingerprint. It is the only way to add information to a vignette; anchored edits to the vignette were considered and ruled out as too complex. Appendices are not sensitive. They are stored and sent exactly as entered, and text cleanup does not apply to them.

A case lists its appendices in a pinned order that is part of the case's digest, and rejects any appendix bound to another source case or vignette (`CaseInput.cross_check`), so an appendix is never reused across source cases. It can, however, appear in several cases of the same source case, for example a case with a lab appendix and one without. At send time the appendices are joined into one text by the recipe's `AppendixLayout`, which is frozen into the skeleton (see "Rendering and runtime keys").

### Request
The request side is built in three layers: chunks are authored in the portal, a recipe selects one chunk per part, and the compiler turns a recipe into a frozen skeleton, which is what trials and judges reference. Chunks affect each other only in these pre-specified ways, all owned by the components:
- the output chunk's guidance goes where the instructions or the prompt insert it, or is appended to the instructions when neither does;
- the toolset's guidance does the same, and is appended after the output guidance;
- a toolset adds `tools` and `tool_choice` to the body, which changes how the output contract is expressed (see "Recipe");
- translations replace every text the other chunks bring (see "Translations");
- a passthrough key that another chunk also produces fails the compilation;
- greedy sampling drops `top_p`, `top_k` and `min_p`, and the seed is left out at send time.

There are no validation retries: the run stores the raw completion and parsing belongs to scoring. Transport retries are a separate matter, declared per run (`Execution.retries`) and counted per call (`ledger:Call.attempts`).

Each chunk is a component that fills one part of a recipe. None of them is a template:
text is plain text. The only runtime placeholders are the prompt's slots, filled by concatenation, so clinical text
is never interpreted; the compile-time placeholders are inserts:
`schema` in the output's guidance, and `output_guidance` and `tool_guidance` in the instructions or the prompt.

Compilation merges adjacent text, so without translations how a chunk splits its text never changes a skeleton.
Translations look up each text as the chunk holds it, and a prompt's literal segments aren't merged before that:
`("Case: ", "please\n", slot)` needs two entries where `("Case: please\n", slot)` needs one.

#### Instructions
- principal author: Researchers
- defined in: `request.py:Instructions`

Instructions (kind `chunk.instructions`) are text sent as the first message, with the role `system` or `developer`. Their cross-component effect is passive: the output chunk's guidance is appended to them, separated by a blank line, and becomes the whole message if there are no instructions. Instead, the text may insert the output guidance (`output_guidance`) anywhere, and likewise the toolset's guidance (`tool_guidance`).

An insert's `before` and `after` text appear only when there is guidance, which is what the old templates did with `{{#if …}}`.
A message that ends up empty is left out. Text-only instructions are a plain string.

#### FewShot
- principal author: Researchers
- defined in: `request.py:FewShot`

A few-shot chunk (kind `chunk.few_shot`) is a list of plain-text user/assistant example messages, placed after the instructions and before the prompt.

#### Prompt
- principal author: Researchers
- defined in: `request.py:Prompt`

A prompt (kind `chunk.prompt`) is the user message: a list of segments, each a literal string, a slot or an insert (`output_guidance` or `tool_guidance`), with a purpose of `generation` or `judge`. A generation prompt must contain the `case` slot and may contain `appendices`. A judge prompt must contain `completion` and may contain `case`, `appendices` and `expectation`. No slot may appear twice, and the prompt's purpose must match the recipe's.

Each guidance may be inserted in the instructions or in the prompt, not both, and a chunk may use each insert at most once.

#### Output
- principal author: Researchers
- defined in: `request.py:Output`

An output chunk (kind `chunk.output`) declares where the answer goes, through one of three contracts.
`native` sends a JSON schema as `response_format` (named `output`, strict).

`tool` asks for a function call whose parameters are the schema, named by the contract's `name`
and described by its optional `description` (the old `tool_description`). Without a toolset, `tool_choice` names
that function, which forces the call; with one, `tool_choice` is `required` (see "Recipe").

`text` constrains nothing and may carry a schema, for scoring and for the guidance to show.

Its optional guidance is the chunk's cross-component effect: it goes where the instructions or
the prompt insert it, or else it is appended to the instructions.

Guidance is text, or segments of text and the `schema` insert, which compilation replaces with the
output's schema as `json.dumps(indent=2, ensure_ascii=False)`, exactly once and only when there is a schema.
A `native` output that shows its schema is the old "native (shown)"; a `text` output that
shows it is the old "prompted". Adjacent text is merged, so text-only guidance is always a plain string.

An output may list schema ops (`schema_ops`). Compilation applies
them in order to the output's schema, and the result is the one schema the skeleton sends and shows. As with
text-cleanup steps, each op name pins one behavior. `inline_refs@1` replaces every local `$ref` (`#…`, JSON
Pointer with `~0`/`~1` and percent-decoding) with its target and drops the top-level `$defs` and `definitions`;
nested ones stay, with their refs inlined. Keywords beside a `$ref` override the target's. It refuses refs outside
the schema, refs to nothing and recursive schemas, which can't be inlined. Refs inside a dropped top-level
definition that nothing references are never resolved, so they are not refused either. Use it for engines that don't resolve `$ref` themselves; vLLM 0.24 does (`docs/vllm.md`).
Without ops, the schema is sent as authored.

#### Sampling
- principal author: Researchers
- defined in: `request.py:Sampling`

A sampling chunk (kind `chunk.sampling`) holds temperature, top-p, top-k, min-p, the presence, frequency and repetition penalties, stop sequences and `max_output_tokens`, which is sent under `max_completion_tokens` (default) or `max_tokens`. Its cross-component effect is canonicalization: with temperature 0 it drops top-p, top-k and min-p, so equivalent greedy chunks share a digest, and at send time no seed is added.

#### Reasoning
- principal author: Researchers
- defined in: `request.py:Reasoning`

A reasoning chunk (kind `chunk.reasoning`) holds the reasoning effort (`none` to `high`), `thinking_token_budget` and `chat_template_kwargs`.

#### Passthrough
- principal author: Researchers
- defined in: `request.py:Passthrough`

A passthrough chunk (kind `chunk.passthrough`) holds engine-specific body keys. It may not set runtime keys or output keys, and compilation fails if it sets a key another chunk also produces.

#### Tool
- principal author: Developers
- defined in: `request.py:Tool`

A tool (kind `tool`) is a function the model may call between turns: a name, a description, a JSON Schema for
its parameters, and the code that runs it (`Code`, plus an `entry_point` such as `chatddx_tools.web:search`). Its
code is pinned like a scorer's, because what it returns is what the model reads next: a new implementation is a
new tool, a new skeleton and a new trial. What it returned is still recorded per run (`docs/ledger.md`), since a
tool such as a web search answers differently from day to day.

#### Toolset
- principal author: Researchers
- defined in: `request.py:Toolset`

A toolset (kind `chunk.tools`) offers tools to the model: the tools, with distinct names, optional guidance
(plain text, the old `tool_guidance`), and `max_rounds` (default 5), the most tool rounds an item may take. Its
guidance goes where the instructions or the prompt insert `tool_guidance`, or else after the instructions,
following the output guidance.

#### Translations
- principal author: Researchers (translators)
- defined in: `request.py:Translations`

Translations (kind `chunk.translations`) map source texts to translations, the way gettext does: each entry is
a text exactly as a chunk brings it, whitespace included, and its translation. A recipe may reference one
(`Recipe.translations`), and compilation then passes every text the recipe brings through it: the instructions,
the few-shot messages, the prompt's literal segments, the output's guidance, the `before` and `after` of
inserts, a tool contract's description, the appendix layout, and the `title` and `description` strings of the
output schema. Property names and `enum`, `const`, `default` and `examples` are never translated, so the
answer's structure and its scoring don't change. Whitespace-only texts pass through. A text without a
translation fails the compilation, which lists every missing text: nothing is guessed and nothing mixes.
`texts(recipe, get)` lists, per part, what a translation needs.

Translations carry no language. A request's language is implicit in its text, and no language tag reaches the
model; the labels are catalog entries (`docs/catalog.md`). The skeleton is literal translated text, and its
compilation records the translations it read.

#### Recipe
- principal author: Researchers
- defined in: `request.py:Recipe`

A recipe is not a component. It holds a purpose, an appendix layout, required references to a prompt,
an output and a sampling chunk, and optional references to `instructions`, `few_shot`, `reasoning`,
`passthrough`, `translations` and `tools` chunks.

`compile_request` turns a recipe into a skeleton and fails on missing or wrongly typed chunks.
The recipe is kept only in the `Compilation` record, next to the compiler version (see `docs/ledger.md:Compilation`).
Because a trial references the skeleton rather than the recipe, the compiler's code is not a factor.

With a toolset, the body lists its tools, in order, as functions.
A `native` or `text` contract gets `tool_choice: auto`, and the model answers when it stops calling tools. A
`tool` contract's answer tool is listed last, with `tool_choice: required`: every turn calls a tool, and the
answer tool ends the item. An answer tool named like a toolset tool fails the compilation. Translations cover
the toolset's guidance and its tools' descriptions and parameter prose.

#### Skeleton
- principal author: none; normally produced by the compiler (`compile_request`)
- defined in: `request.py:Skeleton`

A skeleton (kind `skeleton`) is the frozen request, and the only component normally produced by code rather than people; trials and judges reference it. Nothing prevents hand-written skeletons (see "Possible design issues"). It is fully pre-rendered: the complete chat-completions body except for what is filled in at send time. It holds a purpose, the API (`chat.completions`), the messages as segment lists (so few-shot examples are already baked in as plain text), every other body key, the output contract and the appendix layout.

Its validation repeats the chunks' rules: slots must suit the purpose, greedy sampling is canonicalized,
a `native` contract needs `response_format` and no tools, a `tool` contract needs exactly one tool of
the declared name and description plus `tool_choice`, and a `text` contract may set no output keys.

For `native` and `tool`, the output schema is read from the body rather than stored twice.

A skeleton also references the tools it offers and holds `max_rounds`, both or
neither. With tools, a `native` contract needs `response_format`, `tools` and `tool_choice: auto`; a `tool`
contract needs its answer tool among the tools and `tool_choice: required`; a `text` contract needs `tools`
and `tool_choice: auto`. The tools the body offers, the answer tool aside, must be the referenced tools, by
name and in order (`Skeleton.cross_check`); their descriptions may be translated.

#### Rendering and runtime keys
`render(skeleton, model, seed, fills)` produces the wire body. It concatenates each message's segments, replacing each slot with its fill, then adds `model` (the engine digest for local engines, the requested model for remote ones), the messages and the skeleton's body.

It adds `seed` only when the skeleton is not greedy, and adds `return_token_ids: true` so the response carries the prompt's token ids (see `docs/ledger.md:Call`), unless it is called with `return_token_ids=False`.

The appendix fill is produced beforehand by the layout, which puts text before, between and after the appendices (by default a blank line before and between, nothing after) and yields an empty string when there are none.

The runtime keys (`model`, `messages`, `seed`, `stream`, `n` and `return_token_ids`, listed in `request.py: RUNTIME_KEYS`) may not be set by any chunk, skeleton or canary. `render` sets `model`, `messages`, `seed` and `return_token_ids`; `stream` and `n` are never sent. The wire body itself is transient: only its fingerprint is stored.

A response that calls tools gets another request: `tool_calls(response)` reads the calls,
and `next_request(body, response, results)` appends the assistant's message (content and tool calls;
its reasoning is left out) and one `tool` message per call, in the calls' order.
Everything else in the body stays, the seed included. The loop belongs to the runner: it ends when a
response calls no toolset tool, or after `max_rounds` rounds. A call naming no tool of the skeleton is
answered with an error.

### Engine
An engine is where requests are sent. Researchers pick engines for trials and judges.

#### Model artifact
- principal author: Ops
- defined in: `engine.py:ModelArtifact`

A model artifact (kind `model`) pins a model by its repo, a revision (linted unless it is a commit) and the sha256 of every file, collected by an import script. It is its own component, so several engines can share it.

#### Local engine
- principal author: Ops
- defined in: `engine.py:LocalEngine`

A local engine (kind `engine.local`) is a vLLM server we run. It declares its hardware (GPU, compute capability, VRAM, driver) and runtime (`vllm`, its version, the Nix closure path), references a model artifact, and requires the digest of its chat-template file.

Its raw `argv` and `env` are passed to vLLM, except the flags the start-up script owns (`--model`, `--served-model-name`, `--chat-template`, `--tokenizer`, `--revision`), which they may not set, whether spelled with dashes or underscores, since vLLM reads both. Nor may argv use `--config`: `--config FILE` pulls arguments from a YAML file the digest doesn't cover (and lints can't read), and `--config=FILE` is silently ignored (`docs/vllm.md` 8). Every argument belongs in argv.

Knowledge that changes between vLLM versions lives in lints rather than in these fields, and batch invariance is declared through `env` (for example `VLLM_BATCH_INVARIANT=1`). The served model name is the engine's digest, so every response names the cage it came from. `check_chat_template` warns when the template file doesn't match the declared digest or reads the current date. Hardware and runtime are part of the engine rather than components of their own.

They can therefore not be listed, picked or reused as standalone rows, for example in the portal or as Postgres foreign keys; the same GPU or closure is repeated in every engine that uses it, and asking which engines share a closure means scanning engines instead of following a reference.

#### Remote engine
- principal author: Ops
- defined in: `engine.py:RemoteEngine`

A remote engine (kind `engine.remote`) is an API we don't control. It declares only the API (`openai.chat`), the `base_url` and the requested model; its chat template is not pinned. Of the identifiers it returns, only the model name is checked today (see `docs/ledger.md:Run`). Canary probes at the start and end of a run apply to any engine and are planned per run (see `docs/ledger.md:RunStarted`), so they are not a property of remote engines.

### Trial
- principal author: Researchers
- defined in: `trial.py:Trial`

A trial (kind `trial`) is a scientific intent: a generation skeleton, an engine, the cases, the text-cleanup steps and the seeds. Cases and seeds are listed without duplicates. The seeds are explicit and user-defined; the portal can propose random 31-bit ones (`suggest_seeds`), which users are free to override. Each seed defines one replicate, identified by its index. Text cleanup is an ordered list of operations from a closed set in which each name pins one behavior (`newlines.lf@1`, `unicode.nfc@1`, `strip@1`, `blank_lines.collapse@1`; a changed behavior gets a new name), and it applies to the vignette only. Execution settings (order, concurrency, timeout, retries) are not part of the trial, so re-running a trial means a new run of the same digest. With greedy sampling no seed is sent, but the seeds still count toward the trial's digest (see "Possible design issues").

### Expectation schema
- principal author: Developers
- defined in: `scoring.py:ExpectationSchema`

An expectation schema (kind `expectation_schema`) is a JSON Schema describing the reference data a scorer consumes.
Its `$schema` names the draft it's written in, 2020-12 when absent.

### Expectation
- principal author: Clinicians
- defined in: `scoring.py:Expectation`

An expectation (kind `expectation`) is the reference data for one case, authored in the portal: the case's digest, the expectation schema's digest and the data. Because the key is the case digest, appendices included, the same source case can have different expectations under different appendices. Expectations are not sensitive.

Building an expectation doesn't validate its data against its schema. Lints check it (see "Linting"),
and the scorer decides what to do with data that fails.

### Scorer
- principal author: Researchers
- defined in: `scoring.py:Scorer`

A scorer (kind `scorer`) pins scoring code, written by developers. It declares that code (`Code`: distribution, version, revision), the expectation schema it consumes, an ordered list of views, ordered resource digests (such as synonym tables or ontology releases) and free parameters. Views and resources are referred to by position; their labels belong to `catalog`.

A view scores one part of an output against one part of an expectation. It holds a selector into each,
a metric name that only the scorer's code interprets, optional parameters, and optionally the judge it uses.
A selector is a JSON Pointer (RFC 6901), which picks at most one value, or a JSONPath query (RFC 9535)
from a pinned subset: member names (`.name`, `['name']`), indexes (`[0]`, `[-1]`), wildcards (`.*`, `[*]`),
and filters that test a relative path for existence (`[?@.critical]`) or compare it with a literal
(`[?@.critical == true]`, `!=`).

Within the subset every RFC 9535 implementation picks the same values. Over arrays they come in the same order,
but RFC 9535 leaves the order of an object's members open for wildcards and filters; `select` keeps the
document's order. Anything outside the subset (`..`, slices, other operators, functions) is refused. Selection keeps nulls, as RFC 9535 does.
A view may also split what its output selector picks (`split`), one op name per behavior as with text
cleanup: `lines@1` turns each string into one item per non-blank line, with a leading list marker (`-`, `*`, `•`,
`1.`, `1)`) removed, the old `lines` reader. Without a split, free text is one item, the old `whole`.
`View.output_items(answer)` and `View.expectation_items(data)` apply the view, so every scorer selects the same
way (`src/chatddx/factors/select.py`).

### Judge
- principal author: Researchers
- defined in: `scoring.py:Judge`

A judge (kind `judge`) is an LLM used as a metric: a judge-purpose skeleton, an engine and its own seeds. A judge skeleton must contain the `completion` slot and may use `expectation`, `case` and `appendices`. Judge requests go through the same rendering as generation requests, once per seed, and are recorded per score item (see `docs/ledger.md:JudgeCall`). Their scores are best-effort reproducible. Because a judge prompt can contain case text, judge engines fall under the same clearance hard block as generation engines.

### Scoring
- principal author: Researchers
- defined in: `scoring.py:Scoring`

A scoring (kind `scoring`) is what a score applies: a scorer plus the expectations it scores against. The expectations must all be in the scorer's schema, and a case may have at most one. The judges a scoring uses are those named by its scorer's views.

### Canary set
- principal author: Developers
- defined in: `trial.py:CanarySet`

A canary set (kind `canary_set`) is a list of fixed, non-sensitive probe requests. Each canary holds literal messages, body keys (no runtime keys) and an optional seed. A run names the canary set it uses (see `docs/ledger.md:RunStarted`). Canary sets are components because canary drift is detected by comparing the same set across runs (not implemented yet).

## Linting
Lints (`lint.py`) warn about settings that are valid but risky. They are plain functions over a registry, run on demand; nothing in the factor components stores their findings. Keeping them out of the data layer means knowledge that changes between vLLM releases can change without touching any factor. `lint(registry, digests)` applies one rule per kind. A model artifact whose revision is not a 40-character commit gets `model.revision`, because a branch or tag can move. A local engine whose closure is not a Nix store path gets `engine.closure`. A scorer whose code has no revision gets `scorer.revision`.

Expectation schemas and expectations are checked with the
`jsonschema` library. An expectation schema gets `expectation_schema.invalid` when its `$schema` names a draft
the library can't check, or when it breaks its draft's metaschema. An expectation gets `expectation.invalid`
when its data fails its schema; the message gives the most relevant error's JSON Pointer and how many more
there are. It gets `expectation.unchecked` when a `$ref` the data reaches can't be resolved: refs are resolved
within the schema only, and nothing is fetched. An expectation whose schema is invalid isn't checked, since the
schema's own finding covers it. `format` is an annotation, as 2020-12 has it, and isn't asserted.

Trials and judges are linted as a pair: their skeleton with their engine. On a local vLLM 0.24 engine, flags are
read as vLLM reads them, with `_` and `-` alike in their names, and the pair gets:
- `vllm.temperature_clamped` when the temperature is between 0 and 0.01, which vLLM raises to 0.01;
- `vllm.thinking_budget_refused` when the body sets `thinking_token_budget` and the engine has neither
  `--reasoning-parser` nor `--reasoning-config`;
- `vllm.tools_refused` when the body has tools and the engine lacks `--enable-auto-tool-choice` or
  `--tool-call-parser`;
- `vllm.native_tools_uncallable` when a `native` contract comes with tools, since the schema then constrains the
  whole answer;
- `vllm.grammar_before_reasoning` when a `native` or `tool` answer is constrained and the engine has no
  `--reasoning-parser`, so the model can't reason first: a warning when the skeleton asks for reasoning (or leaves
  it to a model that reasons by default), info when whether it reasons is unknown, nothing when reasoning is off.

On any other engine (a remote engine or another vLLM version), the trial or judge gets `schema.ref_unverified` when
its skeleton's `native` or `tool` schema has `$ref`: the engine isn't known to resolve it, and `inline_refs@1`
removes it.

`lint(…, facts=)` takes the model facts (`src/chatddx/facts/facts.py`). With them, a trial or judge whose model
has no facts gets `facts.missing` (info); otherwise it gets `facts.reasoning_unmatched` when the skeleton's
reasoning settings match none of the model's reasoning levels, `facts.budget_refused` when it sets a thinking
budget the model refuses, `facts.output_refused` when the model refuses its output contract, and
`facts.output_note` (info) when the facts carry a note about that contract.

`lint(…, languages=)` takes a function from a digest to its language, normally `Catalog.language_of`.
With it, a trial gets `language.mixed` when some of its cases are in another language than its request,
and `language.unknown` (info) when the request's language, or some cases', is unknown.
Judges aren't checked.

A scorer gets `view.unreachable` when a view's expectation selector can't pick anything
from documents that follow the expectation schema it consumes. The check errs towards reachable (unknown refs,
unconstrained schemas and unions pass), so a finding is certain.

For a linting reference, see `docs/findings.md:Factors`.

## Possible design issues

### Greedy sampling and seeds:
the seed isn't sent, but the trial's seeds still count toward its hash, so two otherwise identical greedy trials differ only in digest.

### Local engines have no endpoint, and runs don't record where calls went
  `LocalEngine` has no URL (`src/chatddx/factors/engine.py:LocalEngine`), so the runner needs a mapping from engine digest to
  URL that nothing defines yet. By the glossary it is an inventory fact (a location), not a catalog one. One engine
  digest may be served by several identical hosts, and one host serves different engines over time, so the mapping
  can't be part of the factor. The runner can check the binding before sending, because a local engine's
  `served_model_name` is its digest and `/v1/models` lists it. `RemoteEngine.base_url`, by contrast, is part of the
  digest (`engine.py:RemoteEngine`), so moving the same API to a new host makes a new engine. In both cases `Call`
  (`src/chatddx/ledger/ledger.py`) records no URL, so the ledger can't show which endpoint received case-derived
  content, which the clearance check may need.

### Misc
- **Canary drift is not checked.** No function compares canary outputs between phases or between runs. The old code had `compare_canaries`.
- **Judge engines are not verified by canaries** (reconciliation D5, option B, deferred).
- **Skeleton provenance.** Should a skeleton only be accepted with a compilation record? Hand-written ones (e.g. judge prompts) are possible today.
- **Metric names are free strings.** `View.metric` is only meaningful to the scorer code that `Scorer.code` pins; nothing checks that the code knows the name.
- Where the model name comes from (per-item step 6) is repeated in check_run, so it belongs on the engine as one method.
- Missing expectations aren't flagged. Neither the scoring nor check_score warns when a run's case has no expectation, or when a scored item has no expectation behind it.
- Judge calls aren't attested. Their returned model and prompt-token fingerprint aren't checked the way run items are.
- Greedy judges make extra seeds pointless. The test's judge skeleton is greedy, so no seed is sent. Several judge seeds would then send identical requests.
- Case-slot judges need the vignette again. A judge skeleton that uses the case slot makes the scorer fetch the sensitive vignette again and redo the trial's cleanup steps. `prepare_case` covers the cleanup; no helper fetches the vignette.
- No helper turns a canary into a request body (model name, seed, `return_token_ids`).
- `Scorer.resources` are called digests but typed as plain strings, so nothing checks their form.
- The closed set of text-cleanup operations has four members. The old code also had non-breaking spaces to spaces, zero-width character removal, Unicode line breaks to newlines and trailing-whitespace stripping; vignettes may need some of them.
- `Hardware` has no GPU count, so tensor-parallel engines can't be told apart by hardware (the old code had `gpu_count`).

## Proposed amendments
