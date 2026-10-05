# Factors

An LLM can be given a case vignette in countless ways. The instructions, the sampling settings, the GPU, the model
weights and many other choices all affect what comes out. Varying each choice on its own is impractical, so we
group them into a manageable number of categories. We call these categories factors.

There is a catch-22: grouping well requires knowing *how* each choice affects the output, and that is exactly what
the research is trying to find out. So we start from a flexible set of factors based on reasonable guesses and
refine it as we learn.

Factors are the building blocks of the chatddx rig: an environment that can be rebuilt exactly, and shown to be the
same, for scientific rigor. This document describes each factor as it exists today and how factors combine. Most of
it is about pinned factors, the ones stored as components (see "What a factor is").

Code paths are relative to `src/chatddx/factors/` unless they start with `src/` or `docs/`. Principal authors are
the roles in `src/chatddx/core/identity.py:Role`. The package imports no other chatddx package; the model facts,
the catalog, the ledger and the store build on it.

## What a factor is

A *factor* is anything that can change an output or its score: what the research varies, holds fixed, or has to
account for.

A *component* is how most factors are stored: an immutable set of parameters identified by a hash of its content,
its *digest*. Changing a parameter gives a new component with a new digest; nothing is edited in place. Components
refer to each other by digest, so a trial's digest pins every component the trial depends on.

Being a factor and being a component are separate questions. A factor is held in one of three ways.

1. **Pinned**: chosen in advance and fixed by digest: a trial or a scoring, and every component it references.
   Prompts, sampling, engines, model weights, cases, seeds, expectations and scorers are pinned.
2. **Recorded**: set or known when a run or a score starts, and written into its start record, which is immutable
   and sealed with the rest of its log (`docs/ledger.md`):
   - the execution settings, such as order and concurrency (`RunStarted.execution`, `ScoreStarted.execution`, see
     "Execution").
   - the version of the rig that sent the requests (`RunStarted.rig`).
   - the scorer code that actually ran (`ScoreStarted.scorer_code`). Set beside the code the scorer pins
     (`Scorer.code`), it shows whether what ran is what should have run, and `check_score` warns when they differ
     (`docs/ledger.md`, "Score").
3. **Observed**: set by nobody, and only detected afterwards. These are the parts of the world outside our control:
   a remote engine that changes without notice, a tool such as a web search that answers differently from day to
   day, a vignette edited at its source, GPU arithmetic that isn't deterministic. Fingerprints, returned model
   names, drift findings and canaries reveal them (`docs/ledger.md`, `docs/findings.md`).

Canary sets are components but not factors. They are *instruments*: fixed probe requests that measure observed
factors without changing any output. They are components so that the same set can be compared across runs (see
"Canary sets").

Compilations are components but not factors either. They are *provenance*: each says which recipe and compiler
produced a skeleton (see "Compilation"). No component refers to one, so they change no digest and no output.

A trial's outputs therefore depend on the trial's pinned factors and on its run's recorded and observed factors.
Two runs of the same trial are interchangeable only when those agree too (see "Open design issues").

### Not factors
- Records of what happened (runs, scores, calls): `docs/ledger.md`. They hold the recorded factors
  and the evidence of the observed ones, but they aren't factors themselves.
- People, roles, authentication and authorization: `docs/identity.md`.
- Names, labels, languages, tags, descriptions, owners, collaborators and version history: `docs/catalog.md`.
- Sensitivity and vetting: `docs/clearance.md`.
- Knowledge about models (reasoning levels, recommended sampling, output caveats, specs): the model facts
  (`src/chatddx/facts/facts.py`). Facts help write chunks and check them (`src/chatddx/facts/lint.py`), but no
  digest depends on them.
- Instruments: canary sets.
- Provenance: compilations.

## How components work

Some objects live inside components or records without being components themselves: recipes (in compilations), views
(in scorers), canaries (in canary sets) and execution settings (in run records).

### Canonical form and digest
A component is written out in one fixed way, its *canonical form*, so that equal components always give the same
bytes. The canonical form is JSON in which
- field names are sorted;
- fields equal to their default are left out (`kind` is always kept), so adding a field whose default keeps the
  old behavior leaves every existing digest unchanged;
- a `v` field holds the type's schema version.

JSON data inside a component, such as a schema, an expectation's data or a canary's messages, keeps its key order,
because a schema's property order is part of what a model reads. The top-level keys of *settings* (request bodies,
`chat_template_kwargs`, `params`, `env` and translation entries) are sorted instead, so equivalent settings share a
digest; the values inside them keep their order. A model artifact's files are sorted by path.

The digest is `sha256:` followed by the SHA-256 of the canonical bytes (`base.py:Component.digest`). Components
are told apart, and referred to, by their digests.

A copy made with `model_copy(update=…)` is built anew: it is validated, so it follows its kind's rules, and it
gets its own canonical form and digest.

### Versions
Each component type has a schema version (`base.py:Component.schema_version`, 1 for every type today). Adding a
field with a backward-compatible default needs no new version; changing an existing field's meaning or default
does. Bytes written with another version are refused rather than guessed at (`base.py:parse_component`).

### Kinds
Every component has a `kind` that says what it is: `model`, `engine.local`, `engine.remote`, the nine chunk kinds
(`chunk.instructions`, `chunk.few_shot`, `chunk.prompt`, `chunk.output`, `chunk.sampling`, `chunk.reasoning`,
`chunk.passthrough`, `chunk.toolset` and `chunk.translations`), `tool`, `skeleton`, `appendix`, `case`, `trial`,
`expectation_schema`, `expectation`, `scorer`, `judge`, `scoring`, `canary_set` and `compilation`.

### References and checks
A reference is a digest field typed with the kinds it may point to (`Annotated[Digest, RefTo(kind, …)]`). From
these types, `bundle.py:Registry.check` finds every reference and checks that it exists and points to an allowed
kind. Rules that need several components, such as "a trial must use a generation skeleton", are `cross_check`
hooks. The same check runs them, for each component whose own references hold.

### Errors and findings
Problems come in two strengths.

