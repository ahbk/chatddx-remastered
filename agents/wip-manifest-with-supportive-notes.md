# Rig manifest: requirements and design notes

<!--
Working copy of the human draft of docs/manifest.md, with supportive notes by Claude (agent), 2026-09-29.
The notes use the code in src/chatddx/core/manifest/ (branch head 0b40eda) as the truth.
Each note is a blockquote starting with a verdict:
  KEEP     the text matches the code;
  CHANGE   the text contradicts the code or is unclear, with the fix;
  ADD      something the section is missing;
  MOVE     the text belongs in another section;
  REMOVE   the text or section should go.
Code references are written `module.py: Symbol`, relative to src/chatddx/core/manifest/.
-->

## Purpose
A rig to measure how accurately LLMs generate differential diagnoses and management plans from emergency-care case vignettes.
This manifest specifies the rules for:
- an environment that can be provably reconstructed (the "cage").
- observing and recording each exchange within the cage.
- adhering to the policies that govern the data the touches the system.

> **CHANGE:** "the data the touches" → "the data that touches".
>
> **CHANGE:** The third bullet now belongs mostly to bookkeeping, because clearance moved there. What the manifest itself still does for policy:
> - it keeps case text out of every component and every record, storing only fingerprints (`identity.py: Fingerprint`);
> - it marks which records are case-derived (`ledger.py: Record.case_derived`).
>
> Suggested wording: "…and for keeping sensitive data out of what is stored and exported, so that the policies governing it can be enforced."
>
> **ADD:** The old doc's goal sentence is worth keeping: "It must be possible to deliver the cage together with the results, for scientific rigor." Everything in the identity design (digests, bundles, sealed logs) exists for it.

## Glossary
- Factor: A meaningful set of parameters that affects output
- Fingerprint: A sha256 digest calculated from factor parameters
- Pinned: A value that can't be altered without changing the fingerprint of one or more factors.
- Sensitive: Data that is forbidden from being stored on, or passed to, non-approved sources.
- World: Factor parameters outside of the orchestrator's direct control.
- Bundle: All factors combined
- Cage: The constrained environment a bundle enforces
- Portal: The web-interface used to configure factors, watch runs and export results.

> **CHANGE, Factor:** The code calls a factor a *component* (`identity.py: Component`). It also includes things that affect the *score*, not only the output: scorer, expectations, judges. Suggested: "Factor (component in code): an immutable, content-addressed set of parameters that affects an output or its score."
>
> **CHANGE, Fingerprint:** The code has two separate hash concepts, and the glossary merges them.
> - **Digest:** `sha256:<hex>` over a component's canonical bytes. It is the component's identity, and references use it (`identity.py: Component.digest`).
> - **Fingerprint:** a hash of content that must *not* be stored: the vignette, the wire body, the prompt token ids. It carries its algorithm: `sha256`, or `hmac-sha256` with a `key_id` (`identity.py: Fingerprint`).
>
> "Pinned" should then say "digest", not "fingerprint".
>
> **CHANGE, Bundle:** It isn't "all factors". It is a set of root components plus everything they reference (the closure), stored as canonical text keyed by digest, together with the code version that produced it. It can be verified offline, byte for byte (`bundle.py: Bundle`, `Registry.bundle`, `Bundle.load`). Records are not in bundles today; see "Missing sections".
>
> **CHANGE, World:** No code uses this concept yet. If kept, name examples: the vignette source, model files, the Nix closure, the chat-template file, the remote engine.
>
> **ADD terms the rest of the doc relies on:**
> - **Kind:** the discriminator of a component, e.g. `engine.local`, `chunk.prompt`.
> - **Canonical form:** sorted-key JSON with fields left out when equal to their default.
> - **Chunk:** a factor authored in the portal that fills one slot of a request.
> - **Recipe:** the chunks a skeleton was compiled from.
> - **Skeleton:** the frozen request.
> - **Slot:** a placeholder filled at send time.
> - **Segment:** a literal string or a slot.
> - **Trial:** see "Trials".
> - **Replicate:** one seed of a trial, identified by its index.
> - **Run** and **Score:** a stage log.
> - **Record** / **ledger:** everything written while running or scoring.
> - **Seal:** the hash over a finished log.
> - **Finding:** a warning or info produced when something declared doesn't match what was observed.
> - **Canary:** a fixed, non-sensitive probe request.
> - **Case-derived:** content produced from a vignette.

## Scope rule
The manifest details:
- Every factor that can affect output, and every field that makes a factor what it is.
- A ledger recording each step of the request, the response and the scoring.
- WIP governance and metadata (provisionally called bookkeeping)...

