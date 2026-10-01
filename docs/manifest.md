# Rig manifest: requirements and design notes

## Purpose
ChatDDX is a system to measure how accurately LLMs generate differential diagnoses and management plans from emergency-care case vignettes.
This manifest specifies the basic building blocks for chatddx's "rig".

The rig provides:
- an environment that can be provably reconstructed (the "cage").
- means to observe ("record") each exchange within the cage.
- first principles for sensitive data so that policies governing this data can be enforced.

It must be possible to deliver the cage together with the results, for scientific rigor.

## Glossary
- **Factor:** ("component" in code): an immutable, content-addressed set of parameters that affects an output or its score.
- **Fingerprint:** a hash of content that must *not* be stored: vignette, wire body, prompt token ids. It carries its algorithm: `sha256`, or `hmac-sha256` with a `key_id`
- **Pinned:** a value that can't be altered without changing the digest of one or more factors.
- **Sensitive:** data that is forbidden from being stored on, or passed to, non-approved sources.
- **Bundle:** a set of root components plus everything they reference (the closure), stored as canonical text keyed by digest, together with the code version that produced it.
- **Cage:** the constrained environment a bundle enforces
- **Digest:** the factor's identity and how it's referenced, an `sha256:<hex>` over the component's canonical bytes.
- **Kind:** the discriminator of a component, e.g. `engine.local`, `chunk.prompt`.
- **Canonical form:** sorted-key JSON with fields left out when equal to their default, plus the kind's schema version.
- **Chunk:** a factor authored in the portal that fills one part of a recipe (not to be confused with a slot).
- **Recipe:** the chunks a skeleton was compiled from.
- **Skeleton:** the frozen request.
- **Slot:** a placeholder filled at send time.
- **Segment:** a literal string or a slot.
- **Purpose:** whether a prompt or skeleton is for `generation` or for a `judge`; it decides which slots are allowed.
- **Contract:** how an output chunk constrains the answer: `native`, `tool` or `text`.
- **Normalization:** the text-cleanup steps a trial applies to vignettes.
- **Trial:** see "Trial".
- **Replicate:** one seed of a trial, identified by its index.
- **Item key:** (case, replicate index), which identifies one request of a run.
- **Execution:** how a run issues requests: order, concurrency, timeout and retries.
- **View:** the part of an output and of an expectation that a scorer scores, and how.
- **Compilation:** the record of which recipe and compiler produced a skeleton.
- **Run** and **Score:** a stage log.
- **Record** / **ledger:** events logged while compiling, running or scoring.
- **Seal:** the hash over a finished log.
- **Finding:** a warning or info produced when something declared doesn't match what was observed, or when a setting is risky.
- **Canary:** a fixed, non-sensitive probe request.
- **Case-derived:** content produced from a vignette.
- **Wire-body:** the exact request sent, is sensitive if it contains a vignette.

### Roles mentioned
These roles are distinct and not overlapping, a person may inhabit more than one role.
- **Clinicians:** Owners of vignettes and authors of Appendices and Expectations
- **Researchers:** Authors of Sampling, Scorers and Trials
- **Developers:** Authors of schemas, canary sets, scoring and normalizations functions
- **Ops**: Owners of engines and authors of the engine's components

### Terms not directly related to the manifest
- **Vetted:** a bookkeeping fact (clearance), not a manifest fact.
- **Portal:** the web-interface used to configure factors, watch runs and export results.
- **World:** factor parameters outside of the orchestrator's direct control. (the vignette source, model files, the Nix closure, the chat-template file, the remote engine)

## Scope rule
The manifest details:
- Every factor that can affect output, and every field that makes a factor what it is.
- The mechanics of how factors affect eachother when combined (e.g. cross-component effects)
- A ledger recording each step of the request, the response and the scoring.
- Observations are recorded separately from declared factors: factors are components, observations are records.