An *error* stops the work.
- A component that breaks its own rules can't be built: pydantic raises `ValidationError`. Examples are duplicate
  seeds, or a flag the start-up script owns in an engine's `argv`.
- A problem that takes other components to see raises `base.py:StructuralError`. Examples are a missing or wrongly
  typed reference, a failed cross-check, a digest that doesn't match its bytes, and an unknown kind or version.

A *finding* (`base.py:Finding`) is a warning, or an info, about something valid but risky, or about something
observed that doesn't match what was declared. It has a level, a code, a message and, optionally, the digest it
concerns. Findings never stop the work. `docs/findings.md` lists every code.

### Registries and bundles
A `Registry` (`bundle.py`) holds components by digest, in memory. `Registry.add_raw` accepts stored bytes only if
they hash to the given digest, and `Registry.closure(roots)` lists the roots and everything they reference.

A `Bundle` (`bundle.py`) is a self-contained package that can be verified offline: some root digests, the
canonical text of everything they reference, and the version of the code that wrote it (`generator`).
`Bundle.load()` verifies every digest, rebuilds the registry and checks it. When today's code would write a
component differently from its stored bytes, it reports `bundle.recanonicalized`; the stored bytes still decide the
digest.

### Fingerprints
A fingerprint (`base.py:Fingerprint`) is a hash of content that must not be stored, such as a vignette. It is plain
SHA-256 by default, or HMAC-SHA-256 with a `key_id` naming the key (`Fingerprint.of(data, key=(key_id, secret))`).

### Code
`Code` (`base.py`) names a piece of code by its distribution, version and, optionally, revision. Scorers and tools
pin their code this way, and bundles name the code that wrote them.

### Storage
Components are stored in one table for all kinds, plus one row per reference so that every reference gets a
foreign key (`docs/store.md`).

## Terms

| Term | Meaning |
| --- | --- |
| Factor | Anything that can change an output or its score. |
| Pinned, recorded, observed | The three ways a factor is held: fixed in advance by digest, written into a run's or a score's start record, or only detected afterwards. |
| Component | An immutable set of parameters identified by its digest. Pinned factors are held in components. |
| Digest | `sha256:` followed by the hash of a component's canonical form. Components are told apart and referred to by it. |
| Instrument | A component that measures without being a factor: canary sets. |
| Provenance | A component that says how another was made, without being a factor: compilations. |
| Run | One execution of a trial: its requests, responses and records (`docs/ledger.md`). |
| Vignette | The clinical text of one case at its source. Sensitive and never stored; known by its source, its id there and its fingerprint. |
| Case | A vignette plus the appendices sent with it. |
| Appendix | Extra plain text written for one vignette. |
| Chunk | A component that fills one part of a request. |
| Recipe | The choice of chunks a request is compiled from. Not a component. |
| Skeleton | The compiled, frozen request. |
| Slot | A placeholder filled at send time: `vignette`, `appendices`, `completion` or `expectation`. |
| Insert | A placeholder filled at compile time: `schema`, `output_guidance` or `tool_guidance`. |
| Segment | One piece of a text: a literal string, a slot or an insert. |
| Purpose | Whether a request writes an answer (`generation`) or grades one (`judge`). It decides which slots are allowed. |
| Contract | How the answer is constrained: `native`, `tool` or `text`. |
| Guidance | Text that tells the model how to answer, or how to use its tools. |
| Text cleanup | Named steps that tidy a vignette before it is sent. |
| Seed, replicate | A seed fixes the sampler's randomness. Each seed of a trial is one replicate, identified by its position. |
| Engine | Where requests go: a vLLM server we run, or a remote API. |
| Trial | The pinned factors of one experiment: a skeleton, an engine, cases, text cleanup and seeds. |
| Expectation | The reference answer for one case. |
| View | One comparison a scorer makes between part of an output and part of an expectation. |
| Judge | An LLM used to grade outputs. |
| Canary | A fixed, non-sensitive probe request. |
| Inventory | Files written by ops that say where things are. Today they locate vignette sources; engines, model files and chat templates are to follow. |
| Portal | The web interface where components are written and runs are watched. |

## Cases

### Case
- principal author: Clinicians
- defined in: `cases.py:Case`

A case (kind `case`) is a vignette plus the appendices to send with it, in order. `cases.py:Vignette` names the
vignette by the source it comes from, its id there, and the fingerprint of its raw bytes as fetched. The source
gives the id; for a directory of files it is the file's name without its suffix. Sources are declared in the
inventory and hand over vignettes as UTF-8 text (see "Preparing a case"). `Source.cases()`
(`src/chatddx/inventory/sources.py`) builds one case, without appendices, for each vignette a source lists.

The vignette itself is sensitive by default, unstructured, and never edited or stored: only its fingerprint is. A
case's digest covers the vignette and the appendix list, so the same vignette with different appendices is a
different case. Expectations are keyed by the case's digest, which lets appendices change the correct answer.

When a vignette changes at its source, its fingerprint changes, and when it is renamed there, its id does. Either
way that needs a new case, new appendices bound to the new vignette, and new expectations; the catalog tells a
renamed vignette (same fingerprint, new id) from a changed one. Whether a person or an automatic step rebinds them
is open; the catalog has the tools for it (`docs/catalog.md`).

Text cleanup is not part of the case. The trial chooses it (see "Trial").

### Appendix
- principal author: Clinicians
- defined in: `cases.py:Appendix`

An appendix (kind `appendix`) is a block of plain text, written in the portal and bound to one vignette. It is the
only way to add information to a vignette; edits anchored inside the vignette were considered and ruled out as too
complex. Appendices are not sensitive. They are stored and sent exactly as written, and text cleanup doesn't touch
them.

A case lists its appendices in a fixed order that is part of its digest, and refuses an appendix bound to another
vignette (`Case.cross_check`). So an appendix is never reused across vignettes, but it can appear in several cases
of the same vignette: for example a case with a lab appendix and one without.

At send time the appendices are joined into one text by the skeleton's appendix layout (see "Rendering").

### Text cleanup
- defined in: `cases.py:CleanupOp`, `cases.py:clean`

