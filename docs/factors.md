# Factors

A case vignette can be fed to an LLM in a myriad of ways: Instructions, sampling parameters, GPU, the weights and many more choices affects the final output. It's practically impossible to tweak each aspect individually, so we need to group them in meaningful and managable categories, we call these categories factors.

There's a catch-22 to this because we need to know *how* some aspect affects output in order to group them effectively, but the *how* is what our research is all about, so we start with a flexible system of factors based on reasonable guesses and then refine iteratively.

This document describes each factor as they exist today, along with the mechanics of how factors interact when combined.

The factors provide the basic building blocks for chatddx's rig: An environment that can be provably reconstructed for scientific rigor.

## Not factors
Simply stated, if it doesn't affect output or scoring, it's not a factor, some examples follow:
- records and observations: These are specified in `ledger.md`.
- people, roles, authentication and authorization: These are specified in `identity.md`.
- names, labels, tags, descriptions, authors, owners, collaborators, version history: These are specified in the `catalog.md`.
- sensitivity and vetting parameters: These are specified in `clearance.md`.

## In depth
In the code, A factor is immutable and identified by its digest. They reference eachother by this digest, forming a graph. Each reference is typed (`Annotated[Digest, RefTo(kind, …)]`). From these types, `Registry.check` derives the graph and checks that every reference exists and has an allowed kind. Rules that span components, such as "a trial must use a generation skeleton", are `cross_check` hooks run by the same check.

The kinds are `model`, `engine.local`, `engine.remote`, the seven `chunk.*` kinds, `skeleton`, `appendix`, `case`, `trial`, `expectation_schema`, `expectation`, `scorer`, `judge`, `scoring` and `canary_set`.

The suggested storage is one table for all kinds, `factor.component` (`digest`, `kind`, `v`, `canonical`, `doc`), plus `factor.component_ref` (`src`, `path`, `dst`, `kinds`), which holds one row per typed reference so that every reference gets a foreign key, including references inside lists and references that allow several kinds.

Note on foreign keys: `Store.add` writes one `factor.component_ref` row per `Component.refs()` site, and the foreign key sits on that table. The allowed kinds are stored in the row's kinds column and checked by a tier-2 trigger (see `docs/store.md` for postgres' integrity tiers).

Note on `x-ref`: `RefTo` puts `x-ref`: [allowed kinds] on each reference field when Pydantic generates a JSON Schema for a component. Nothing is consuming it right now and it may be dead weight if the portal doesn't need it.

## Index

### Case
- principal author: Clinicians
- defined in: `cases.py:CaseInput`

A case (kind `case`) names a vignette in the predefined source by source name and id (`SourceCase`), records the fingerprint of that vignette, and lists the appendices to append, in order. An import script supplies the vignette fingerprint, taken over the raw vignette as fetched. The vignette itself is sensitive, unstructured and never edited, and it is never stored: only its fingerprint is. The case's digest covers all three parts (source reference, fingerprint and appendix list), so the vignette is not its sole contributor, and the same source case with different appendices is a different case. Expectations are keyed by this digest, which lets appendices change the correct answer.

A changed vignette at the source has a new fingerprint. It therefore needs a new case, new appendices bound to the new fingerprint, and new expectations; whether a user or an automatic step re-binds them is open. Text cleanup is not part of the case: a trial chooses it (see "Trial").

### Appendix
- principal author: Clinicians
- defined in: `cases.py:Appendix`

An appendix (kind `appendix`) is a block of plain text, written in the portal and attached to one source case and one vignette fingerprint. It is the only way to add information to a vignette; anchored edits to the vignette were considered and ruled out as too complex. Appendices are not sensitive. They are stored and sent exactly as entered, and text cleanup does not apply to them.

A case lists its appendices in a pinned order that is part of the case's digest, and rejects any appendix bound to another source case or vignette (`CaseInput.cross_check`), so an appendix is never reused across source cases. It can, however, appear in several cases of the same source case, for example a case with a lab appendix and one without. At send time the appendices are joined into one text by the recipe's `AppendixLayout`, which is frozen into the skeleton (see "Rendering and runtime keys").