> **CHANGE:** The third bullet contradicts the design. Bookkeeping (ownership, names, tags, collaborators, version chains, clearance) is deliberately *outside* the manifest. Suggested replacement: "Excluded: anything with no bearing on output or score: names, descriptions, authors, owners, tags, labels, collaborators, version history and clearance. These live in the bookkeeping layer, which references factors by digest and records by id."
>
> **ADD:** The old rule "observations are recorded separately from declared factors" still holds and should stay: factors are components, observations are records (`ledger.py`).
>
> **ADD:** Also out of scope: portal state that hasn't been compiled or saved yet, e.g. a recipe being assembled in the UI.

## Requirements
- Due to the chaotic nature of both emergency clinicians and inference servers alike, LLM judges will be needed to be carefully orchestrated to score outputs against sloppy notes.
- Conversely, simple and reliable scoring methods are needed to produce clean easily processed outputs, which will require strict and simple pattern matching.
- Due to the sensitive nature of patient data passing through the orchestrator, a separate path is needed where data can be passed along revealing just enough for the orchestrator to fulfill its duty. Cases will enter the system partly de-identified from a predefined source. The source exposes no revision identifier.
- Expectations, case appendices, request-time parameters and batch runners need to be administrated by regular users through the portal.
- The result from scorers needs to be aggregated and exported. Since python is well-suited for statistical analyzis, processing the data into publishable results may be on the table.
- Vignettes are unstructured and cannot be edited. They may or may not include labs and imaging results, and may even lack basic details such as age or sex.
- a self-contained, materialized export (the bundle) that World can read.
- scripts that can read the World to import factor parameters.

> **KEEP, bullets 1–2:** They match `scoring.py`. Judges are `Judge` components that a `View` points at. Pattern matching is scorer code, named by `View.metric` and pinned by `Scorer.code`.
>
> **CHANGE, bullet 3:**
> - "Partly de-identified" contradicts the old doc ("already de-identified") and the non-sensitivity decision for appendices. Settle which one is true. If de-identification is partial, say who finishes it (the clinicians, on dedicated storage).
> - "A separate path … revealing just enough": the code realises this by never storing the vignette, wire bodies or prompt token ids; only fingerprints are stored (`ledger.py: Call.request`, `Call.prompt_tokens`, `fingerprint_prompt_tokens`). Completions *are* stored in plain text; see "Possible design issues".
>
> **CHANGE, bullet 4:** Replace "batch runners" with what users actually author.
> - **Chunks:** instructions, few-shot, prompt, output, sampling, reasoning, passthrough.
> - **Recipes,** which are compiled into skeletons.
> - **Appendices, expectations, trials, judges, scorings,** and starting runs.
>
> Engines, models, canary sets, expectation schemas and scorers are authored by ops or developers.
>
> **CHANGE, bullet 5:** Nothing in the manifest aggregates or exports scores yet. Either mark this as a requirement on a later layer, or add an "Export" section (see "Missing sections"). "analyzis" → "analysis".
>
> **MOVE, bullet 6:** This describes cases; move it to "Case".
>
> **CHANGE, bullets 7–8:** These read as requirements of the bundle and of import tooling. Suggested:
> - "a self-contained export (bundle) that can be verified offline and read by the container's start-up script";
> - "import scripts that fingerprint World inputs (model files, closure, chat template, vignettes) into factors". No such scripts exist yet.

## Current stack situation
- **Local inference.** Two servers, an RTX 3070 (sm_86, 8 GB) and an RTX 5090 (sm_120, 32 GB). Both serve vLLM 0.24.0 in a NixOS container that reads load-time parameters from the manifest.
- **Remote inference.** An A100 serving Gemma behind an OpenAI-compatible API. More may follow. We don't control the engine and it can change without notice, so some uncertainty is accepted.
  - The endpoint is vetted for sensitive cases.
  - It honors `seed` and returns model identifiers.
- **Orchestrator.** Runs in Kubernetes and owns the manifest. Its web UI is for authoring reusable, composable request-time chunks, appendices and expectations, all stored in Postgres.

> **KEEP:** This is accurate.
>
> **CHANGE, local:** Be precise about what the container reads. It reads a `LocalEngine`:
> - the model files, via `ModelArtifact`;
> - the chat-template file;
> - raw `argv`/`env`.
>
> The start-up script owns `--model`, `--served-model-name` (set to the engine digest), `--chat-template`, `--tokenizer` and `--revision` (`engine.py: OWNED_FLAGS`).
>
> **CHANGE, remote:** "Vetted" is now a bookkeeping fact (clearance), not a manifest fact. "Returns model identifiers": the run check compares the returned `model` with the declared one (`ledger.py: check_run`). `system_fingerprint` is kept in the raw response but not checked.
>
> **CHANGE, orchestrator:** "Portal" is the glossary term; use it. Add: "factor tables are insert-only and keyed by digest; records are append-only; nothing is updated".
>
> **ADD:** "No comparison between the 3070 and the 5090; each hardware class is its own cage." This was a decision in the old doc and is still true: `Hardware` is part of the engine digest.

