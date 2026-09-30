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
- **Trial:** see "Trials".
- **Replicate:** one seed of a trial, identified by its index.
- **Item key:** (case, replicate index), which identifies one request of a run.
- **Execution:** how a run issues requests: order, concurrency, timeout and retries.
- **View:** the part of an output and of an expectation that a scorer scores, and how.
- **Compilation:** the record of which recipe and compiler produced a skeleton.
- **Run** and **Score:** a stage log.
- **Record** / **ledger:** events logged while running or scoring.
- **Seal:** the hash over a finished log.
- **Finding:** a warning or info produced when something declared doesn't match what was observed.
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

- **Orchestrator.** Runs in Kubernetes, owns the manifest and hosts the portal. A postgres database store user-managed data: factor tables are insert-only and keyed by digest; records are append-only; nothing is updated.

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

HMAC was considered for identity and drift protection of the vignettes, but is currently not implemented due to:
- the clinicians working on plaintext anyway;
- anyone holding a case can verify its hash, which helps delivering the cage;
- there are no keys to manage.
- the algorithm field allows HMAC later.

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
1. fingerprint_prompt_tokens(response) takes prompt_token_ids out of the response [why? what are the prompt_token_ids?] and returns their fingerprint. The runner stores a RunItem row with the key (case, replicate), the observed vignette fingerprint and a Call. The Call holds the request's fingerprint, the timings, the attempt count, the response without its prompt token IDs, and the prompt-token fingerprint.
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

The kinds are `model`, `engine.local`, `engine.remote`, the seven `chunk.*` kinds, `skeleton`, `appendix`, `case`, `trial`, `expectation_schema`, `expectation`, `scorer`, `judge`, `scoring` and `canary_set`. Each is produced by a different party: ops, developers, researchers, clinicians, or (for skeletons only) the compiler.

### Case
A case (`cases.py: CaseInput`, kind `case`) names a vignette in the predefined source by source name and id (`SourceCase`), records the fingerprint of that vignette, and lists the appendices to append, in order. Researchers or clinicians create cases in the portal; an import script supplies the vignette fingerprint, taken over the raw vignette as fetched. The vignette itself is sensitive, unstructured and never edited, and it is never stored: only its fingerprint is. The case's digest covers all three parts (source reference, fingerprint and appendix list), so the vignette is not its sole contributor, and the same source case with different appendices is a different case. Expectations are keyed by this digest, which lets appendices change the correct answer.

A changed vignette at the source has a new fingerprint. It therefore needs a new case, new appendices bound to the new fingerprint, and new expectations; whether a user or an automatic step re-binds them is open. Text cleanup is not part of the case: a trial chooses it (see "Trial").

### Appendix
An appendix (kind `appendix`) is a block of plain text that clinicians write in the portal and attach to one source case and one vignette fingerprint. It is the only way to add information to a vignette; anchored edits to the vignette were considered and ruled out as too complex. Appendices are not sensitive. They are stored and sent exactly as entered, and text cleanup does not apply to them.

A case lists its appendices in a pinned order that is part of the case's digest, and rejects any appendix bound to another source case or vignette (`CaseInput.cross_check`), so an appendix is never reused across source cases. It can, however, appear in several cases of the same source case, for example a case with a lab appendix and one without. At send time the appendices are joined into one text by the recipe's `AppendixLayout`, which is frozen into the skeleton (see "Rendering and runtime keys").

### Request
The request side is built in three layers. Researchers author chunks in the portal; a recipe selects one chunk per part; the compiler turns a recipe into a frozen skeleton, which is what trials and judges reference. Chunks affect each other only in two pre-specified ways, both owned by the manifest: the output chunk's guidance is appended to the instructions, and greedy sampling drops `top_p`, `top_k` and `min_p` (the seed is left out at send time). There are no validation retries: the run stores the raw completion and parsing belongs to scoring. Transport retries are a separate matter, declared per run (`Execution.retries`) and counted per call (`Call.attempts`).

#### Chunks
Each chunk is a component that fills one part of a recipe. None of them is a template: text is plain text, and the only runtime placeholders are the prompt's slots, filled by concatenation, so clinical text is never interpreted.