Text cleanup is an ordered list of steps from a closed set. Each step's name pins one behavior; a changed behavior
gets a new name.
- `newlines.lf@1` turns `\r\n` and `\r` into `\n`.
- `unicode.nfc@1` applies Unicode NFC normalization.
- `strip@1` removes whitespace at the start and the end.
- `blank_lines.collapse@1` turns two or more blank lines in a row into one. A line with only spaces or tabs counts
  as blank.

Cleanup applies to the vignette only, after it has been fingerprinted.

### Preparing a case
- defined in: `cases.py:prepare_case`

`prepare_case(case, raw, get, layout, cleanup)` turns a case and its raw vignette into the slot fills for a request:
- `vignette`: the raw bytes, decoded as UTF-8 and cleaned;
- `appendices`: the appendices, joined by the layout.

UTF-8 is the contract with sources. A byte-order mark at the start is part of the encoding, so decoding drops it
and it never reaches the model. Bytes that aren't UTF-8 raise a `StructuralError` naming the vignette.

It also fingerprints the raw bytes and reports `case.drift` when they don't match the case's fingerprint. A vignette
fingerprinted with an HMAC key needs the same key id, or it raises.

## Requests

### Overview
A request is built in three layers:
1. Chunks are written in the portal, one component per part of the request.
2. A recipe picks one chunk per part.
3. The compiler turns the recipe into a skeleton: the frozen request that trials and judges reference.

Nothing in a request is a template: text is plain text. There are two kinds of placeholder, and nothing else is
interpreted:
- inserts, filled at compile time;
- slots, filled at send time by plain concatenation, so clinical text is never interpreted.

There are no validation retries: a run stores the raw completion, and parsing it belongs to scoring. Transport
retries are a separate matter, declared per run (`trial.py:Execution`) and counted per call (`docs/ledger.md:Call`).

### How chunks affect each other
Chunks affect each other only in these fixed ways, all decided in code:
- The output chunk's guidance goes where the instructions or the prompt insert it (`output_guidance`). Otherwise it
  is appended to the instructions after a blank line, and it becomes the whole first message when there are no
  instructions.
- The toolset's guidance does the same through `tool_guidance`, and is appended after the output guidance.
- A toolset adds `tools` and `tool_choice` to the request, which changes how the output contract is expressed (see
  "Recipe and compilation").
- Translations replace every text the other chunks bring (see "Translations").
- Greedy sampling (temperature 0) drops `top_p`, `top_k` and `min_p`, and no seed is sent.

Compilation merges adjacent text, so without translations, how a chunk splits its text never changes the skeleton.
With translations it can: each text is looked up as the chunk holds it, and a prompt's literal segments are not
merged first. `("Case: ", "please\n", slot)` needs two translation entries where `("Case: please\n", slot)` needs
one.

### Instructions
- principal author: Researchers
- defined in: `request.py:Instructions`

Instructions (kind `chunk.instructions`) are the first message, with the role `system` (the default) or
`developer`. The text is a plain string, or segments of strings and the inserts `output_guidance` and
`tool_guidance`, each used at most once.

An insert has optional `before` and `after` text, which appear only when there is guidance to insert. A first
message that ends up empty is left out. Text without inserts is stored as one plain string.

### Few-shot
- principal author: Researchers
- defined in: `request.py:FewShot`

A few-shot chunk (kind `chunk.few_shot`) is a list of example messages, each from the `user` or the `assistant`,
in plain text. They go after the instructions and before the prompt.

### Prompt
- principal author: Researchers
- defined in: `request.py:Prompt`

A prompt (kind `chunk.prompt`) is the user message: a list of segments, each a literal string, a slot or an insert
(`output_guidance` or `tool_guidance`). Its purpose is `generation` (the default) or `judge`, and it must match the
recipe's.
- A generation prompt must contain the `vignette` slot, and may contain `appendices`.
- A judge prompt must contain `completion`, and may contain `vignette`, `appendices` and `expectation`.

No slot may appear twice. Each guidance may be inserted in the instructions or in the prompt, not both.

### Output
- principal author: Researchers
- defined in: `request.py:Output`

An output chunk (kind `chunk.output`) says what shape the answer has and how that shape is enforced. Its `contract`
is one of:
- `native`: the schema is sent as `response_format` (named `output`, strict), and the engine constrains the answer
  to it.
- `tool`: the answer is a call to a function whose parameters are the schema. The contract names the function and
  may describe it. Without a toolset, `tool_choice` names the function, which forces the call; with a toolset it is
  `required` (see "Recipe and compilation").
- `text`: nothing is constrained.

The schema always goes in `json_schema`. It is required for `native` and `tool`, and optional for `text`, where it
serves scoring and can be shown to the model.

Optional guidance tells the model how to answer. It is plain text, or segments of text and the `schema` insert,
which compilation fills with the output's schema as `json.dumps(indent=2, ensure_ascii=False)`. Only an output with
a schema can show it, and only once.

An output may list schema operations (`schema_ops`), applied in order to its schema. The result is the one schema
the skeleton both sends and shows. As with text cleanup, each name pins one behavior. There is one today:
- `inline_refs@1` replaces every local `$ref` (`#…`, a JSON Pointer with `~0`/`~1` and percent-decoding) with what
  it points to, and drops the top-level `$defs` and `definitions`; nested ones stay, with their refs inlined.
  Keywords beside a `$ref` override the target's. It refuses refs outside the schema, refs to nothing, and
  recursive schemas, which can't be inlined. Refs inside a top-level definition that nothing uses are dropped
  without being checked. Use it for engines that don't resolve `$ref` themselves; vLLM 0.24 does
  (`docs/vllm.md`).

Without operations, the schema is sent as written.

### Sampling
- principal author: Researchers
- defined in: `request.py:Sampling`

A sampling chunk (kind `chunk.sampling`) holds the temperature, `top_p`, `top_k`, `min_p`, the presence, frequency
and repetition penalties, stop sequences, and `max_output_tokens`. The last is sent as `max_completion_tokens` (the
default) or `max_tokens`, as `max_tokens_key` says. Whatever a chunk leaves out is left to the server.