## Compromises
- **Two tiers: bitwise and best-effort.** Bitwise reproducibility is not pursued once its cost exceeds its value; delivering the cage is the goal.

- **Frictionless development.** A mismatch between what is declared and what is observed produces a warning, never a crash. Examples:
  - a served model name that isn't the engine digest;
  - case drift;
  - attestation differences;
  - canary drift.

  The runner warns; it does not refuse. Only structurally malformed specs raise errors.

> **CHANGE, tiers:** The code declares no tier (the old tier assessment was dropped). The tier is *observed*:
> - an engine with batch invariance in its `env` is expected to reproduce bitwise;
> - otherwise best-effort.
>
> Canaries, prompt-token fingerprints and comparing completions show which one applies. Say so explicitly.
>
> **CHANGE, examples:** List what the code actually reports (`identity.py: Finding` codes):
> - `attestation.model`: returned model ≠ declared;
> - `attestation.prompt_tokens`: prompt tokens have no fingerprint;
> - `attestation.prompt_tokens_drift`;
> - `run.incomplete`;
> - `ledger.seal`: rows changed after sealing;
> - `engine.chat_template`, `engine.chat_template_date`;
> - `bundle.recanonicalized`;
> - plus the lints in `lint.py`.
>
> **Case drift and canary drift are not implemented.** `RunItem.vignette` records the observed fingerprint, but nothing compares it with `CaseInput.vignette`, and nothing compares canary outputs between phases or runs. Keep them as intended behavior and list them under "Possible design issues".
>
> **ADD:** State the one exception to "warnings, not crashes": sending case-derived content to an engine without clearance is a hard block, judge engines included. Its data now lives in bookkeeping, but the rule belongs here.
>
> **ADD more compromises decided during reconciliation:**
> - plain sha256 fingerprints instead of HMAC (currently a note under "Case"; it fits here better);
> - execution settings are not part of trial identity;
> - the remote engine's chat template is not pinned;
> - greedy trials still hash their seeds.
>
> **CHANGE:** "Structurally malformed specs" → "structurally malformed specs and records". The code raises `StructuralError` or pydantic `ValidationError` for both (e.g. run items outside the trial, stages out of order).

## Orchestrator dependencies
  - Requests/response-handling: Hand-made and, to the extent it actually helps, pydantic-ai.
  - Scoring: text-matching functions and LLM judges.
  - pydantic 2.13.5 and Python 3.13+.

> **KEEP:** pydantic 2.13.5 and Python 3.13+ match `pyproject.toml`.
>
> **CHANGE:** pydantic-ai isn't used anywhere in the code. The earlier decision was "compile-time capture only; drop it if it adds no value". Either keep that wording or drop pydantic-ai from this list. The same decision is repeated under "Request"; keep it in one place.
>
> **ADD:** The same package is imported by the orchestrator, the runner, the scorer and the container's start-up script, and may move to its own repo (from the old doc).

## Factors
Factors affecting the output are grouped in components organized into a content-addressed component graph by.

> **CHANGE:** The sentence is unfinished ("…graph by."). Suggested: "Factors are immutable components. Each one is identified by the digest of its canonical form and references others by digest, which forms a graph."
>
> **ADD / MOVE:** The canonical-form bullets under "Request" apply to *every* component. Move them here, or into a new "Identity" section (see "Missing sections"):
> - defaults are left out of the canonical form;
> - each kind has its own schema version;
> - bundles store canonical bytes.
>
> Also add the rule the first bullet implies: a new field must default to the old behavior, and a default must never change without bumping the kind's schema version (`identity.py: Frozen._serialize`, `Component.schema_version`).
>
> **ADD:** References are typed. `Annotated[Digest, RefTo(kind, …)]` gives:
> - the reference graph and its checks (`bundle.py: Registry.check`);
> - the `x-ref` entries in the JSON Schema, from which the portal and Postgres foreign keys can be derived.
>
> Cross-component rules (e.g. a trial must use a generation skeleton) are `cross_check` hooks.
>
> **ADD:** A list of the kinds with a single-line description each would anchor the subsections that follow:
> - `model`, `engine.local`, `engine.remote`;
> - the seven `chunk.*` kinds;
> - `skeleton`, `appendix`, `case`, `trial`;
> - `expectation_schema`, `expectation`, `scorer`, `judge`, `scoring`;
> - `canary_set`.