- **Instructions** (`chunk.instructions`) is plain text sent as the first message, with the role `system` or `developer`. Its cross-component effect is passive: the output chunk's guidance is appended to it, separated by a blank line, and becomes the whole message if there are no instructions.
- **FewShot** (`chunk.few_shot`) is a list of plain-text user/assistant example messages, placed after the instructions and before the prompt.
- **Prompt** (`chunk.prompt`) is the user message: a list of segments, each a literal string or a slot, with a purpose of `generation` or `judge`. A generation prompt must contain the `case` slot and may contain `appendices`. A judge prompt must contain `completion` and may contain `case`, `appendices` and `expectation`. No slot may appear twice, and the prompt's purpose must match the recipe's.
- **Output** (`chunk.output`) declares where the answer goes, through one of three contracts. `native` sends a JSON schema as `response_format` (named `output`, strict). `tool` forces a single function call whose parameters are the schema. `text` constrains nothing and may carry a schema that only scoring uses. Its optional guidance is the chunk's cross-component effect: it is appended to the instructions. There is no coercion: parsing belongs to the scorer.
- **Sampling** (`chunk.sampling`) holds temperature, top-p, top-k, min-p, the presence, frequency and repetition penalties, stop sequences and `max_output_tokens`, which is sent under `max_completion_tokens` (default) or `max_tokens`. Its cross-component effect is canonicalization: with temperature 0 it drops top-p, top-k and min-p, so equivalent greedy chunks share a digest, and at send time no seed is added.
- **Reasoning** (`chunk.reasoning`) holds the reasoning effort (`none` to `high`), `thinking_token_budget` and `chat_template_kwargs`.
- **Passthrough** (`chunk.passthrough`) holds engine-specific body keys. It may not set runtime keys or output keys, and compilation fails if it sets a key another chunk also produces.

#### Recipe and compilation
A recipe (`request.py: Recipe`) is not a component. It holds a purpose, an appendix layout, required references to a prompt, an output and a sampling chunk, and optional references to instructions, few-shot, reasoning and passthrough chunks. `compile_request` turns a recipe into a skeleton and fails on missing or wrongly typed chunks. The recipe is kept only in the `Compilation` record, next to the compiler version (see "Records"). Because a trial references the skeleton rather than the recipe, the compiler's code is not a factor.

#### Skeleton
A skeleton (kind `skeleton`) is the frozen request, and the only component produced by code rather than people: `compile_request` builds it from a recipe, and trials and judges reference it. It is fully pre-rendered: the complete chat-completions body except for what is filled in at send time. It holds a purpose, the API (`chat.completions`), the messages as segment lists (so few-shot examples are already baked in as plain text), every other body key, the output contract and the appendix layout. Its validation repeats the chunks' rules: slots must suit the purpose, greedy sampling is canonicalized, a `native` contract needs `response_format` and no tools, a `tool` contract needs exactly one tool of the declared name plus `tool_choice`, and a `text` contract may set no output keys. For `native` and `tool`, the output schema is read from the body rather than stored twice.

#### Rendering and runtime keys
`render(skeleton, model, seed, fills)` produces the wire body. It concatenates each message's segments, replacing each slot with its fill, then adds `model` (the engine digest for local engines, the requested model for remote ones), the messages and the skeleton's body. It adds `seed` only when the skeleton is not greedy, and adds `return_token_ids: true` so the response carries the prompt's token ids (see "Runs"). The appendix fill is produced beforehand by the layout, which puts text before, between and after the appendices (by default a blank line before and between, nothing after) and yields an empty string when there are none. The keys `render` owns (`model`, `messages`, `seed`, `stream`, `n` and `return_token_ids`, listed in `request.py: RUNTIME_KEYS`) may not be set by any chunk, skeleton or canary.

### Engine
An engine is where requests are sent. Ops author engines; researchers pick them for trials and judges.

A **model artifact** (kind `model`) pins a model by its repo, a revision (linted unless it is a commit) and the sha256 of every file, collected by an import script. It is its own component, so several engines can share it.

A **local engine** (kind `engine.local`) is a vLLM server we run. It declares its hardware (GPU, compute capability, VRAM, driver) and runtime (`vllm`, its version, the Nix closure path), references a model artifact, and requires the digest of its chat-template file. Its raw `argv` and `env` are passed to vLLM, except the flags the start-up script owns (`--model`, `--served-model-name`, `--chat-template`, `--tokenizer`, `--revision`), which they may not set. Knowledge that changes between vLLM versions lives in lints rather than in these fields, and batch invariance is declared through `env` (for example `VLLM_BATCH_INVARIANT=1`). The served model name is the engine's digest, so every response names the cage it came from. `check_chat_template` warns when the template file doesn't match the declared digest or reads the current date. Hardware and runtime are part of the engine rather than components of their own. They can therefore not be listed, picked or reused as standalone rows, for example in the portal or as Postgres foreign keys; the same GPU or closure is repeated in every engine that uses it, and asking which engines share a closure means scanning engines instead of following a reference.