### Excluded
- anything with no bearing on output or score: names, descriptions, authors, owners, tags, labels, collaborators, version history and clearance. These live in the bookkeeping layer, which references factors by digest and records by id.
- portal state that hasn't been compiled or saved yet, e.g. a recipe being assembled in the UI.
- bookkeeping layer owns names, owners, tags, collaborators, version history, clearance, who started a run, and labels for views and resources. It references digests and record ids, but the manifest does not reach reference bookkeeping.
- How scores are aggregated and exported together with the bundle (and eventually the ledger) for publication. This is a requirement but the manifest should only provide the building blocks, not the procedures.
- How sensitivity is enforced: manifest provide the means but not the enforcment itself.
- Storage: How components and records map to Postgres, this is known, but not details the manifest should be aware off:
    - factor tables are insert-only and keyed by digest, with the canonical text as the source of truth (`jsonb` only as a searchable copy);
    - records are append-only stage logs and item rows;
    - nothing is updated;
    - version chains between digests belong to bookkeeping.

## Requirements
- Due to the chaotic nature of both emergency clinicians and inference servers alike, LLM judges will be needed to be carefully orchestrated to score outputs against sloppy notes.
- Conversely, simple and reliable scoring methods are needed to produce clean easily processed outputs, which will require strict and simple pattern matching.
- Due to the sensitive nature of patient data passing through the orchestrator, a separate path is needed where data can be passed along revealing just enough for the orchestrator to fulfill its duty. Cases will enter the system de-identified from a predefined source.
- Nothing can be expected of the vignettes: They may or may not include labs and imaging results, and may even lack basic details such as age or sex. The source exposes no revision identifier.
- Portal interfaces for managing instructions, few-shots, prompts, output, sampling, reasoning, passthrough, recepies, appendices, expectations, trials, judges, scorings and starting and monitoring runs.
- Engines, models, canary sets, expectation schemas and scorers are authored by ops or developers.
- a self-contained export (bundle) that can be verified offline and read by the container's start-up script
- scripts that can read the World to import factor parameters.
- import scripts that fingerprint World inputs (model files, closure, chat template, vignettes) into factors.

### Intended evolution of configurations
The researches will want to iterate and continuously refine configurations according to the following workflow.
1. Chunks are added by hand-edits
2. Successful variations are saved as configurations proper
3. Unsuccessful variations are deleted
4. A configuration with several successful variations branch out
5. Unsuccessful configurations are deleted

"Delete" in this case is a `deleted` flag in the bookkeeping layer and rarely, if ever, true deletions.
None of this relates to the manifest directly, but the manifest must permit this workflow.

### On the table
- The result from scorers needs to be aggregated and exported. Since python is well-suited for statistical analysis, processing the data into publishable results may become a requirement.

## Target stack
- **Local inference.** Two servers, an RTX 3070 (sm_86, 8 GB) and an RTX 5090 (sm_120, 32 GB). Both serve vLLM 0.24.0 in a NixOS container that reads a `LocalEngine`, the model files, via `ModelArtifact`, the chat-template file and raw `argv`/`env`.

The start-up script owns `--model`, `--served-model-name` (set to the engine digest), `--chat-template`, `--tokenizer` and `--revision` (`engine.py: OWNED_FLAGS`).

No comparison between the 3070 and the 5090; each hardware class is its own cage.

- **Remote inference.** An A100 serving Gemma behind an OpenAI-compatible API. More may follow. We don't control the engine and it can change without notice, so some uncertainty is accepted.
  - The endpoint is vetted for sensitive cases.
  - It honors `seed` and returns model identifiers.

Returns model identifiers: the run check compares the returned `model` with the declared one (`ledger.py: check_run`). `system_fingerprint` is kept in the raw response but not checked.

- **Orchestrator.** Runs in Kubernetes, owns the manifest and hosts the portal. A postgres database store user-managed data: factors and records are append-only; nothing is updated.

## Compromises
The code declares no tier (the old tier assessment was dropped). The tier is *observed*:
 - an engine with batch invariance in its `env` may be bitwise reproducible;
 - otherwise best-effort.

Canaries, prompt-token fingerprints and comparing completions show which one applies.