### Case
A case is a reference to a text file fetched from an uncertain environment. Their content is called vignette, which is sensitive.
The vignette is normalized through a pinned predefined ordered set of operations.
The normalized vignette is the input of the case's hash function and the sole contributor to its fingerprint.

Note: HMAC has been considered to ensure integrity of the vignettes, but is currently not implemented due to:
- the clinicians working on plaintext anyway;
- anyone holding a case can verify its hash, which helps delivering the cage;
- there are no keys to manage.
- the algorithm field allows HMAC later.

Accepted risks: confirming that a known text is in the study, brute-forcing of short values.

> **CHANGE:** In the code, a case (`cases.py: CaseInput`, kind `case`) is three things: a source reference (`SourceCase`: source name + id), the vignette's fingerprint, and an ordered list of appendix digests. So:
> - the case's *digest* covers source, id, vignette fingerprint and appendices. The vignette is not its "sole contributor";
> - the *vignette fingerprint* is a field of the case, supplied when the case is created.
>
> **CHANGE, normalization:** It is not part of the case. It is chosen per trial (`trial.py: Trial.normalization`). The set of operations is closed, and each name pins one behavior: `newlines.lf@1`, `unicode.nfc@1`, `strip@1`, `blank_lines.collapse@1` (`cases.py: NormalizeOp`). A changed behavior gets a new name.
>
> The fingerprint should therefore be taken over the **raw** vignette as fetched, not the normalized one. Otherwise:
> - it would depend on a trial's choice;
> - drift at the source could hide behind normalization.
>
> Decide this explicitly. The code doesn't enforce either, but raw is the consistent choice.
>
> **MOVE, from "Requirements":** "Vignettes are unstructured and cannot be edited. They may or may not include labs and imaging results, and may even lack basic details such as age or sex." Add: "The source exposes no revision identifier, so drift is detected by comparing fingerprints."
>
> **CHANGE, HMAC note:**
> - "Ensure integrity": the fingerprint serves identity and drift detection. The source's integrity is outside IT control, as the old doc said.
> - Add to the accepted risks: whether a hash of pseudonymised health data counts as personal data (GDPR) is for the data protection officer.
> - Consider moving the note to "Compromises".
>
> **ADD:** "Expectations are keyed by the case, including its appendices, so appendices can change the correct answer." Expectations currently have no section; see "Missing sections".
>
> **ADD:** When the source's vignette changes, its fingerprint changes. That means a new case, new appendices bound to the new fingerprint, and new expectations. Who re-binds them, a user or an automatic step, is still open.

### Appendix
Appendices are notes bound to a case's fingerprint. It is the only means for clinicians to attach additional information to a case.
They are appended to the case in a pinned predefined order.
Appendices are meant to be entered through the portal and are not sensitive.
Unlike vignettes, any processing are applied post save and they are passed to the request as is.

Note: Anchored edits has been considered but ruled out due to the complexity of such implementation.

> **CHANGE, binding:** An appendix is bound to the source case *and* the vignette fingerprint (`cases.py: Appendix.case`, `Appendix.vignette`), not to the case's digest. That can't be otherwise, because the case's digest includes its appendices. A case rejects appendices bound to another case or vignette (`CaseInput.cross_check`). Appendices can't be reused across cases.
>
> **CHANGE, order:** The order is not predefined. It is the order of `CaseInput.appendices`, which is part of the case's digest. How they are joined into the prompt is the recipe's `AppendixLayout` (text before, between and after), which is frozen into the skeleton.
>
> **CHANGE:** "Any processing are applied post save" doesn't match the code. No processing is applied: normalization applies to the vignette only, and appendix text is sent verbatim. Suggested: "Appendix text is stored and sent as entered; normalization applies only to the vignette."
>
> **KEEP:** "Not sensitive"; the anchored-edits note.

### Request
The following factors are combined in a request:
  - Skeleton: A capture of the shape/constraints of a generation.
  - Output: A jsonschema for structured output (or None for free text), a coercion method and guidance injected into the instruction template.
  - Instructions: A Jinja2 template that will be resolved and passed as system prompt with the request.
  - CanarySet: A set of canary probe requests to detect changes in the model.
  - Prompt: A string or slot for JIT insertion.
  - FewShot: A set of examples to prime the model before the actual instruction.

Request chunks fill typed slots, and "cross-slot effects" happen only in pre-specified ways. The manifest this mechanics:
  - output type injects guidance into the instructions;
  - canonicalization, e.g. greedy sampling drops the seed, top_p, top_k and min_p.

The canonical form:
- leaves out fields equal to their default
- each kind has its own schema version
- bundles store canonical bytes.