With temperature 0 the chunk is greedy: it drops `top_p`, `top_k` and `min_p`, so equivalent greedy chunks share a
digest, and no seed is sent.

### Reasoning
- principal author: Researchers
- defined in: `request.py:Reasoning`

A reasoning chunk (kind `chunk.reasoning`) holds the reasoning effort (`none`, `minimal`, `low`, `medium` or
`high`, sent as `reasoning_effort`), a `thinking_token_budget` and `chat_template_kwargs`. A recipe without one
leaves reasoning to the model.

### Passthrough
- principal author: Researchers
- defined in: `request.py:Passthrough`

A passthrough chunk (kind `chunk.passthrough`) holds engine-specific request keys, such as vLLM's `min_tokens` or
`ignore_eos`. It may not set (`request.py:Passthrough`):
- runtime keys (see "Rendering");
- output keys: `response_format`, `tools` and `tool_choice`;
- any key the sampling or reasoning chunks manage (`request.py:MANAGED_KEYS`), whether the recipe sets it or not.
  So a passthrough can't make a request greedy behind the sampling chunk's back, or add `max_tokens` beside
  `max_completion_tokens`;
- vLLM keys that go around other factors (`request.py:BYPASS_KEYS`): `chat_template` would replace the engine's
  pinned template for the request, `structured_outputs` would constrain the answer outside the output contract, and
  `return_prompt_text` and `prompt_logprobs` would put the prompt's text, and so the case's, in the stored response.

Since no other chunk can produce a passthrough key, a passthrough never clashes with the rest of the recipe.

### Tool
- principal author: Developers
- defined in: `request.py:Tool`

A tool (kind `tool`) is a function the model may call between turns: a name, a description, a JSON Schema for its
parameters, and the code that runs it (`Code`, plus an `entry_point` such as `chatddx_tools.web:search`). Its code
is pinned like a scorer's, because what it returns is what the model reads next: a new implementation is a new
tool, so a new skeleton and a new trial. What a tool returned is still recorded per run (`docs/ledger.md`), since a
tool such as a web search can answer differently from day to day: an observed factor.

### Toolset
- principal author: Researchers
- defined in: `request.py:Toolset`

A toolset (kind `chunk.toolset`) offers tools to the model. It holds the tools, with distinct names; optional
guidance, in plain text; and `max_rounds` (5 by default), the most tool rounds one item may take. Its guidance goes
where the instructions or the prompt insert `tool_guidance`, or else after the instructions, following the output
guidance.

### Translations
- principal author: Researchers (translators)
- defined in: `request.py:Translations`

Translations (kind `chunk.translations`) map source texts to their translations, the way gettext does. Each entry
is a text exactly as a chunk holds it, whitespace included, and its translation.

A recipe may reference one, and compilation then passes every text the recipe brings through it:
- the instructions, the few-shot messages and the prompt's literal segments;
- the output's guidance, the `before` and `after` of inserts, and a tool contract's description;
- the appendix layout;
- the toolset's guidance and its tools' descriptions;
- the `title` and `description` strings of the output schema and of the tools' parameters.

Property names, and the values of `enum`, `const`, `default` and `examples`, are never translated, so the answer's
structure and its scoring don't change. Whitespace-only texts pass through unchanged. A text without a translation
fails the compilation, which lists every missing text: nothing is guessed, and languages never mix.
`texts(recipe, get)` lists, part by part, the texts a translation needs.

Translations carry no language. A request's language is implicit in its text, and no language tag reaches the
model; language labels are catalog entries (`docs/catalog.md`). The skeleton holds the translated text, and its
compilation keeps the recipe, which names the translations.

### Recipe and compilation
- principal author: Researchers
- defined in: `request.py:Recipe`, `request.py:compile_request`

A recipe is not a component. It holds:
- a purpose and an appendix layout;
- required references to a prompt, an output and a sampling chunk;
- optional references to `instructions`, `few_shot`, `reasoning`, `passthrough`, `translations` and `toolset`
  chunks.

`compile_request(recipe, get)` turns a recipe into a skeleton. It fails on a missing or wrongly typed chunk, a
prompt whose purpose differs from the recipe's, a missing translation, a guidance inserted in both the instructions
and the prompt, or an answer tool named like one of the toolset's tools.

The request body it builds holds the sampling, reasoning and passthrough keys, plus whatever the contract needs:
- `native`: `response_format`;
- `tool`: the answer function in `tools`, and a `tool_choice` that names it;
- `text`: nothing, since the schema travels on the skeleton's contract instead.

With a toolset, the body lists the toolset's tools, in order, as functions. A `native` or `text` contract then gets
`tool_choice: auto`, and the model answers when it stops calling tools. A `tool` contract's answer function is
listed last, with `tool_choice: required`: every turn calls a tool, and calling the answer tool ends the item.

The recipe is kept only in the compilation, next to the compiler's version (see "Compilation"). Trials reference
the skeleton, not the recipe, so the compiler's code is not a separate factor: whatever it did is frozen in the
skeleton.

### Compilation
- principal author: none; written by the compiler
- defined in: `request.py:Compilation`

A compilation (kind `compilation`) says that a recipe compiled to a skeleton. It holds the recipe, the skeleton's
digest and the version of the compiler's code (`compiler`). It is the only place a recipe is kept, and so the
lineage from the chunks written in the portal to the frozen request.