- **Frictionless development.** A mismatch between what is declared and what is observed produces a warning, never a crash. Examples:
 - `attestation.model`: returned model ≠ declared;
 - `attestation.prompt_tokens`: prompt tokens have no fingerprint;
 - `attestation.prompt_tokens_drift`;
 - `run.incomplete`;
 - `ledger.seal`: rows changed after sealing;
 - `engine.chat_template`, `engine.chat_template_date`;
 - `bundle.recanonicalized`;
 - plus the lints in `lint.py`.

The runner warns; it does not refuse. Only structurally malformed specs and records raise errors:

**Case drift and canary drift are not implemented.** `RunItem.vignette` records the observed fingerprint, but nothing compares it with `CaseInput.vignette`, and nothing compares canary outputs between phases or runs. Keep them as intended behavior and list them under "Possible design issues".

The one exception to "warnings, not crashes": sending case-derived content to an engine without clearance is a hard block, judge engines included.

Other compromises decided during reconciliation:
 - plain sha256 fingerprints instead of HMAC
 - execution settings are not part of trial identity
 - the remote engine's chat template is not pinned
 - greedy trials still hash their seeds

HMAC was considered for identity and drift protection of the vignettes, but plain sha256 is the default due to:
- the clinicians working on plaintext anyway;
- anyone holding a case can verify its hash, which helps delivering the cage;
- there are no keys to manage.
- `Fingerprint.of` already supports `hmac-sha256` given a key and its id, so a source can switch without a schema change.

Accepted risks: confirming that a known text is in the study, brute-forcing of short values, whether a hash of pseudonymised health data counts as personal data (GDPR) is for the data protection officer.

## Orchestrator dependencies
  - Requests/response-handling: Hand-made and, to the extent it actually helps, pydantic-ai.
  - Scoring: text-matching functions and LLM judges.
  - pydantic 2.13.5 and Python 3.13+.

The manifest package is imported by the orchestrator, the runner, the scorer and the container's start-up script. It's currently a part of the orchestator and may move to its own repo.

## Intended dataflow
The manifest does not dictate process, it supplies the pieces that some runner chains together in whatever way they deem fit.
There is however an intended flow of data behind the pieces, which is described below.

### Before a run
1. A Recipe is created from stored instances of each Chunk: Prompt, Output and Sampling are required, Instructions, FewShot, Reasoning and Passthrough are optional.

2. The Recipe is compiled into a Skeleton, which is stored as a frozen factor, holding:
  - messages: each message's content is a list of plain text pieces or Slots (`case`, `appendices`)
  - body: sampling, output format and reasoning settings; greedy sampling has already dropped top_p, top_k and min_p
  - if few shots were included in the recipe, they're baked into the skeleton as plain text
  - appendix_layout: how appendices are joined into one piece of text

  A Compilation record notes which recipe produced which skeleton.

3. A CaseInput is a source case id, the vignette's fingerprint and a list of Appendix references.
4. A Trial names a skeleton, an engine (LocalEngine or RemoteEngine), the case inputs, the text-cleanup steps and the seeds.

### Per run
1. The runner records a RunStarted row which holds the trial, the execution settings (Execution), the canary set, and the version of the rig code.
2. Execution.schedule(cases, len(seeds)) gives the list of (case, replicate) pairs in the declared order

#### Per (case, replicate)
1. Load the trial, its skeleton, engine and CaseInput, then that case's appendices, all through Registry.get.
2. Fetch the vignette from the source by its id. Nothing in the code does this yet; it's the runner's job.
3. Check for drift: compare Fingerprint.of(raw vignette) with the fingerprint stored on the CaseInput. A mismatch is a warning. The code has the parts but no helper that does this check.
4. Clean the text: normalize(vignette, trial.normalization). As written, only the vignette is cleaned, not the appendices.
5. Join the appendices: skeleton.appendix_layout.join([appendix texts]).
6. Pick the model name from the engine: for a local engine it's engine.served_model_name, which is the engine's hash; for a remote engine it's engine.model.
7. Pick the seed: trial.seeds[replicate].
8. Render the request: render(skeleton, model=…, seed=…, fills={"case": …, "appendices": …}). This:
  - replaces each slot with its text by plain concatenation, with no templating
  - adds model, messages and the skeleton's body;
  - adds seed only if the sampling isn't greedy;
  - adds return_token_ids: true.