Chat template and prompt-token checks:
- Local engines must declare a hash of their chat-template file.
- Templates that read the current date are flagged.
- Requests ask for token IDs, and each item stores a fingerprint of the prompt tokens so runs can be compared.
- Requests are fully pre-rendered. The complete chat-completions body with runtime slots for the case. Slots are segment lists rather than templates, so clinical text is never accidentally modified.
- **No validation retries.** Parsing moves to scoring; a run stores the raw completion.
- **pydantic-ai: compile-time capture only.** Drop it if it adds no value.
- **Seeds are explicit and user-defined.** The UI suggests random ones that users are free to override.

- A verification plan is declared in the run's first log row (`RunStarted`, next to the trial, execution settings and the canary set) and when canaries run. It is declared before the run and never changes afterwards, but it is not part of trial identity. "Run it exactly like run X" means copying X's first row. Canary sets stay components, because canary drift is found by comparing the same set across runs.

> This section needs the most work. Suggested structure: **Chunks** → **Recipe and compilation** → **Skeleton** → **Rendering**. Details follow.
>
> **CHANGE, the list:** It mixes the product with its ingredients and leaves some chunks out.
> - **Skeleton** isn't a chunk. It is the *result* of compiling a recipe (`request.py: compile_request`), and trials and judges reference it.
> - **CanarySet** isn't part of a request. It belongs to run verification; **MOVE** it to "Records".
> - **Missing chunks:** `chunk.sampling`, `chunk.reasoning`, `chunk.passthrough`.
>
> **CHANGE, per chunk** (all in `request.py`):
> - **Instructions** (`chunk.instructions`): *plain text*, **not a Jinja2 template**. The design avoids templating everywhere, so clinical text is never interpreted. The role is `system` or `developer`.
> - **FewShot** (`chunk.few_shot`): user/assistant example messages in plain text, placed between the instructions and the prompt.
> - **Prompt** (`chunk.prompt`): a list of segments, each a literal string or a slot.
>   - A generation prompt must contain the `case` slot exactly once and may contain `appendices` once.
>   - A judge prompt must contain `completion` and may use `case`, `appendices` and `expectation`.
>   - "A string or slot for JIT insertion" understates this.
> - **Output** (`chunk.output`): one of three contracts:
>   - `native`: `response_format` with a JSON schema;
>   - `tool`: one forced function call;
>   - `text`: optionally carrying a schema used only by scoring.
>
>   It also carries optional guidance, which is appended to the instructions. There is **no coercion method**, because parsing belongs to the scorer.
> - **Sampling** (`chunk.sampling`): temperature, top-p/k, min-p, penalties, stop sequences, and `max_output_tokens`, sent as `max_completion_tokens` or `max_tokens`.
> - **Reasoning** (`chunk.reasoning`): effort, `thinking_token_budget` and `chat_template_kwargs`.
> - **Passthrough** (`chunk.passthrough`): engine-specific body keys. It may not set runtime or output keys, and it may not override a key another chunk sets.
>
> **CHANGE, "The manifest this mechanics":** → "The manifest owns these mechanics". The two listed are correct:
> - output guidance is appended to the instructions;
> - greedy sampling (temperature exactly 0) drops `top_p`, `top_k` and `min_p` when a chunk or skeleton is created, and the seed is left out when the request is rendered (`request.py: GREEDY_DROPS`, `render`).
>
> **MOVE, "The canonical form":** It applies to every component, not only requests. Move it to "Factors".
>
> **MOVE, "Chat template and prompt-token checks":**
> - The chat-template bullets belong to "Engine" (`engine.py: LocalEngine.chat_template`, `check_chat_template`).
> - The prompt-token bullet belongs to "Records" (`ledger.py: Call.prompt_tokens`, `compare_prompt_tokens`).
>
> **KEEP, "Requests are fully pre-rendered…"**, but make it its own "Skeleton" paragraph.
> - A skeleton contains the messages as segment lists, every other body key, the output contract and the appendix layout.
> - At send time, `render` fills the slots and adds `model` (the engine digest for local engines), `seed` (unless greedy) and `return_token_ids`.
> - No chunk or skeleton may set `model`, `messages`, `seed`, `stream`, `n` or `return_token_ids` (`request.py: RUNTIME_KEYS`).
> - "Accidentally modified" → "never interpreted".
>
> **CHANGE, "No validation retries":**
> - True for *parsing*.
> - Add that transport retries exist, are declared per run (`trial.py: Execution.retries`), and are counted per call (`ledger.py: Call.attempts`).
>
> **MOVE, "Seeds are explicit":** It belongs to "Trials". Seeds live on the trial (and on judges), not on the request. The helper `trial.py: suggest_seeds` proposes 31-bit random seeds.
>
> **MOVE, pydantic-ai:** Keep it in "Orchestrator dependencies" only.
>
> **MOVE, "A verification plan…":** It belongs to "Records". The paragraph itself is accurate. The planned phases are `RunStarted.verify_at` (default start and end).
>
> **ADD, Recipe and compilation:** The chunks a skeleton was compiled from form a `Recipe`:
> - an optional reference each to instructions, few-shot, reasoning and passthrough;
> - required references to prompt, output and sampling;
> - a purpose;
> - an appendix layout.
>
> A recipe is not a component. It is stored in the `Compilation` record together with the compiler version (`request.py: Recipe`, `ledger.py: Compilation`). A trial references only the skeleton, so compiler code is not a factor.