A compilation is provenance, not a factor: no component refers to it, so it changes no digest and no output.
Trials and judges reference the skeleton, which holds whatever the compiler did. The same recipe compiled by the
same compiler always gives the same compilation. Different recipes can give the same skeleton (see "How chunks
affect each other"), so a skeleton may have several compilations, and a hand-written one has none.

Its references, the recipe's chunks and the skeleton, are checked like any component's. A bundle carries a
compilation when it is one of the roots, and the chunks then come with it, so the bundle shows how its skeleton was
made. Nothing checks that the recipe compiles to the skeleton.

### Skeleton
- principal author: none; normally produced by `compile_request`
- defined in: `request.py:Skeleton`

A skeleton (kind `skeleton`) is the frozen request that trials and judges reference. It is the only component
normally produced by code rather than people, though nothing prevents writing one by hand (see "Open design
issues").

It is fully pre-rendered: the whole chat-completions body except what is filled in at send time. It holds a
purpose, the API (`chat.completions`), the messages as lists of segments (few-shot examples already baked in as
plain text), every other body key, the output contract and the appendix layout. With a toolset, it also references
the tools it offers and holds `max_rounds`.

Its own checks repeat the chunks' rules, so a hand-written skeleton meets them too:
- the body sets no runtime keys;
- the messages use the slots their purpose requires and none it forbids, each at most once;
- greedy sampling is canonicalized;
- the body's `tools`, if any, is a list of functions with distinct names;
- a `native` contract needs `response_format` with a schema object;
- a `tool` contract needs its function, with the declared description and a schema object as its parameters, plus
  `tool_choice`; without a toolset it must be the only function;
- a `text` contract sets no output keys;
- tools and `max_rounds` come together. With them, `native` needs `tool_choice: auto` beside `response_format`,
  `tool` needs `tool_choice: required`, and `text` needs `tool_choice: auto`.

The functions the body offers, apart from the answer function, must be the referenced tools, by name and in order
(`Skeleton.cross_check`). Their descriptions may differ, since they may be translated.

For `native` and `tool`, the output schema is read from the body rather than stored twice (`Skeleton.output_schema`).

### Rendering
- defined in: `request.py:render`

`render(skeleton, model=, seed=, fills=)` produces the request that is sent (the wire body):
1. Each message's segments are joined, with every slot replaced by its fill.
2. `model`, the messages and the skeleton's body are added. The caller passes the model: the engine's digest for a
   local engine, or the configured model for a remote one.
3. `seed` is added unless the skeleton is greedy.
4. `return_token_ids: true` is added, so the response carries the prompt's token ids (`docs/ledger.md:Call`),
   unless `render` is called with `return_token_ids=False`.

The `appendices` fill comes from the appendix layout (`AppendixLayout`), which puts text before, between and after
the appendices (by default a blank line before and between, nothing after) and gives an empty string when there are
none.

The runtime keys `model`, `messages`, `seed`, `stream`, `n` and `return_token_ids` (`request.py:RUNTIME_KEYS`) may
not be set by any chunk, skeleton or canary. `render` sets four of them; `stream` and `n` are never sent. The wire
body itself is never stored, only its fingerprint.

### Tool rounds
- defined in: `request.py:tool_calls`, `request.py:next_request`

A response that calls tools gets another request. `tool_calls(response)` reads the calls, and
`next_request(body, response, results)` appends the assistant's message (its content and tool calls, without its
reasoning) and one `tool` message per call, in the calls' order. Everything else in the body stays, the seed
included.

The loop belongs to the runner. It ends when a response calls no toolset tool, or after `max_rounds` rounds. A call
that names no tool of the skeleton is answered with an error.

## Engines
An engine is where requests are sent. Researchers pick engines for trials and judges. Every engine speaks the same
API, `chat.completions`: OpenAI-compatible chat completions.

### Model artifact
- principal author: Ops
- defined in: `engine.py:ModelArtifact`

A model artifact (kind `model`) pins a model's weights: the repository, a revision, and the SHA-256 of every file,
collected by an import script. It is a component of its own, so several engines can share it.

### Local engine
- principal author: Ops
- defined in: `engine.py:LocalEngine`

A local engine (kind `engine.local`) is a vLLM server we run. It declares:
- its hardware: GPU, compute capability, VRAM and driver;
- its runtime: the server (`vllm`), its version and its Nix closure path;
- the model artifact it serves;
- the SHA-256 of its chat-template file, which pins the template's content; where the file lives is the
  inventory's business, so moving it doesn't change the engine;
- the raw `argv` and `env` passed to vLLM.

The start-up script owns `--model`, `--served-model-name`, `--chat-template`, `--tokenizer` and `--revision`
(`engine.py:OWNED_FLAGS`), so `argv` may not set them, whether spelled with dashes or underscores, since vLLM reads
both. Nor may `argv` use `--config`: `--config FILE` pulls arguments from a file the digest doesn't cover, and
`--config=FILE` is silently ignored (`docs/vllm.md`, item 8). Every argument belongs in `argv`.

Two more rules close the ways around these:
- `argv` may not abbreviate a flag it may not use. vLLM expands an unambiguous prefix to the whole flag, and the
  last occurrence of a flag wins, so `--served-model x` would replace the served model name.
- `argv` may not have bare arguments. `vllm serve` takes the model as its only bare argument, and the start-up
  script gives it, so another one stops vLLM from starting. A bare argument right after a flag written without `=`
  may be that flag's value, so it passes.

The served model name is the engine's digest, so every response names the exact engine it came from. Batch
invariance is declared through `env`, for example `VLLM_BATCH_INVARIANT=1`. Knowledge that changes between vLLM
versions lives in lints, not in these fields.

`check_chat_template(engine, template)` warns when the template file doesn't match the declared hash
(`engine.chat_template`) or reads the current date (`engine.chat_template_date`).

Hardware and runtime are part of the engine rather than components of their own. So they can't be listed, picked
or reused on their own, for example in the portal or as database foreign keys: the same GPU or closure is repeated
in every engine that uses it, and finding the engines that share a closure means scanning them all.

### Remote engine
- principal author: Ops
- defined in: `engine.py:RemoteEngine`

A remote engine (kind `engine.remote`) is an API we don't control. It declares only the API, the `base_url` and the
requested model; its chat template is not pinned. What the API does on a given day is an observed factor. Of
what it returns, only the model name is checked today (`docs/ledger.md:Run`).

Canary probes at the start and end of a run apply to any engine and are planned per run
(`docs/ledger.md:RunStarted`), so they are not part of the engine.

## Trials

### Trial
- principal author: Researchers
- defined in: `trial.py:Trial`

A trial (kind `trial`) is one experimental condition (the smallest unit of a scientific intent) and holds:
- a generation skeleton
- an engine
- the cases
- the text-cleanup steps (`cleanup`)
- the seeds

A trial pins the factors chosen in advance; each of its runs adds recorded and observed ones.

#### Send order and de-duplication
Cases and seeds are listed without duplicates. Cases are sorted by digest so their order is no part of the trial.
The run declares the send order (see "Execution"). Seeds keep their order as each seed's position names a
replicate.

#### Seeds are explicit
A helper can propose distinct random 31-bit seeds (`suggest_seeds`), which users are free to change. Each seed
defines one replicate, identified by its position. With greedy sampling no seed is sent, but the seeds still count
toward the trial's digest (see "Open design issues").

### Execution
- defined in: `trial.py:Execution`, recorded in `docs/ledger.md:RunStarted` and `docs/ledger.md:ScoreStarted`

When a run issues its requests, or a score issues its judge requests, `Execution` provides the following
details:
- `order`, the order in which a run/score sends its items:
  - `case_major@1` (the default: every replicate of a case before the next case, cases in digest order)
  - `replicate_major@1` (every case once per replicate, cases in digest order)
  - `shuffled@1`, which needs a `shuffle_seed` and orders the items by a hash of that seed, the case and the replicate.
- `concurrency` (1 by default)
- `timeout_s` (optional)
- `retries` (0 by default)

`Execution.schedule(cases, replicates)` lists the (case, replicate) pairs in send order.

These settings are a recorded factor (but not a component proper) written into the start record of each run or
score, without affecting the trial's (or scoring's) digest, so running a trial (or scoring) again with other
settings is a rerun of the same trial (or scoring).