9. Send the request to the engine's /v1/chat/completions.
  - For a remote engine the URL is RemoteEngine.base_url. For a local engine, the URL and the clearance check now belong to bookkeeping, and nothing enforces the check yet.
  - Canaries are sent in the same way, but from their literal Canary bodies, without slots. The code is unclear here as helpers for creating requests and nothing compare canary outputs, the old code had compare canaries.

### After the run
1. After each response, fingerprint_prompt_tokens(response) takes prompt_token_ids out of the response [why? what are the prompt_token_ids?] and returns their fingerprint. The runner stores a RunItem row with the key (case, replicate), the observed vignette fingerprint and a Call. The Call holds the request's fingerprint, the timings, the attempt count, the response without its prompt token IDs, and the prompt-token fingerprint.
2. At the end, Run(...).finish() produces a RunFinished row with the seal [how is the seal produced?]. check_run then compares what the run declared with what came back.

### Scoring records
1. Write a ScoreStarted row naming:
  - the run it scores;
  - the Scoring;
  - the rig's code version;
  - the scorer code actually running (scorer_code).
2. Match each run item to its expectation. The item key's case is looked up among the scoring's expectations, whose case field is the same case reference. The code has no function for this; the scorer code does it.
3. Get the model's output out of the raw response. Parsing belongs to the scorer (content, tool-call arguments, JSON extraction, best-effort handling when validation fails). Nothing in the manifest does it.
4. Apply each view. A View points into the parsed output (output, a JSON pointer) and into the expectation (expectation), and names a metric that the scorer's code interprets, with params.
5. If the view names a Judge:
  - Build the judge request with render() from the judge's Skeleton (purpose "judge"). Its slots can be filled with the completion, the expectation, and optionally the case and appendices.
  - Send it to the judge's engine, once per seed in Judge.seeds.
  - Record each call as a JudgeCall: which judge, which seed index, and the call itself.