### Scorer
Is a factor that doesn't affect the output exists in the component graph to achieve repeatable trials.

> **CHANGE:** Merge this with "Scoring" and swap the content: the text under "Scoring" describes the *scorer*. With the glossary fix (factors affect output *or score*), this sentence becomes unnecessary. Suggested content for "Scorer" (`scoring.py: Scorer`):
> - pinned code (`Code`: distribution, version, revision);
> - the expectation schema it consumes;
> - an ordered list of views;
> - ordered resource digests (synonym tables, ontology releases), referred to by position;
> - free parameters.

### Scoring
Scorers are pinned code and resources (synonym tables, ontology releases).
- Each scorer declares the expectation schema it consumes.
- **Views** reach into the output and expectation schemas and score parts of them.
- **Outputs that fail validation are scored best-effort.**
- **Judges** are sent as judge-purpose skeletons through our own runner. Their scores are best-effort reproducible.

> **CHANGE:** In the code, a *scoring* (`scoring.py: Scoring`) is a scorer plus the list of expectations it scores against: at most one per case, all in the scorer's schema. Put that here; the bullets above belong to "Scorer".
>
> **CHANGE, views:** Be concrete (`scoring.py: View`). A view has:
> - a JSON pointer into the output;
> - a JSON pointer into the expectation;
> - a metric name, interpreted by the scorer's code;
> - optional judge and parameters.
>
> Views are referred to by position, and score items name a view by its index.
>
> **CHANGE, judges:**
> - A judge (`scoring.py: Judge`) is a judge-purpose skeleton, an engine and its own seeds. A view points at the judge it uses.
> - Judge calls are recorded per score item (`ledger.py: JudgeCall`), with the index of the seed used.
> - Judge engines are covered by the clearance hard block.
>
> **KEEP:** "Outputs that fail validation are scored best-effort"; parsing is the scorer's job, not the manifest's.

### Engine
- **Remote engines.** Declare the endpoint and the requested model. Attest the returned identifiers. Run canary probes at the start and end of each run.

- **Execution is declared per run in `RunStarted`**: order (case-major, replicate-major, or shuffled with a seed), concurrency, timeout and retries. If batch invariance is declared in the engine's environment, re-runs may be bitwise identical. Without it the run falls in the best-effort tier. Canaries, prompt-token fingerprints and comparing completions between runs of the same trial show which case applies. Timeouts and retries never change a successful output. The attempt count is recorded per call.

Note: `Hardware` and `Runtime` are properties of `LocalEngine`, so they can't be listed, picked or reused as standalone rows, for example in the UI or as Postgres foreign keys. The same GPU or closure is repeated in every engine that uses it. Asking "which engines share this closure" means scanning engines instead of following a reference.

> **ADD, local engines are missing** (`engine.py: LocalEngine`). A local engine consists of:
> - **Hardware:** GPU, compute capability, VRAM, driver.
> - **Runtime:** `vllm`, its version, the Nix closure path.
> - **A reference to a `ModelArtifact`:** repo, revision and the sha256 of every file. This is its own component, reusable across engines; it may deserve its own subsection.
> - **A required chat-template file digest.** Templates that read the current date are flagged (`check_chat_template`).
> - **Raw `argv` and `env`,** with the start-up-script-owned flags forbidden. Version-specific knowledge lives in lints, not in the data layer.
> - **The served model name,** which is the engine's digest.
>
> Batch invariance is declared through `env` (e.g. `VLLM_BATCH_INVARIANT=1`).
>
> **CHANGE, remote:** Add what the remote engine is in the code (`engine.py: RemoteEngine`): `base_url`, the requested `model` and `api` (`openai.chat`). Its chat template is not pinned. "Attest the returned identifiers": only the returned `model` is checked today. "Canary probes at the start and end": canaries apply to any engine and are planned per run, so this is a runner rule, not a remote-engine property.
>
> **MOVE, "Execution is declared per run…":** Move it to "Records → Runs". It is about runs, not engines. Keep the one sentence about batch invariance here, since that *is* an engine property.
>
> **KEEP:** The hardware/runtime note. Add "No comparison between the 3070 and the 5090" here, or under "Current stack situation".