### Request
The request side is built in three layers: chunks are authored in the portal, a recipe selects one chunk per part, and the compiler turns a recipe into a frozen skeleton, which is what trials and judges reference. Chunks affect each other only in two pre-specified ways, both owned by the components: the output chunk's guidance is appended to the instructions, and greedy sampling drops `top_p`, `top_k` and `min_p` (the seed is left out at send time). There are no validation retries: the run stores the raw completion and parsing belongs to scoring. Transport retries are a separate matter, declared per run (`Execution.retries`) and counted per call (`ledger:Call.attempts`).

Each chunk is a component that fills one part of a recipe. None of them is a template: text is plain text, and the only runtime placeholders are the prompt's slots, filled by concatenation, so clinical text is never interpreted.

#### Instructions
- principal author: Researchers
- defined in: `request.py:Instructions`

Instructions (kind `chunk.instructions`) are plain text sent as the first message, with the role `system` or `developer`. Their cross-component effect is passive: the output chunk's guidance is appended to them, separated by a blank line, and becomes the whole message if there are no instructions.

#### FewShot
- principal author: Researchers
- defined in: `request.py:FewShot`

A few-shot chunk (kind `chunk.few_shot`) is a list of plain-text user/assistant example messages, placed after the instructions and before the prompt.

#### Prompt
- principal author: Researchers
- defined in: `request.py:Prompt`

A prompt (kind `chunk.prompt`) is the user message: a list of segments, each a literal string or a slot, with a purpose of `generation` or `judge`. A generation prompt must contain the `case` slot and may contain `appendices`. A judge prompt must contain `completion` and may contain `case`, `appendices` and `expectation`. No slot may appear twice, and the prompt's purpose must match the recipe's.

#### Output
- principal author: Researchers
- defined in: `request.py:Output`

An output chunk (kind `chunk.output`) declares where the answer goes, through one of three contracts. `native` sends a JSON schema as `response_format` (named `output`, strict). `tool` forces a single function call whose parameters are the schema. `text` constrains nothing and may carry a schema that only scoring uses. Its optional guidance is the chunk's cross-component effect: it is appended to the instructions. There is no coercion: parsing belongs to the scorer.

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

#### Recipe
- principal author: Researchers
- defined in: `request.py:Recipe`

A recipe is not a component. It holds a purpose, an appendix layout, required references to a prompt, an output and a sampling chunk, and optional references to instructions, few-shot, reasoning and passthrough chunks. `compile_request` turns a recipe into a skeleton and fails on missing or wrongly typed chunks. The recipe is kept only in the `Compilation` record, next to the compiler version (see "Compilation"). Because a trial references the skeleton rather than the recipe, the compiler's code is not a factor.

#### Skeleton
- principal author: none; normally produced by the compiler (`compile_request`)
- defined in: `request.py:Skeleton`

A skeleton (kind `skeleton`) is the frozen request, and the only component normally produced by code rather than people; trials and judges reference it. Nothing prevents hand-written skeletons (see "Possible design issues"). It is fully pre-rendered: the complete chat-completions body except for what is filled in at send time. It holds a purpose, the API (`chat.completions`), the messages as segment lists (so few-shot examples are already baked in as plain text), every other body key, the output contract and the appendix layout. Its validation repeats the chunks' rules: slots must suit the purpose, greedy sampling is canonicalized, a `native` contract needs `response_format` and no tools, a `tool` contract needs exactly one tool of the declared name plus `tool_choice`, and a `text` contract may set no output keys. For `native` and `tool`, the output schema is read from the body rather than stored twice.