A **remote engine** (kind `engine.remote`) is an API we don't control. It declares only the API (`openai.chat`), the `base_url` and the requested model; its chat template is not pinned. Of the identifiers it returns, only the model name is checked today (see `check_run`). Canary probes at the start and end of a run apply to any engine and are planned per run (see "Runs"), so they are not a property of remote engines.

### Trial
A trial (kind `trial`) is a scientific intent, authored by researchers in the portal: a generation skeleton, an engine, the cases, the text-cleanup steps and the seeds. Cases and seeds are listed without duplicates. The seeds are explicit and user-defined; the portal can propose random 31-bit ones (`suggest_seeds`), which users are free to override. Each seed defines one replicate, identified by its index. Text cleanup is an ordered list of operations from a closed set in which each name pins one behavior (`newlines.lf@1`, `unicode.nfc@1`, `strip@1`, `blank_lines.collapse@1`; a changed behavior gets a new name), and it applies to the vignette only. Execution settings (order, concurrency, timeout, retries) are not part of the trial, so re-running a trial means a new run of the same digest. With greedy sampling no seed is sent, but the seeds still count toward the trial's digest (see "Possible design issues").

### Expectation
An expectation schema (kind `expectation_schema`) is a JSON Schema published by scorer developers. It describes the reference data a scorer consumes. An expectation (kind `expectation`) is that reference data for one case, authored in the portal: the case's digest, the schema's digest and the data. Because the key is the case digest, appendices included, the same source case can have different expectations under different appendices. Expectations are not sensitive. The manifest does not validate the data against its schema; the scorer does.

### Scorer
A scorer (kind `scorer`) is pinned scoring code, authored by developers. It declares its code (`Code`: distribution, version, revision), the expectation schema it consumes, an ordered list of views, ordered resource digests (such as synonym tables or ontology releases) and free parameters. Views and resources are referred to by position; their labels belong to bookkeeping. A view scores one part of an output against one part of an expectation: it holds a JSON pointer into each, a metric name that only the scorer's code interprets, optional parameters, and optionally the judge it uses. Parsing is the scorer's job, not the manifest's: outputs that fail validation are scored best-effort.

### Judge
A judge (kind `judge`) is an LLM used as a metric, authored by researchers: a judge-purpose skeleton, an engine and its own seeds. A judge skeleton must contain the `completion` slot and may use `expectation`, `case` and `appendices`. Judge requests go through the same rendering as generation requests, once per seed, and are recorded per score item (`JudgeCall`). Their scores are best-effort reproducible. Because a judge prompt can contain case text, judge engines fall under the same clearance hard block as generation engines.

### Scoring
A scoring (kind `scoring`) is what a score applies, authored by researchers: a scorer plus the expectations it scores against. The expectations must all be in the scorer's schema, and a case may have at most one. The judges a scoring uses are those named by its scorer's views.

### Canary set
A canary set (kind `canary_set`) is a list of fixed, non-sensitive probe requests, authored by ops or developers. Each canary holds literal messages, body keys (no runtime keys) and an optional seed. A run names the canary set it uses (see "Runs"). Canary sets are components because canary drift is found by comparing the same set across runs.

## Records
Records are the ledger: what was observed while compiling, running and scoring. They are not components and not part of the component graph; they reference components by digest and each other by id. Every record table is append-only. A run or a score is a stage log (a started row, item rows and a finished row), and a new stage is a new row type, not a new table. Records that hold completions are case-derived (`Record.case_derived`), which is what the sensitivity policy acts on.

### Runs
A run is written by the runner, one row at a time as it happens. **`RunStarted`** opens it with the run id, the time, the rig's code version, the trial, the execution settings, the canary set and when canaries run (`verify_at`, by default at start and end). This row is the run's verification plan. It is declared before the first request and never changes, but it is not part of the trial's identity, so running exactly like run X means copying X's first row. The execution settings (`Execution`) are the order (case-major, replicate-major, or shuffled with a seed), concurrency, timeout and retries. Order and concurrency affect outputs only on engines that are not batch invariant: with batch invariance declared in the engine's `env`, re-runs may be bitwise identical, and otherwise the run falls in the best-effort tier. Timeouts and retries never change a successful output. Neither case drift (comparing the observed vignette fingerprint with the case's) nor canary drift (comparing canary outputs between phases or runs) is checked yet; see "Possible design issues".

Each **`RunItem`** is keyed by (case, replicate index), never repeating the engine or seed, and holds the vignette fingerprint observed at fetch time, for drift detection, and a `Call`. Each **`CanaryCall`** holds the phase (`start` or `end`), the canary's position in the set, and a `Call`. **`RunFinished`** closes the log with the time, the run's findings and the seal: the sha256 over the started row, all item rows and all canary-call rows.