### Trials
A trial is a scientific intent: skeleton × engine × case × normalization × seeds.

> **CHANGE:** "case" → "cases". A trial holds an ordered, duplicate-free list of cases (`trial.py: Trial.cases`); there are no case sets.
>
> **ADD:**
> - The skeleton must be a generation skeleton.
> - Seeds are explicit and duplicate-free. A replicate is identified by its seed's index. The portal suggests random seeds (`suggest_seeds`).
> - Normalization is an ordered list of pinned operations (see "Case").
> - Execution settings are not part of the trial.
> - "Re-running a trial" means a new run of the same trial digest.
>
> **ADD:** The known wrinkle: with greedy sampling the seed isn't sent, but the seeds still count toward the trial's digest (already listed under "Possible design issues").

## Records
- Records (runs, scores and compilations) are a separate ledger outside of the component graph that reference components by digests. It is written as a row as it happens:
  - a `RunStarted` row;
  - item rows and canary-call rows;
  - a `RunFinished` row carrying the end time, the run's warnings and a seal (a hash over the started row and all item rows).
  - **No repeated keys:** item rows are keyed by (case, replicate index) and never repeat the engine or the seed. Checks against the trial reject items the trial does not contain.
  - **Rows changed after sealing** produce a warning, in keeping with "warnings, not crashes".
  - The canaries

> **CHANGE, structure:** Split into **Runs**, **Scores** and **Compilation**. Only runs and scores are stage logs; a compilation is a single row.
>
> **CHANGE, the seal:** It covers the started row, all item rows *and* all canary-call rows (`ledger.py: Run.seal`).
>
> **CHANGE, dangling bullet:** "The canaries" is unfinished. Fill it with the verification paragraph moved from "Request", plus:
> - canary calls must be planned: their phase is in `verify_at`, and their probe index exists in the canary set;
> - a run without a canary set may not contain canary calls (`ledger.py: check_run`).
>
> **ADD, Runs:**
> - `RunStarted` carries the run id, time, rig code version, trial, execution settings, canary set and `verify_at`.
> - Each `RunItem` carries the item key, the *observed* vignette fingerprint (for drift) and a `Call`.
> - `RunFinished` carries the time, findings and seal.
> - Stages must follow the order `started` → `finished`, and every row must belong to the same run.
> - A new stage is a new row type, not a new table.
>
> **ADD, Call** (`ledger.py: Call`), shared by run items, canary calls and judge calls:
> - the fingerprint of the wire body;
> - start and end times, HTTP status;
> - the raw response with `prompt_token_ids` removed, plus the prompt tokens' fingerprint;
> - attempts;
> - error.
>
> **ADD, Scores:**
> - `ScoreStarted`: score id, run id, time, rig code, observed scorer code, scoring.
> - `ScoreItem`: item key, view index, value, detail, judge calls.
> - `ScoreFinished`: time, findings, seal.
> - Score items must refer to items in the run and to views that exist; judge calls must use a judge from the scorer's views and a seed index in range (`ledger.py: check_score`).
>
> **ADD, sensitivity:** Run and score records are case-derived, because they hold completions. Compilations are not (`ledger.py: Record.case_derived`).

### CanaryCall

> **REMOVE as a section:** `CanaryCall` is a row type of a run, not a peer of runs. Cover it in "Runs": phase (`start`/`end`), probe index and a `Call`. If you want a subsection, the more useful one is **Verification (canaries)**, holding:
> - the canary set, a component: literal, non-sensitive probe requests, each with an optional seed;
> - the plan in `RunStarted`;
> - the calls.

### Compilation

> **ADD content:** A compilation (`ledger.py: Compilation`) is one row: a recipe, the skeleton it produced, the compiler's code version and the time. It is the lineage from portal chunks to the frozen request. Recompiling with a newer compiler adds a new row, not a new factor. It is not case-derived.

---

## Possible design issues

### Policy for case-derived content deferred
Completions are stored in plain text in `RunRecord`, which can quote or reveal sensitive information. A policy for such case-derived content is has not settled.

> **CHANGE:** `RunRecord` no longer exists. Completions are in the raw responses of `RunItem.call`, `CanaryCall.call` (non-sensitive) and `JudgeCall.call`. "is has not" → "has not". Add the two deferred items:
> - database-level read restriction on case-derived record tables;
> - a destination check when records are exported.