#### Rendering and runtime keys
`render(skeleton, model, seed, fills)` produces the wire body. It concatenates each message's segments, replacing each slot with its fill, then adds `model` (the engine digest for local engines, the requested model for remote ones), the messages and the skeleton's body. It adds `seed` only when the skeleton is not greedy, and adds `return_token_ids: true` so the response carries the prompt's token ids (see `docs/ledger.md:Call`). The appendix fill is produced beforehand by the layout, which puts text before, between and after the appendices (by default a blank line before and between, nothing after) and yields an empty string when there are none. The keys `render` owns (`model`, `messages`, `seed`, `stream`, `n` and `return_token_ids`, listed in `request.py: RUNTIME_KEYS`) may not be set by any chunk, skeleton or canary. The wire body itself is transient: only its fingerprint is stored.

### Engine
An engine is where requests are sent. Researchers pick engines for trials and judges.

#### Model artifact
- principal author: Ops
- defined in: `engine.py:ModelArtifact`

A model artifact (kind `model`) pins a model by its repo, a revision (linted unless it is a commit) and the sha256 of every file, collected by an import script. It is its own component, so several engines can share it.

#### Local engine
- principal author: Ops
- defined in: `engine.py:LocalEngine`

A local engine (kind `engine.local`) is a vLLM server we run. It declares its hardware (GPU, compute capability, VRAM, driver) and runtime (`vllm`, its version, the Nix closure path), references a model artifact, and requires the digest of its chat-template file. Its raw `argv` and `env` are passed to vLLM, except the flags the start-up script owns (`--model`, `--served-model-name`, `--chat-template`, `--tokenizer`, `--revision`), which they may not set. Knowledge that changes between vLLM versions lives in lints rather than in these fields, and batch invariance is declared through `env` (for example `VLLM_BATCH_INVARIANT=1`). The served model name is the engine's digest, so every response names the cage it came from. `check_chat_template` warns when the template file doesn't match the declared digest or reads the current date. Hardware and runtime are part of the engine rather than components of their own. They can therefore not be listed, picked or reused as standalone rows, for example in the portal or as Postgres foreign keys; the same GPU or closure is repeated in every engine that uses it, and asking which engines share a closure means scanning engines instead of following a reference.

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

### Expectation
- principal author: Clinicians
- defined in: `scoring.py:Expectation`

An expectation (kind `expectation`) is the reference data for one case, authored in the portal: the case's digest, the expectation schema's digest and the data. Because the key is the case digest, appendices included, the same source case can have different expectations under different appendices. Expectations are not sensitive. The factors do not validate the data against its schema; the scorer does.

### Scorer
- principal author: Researchers
- defined in: `scoring.py:Scorer`

A scorer (kind `scorer`) pins scoring code, written by developers. It declares that code (`Code`: distribution, version, revision), the expectation schema it consumes, an ordered list of views, ordered resource digests (such as synonym tables or ontology releases) and free parameters. Views and resources are referred to by position; their labels belong to `catalog`. A view scores one part of an output against one part of an expectation: it holds a JSON pointer into each, a metric name that only the scorer's code interprets, optional parameters, and optionally the judge it uses. Parsing is the scorer's job, not the factors': outputs that fail validation are scored best-effort.

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
Lints (`lint.py`) warn about settings that are valid but risky. They are plain functions over a registry, run on demand; nothing in the factor components stores their findings. Keeping them out of the data layer means knowledge that changes between vLLM releases can change without touching any factor. `lint(registry, digests)` applies one rule per kind. A model artifact whose revision is not a 40-character commit gets `model.revision`, because a branch or tag can move. A local engine whose closure is not a Nix store path gets `engine.closure`. A scorer whose code has no revision gets `scorer.revision`. A trial on a local vLLM 0.24 engine whose skeleton sets a temperature between 0 and 0.01 gets `vllm.temperature_clamped`, because vLLM 0.24 raises such temperatures to 0.01 (an unverified assumption, see "Possible design issues"). This last rule is the only one keyed by the declared runtime version.

## Possible design issues

### Greedy sampling and seeds:
the seed isn't sent, but the trial's seeds still count toward its hash, so two otherwise identical greedy trials differ only in digest.