6. Write one ScoreItem per (run item, view):
  - the item key;
  - the view's position;
  - value (a number, or empty if it couldn't be scored);
  - optional detail;
  - the judge calls.
7. Close the log. Score.finish() computes the seal over the started row and all item rows, then write the ScoreFinished row with any warnings.
8. Check it. check_score(score, run, registry):
 - raises if the score belongs to another run;
 - raises if a view position is out of range;
 - raises if an item isn't in the run;
 - raises if a judge isn't one the scorer's views name, or a seed index is out of range;
 - warns if rows changed after sealing.

The "scored results" are just those ScoreItem rows. What aggregates them isn't named by the manifest.

## Factors (Components)
A factor is an immutable component identified by its digest: the sha256 of its canonical form. The canonical form is sorted-key JSON that leaves out fields equal to their default and carries the kind's own schema version. A new field must therefore default to the old behavior, and changing a default requires bumping that kind's schema version. Bundles store these canonical bytes, so a bundle verifies against its bytes rather than against whatever the current code would produce.

Factors reference each other by digest, forming a graph. Each reference is typed (`Annotated[Digest, RefTo(kind, …)]`). From these types, `Registry.check` derives the graph and checks that every reference exists and has an allowed kind, and the JSON Schema gains `x-ref` entries from which the portal and the Postgres foreign keys can be derived. Rules that span components, such as "a trial must use a generation skeleton", are `cross_check` hooks run by the same check.

The kinds are `model`, `engine.local`, `engine.remote`, the seven `chunk.*` kinds, `skeleton`, `appendix`, `case`, `trial`, `expectation_schema`, `expectation`, `scorer`, `judge`, `scoring` and `canary_set`.

The suggested storage is one table for all kinds, `factor.component` (`digest`, `kind`, `v`, `canonical`, `doc`), plus `factor.component_ref` (`src`, `path`, `dst`, `kinds`), which holds one row per typed reference so that every reference gets a foreign key, including references inside lists and references that allow several kinds.

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
The request side is built in three layers: chunks are authored in the portal, a recipe selects one chunk per part, and the compiler turns a recipe into a frozen skeleton, which is what trials and judges reference. Chunks affect each other only in two pre-specified ways, both owned by the manifest: the output chunk's guidance is appended to the instructions, and greedy sampling drops `top_p`, `top_k` and `min_p` (the seed is left out at send time). There are no validation retries: the run stores the raw completion and parsing belongs to scoring. Transport retries are a separate matter, declared per run (`Execution.retries`) and counted per call (`Call.attempts`).

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
`render(skeleton, model, seed, fills)` produces the wire body. It concatenates each message's segments, replacing each slot with its fill, then adds `model` (the engine digest for local engines, the requested model for remote ones), the messages and the skeleton's body. It adds `seed` only when the skeleton is not greedy, and adds `return_token_ids: true` so the response carries the prompt's token ids (see "Call"). The appendix fill is produced beforehand by the layout, which puts text before, between and after the appendices (by default a blank line before and between, nothing after) and yields an empty string when there are none. The keys `render` owns (`model`, `messages`, `seed`, `stream`, `n` and `return_token_ids`, listed in `request.py: RUNTIME_KEYS`) may not be set by any chunk, skeleton or canary. The wire body itself is transient: only its fingerprint is stored (see "Call").

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

A remote engine (kind `engine.remote`) is an API we don't control. It declares only the API (`openai.chat`), the `base_url` and the requested model; its chat template is not pinned. Of the identifiers it returns, only the model name is checked today (see "Run"). Canary probes at the start and end of a run apply to any engine and are planned per run (see "RunStarted"), so they are not a property of remote engines.

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

An expectation (kind `expectation`) is the reference data for one case, authored in the portal: the case's digest, the expectation schema's digest and the data. Because the key is the case digest, appendices included, the same source case can have different expectations under different appendices. Expectations are not sensitive. The manifest does not validate the data against its schema; the scorer does.

### Scorer
- principal author: Researchers
- defined in: `scoring.py:Scorer`

A scorer (kind `scorer`) pins scoring code, written by developers. It declares that code (`Code`: distribution, version, revision), the expectation schema it consumes, an ordered list of views, ordered resource digests (such as synonym tables or ontology releases) and free parameters. Views and resources are referred to by position; their labels belong to bookkeeping. A view scores one part of an output against one part of an expectation: it holds a JSON pointer into each, a metric name that only the scorer's code interprets, optional parameters, and optionally the judge it uses. Parsing is the scorer's job, not the manifest's: outputs that fail validation are scored best-effort.

### Judge
- principal author: Researchers
- defined in: `scoring.py:Judge`

A judge (kind `judge`) is an LLM used as a metric: a judge-purpose skeleton, an engine and its own seeds. A judge skeleton must contain the `completion` slot and may use `expectation`, `case` and `appendices`. Judge requests go through the same rendering as generation requests, once per seed, and are recorded per score item (see "JudgeCall"). Their scores are best-effort reproducible. Because a judge prompt can contain case text, judge engines fall under the same clearance hard block as generation engines.

### Scoring
- principal author: Researchers
- defined in: `scoring.py:Scoring`

A scoring (kind `scoring`) is what a score applies: a scorer plus the expectations it scores against. The expectations must all be in the scorer's schema, and a case may have at most one. The judges a scoring uses are those named by its scorer's views.

### Canary set
- principal author: Developers
- defined in: `trial.py:CanarySet`

A canary set (kind `canary_set`) is a list of fixed, non-sensitive probe requests. Each canary holds literal messages, body keys (no runtime keys) and an optional seed. A run names the canary set it uses (see "RunStarted"). Canary sets are components because canary drift is detected by comparing the same set across runs (not implemented yet).

## Records
Records are the ledger: what was observed while compiling, running and scoring. They are not components and not part of the component graph; they reference components by digest and each other by id. Every record table is append-only. A run or a score is a stage log (a started row, item rows and a finished row), and a new stage is a new row type, not a new table. Every run and score row is marked case-derived (`Record.case_derived`), because those logs hold completions; compilations are not. This marking is what the sensitivity policy acts on.

The suggested storage is one stage table per log type (`run_stage`, `score_stage`) keyed by id and stage, item tables (`run_item`, `canary_call`, `score_item`) and a `compilation` table, each holding the record's canonical bytes (Record.canonical) in a payload column.

A record is stored in its canonical form (defaults omitted, stage kept, plus the record type's schema version v, bumped under the same rule as components). Record.parse refuses a row whose v differs.

Record timestamps are normalized to UTC.

### Runs
A run is written one row at a time as it happens.

#### RunStarted
- principal author: none; written by the runner
- defined in: `ledger.py:RunStarted`
- suggested storage table: `ledger.run_stage` (stage `started`)

`RunStarted` opens a run with the run id, the time, the rig's code version, the trial, the execution settings, the canary set and when canaries run (`verify_at`, by default at start and end). This row is the run's verification plan. It is declared before the first request and never changes, but it is not part of the trial's identity, so running exactly like run X means copying X's first row. The execution settings (`Execution`) are the order (case-major, replicate-major, or shuffled with a seed), concurrency, timeout and retries. Order and concurrency affect outputs only on engines that are not batch invariant: with batch invariance declared in the engine's `env`, re-runs may be bitwise identical, and otherwise the run falls in the best-effort tier. Timeouts and retries never change a successful output. Neither case drift (comparing the observed vignette fingerprint with the case's) nor canary drift (comparing canary outputs between phases or runs) is checked yet; see "Possible design issues".

#### RunItem
- principal author: none; written by the runner
- defined in: `ledger.py:RunItem`
- suggested storage table: `ledger.run_item`

A run item is keyed by (case, replicate index), never repeating the engine or seed, and holds the vignette fingerprint observed at fetch time, for drift detection, and a `Call`.

#### CanaryCall
- principal author: none; written by the runner
- defined in: `ledger.py:CanaryCall`
- suggested storage table: `ledger.canary_call`

A canary call holds the phase (`start` or `end`), the canary's position in the set, and a `Call`.

#### RunFinished
- principal author: none; written by the runner
- defined in: `ledger.py:RunFinished`
- suggested storage table: `ledger.run_stage` (stage `finished`)

`RunFinished` closes the log with the time, the run's findings, the seal and all canary-call rows.

The seal is the sha256 over the canonical started row and the canonical item rows sorted by their bytes, so it doesn't depend on the order rows are read back in.

#### Call
- principal author: none; written by the runner or scorer as part of another row
- defined in: `ledger.py:Call`
- suggested storage table: inside `ledger.run_item`, `ledger.canary_call` and `ledger.score_item`

A call records one exchange and is shared by run items, canary calls and judge calls. It holds the fingerprint of the wire body, the start and end times, the HTTP status, the number of attempts, any error, and the raw response. The response is stored without its `prompt_token_ids`: those are the token ids the engine actually read after applying its chat template, and since they encode the case text, only their fingerprint is kept (`fingerprint_prompt_tokens`). Comparing these fingerprints between runs of the same trial (`compare_prompt_tokens`) shows whether the engine read the same tokens, without storing case text.

The request fingerprint (Call.request) is `fingerprint_request` taken over the body's canonical bytes.

#### Run
- principal author: none; assembled from stored rows
- defined in: `ledger.py:Run`
- suggested storage table: none

`Run` reassembles a run's log from its rows and rejects stages out of order (`started` → `finished`) or rows from another run; `Run.finish()` produces the finished row. `check_run` compares the run with its trial. It raises for items the trial does not contain, duplicate items, and canary calls outside the plan (a phase not in `verify_at`, a position not in the set, or any canary call when no set is named). It warns when a finished run is missing items, when the engine returned a different model name than declared, when items have no prompt-token fingerprint, and when rows changed after sealing.

### Scores
A score is written the same way as a run.

#### ScoreStarted
- principal author: none; written by the scorer
- defined in: `ledger.py:ScoreStarted`
- suggested storage table: `ledger.score_stage` (stage `started`)

`ScoreStarted` holds the score id, the run it scores, the time, the rig's code version, the scorer code actually running and the scoring.

#### ScoreItem
- principal author: none; written by the scorer
- defined in: `ledger.py:ScoreItem`
- suggested storage table: `ledger.score_item`

A score item holds a run item's key, a view's position, the value (a number, or empty if the item couldn't be scored), optional detail and the judge calls made for it.

#### JudgeCall
- principal author: none; written by the scorer as part of a score item
- defined in: `ledger.py:JudgeCall`
- suggested storage table: inside `ledger.score_item`

A judge call names the judge, the index of the seed used and the `Call`.

#### ScoreFinished
- principal author: none; written by the scorer
- defined in: `ledger.py:ScoreFinished`
- suggested storage table: `ledger.score_stage` (stage `finished`)

`ScoreFinished` closes the log with the time, the findings and the seal over the started row and all item rows. Scores have no canary calls. Judge calls live inside score items, so they are sealed with them.

Just as with `RunFinished`, the seal is the sha256 over the canonical started row and the canonical item rows sorted by their bytes, so it doesn't depend on the order rows are read back in. Record timestamps are normalized to UTC.

#### Score
- principal author: none; assembled from stored rows
- defined in: `ledger.py:Score`
- suggested storage table: none

`Score` reassembles a score's log the way `Run` does, and `Score.finish()` produces the finished row. `check_score` raises when the score belongs to another run, a view position doesn't exist, an item isn't in the run, or a judge call uses a judge the scorer's views don't name or a seed index out of range. It warns when rows changed after sealing.

### Compilation
- principal author: none; written by the compiler
- defined in: `ledger.py:Compilation`
- suggested storage table: `factor.compilation`

A compilation is a single row: a recipe, the skeleton it produced, the compiler's code version and the time. It is the lineage from portal chunks to the frozen request; recompiling with a newer compiler adds a new row, not a new factor. It holds no case text and is not case-derived.

## Linting
Lints (`lint.py`) warn about settings that are valid but risky. They are plain functions over a registry, run on demand; nothing in the manifest stores their findings. Keeping them out of the data layer means knowledge that changes between vLLM releases can change without touching any factor. `lint(registry, digests)` applies one rule per kind. A model artifact whose revision is not a 40-character commit gets `model.revision`, because a branch or tag can move. A local engine whose closure is not a Nix store path gets `engine.closure`. A scorer whose code has no revision gets `scorer.revision`. A trial on a local vLLM 0.24 engine whose skeleton sets a temperature between 0 and 0.01 gets `vllm.temperature_clamped`, because vLLM 0.24 raises such temperatures to 0.01 (an unverified assumption, see "Possible design issues"). This last rule is the only one keyed by the declared runtime version.

## Findings and errors
A finding (`identity.py: Finding`) has a level (`warning` by default, or `info`), a code, a message and optionally the subject it concerns. Findings are how "warnings, not crashes" is implemented: anything declared that doesn't match what was observed, and anything risky, becomes a finding. The run and score checks return their findings, and a run's or score's findings are stored in its finished row. The codes are:

- `attestation.model`: the engine returned a different model name than declared: the engine digest for a local engine, the requested model for a remote one (`check_run`).
- `attestation.prompt_tokens`: some run items have no prompt-token fingerprint, for example because the engine didn't return token ids (`check_run`).
- `attestation.prompt_tokens_drift`: an item read different prompt tokens than the same item in another run (`compare_prompt_tokens`).
- `run.incomplete`: a finished run lacks some of the trial's items (`check_run`).
- `ledger.seal`: a run's or score's rows no longer match the seal in its finished row (`check_run`, `check_score`).
- `engine.chat_template` and `engine.chat_template_date`: the chat-template file doesn't match the engine's declared digest, or reads the current date (`check_chat_template`).
- `bundle.recanonicalized`: the current code would serialize a bundled component differently from its stored bytes, which remain authoritative (`Bundle.load`).
- `model.revision`, `engine.closure`, `scorer.revision` and `vllm.temperature_clamped`: the lints above.

Structurally malformed input raises instead. Constructing a component, canary or record that breaks its own rules raises pydantic's `ValidationError`: for example flags the start-up script owns in `argv`, slots unsuitable for the purpose, a skeleton body at odds with its contract, runtime keys in a body, duplicate seeds or cases, a shuffle seed without shuffled order, or a stage log out of order. Problems that need other components or records to see raise `StructuralError`: a digest that doesn't match its bytes, an unknown kind or schema version, a missing or wrongly typed reference, a failed `cross_check`, a recipe whose prompt purpose differs from its own or whose passthrough overrides a managed key, and the run and score checks' own violations (items outside the trial, unplanned canary calls, a score of another run, views, items, judges or seeds that don't exist).

The one hard block is clearance: sending case-derived content to an engine that isn't cleared, judge engines included, must be refused. Clearance is bookkeeping data and the manifest does not enforce it; the runner must.

## vLLM 0.24 assumptions (for the fake vLLM)
1. ChatCompletionResponse.prompt_token_ids is a top-level list[int] | None, set only when request.return_token_ids is true:
  - https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L129
  - https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L1070-L1072

2. 0 < temperature < 1e-2 is logged and raised to 1e-2 (_MAX_TEMP):
  - https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/sampling_params.py#L428-L438

3. Extra fact: greedy is temperature < 1e-5, checked after the clamp, so only an exact 0 is greedy; that matches Skeleton.greedy.

Fake vLLM based on 0.24.0 should pin both

---

## Possible design issues

### Policy for case-derived content deferred
Completions are stored in plain text in run items, and they can quote or reveal sensitive information.

Completions are in the raw responses of `RunItem.call`, `CanaryCall.call` (non-sensitive) and `JudgeCall.call`.
A policy for such case-derived content has not settled:
 - database-level read restriction on case-derived record tables;
 - a destination check when records are exported.

### Greedy sampling and seeds:
the seed isn't sent, but the trial's seeds still count toward its hash, so two otherwise identical greedy trials differ only in digest.

### Misc
- **Case drift is not checked.** The observed vignette fingerprint is recorded per item but never compared with the case's.
- **Canary drift is not checked.** No function compares canary outputs between phases or between runs. The old code had `compare_canaries`.
- **Judge engines are not verified by canaries** (reconciliation D5, option B, deferred).
- **Skeleton provenance.** Should a skeleton only be accepted with a compilation record? Hand-written ones (e.g. judge prompts) are possible today.
- **Re-binding after vignette drift.** A changed vignette orphans its appendices and expectations; who re-binds them?
- **Records aren't in bundles.** How the ledger is delivered together with the cage isn't specified.
- **Metric names are free strings.** `View.metric` is only meaningful to the scorer code that `Scorer.code` pins; nothing checks that the code knows the name.
- No helper prepares a case, i.e. per-item steps 2–5 (fetch, drift check, cleanup, joining appendices). The old code had one (prepare_case), and it's worth adding back.
- Where the model name comes from (per-item step 6) is repeated in check_run, so it belongs on the engine as one method.
- Nothing compares declared and observed scorer code. ScoreStarted.scorer_code is never checked against Scorer.code, so a mismatch goes unnoticed.
- Missing expectations aren't flagged. Neither the scoring nor check_score warns when a run's case has no expectation, or when a scored item has no expectation behind it.
- Judge calls aren't attested. Their returned model and prompt-token fingerprint aren't checked the way run items are.
- Greedy judges make extra seeds pointless. The test's judge skeleton is greedy, so no seed is sent. Several judge seeds would then send identical requests.
- Case-slot judges need the vignette again. A judge skeleton that uses the case slot makes the scorer fetch the sensitive vignette and redo the trial's cleanup steps. No helper exists for either.
- No helper turns a canary into a request body (model name, seed, `return_token_ids`).
- `Scorer.resources` are called digests but typed as plain strings, so nothing checks their form.
- The closed set of text-cleanup operations has four members. The old code also had non-breaking spaces to spaces, zero-width character removal, Unicode line breaks to newlines and trailing-whitespace stripping; vignettes may need some of them.
- `Hardware` has no GPU count, so tensor-parallel engines can't be told apart by hardware (the old code had `gpu_count`).
- The engine's `system_fingerprint` is kept in each call's raw response (`Call.system_fingerprint`) but never compared between calls or runs.

## Proposed amendments