### Greedy sampling and seeds:
the seed isn't sent, but the trial's seeds still count toward its hash, so two otherwise identical greedy trials differ only in hash.

> **KEEP:** This is accurate. "Hash" → "digest". Drop the trailing colon in the heading.

### Unverified vLLM 0.24 assumptions:
The prompt_token_ids response field and the temperature clamp below 0.01 are listed in the reconciliation file.

> **CHANGE:** State the two assumptions inline rather than pointing at an agent file:
> - with `return_token_ids: true`, the response carries a top-level `prompt_token_ids` list;
> - temperatures in (0, 0.01) are raised to 0.01.
>
> Add that a fake vLLM based on 0.24.0 should pin both (AGENTS.md asks for one; none exists yet).
>
> **ADD, further open issues found while checking the draft against the code:**
> - **Case drift is not checked.** The observed vignette fingerprint is recorded per item but never compared with the case's.
> - **Canary drift is not checked.** No function compares canary outputs between phases or between runs.
> - **Judge engines are not verified by canaries** (reconciliation D5, option B, deferred).
> - **Skeleton provenance.** Should a skeleton only be accepted with a compilation record? Hand-written ones (e.g. judge prompts) are possible today.
> - **Re-binding after vignette drift.** A changed vignette orphans its appendices and expectations; who re-binds them?
> - **Records aren't in bundles.** How the ledger is delivered together with the cage isn't specified.
> - **Metric names are free strings.** `View.metric` is only meaningful to the scorer code that `Scorer.code` pins; nothing checks that the code knows the name.
> - **Vignette fingerprint: raw or normalized?** See "Case".

---

## Missing sections

1. **Identity and canonical form.** How a digest is computed, what is left out of the canonical form, per-kind schema versions, the evolution rule (new fields default to old behavior; changing a default bumps the version), typed references and `x-ref`, and cross-component checks. Code: `identity.py`.
2. **Bundle.** Roots plus their closure, canonical text keyed by digest, the generator's code version, offline verification byte for byte, the `bundle.recanonicalized` warning when current code would serialize a component differently, and the fact that bundles carry no sensitive content. Code: `bundle.py`.
3. **Model artifact.** Repo, revision (a commit, or it's linted) and per-file sha256; reused across engines. Code: `engine.py: ModelArtifact`.
4. **Expectations.** Expectation schemas (from scorer developers) and expectations (from the portal), keyed by case including appendices; not sensitive; at most one per case in a scoring. Code: `scoring.py: ExpectationSchema`, `Expectation`.
5. **Judges.** Judge-purpose skeletons and their slots (`completion`, `expectation`, `case`, `appendices`), engine, seeds, how views point at them, and the clearance hard block. Code: `scoring.py: Judge`, `request.py: SLOTS_BY_PURPOSE`.
6. **Rendering and runtime keys.** What `render` adds at send time, the keys no factor may set, greedy seed handling, and the appendix layout. Code: `request.py: render`, `RUNTIME_KEYS`.
7. **Verification.** Canary sets, the per-run plan, canary calls, and the drift checks still to be built. Code: `trial.py: CanarySet`, `ledger.py: RunStarted`, `CanaryCall`.
8. **Findings and errors.** The catalogue of finding codes and what each means, versus what raises `StructuralError`/`ValidationError`, and the single hard block. Code: `identity.py: Finding`, `ledger.py`, `engine.py`, `bundle.py`, `lint.py`.
9. **Lint.** Advice that isn't structural: a model revision that isn't a commit, a closure that isn't a Nix store path, scorer code without a revision, and the vLLM 0.24 temperature clamp. Lint rules are keyed by the declared runtime version and kept out of the data layer. Code: `lint.py`.
10. **Storage.** How components and records map to Postgres:
    - factor tables are insert-only and keyed by digest, with the canonical text as the source of truth (`jsonb` only as a searchable copy);
    - records are append-only stage logs and item rows;
    - nothing is updated;
    - version chains between digests belong to bookkeeping.
11. **Bookkeeping boundary.** What the bookkeeping layer owns: names, owners, tags, collaborators, version history, clearance, who started a run, and labels for views and resources. What it may reference: digests and record ids. What must never leak into factors or records.
12. **Governance.** The sensitivity model: vignettes and case-derived output are sensitive; appendices, expectations and all components are not. Also the hard block and where it is enforced, and fingerprints instead of stored text.
13. **Export and results.** How scores are aggregated and exported together with the bundle (and eventually the ledger) for publication. It is currently only a requirement with no design.
14. **Proposed amendments.** AGENTS.md requires this section to stay at the end of the doc. When you integrate an amendment, remove it from the list; the draft above has dropped the section entirely.