### Local engines have no endpoint, and runs don't record where calls went
  `LocalEngine` has no URL (`src/chatddx/factors/engine.py:51`), so the runner needs a mapping from engine digest to
  URL that nothing defines yet. By the glossary it is an inventory fact (a location), not a catalog one. One engine
  digest may be served by several identical hosts, and one host serves different engines over time, so the mapping
  can't be part of the factor. The runner can check the binding before sending, because a local engine's
  `served_model_name` is its digest and `/v1/models` lists it. `RemoteEngine.base_url`, by contrast, is part of the
  digest (`engine.py:76`), so moving the same API to a new host makes a new engine. In both cases `Call`
  (`src/chatddx/ledger/ledger.py`) records no URL, so the ledger can't show which endpoint received case-derived
  content, which the clearance check may need.

### Misc
- **Case drift is not checked.** The observed vignette fingerprint is recorded per item but never compared with the case's.
- **Canary drift is not checked.** No function compares canary outputs between phases or between runs. The old code had `compare_canaries`.
- **Judge engines are not verified by canaries** (reconciliation D5, option B, deferred).
- **Skeleton provenance.** Should a skeleton only be accepted with a compilation record? Hand-written ones (e.g. judge prompts) are possible today.
- **Re-binding after vignette drift.** A changed vignette orphans its appendices and expectations; who re-binds them?
- **Metric names are free strings.** `View.metric` is only meaningful to the scorer code that `Scorer.code` pins; nothing checks that the code knows the name.
- No helper prepares a case, i.e. per-item steps 2–5 (fetch, drift check, cleanup, joining appendices). The old code had one (prepare_case), and it's worth adding back.
- Where the model name comes from (per-item step 6) is repeated in check_run, so it belongs on the engine as one method.
- Missing expectations aren't flagged. Neither the scoring nor check_score warns when a run's case has no expectation, or when a scored item has no expectation behind it.
- Judge calls aren't attested. Their returned model and prompt-token fingerprint aren't checked the way run items are.
- Greedy judges make extra seeds pointless. The test's judge skeleton is greedy, so no seed is sent. Several judge seeds would then send identical requests.
- Case-slot judges need the vignette again. A judge skeleton that uses the case slot makes the scorer fetch the sensitive vignette and redo the trial's cleanup steps. No helper exists for either.
- No helper turns a canary into a request body (model name, seed, `return_token_ids`).
- `Scorer.resources` are called digests but typed as plain strings, so nothing checks their form.
- The closed set of text-cleanup operations has four members. The old code also had non-breaking spaces to spaces, zero-width character removal, Unicode line breaks to newlines and trailing-whitespace stripping; vignettes may need some of them.
- `Hardware` has no GPU count, so tensor-parallel engines can't be told apart by hardware (the old code had `gpu_count`).

## Proposed amendments
- CHANGE in "Request": "None of them is a template: text is plain text, and the only runtime placeholders are the
  prompt's slots, filled by concatenation, so clinical text is never interpreted." to "None of them is a template:
  text is plain text. The only runtime placeholders are the prompt's slots, filled by concatenation, so clinical text
  is never interpreted; the only compile-time placeholder is the output guidance's `schema` insert."
  (`src/chatddx/factors/request.py:46`)
- CHANGE in "Output": "`text` constrains nothing and may carry a schema that only scoring uses. Its optional guidance
  is the chunk's cross-component effect: it is appended to the instructions." to "`text` constrains nothing and may
  carry a schema, for scoring and for the guidance to show. Its optional guidance is the chunk's cross-component
  effect: it is appended to the instructions. Guidance is text, or segments of text and the `schema` insert, which
  compilation replaces with the output's schema as `json.dumps(indent=2, ensure_ascii=False)`, exactly once and only
  when there is a schema. A `native` output that shows its schema is the old "native (shown)"; a `text` output that
  shows it is the old "prompted". Adjacent text is merged, so text-only guidance is always a plain string."
  (`src/chatddx/factors/request.py:Output`, `compile_request`)
