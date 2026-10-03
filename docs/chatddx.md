# The ChatDDX Rig - requirements and design notes

## Purpose
ChatDDX is a system to measure how accurately LLMs generate differential diagnoses and management plans from emergency-care case vignettes.

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
- **Canonical form:** sorted-key JSON with fields left out when equal to their default, plus the type's schema version.
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
- **Vetted:** a bookkeeping fact.
- **Portal:** the web-interface used to configure factors, watch runs and export results.
- **World:** factor parameters outside of the orchestrator's direct control. (the vignette source, model files, the Nix closure, the chat-template file, the remote engine)

### Yet to be properly defined
- **Inventory:** A set of toml-files declared by the ops.
- **Manifest:** A document derived from an inventory that the World can read from.
- **Rig:** Unclear, is it a bundle?

### Roles mentioned
These roles are distinct and not overlapping, a person may inhabit more than one role.
- **Clinicians:** Owners of vignettes and authors of Appendices and Expectations
- **Researchers:** Authors of Sampling, Scorers and Trials
- **Developers:** Authors of schemas, canary sets, scoring and normalizations functions
- **Ops**: Owners of engines and authors of the engine's components

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

"Delete" in this case is a `deleted` flag in the `catalog` layer and only true deletions for non-referenced instances.

### On the table
- The result from scorers needs to be aggregated and exported. Since python is well-suited for statistical analysis, processing the data into publishable results may become a requirement.

### Work in progress
- How scores are aggregated and exported together with the factors and the ledger for publication.
- How sensitivity is enforced: factors and ledgers provide the means but not the enforcment itself.
- Storage: How components and records map to Postgres, table structures are suggested for clarity but not prescribed. In general:
    - factor and records are insert-only
    - nothing is updated;
    - version chains between digests belong to bookkeeping.

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

## Intended dataflow
The factors and the ledger does not dictate a concrete process, it supplies the pieces that some runner chains together in whatever way they deem fit.
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
3. Get the model's output out of the raw response. Parsing belongs to the scorer (content, tool-call arguments, JSON extraction, best-effort handling when validation fails). Nothing in `factors` does this.
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

The "scored results" are just those ScoreItem rows. What aggregates them isn't named yet.

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

The one hard block is clearance: sending case-derived content to an engine that isn't cleared, judge engines included, must be refused. Clearance is bookkeeping data and the factors do not enforce it; the runner must.