This suits batch-invariant engines, but they change outputs on engines that aren't batch invariant (where they
affect how requests are batched and so the arithmetic). See "Runs of the same trial aren't interchangeable" under
"Open design issues" for more information. Timeout and retries also decide whether a request gets an answer at all.

A run's calls, and a score's judge calls, show whether the settings were followed: `check_run` and `check_score`
warn when a call took more attempts than `retries` allows, when items were sent out of order, or when more calls
were in flight than `concurrency` allows (`docs/ledger.md`, "Run" and "Score"). Timeouts aren't checked.

## Scoring

### Expectation schema
- principal author: Developers
- defined in: `scoring.py:ExpectationSchema`

An expectation schema (kind `expectation_schema`) is a JSON Schema for the reference data a scorer consumes. Its
`$schema` names the draft it's written in, 2020-12 when absent.

### Expectation
- principal author: Clinicians
- defined in: `scoring.py:Expectation`

An expectation (kind `expectation`) is the reference answer for one case, written in the portal: the case's digest,
the expectation schema's digest and the data. Because it is keyed by the case, appendices included, the same
vignette can have different expectations under different appendices. Expectations are not sensitive.

Building an expectation doesn't check its data against its schema. Lints do (see "Lints"), and the scorer decides
what to do with data that fails.

### Scorer
- principal author: Researchers
- defined in: `scoring.py:Scorer`

A scorer (kind `scorer`) pins scoring code written by developers. It declares:
- the code (`Code`: distribution, version and revision), and its `entry_point`: the function in that code that does
  the scoring, written `module:attribute` as for tools (for example `chatddx_scoring.match:score`);
- the expectation schema it consumes;
- an ordered list of views;
- ordered resource digests, such as synonym tables or ontology releases;
- free parameters.

Views and resources are referred to by position; their labels live in the catalog.

### View
- defined in: `scoring.py:View`

A view scores one part of an output against one part of an expectation. It holds:
- `output` and `expectation`: a selector into each (the whole document by default);
- `split`: optionally, how to split what the output selector picks;
- `metric`: a name that only the scorer's code interprets, with optional `params`;
- `judge`: optionally, the judge it uses. The view's selections are then what the judge sees (see "Judge").

A selector is either a JSON Pointer (RFC 6901), which picks at most one value, or a JSONPath query (RFC 9535) from
a fixed subset:
- member names: `.name`, `['name']`;
- indexes: `[0]`, `[-1]`;
- wildcards: `.*`, `[*]`;
- filters that test whether a relative path exists (`[?@.critical]`) or compare it with a literal
  (`[?@.critical == true]`, `!=`).

Anything else (`..`, slices, other operators, functions) is refused. Within the subset every RFC 9535
implementation picks the same values. Over arrays they come in the same order, but RFC 9535 leaves the order of an
object's members open for wildcards and filters; `select` keeps the document's order. Selection keeps nulls, as
RFC 9535 does.

`split: lines@1` turns each picked string into one item per non-blank line, trimmed, with a leading list marker
(`-`, `*`, `•`, `1.` or `1)`, followed by a space) removed. Without a split, a free text is one item. As with text
cleanup, the name pins the behavior.

Only the output side is split. A model answering under a `text` contract writes one block of free text, which has
to be broken into items before it can be compared. An expectation is written against its schema, so a list in it is
already a JSON array, and free-text notes in expectations are for judges to read (see "Judge").

`View.output_items(answer)` and `View.expectation_items(data)` apply the view, so every scorer selects the same way
(`select.py`).

### Judge
- principal author: Researchers
- defined in: `scoring.py:Judge`

A judge (kind `judge`) is an LLM used as a metric: a judge-purpose skeleton, an engine, its own seeds, without
duplicates, and a fill rule. A judge skeleton must contain the `completion` slot and may use `expectation`,
`vignette` and `appendices`.

The view decides what the judge sees, and the judge decides how it is written out.
`Judge.fills(view, answer, expectation)`, for a view that names this judge, fills `completion` with what the view's
output selector picks from the answer, and `expectation` with what its expectation selector picks from the
expectation's data. The answer is the model's output as the scorer parsed it. The judge's `fill` rule
(`scoring.py:FillOp`) turns those selections into text, and, as with text cleanup, its name pins one behavior. There
is one rule today, `text@1`, the default:
- a single string goes in as it is, without quotes or escapes;
- a list of strings, whether picked as one list or as several strings, goes in one string per line, and nothing
  picked gives an empty text;
- anything else goes in as JSON, indented by two spaces, keeping its key order and its non-ASCII characters.

The `vignette` and `appendices` slots are filled as for the trial (see "Preparing a case").

