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
- **Canonical form:** sorted-key JSON with fields left out when equal to their default.
- **Chunk:** a factor authored in the portal that fills one slot of a request.
- **Recipe:** the chunks a skeleton was compiled from.
- **Skeleton:** the frozen request.
- **Slot:** a placeholder filled at send time.
- **Segment:** a literal string or a slot.
- **Trial:** see "Trials".
- **Replicate:** one seed of a trial, identified by its index.
- **Run** and **Score:** a stage log.
- **Record** / **ledger:** events logged while running or scoring.
- **Seal:** the hash over a finished log.
- **Finding:** a warning or info produced when something declared doesn't match what was observed.
- **Canary:** a fixed, non-sensitive probe request.
- **Case-derived:** content produced from a vignette.

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

## Current stack situation
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
Factors are immutable components. They reference each other by the digest of their canonical form which forms a graph.

The canonical form:
- leaves out fields equal to their default (a new field must default to the old behavior, and a default must never change without bumping the kind's schema version)
- each kind has its own schema version
- bundles store canonical bytes.

References are typed. `Annotated[Digest, RefTo(kind, …)]` gives:
 - the reference graph and its checks (`bundle.py: Registry.check`);
 - the `x-ref` entries in the JSON Schema, from which the portal and Postgres foreign keys can be derived.

Cross-component rules (e.g. a trial must use a generation skeleton) are `cross_check` hooks.

The factors are:
 - `model`, `engine.local`, `engine.remote`;
 - the seven `chunk.*` kinds;
 - `skeleton`, `appendix`, `case`, `trial`;
 - `expectation_schema`, `expectation`, `scorer`, `judge`, `scoring`;
 - `canary_set`.

### Case
A case is a reference to a text file fetched from an uncertain environment. Their content is called vignette, which is sensitive.

Vignettes are unstructured and cannot be edited. so drift is detected by comparing fingerprints. The fingerprint is taken over the raw vignette as fetched.

In the code, a case (`cases.py: CaseInput`, kind `case`) is three things: a source reference (`SourceCase`: source name + id), the vignette's fingerprint, and an ordered list of appendix digests. So:
 - the case's *digest* covers source, id, vignette fingerprint and appendices. The vignette is not its "sole contributor";
 - the *vignette fingerprint* is a field of the case, supplied when the case is created.

Normalization is chosen per trial (`trial.py: Trial.normalization`). The set of operations is closed, and each name pins one behavior: `newlines.lf@1`, `unicode.nfc@1`, `strip@1`, `blank_lines.collapse@1` (`cases.py: NormalizeOp`). A changed behavior gets a new name.

Expectations are keyed by the case, including its appendices, so appendices can change the correct answer.

When the source's vignette changes, its fingerprint changes. That means a new case, new appendices bound to the new fingerprint, and new expectations. Who re-binds them, a user or an automatic step, is still open.

### Appendix
Appendices are notes bound to a source case and vignette fingerprint. It is the only means for clinicians to attach additional information to a vignette.

They are appended to the case in a pinned order of `CaseInput.appendices`, which is part of the case's digest.

They are joined into the prompt with the recipe's `AppendixLayout` (text before, between and after), which is frozen into the skeleton.

Appendices are written and maintained in the portal and are not sensitive.

Appendix text is stored and sent as entered; normalization applies only to the vignette.

A case rejects appendices bound to another case or vignette (`CaseInput.cross_check`), so appendices can't be reused across cases. Why can they even be bound to more than one case though?

Note: Anchored edits has been considered but ruled out due to the complexity of such implementation.

### Request
Request chunks fill typed slots, and "cross-component effects" happen only in pre-specified ways. The manifest owns this mechanics:
  - output type injects guidance into the instructions;
  - canonicalization, e.g. greedy sampling drops the seed, top_p, top_k and min_p.

- **No validation retries.** Parsing moves to scoring; a run stores the raw completion, transport retries exist, are declared per run (`trial.py: Execution.retries`), and are counted per call (`ledger.py: Call.attempts`). (True for *parsing*).

#### Chunks
(list each chunk along with the special "cross-component effects" attached to them)
`chunk.sampling`
`chunk.reasoning`
`chunk.passthrough`

 - **Instructions** (`chunk.instructions`): *plain text*, **not a Jinja2 template**. The design avoids templating everywhere, so clinical text is never interpreted. The role is `system` or `developer`.
 - **FewShot** (`chunk.few_shot`): user/assistant example messages in plain text, placed between the instructions and the prompt.
 - **Prompt** (`chunk.prompt`): a list of segments, each a literal string or a slot.
   - A generation prompt must contain the `case` slot exactly once and may contain `appendices` once.
   - A judge prompt must contain `completion` and may use `case`, `appendices` and `expectation`.
   - "A string or slot for JIT insertion" understates this.

 - **Output** (`chunk.output`): one of three contracts:
   - `native`: `response_format` with a JSON schema;
   - `tool`: one forced function call;
   - `text`: optionally carrying a schema used only by scoring.

    It also carries optional guidance, which is appended to the instructions.
    There is **no coercion method**, because parsing belongs to the scorer.

 - **Sampling** (`chunk.sampling`): temperature, top-p/k, min-p, penalties, stop sequences, and `max_output_tokens`, sent as `max_completion_tokens` or `max_tokens`.
 - **Reasoning** (`chunk.reasoning`): effort, `thinking_token_budget` and `chat_template_kwargs`.
 - **Passthrough** (`chunk.passthrough`): engine-specific body keys. It may not set runtime or output keys, and it may not override a key another chunk sets.

#### Recipe and compilation
The chunks a skeleton was compiled from form a `Recipe`:
 - an optional reference each to instructions, few-shot, reasoning and passthrough;
 - required references to prompt, output and sampling;
 - a purpose;
 - an appendix layout.

A recipe is not a component. It is stored in the `Compilation` record together with the compiler version (`request.py: Recipe`, `ledger.py: Compilation`). A trial references only the skeleton, so compiler code is not a factor.

#### Skeleton
Skeleton is the only derived component i.e. the only component the code produces itself (through `compile_request`).
Is the result of compiling a recipe, and trials and judges reference it.
- Requests are fully pre-rendered. The complete chat-completions body with runtime slots for the case. Slots are segment lists rather than templates, so clinical text is never accidentally modified.

 - A skeleton contains the messages as segment lists, every other body key, the output contract and the appendix layout.
 - At send time, `render` fills the slots and adds `model` (the engine digest for local engines), `seed` (unless greedy) and `return_token_ids`.
 - No chunk or skeleton may set `model`, `messages`, `seed`, `stream`, `n` or `return_token_ids` (`request.py: RUNTIME_KEYS`).
 - "Accidentally modified" → "never interpreted".

#### Rendering
## Runtime Keys
To fill in: What `render` adds at send time, the keys no factor may set, greedy seed handling, and the appendix layout. Code: `request.py: render`, `RUNTIME_KEYS`.

### Expectation
Expectation schemas (from scorer developers) and expectations (from the portal), keyed by case including appendices; not sensitive; at most one per case in a scoring. Code: `scoring.py: ExpectationSchema`, `Expectation`.

### Judge
Judge-purpose skeletons and their slots (`completion`, `expectation`, `case`, `appendices`), engine, seeds, how views point at them, and the clearance hard block. Code: `scoring.py: Judge`, `request.py: SLOTS_BY_PURPOSE`.

### Scoring
A scoring (`scoring.py: Scoring`) is a scorer plus the list of expectations it scores against: at most one per case, all in the scorer's schema. Put that here; the bullets above belong to "Scorer".

 - a JSON pointer into the output;
 - a JSON pointer into the expectation;
 - a metric name, interpreted by the scorer's code;
 - optional judge and parameters.

### Scorer
- pinned code (`Code`: distribution, version, revision);
- the expectation schema it consumes;
- an ordered list of views;
- ordered resource digests (synonym tables, ontology releases), referred to by position;
- free parameters.
Scorers are pinned code and resources (synonym tables, ontology releases).

- Each scorer declares the expectation schema it consumes.
- **Views** reach into the output and expectation schemas and score parts of them.
- **Outputs that fail validation are scored best-effort.** parsing is the scorer's job, not the manifest's.
- **Judges** are sent as judge-purpose skeletons through our own runner. Their scores are best-effort reproducible.

#### Judges
 - A judge (`scoring.py: Judge`) is a judge-purpose skeleton, an engine and its own seeds. A view points at the judge it uses.
 - Judge calls are recorded per score item (`ledger.py: JudgeCall`), with the index of the seed used.
 - Judge engines are covered by the clearance hard block.

### Engine
Chat template
- Local engines must declare a hash of their chat-template file.
- Templates that read the current date are flagged.

Model artifacts... Repo, revision (a commit, or it's linted) and per-file sha256; reused across engines.

#### Remote engines
Declare the endpoint and the requested model. Attest the returned identifiers. Run canary probes at the start and end of each run.
`base_url`, the requested `model` and `api` (`openai.chat`). Its chat template is not pinned. "Attest the returned identifiers": only the returned `model` is checked today. "Canary probes at the start and end": canaries apply to any engine and are planned per run, so this is a runner rule, not a remote-engine property.

Note: `Hardware` and `Runtime` are properties of `LocalEngine`, so they can't be listed, picked or reused as standalone rows, for example in the UI or as Postgres foreign keys. The same GPU or closure is repeated in every engine that uses it. Asking "which engines share this closure" means scanning engines instead of following a reference.

A local engine consists of:
- **Hardware:** GPU, compute capability, VRAM, driver.
- **Runtime:** `vllm`, its version, the Nix closure path.
- **A reference to a `ModelArtifact`:** repo, revision and the sha256 of every file. This is its own component, reusable across engines; it may deserve its own subsection.
- **A required chat-template file digest.** Templates that read the current date are flagged (`check_chat_template`).
- **Raw `argv` and `env`,** with the start-up-script-owned flags forbidden. Version-specific knowledge lives in lints, not in the data layer.
- **The served model name,** which is the engine's digest.

Batch invariance is declared through `env` (e.g. `VLLM_BATCH_INVARIANT=1`).

### Trials
A trial is a scientific intent: skeleton × engine × cases × normalization × seeds.
- **Seeds are explicit and user-defined.** The UI suggests random ones that users are free to override. The helper `trial.py: suggest_seeds` proposes 31-bit random seeds.

 - The skeleton must be a generation skeleton.
 - Seeds are explicit and duplicate-free. A replicate is identified by its seed's index. The portal suggests random seeds (`suggest_seeds`).
 - Normalization is an ordered list of pinned operations (see "Case").
 - Execution settings are not part of the trial.
 - "Re-running a trial" means a new run of the same trial digest.

With greedy sampling the seed isn't sent, but they still count toward the trial's digest (see "Possible design issues").

## Records
### Verification
- Requests ask for token IDs, and each item stores a fingerprint of the prompt tokens so runs can be compared.
  - CanarySet: A set of canary probe requests to detect changes in the model.
- Records (runs, scores and compilations) are a separate ledger outside of the component graph that reference components by digests. It is written as a row as it happens:
  - a `RunStarted` row;
  - item rows and canary-call rows;
  - a `RunFinished` row carrying the end time, the run's warnings and a seal (a hash over the started row and all item rows and all canary-call rows).
  - **No repeated keys:** item rows are keyed by (case, replicate index) and never repeat the engine or the seed. Checks against the trial reject items the trial does not contain.
  - **Rows changed after sealing** produce a warning, in keeping with "warnings, not crashes".

Chat template and prompt-token checks:
- Local engines must declare a hash of their chat-template file.
- Templates that read the current date are flagged.
- Requests ask for token IDs, and each item stores a fingerprint of the prompt tokens so runs can be compared.
- Requests are fully pre-rendered. The complete chat-completions body with runtime slots for the case. Slots are segment lists rather than templates, so clinical text is never accidentally modified.

A verification plan is declared in the run's first log row (`RunStarted`, next to the trial, execution settings and the canary set) and when canaries run. It is declared before the run and never changes afterwards, but it is not part of trial identity. "Run it exactly like run X" means copying X's first row. Canary sets stay components, because canary drift is found by comparing the same set across runs.
- canary calls must be planned: their phase is in `verify_at`, and their probe index exists in the canary set;
- a run without a canary set may not contain canary calls (`ledger.py: check_run`).
- the canary set, a component: literal, non-sensitive probe requests, each with an optional seed;
- the plan in `RunStarted`;
- the calls.
Canary sets, the per-run plan, canary calls, and the drift checks still to be built.

### Runs
**Execution is declared per run in `RunStarted`**: order (case-major, replicate-major, or shuffled with a seed), concurrency, timeout and retries. If batch invariance is declared in the engine's environment, re-runs may be bitwise identical. Without it the run falls in the best-effort tier. Canaries, prompt-token fingerprints and comparing completions between runs of the same trial show which case applies. Timeouts and retries never change a successful output. The attempt count is recorded per call.

- `RunStarted` carries the run id, time, rig code version, trial, execution settings, canary set and `verify_at`.
- Each `RunItem` carries the item key, the *observed* vignette fingerprint (for drift) and a `Call`.
- `RunFinished` carries the time, findings and seal.
- Stages must follow the order `started` → `finished`, and every row must belong to the same run.
- A new stage is a new row type, not a new table.
**A Call** is shared by run items, canary calls and judge calls:
- the fingerprint of the wire body;
- start and end times, HTTP status;
- the raw response with `prompt_token_ids` removed, plus the prompt tokens' fingerprint;
  - attempts;
  - error.

### Scores
- `ScoreStarted`: score id, run id, time, rig code, observed scorer code, scoring.
- `ScoreItem`: item key, view index, value, detail, judge calls.
- `ScoreFinished`: time, findings, seal.
- Score items must refer to items in the run and to views that exist; judge calls must use a judge from the scorer's views and a seed index in range (`ledger.py: check_score`).

### Compilation

A compilation (`ledger.py: Compilation`) is one row: a recipe, the skeleton it produced, the compiler's code version and the time. It is the lineage from portal chunks to the frozen request. Recompiling with a newer compiler adds a new row, not a new factor. It is not case-derived.

Run and score records are case-derived, because they hold completions. Compilations are not (`ledger.py: Record.case_derived`).

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
- No helper prepares a case, i.e. steps 8–11 (fetch, drift check, cleanup, joining appendices). The old code had one (prepare_case), and it's worth adding back.
- Whether text cleanup should apply to appendices too hasn't been decided.
- The request fingerprint (Call.request) is never defined as "the fingerprint of the rendered body's canonical bytes". A helper would pin that down.
- Where the model name comes from (step 12) is repeated in check_run, so it belongs on the engine as one method.
- Nothing compares declared and observed scorer code. ScoreStarted.scorer_code is never checked against Scorer.code, so a mismatch goes unnoticed.
- Missing expectations aren't flagged. Neither the scoring nor check_score warns when a run's case has no expectation, or when a scored item has no expectation behind it.
- Judge calls aren't attested. Their returned model and prompt-token fingerprint aren't checked the way run items are.
- Greedy judges make extra seeds pointless. The test's judge skeleton is greedy, so no seed is sent. Several judge seeds would then send identical requests.
- Case-slot judges need the vignette again. A judge skeleton that uses the case slot makes the scorer fetch the sensitive vignette and redo the trial's cleanup steps. No helper exists for either.