A **`Call`** records one exchange and is shared by run items, canary calls and judge calls. It holds the fingerprint of the wire body, the start and end times, the HTTP status, the number of attempts, any error, and the raw response. The response is stored without its `prompt_token_ids`: those are the token ids the engine actually read after applying its chat template, and since they encode the case text, only their fingerprint is kept (`fingerprint_prompt_tokens`). Comparing these fingerprints between runs of the same trial (`compare_prompt_tokens`) shows whether the engine read the same tokens, without storing case text.

`Run` reassembles the log and rejects stages out of order (`started` → `finished`) or rows from another run; `Run.finish()` produces the finished row. `check_run` compares the run with its trial. It raises for items the trial does not contain, duplicate items, and canary calls outside the plan (a phase not in `verify_at`, a position not in the set, or any canary call when no set is named). It warns when a finished run is missing items, when the engine returned a different model name than declared, when items have no prompt-token fingerprint, and when rows changed after sealing.

### Scores
A score is written by the scorer in the same way. **`ScoreStarted`** holds the score id, the run it scores, the time, the rig's code version, the scorer code actually running and the scoring. Each **`ScoreItem`** holds a run item's key, a view's position, the value (a number, or empty if the item couldn't be scored), optional detail and the judge calls made for it. A **`JudgeCall`** names the judge, the index of the seed used and the `Call`. **`ScoreFinished`** closes the log with the time, the findings and the seal over the started row and all item rows. `check_score` raises when the score belongs to another run, a view position doesn't exist, an item isn't in the run, or a judge call uses a judge the scorer's views don't name or a seed index out of range. It warns when rows changed after sealing.

### Compilation
A compilation (`ledger.py: Compilation`) is a single row written by the compiler: a recipe, the skeleton it produced, the compiler's code version and the time. It is the lineage from portal chunks to the frozen request; recompiling with a newer compiler adds a new row, not a new factor. It holds no case text and is not case-derived.

## Linting
Fill in: a model revision that isn't a commit, a closure that isn't a Nix store path, scorer code without a revision, and the vLLM 0.24 temperature clamp. Lint rules are keyed by the declared runtime version and kept out of the data layer. Code: `lint.py`.

## Findings and errors
To fill in: The catalogue of finding codes and what each means, versus what raises `StructuralError`/`ValidationError`, and the single hard block. Code: `identity.py: Finding`, `ledger.py`, `engine.py`, `bundle.py`, `lint.py`.

---

## Possible design issues

### Policy for case-derived content deferred
Completions are stored in plain text in `RunRecord`, which can quote or reveal sensitive information.

Completions are in the raw responses of `RunItem.call`, `CanaryCall.call` (non-sensitive) and `JudgeCall.call`.
A policy for such case-derived content has not settled:
 - database-level read restriction on case-derived record tables;
 - a destination check when records are exported.

### Greedy sampling and seeds:
the seed isn't sent, but the trial's seeds still count toward its hash, so two otherwise identical greedy trials differ only in digest.

### Unverified vLLM 0.24 assumptions:
- with `return_token_ids: true`, the response carries a top-level `prompt_token_ids` list;
- temperatures in (0, 0.01) are raised to 0.01.
A fake vLLM based on 0.24.0 should pin both (AGENTS.md asks for one; none exists yet).

### Misc
- **Case drift is not checked.** The observed vignette fingerprint is recorded per item but never compared with the case's.
- **Canary drift is not checked.** No function compares canary outputs between phases or between runs. The old code had `compare_canaries`.
- **Judge engines are not verified by canaries** (reconciliation D5, option B, deferred).
- **Skeleton provenance.** Should a skeleton only be accepted with a compilation record? Hand-written ones (e.g. judge prompts) are possible today.
- **Re-binding after vignette drift.** A changed vignette orphans its appendices and expectations; who re-binds them?
- **Records aren't in bundles.** How the ledger is delivered together with the cage isn't specified.
- **Metric names are free strings.** `View.metric` is only meaningful to the scorer code that `Scorer.code` pins; nothing checks that the code knows the name.
- **Vignette fingerprint: raw or normalized?** See "Case".
- No helper prepares a case, i.e. per-item steps 2–5 (fetch, drift check, cleanup, joining appendices). The old code had one (prepare_case), and it's worth adding back.
- Whether text cleanup should apply to appendices too hasn't been decided.
- The request fingerprint (Call.request) is never defined as "the fingerprint of the rendered body's canonical bytes". A helper would pin that down.
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