Judge requests go through the same rendering as generation requests, once per seed, and are recorded per score item
(`docs/ledger.md:JudgeCall`). Their scores are reproducible on a best-effort basis. Given the scorer's parse of an
answer, a judge request can be rebuilt from stored data and checked against the fingerprint its call stored; the
parsing belongs to the scorer's code, so the rig can't do that check on its own. Because a judge prompt can
contain case text, judge engines fall under the same clearance rule as generation engines (`docs/clearance.md`).

### Scoring
- principal author: Researchers
- defined in: `scoring.py:Scoring`

A scoring (kind `scoring`) is what a score applies: a scorer plus the expectations it scores against. Every
expectation must reference the scorer's expectation schema, and each case may have at most one expectation. The
judges a scoring uses are the ones its scorer's views name.

## Canary sets
- principal author: Developers
- defined in: `trial.py:CanarySet`

A canary set (kind `canary_set`) is a list of fixed, non-sensitive probe requests. Each canary holds literal
messages, body keys (no runtime keys) and an optional seed. A run names the canary set it uses
(`docs/ledger.md:RunStarted`).

A canary set is an instrument, not a factor: it changes no output and no score, and it is there to detect observed
factors, such as an engine that changed between runs. It is a component so that the same set can be compared
across runs; that comparison isn't implemented yet.

## Lints
- defined in: `lint.py:lint`

Lints warn about settings that are valid but risky. They are plain functions over a registry, run on demand, and no
component stores their findings. Keeping them out of the components means knowledge that changes between vLLM
releases can change without changing any digest. `docs/findings.md:Factors` explains each code.

`lint(registry, digests, languages=, reasons=)` checks each listed component according to its kind.

**Model artifacts, local engines and scorers**
- `model.revision`: the revision isn't a 40-character commit, so it can move.
- `engine.closure`: a local engine's closure isn't a Nix store path.
- `scorer.revision`: the scorer's code has no revision.
- `view.unreachable`: a view's expectation selector can't pick anything from documents that follow the scorer's
  expectation schema. The check leans towards "reachable" (unknown refs, unconstrained schemas and unions pass), so
  a finding is certain.

**Expectation schemas and expectations**, checked with the `jsonschema` library
- `expectation_schema.invalid`: `$schema` names a draft the library can't check, or the schema breaks its draft's
  metaschema.
- `expectation.invalid`: the data fails its schema. The message gives the most relevant error's location and how
  many more there are.
- `expectation.unchecked`: a `$ref` the data reaches can't be resolved. Refs resolve within the schema only, and
  nothing is fetched.

An expectation whose schema is invalid isn't checked, since the schema's own finding covers it. `format` is an
annotation, as in draft 2020-12, and isn't enforced.

**Trials and judges**, each checked as a pair of skeleton and engine. On a local vLLM 0.24 engine, flags are read
as vLLM reads them, with `_` and `-` alike:
- `vllm.temperature_clamped`: a temperature between 0 and 0.01, which vLLM raises to 0.01.
- `vllm.thinking_budget_refused`: `thinking_token_budget` without `--reasoning-parser` or `--reasoning-config`.
- `vllm.tools_refused`: tools without both `--enable-auto-tool-choice` and `--tool-call-parser`.
- `vllm.native_tools_uncallable`: a `native` contract with tools. The schema then constrains the whole answer, so no
  tool can be called.
- `vllm.grammar_before_reasoning`: a constrained `native` or `tool` answer without `--reasoning-parser`, so the
  model can't reason first. It's a warning when the skeleton asks for reasoning, or leaves it to a model that
  reasons by default; info when that's unknown; nothing when reasoning is off. Whether a model reasons by default
  comes from `reasons=`, a function from an engine's digest to `True`, `False` or unknown, normally the model
  facts' (`src/chatddx/facts/lint.py:reasons`). Without it, the default is unknown.

On any other engine (a remote engine, or another vLLM version):
- `schema.ref_unverified`: a `native` or `tool` schema has `$ref`, which the engine isn't known to resolve;
  `inline_refs@1` removes it.

Checks against the model facts (`facts.*` findings) belong to the facts, not to this package
(`src/chatddx/facts/lint.py`).

With languages (`languages=`, normally `Catalog.language_of`), for trials only:
- `language.mixed`: some cases are in another language than the request.
- `language.unknown` (info): the language of the request, or of some cases, is unknown.

Other findings in this package come from the functions that observe them: `case.drift` (`prepare_case`),
`engine.chat_template` and `engine.chat_template_date` (`check_chat_template`), and `bundle.recanonicalized`
(`Bundle.load`).

## Open design issues

### Runs of the same trial aren't interchangeable
A trial's digest covers its pinned factors only. Its outputs also depend on its run's recorded and observed
factors: execution settings on an engine that isn't batch invariant, a remote engine that changed between runs, a
tool that answered differently. Seeds within a run are replicates; runs of the same trial are a second source of
variation, and it has no name yet.

Nothing aggregates scores today, but anything that compares or pools runs by the trial's digest alone would mix
conditions without noticing. It needs to group by the recorded factors as well, and to check the evidence of the
observed ones (prompt-token fingerprints, returned models, canaries) before pooling.

### Greedy sampling and seeds
No seed is sent with greedy sampling, but a trial's seeds still count toward its digest, so two otherwise identical
greedy trials differ only in digest. Likewise, the seeds of a greedy judge all send the same request.

### The inventory doesn't locate local engines yet, and runs don't record where calls went
A local engine pins what it is but not where it is: it has no URL, and its model files and chat template are
pinned by hash only. A runner needs a mapping from engine digest to URL, and the start-up script needs the paths of
the model files and the chat template on its host. Nothing defines these yet. Being locations, they belong in the
inventory, not in the catalog; today the inventory locates only vignette sources (`src/chatddx/inventory/`). One
engine may be served by several identical hosts, and one host serves different engines over time, so the mapping
can't be part of the engine. The runner can check the mapping before sending, because a local engine's served
model name is its digest and `/v1/models` lists it.

A remote engine's `base_url`, by contrast, is part of its digest, so moving the same API to a new host makes a new
engine. Either way `Call` (`src/chatddx/ledger/ledger.py`) records no URL, so the ledger can't show which endpoint
received case-derived content, which clearance may need.

### Smaller issues
- **Canary drift isn't checked.** Nothing compares canary outputs between phases or between runs.
- **Judge engines are never probed.** Canary probes run on the run's engine only.
- **Skeleton provenance.** Should a skeleton be accepted only with a compilation? Hand-written ones, such as judge
  prompts, are possible today.
- **Flags are checked without vLLM's list of flags.** So a bare argument after a flag that takes no value, as in
  `--enforce-eager google/gemma`, passes the argv check, and vLLM then refuses to start. Likewise lints read flags
  by their full names, so an abbreviated flag such as `--reasoning-pars qwen3` works in vLLM but escapes the lints
  that look for `--reasoning-parser`.
- **The scorer interface is unspecified.** A scorer's entry point says where its code is, not what that code must
  do: which arguments it takes and what it returns. Only that code gives `View.metric` a meaning, and nothing checks
  that it knows the name.
- **The model name is chosen in several places.** `render` needs the engine's digest for a local engine and
  `model` for a remote one. `src/chatddx/ledger/ledger.py:check_run` makes that choice, and so must every runner; it
  belongs on the engine, as one method.
- **Missing expectations aren't flagged.** Neither a scoring nor `check_score` warns when a run's case has no
  expectation, or when a scored item has no expectation behind it.
- **Judge calls aren't attested.** Their returned model and prompt-token fingerprint aren't checked the way run
  items are.
- **Judges that read the vignette must fetch it again.** A judge skeleton with the `vignette` slot makes the scorer
  fetch the sensitive vignette again and redo the trial's cleanup. `prepare_case` covers the cleanup; nothing
  fetches the vignette.
- **No helper turns a canary into a request** (model name, seed, `return_token_ids`).
- **A missing slot fill is a bare `KeyError`.** `render` raises `KeyError` when `fills` lacks a slot the skeleton
  uses, instead of a `StructuralError` naming the slot.
- **Malformed tool calls fall between two helpers.** `tool_calls` skips a call without an id, a name or string
  arguments, but `next_request` copies the response's tool calls as they are. The next request then holds a call
  that no `tool` message answers, which an engine is likely to refuse.
- **NaN and infinity get past validation.** A float field such as `Sampling.temperature`, or a value in a body,
  accepts them, but the canonical form writes them as `null`. The stored component then reads back as a different
  one (with `temperature` unset, under another digest), while the request still carries `NaN`.
- **`Scorer.resources` are meant to be digests but typed as plain strings,** so nothing checks their form.
- **Four cleanup steps exist.** Vignettes may also need non-breaking spaces turned into spaces, zero-width
  characters removed, Unicode line breaks turned into newlines, or trailing whitespace stripped.
- **`Hardware` has no GPU count,** so engines that split a model across GPUs can't be told apart by their hardware.

## Potential design improvements

### Execution settings as a component
Today `Execution` is a value inside each run's start record (`RunStarted.execution`): recorded, sealed with the
run's log, and compared by value. It has no digest, so the catalog can't name a set of settings (for example
"sequential" or "8-way"), the portal can't offer them for picking, and finding the runs that used the same settings
means comparing values across run records.

Making it a component, a new kind such as `execution` that `RunStarted` references by digest, would give it a name,
a catalog thread and a foreign key like the pinned factors. It would still stay out of the trial's digest, so it
would change how execution settings are stored, not what a trial means. The cost is a new kind (with the catalog's
thread kinds and their migration) and a change to the run record.

### Chat templates as components
Today a local engine pins its chat template by the file's SHA-256 (`LocalEngine.chat_template`), like the model
files: a world input, fetched from wherever the inventory says it is. A bundle therefore holds the template's hash
but not the template, and only `check_chat_template`, with the file in hand, can read it.

Making the template a component, a new kind such as `chat_template` holding the template's text, that the engine
references by digest, would put it inside the bundle: it is small and not sensitive, so a bundle could rebuild the
engine's prompt formatting offline. Lints could read it too, so the "reads the current date" check
(`engine.chat_template_date`) would run when factors are linted, not only on a host. Engines sharing a template
would share the component. It would move the template from world input to authored factor, and cost a new kind
(with the catalog's thread kinds and their migration) and a change to `check_chat_template`.

### Splitting expectations
If expectations written as free text turn up, a view could split them too. Two ways:
- a separate `View.expectation_split`, next to `split`. It is additive: with "none" as its default, every existing
  digest stays the same. Each side gets the split it needs.
- one `split` for both sides. That changes the meaning of an existing field, so `Scorer.schema_version` would have
  to go up, and the two sides rarely need the same split.

### Finding code through packaging entry points
Tools and scorers name their function in the component (`entry_point`, `module:attribute`). Python packaging offers
another way: the distribution declares its function in its own metadata, under a group such as `chatddx.scorers` or
`chatddx.tools`, and the runner looks it up with `importlib.metadata`. The pinned distribution and version would
then decide which function runs, with no field in the component. It would keep the choice with the code's authors,
at the cost of being less visible in the factors, and tools and scorers should switch together.

### Passthrough: an allow list, or lints
A passthrough refuses a fixed list of keys and lets everything else through. Two other ways:
- an allow list: a passthrough may set only known engine extras. Nothing unknown gets through, but every new key
  needs a code change, and remote engines accept other keys than vLLM, so the list would depend on the engine.
- lints instead of refusals: risky keys would give a finding, in the spirit of "warnings, not crashes". But a
  passthrough already refuses runtime and output keys, and keys that put case text in the stored response are
  closer to the clearance block than to a warning.

### Reference kinds in components' JSON Schemas
`RefTo` could put the kinds a reference may point to on its field in the JSON Schema pydantic writes for a
component (as an `x-ref` keyword, say). A portal form could then offer a picker of the right kind for every
reference, straight from the schema. `RefTo` used to do this, and was stopped because nothing read it. The JSON
Schema plays no part in a component's digest, so adding it back changes no digest.

## Proposed amendments
