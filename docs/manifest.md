# Rig manifest: requirements and design notes

## Purpose

Measure how accurately LLMs generate differential diagnoses and management plans from emergency-care case vignettes. The manifest is the declared cage each output was produced in. It must be possible to deliver that cage together with the results, for scientific rigor.

## Scope rule

- The manifest contains every factor that can affect output, and every field that makes a factor what it is.
- Metadata with no bearing on output (names, descriptions, authors, UI labels) is excluded.
- Observations are recorded separately from declared factors. An observation shows whether a factor held; it does not cause the output.

## The rig

- **Local inference.** Two servers, an RTX 3070 (sm_86, 8 GB) and an RTX 5090 (sm_120, 32 GB). Both serve vLLM 0.24.0 in a NixOS container that reads load-time parameters from the manifest.
- **Remote inference.** An A100 serving Gemma behind an OpenAI-compatible API. More may follow. We don't control the engine and it can change without notice, so some uncertainty is accepted.
  - The endpoint is vetted for sensitive cases.
  - It honors `seed` and returns model identifiers.
- **Orchestrator.** Runs in Kubernetes and owns the manifest. Its web UI is for authoring reusable, composable request-time chunks, appendices and expectations, all stored in Postgres.
- **Cases.**
  - They are sensitive and fetched by identifier from a predefined source. They arrive already de-identified.
  - Vignettes are unstructured and cannot be edited. They may or may not include labs and imaging, and may lack age or sex.
  - The source exposes no revision identifier.
- **Pipeline.** A trial is (request chunks) × (case set). A batch runner turns a trial into a run, and a scorer turns a run into a score. Trials, runs and scores are all immutable and reproducible.
- **Tooling.**
  - Requests: Hand-made or pydantic-ai.
  - Scoring: text-matching functions, and possibly LLM judges.
- **Implementation.** pydantic 2.13.4, Python 3.13+.
  - The same module is imported by the orchestrator, the runner, the scorer and the container's start-up script.
  - It lives with the orchestrator for now and moves to its own repo later.

## Decisions

### Identity and composition
- A content-addressed component graph: frozen models, canonical JSON, sha256 digests, and references by digest. Alongside it, a self-contained, materialized export (the bundle).
- Request chunks fill typed slots, and cross-slot effects happen only in pre-specified ways. The manifest owns the mechanics:
  - textual slot filling, e.g. the output type injects guidance into the instructions;
  - canonicalization, e.g. greedy sampling drops the seed, top_p, top_k and min_p.

  Policy belongs to the business layer, e.g. reasoning effort deciding the output budget. The manifest holds cleanly cut sections plus an engine-specific passthrough.

### Request construction
- Requests are fully pre-rendered. Because case text can't be stored, the frozen thing is a skeleton: the complete chat-completions body with runtime slots for the case and appendices. Slots are segment lists rather than templates, so clinical text is never interpreted.
- **No validation retries.** Parsing moves to scoring; a run stores the raw completion.
- **pydantic-ai: compile-time capture only.** Drop it if it adds no value.
- **Seeds are explicit and user-defined.** The UI suggests random ones that users are free to override.

### Cases
- Case input is kept apart from expectations; the two are unrelated.
  - **Appendices:** append-only blocks added to a case. Anchored edits to the vignette are not supported.
  - **Expectations:** reference data used only for scoring.
- **Normalization** (newlines, stripping and similar) is an ordered, closed set of operations. De-identification happens upstream.
- **Drift detection** relies on a keyed HMAC of the raw vignette only. The source's governance and integrity is outside IT control.

### Scoring
- Each scorer declares the expectation schema it consumes.
- **Views** reach into the output and expectation schemas and score parts of them.
- **Outputs that fail validation are scored best-effort.**
- **Judges** are sent as judge-purpose skeletons through our own runner. Their scores are best-effort reproducible.

### Reproducibility and validation
- **Two tiers: bitwise and best-effort.** Bitwise reproducibility is not pursued once its cost exceeds its value; delivering the cage is the goal.
- **No comparison between the 3070 and the 5090.** Each hardware class is its own cage.
- **Frictionless development.** A mismatch between what is declared and what is observed produces a warning, never a crash. Examples:
  - a served model name that isn't the engine digest;
  - case drift;
  - attestation differences;
  - canary drift.

  The runner warns; it does not refuse. Only structurally malformed specs raise errors.
- **Remote engines.** Declare the endpoint and the requested model. Attest the returned identifiers. Run canary probes at the start and end of each run.

### Governance
- There is a per-engine clearance constraint, for the day unvetted hardware is used.
- **The one exception to "warnings, not crashes":** sending case-derived content to an engine without clearance is a hard block. This covers judge engines as well as the generation engine.

## Misc notes

- Appendices are bound to HMAC identifiers and not reusable across cases.
- Judge engines fall under the governance block.
- Expectations are keyed by source case+appendices, so appendices change the correct answer.

---

## Possible design issues

These are prompted by the references `RunRecord` and `ScoreRecord` omit, but most go beyond that. Items 1 to 4 are about the reference graph itself. Items 5 to 10 are elements I introduced into the data layer that may not belong there, or that sit uneasily in it.

### 1. The reference graph is declared twice
`Ref` is an untyped string. Which fields are references, and to which kinds, is repeated by hand in each class's `references()`. Nothing forces the two to agree, so they drifted.

Consequences:
- The JSON Schema used by the UI can't see references.
- Postgres can't derive foreign keys from the schema.
- The context's references (endpoint bindings, case policies, lineage) aren't checked at all. A binding to an engine that isn't in the bundle is silently ignored.

This is a mechanism flaw, not a model flaw. The graph should come from the field types.

### 2. Every missed reference is in the records layer
All the misses are in `RunRecord` and `ScoreRecord`; none are in the factor components. Records were built on the same `Component` and `Bundle` machinery as specs, but they are a different kind of thing:
- A spec's digest means *identity of factors*.
- A record's digest means *integrity of an observation log*.

A record's digest includes timestamps and lint findings, which contradicts the scope rule as written, even though records aren't factors. The manifest now carries the runner's and scorer's storage schema.

Open question: should records live in a separate ledger schema that references a spec bundle, rather than inside the manifest's component union?

### 3. Canaries have no declared home
The canary set is referenced only by the run that used it. No trial or engine declares which canaries verify it, so the runner picks them. By the scope rule, canaries don't affect output and so don't belong in trial identity. But they are part of what "delivering the cage" means.

The concept of a *verification plan* is missing from the model. It is neither a factor nor an observation.

### 4. Records repeat keys and can contradict themselves
- `EngineAttestation.engine` repeats the trial's engine.
- `ScoreItem` and `JudgeCall` repeat `case_input` and `replicate` from run items instead of pointing at them.

Nothing checks that these agree, so a record can name the wrong engine or a case the run never contained. This is the other half of the missed-reference issue: even with references followed, their consistency isn't checked.

### 5. Digests are recomputed from current code, so the schema can't evolve
A digest is recomputed from `model_dump()` every time it's needed, and `model_dump()` includes defaults. Two consequences:
- Adding any optional field to a component kind changes the digest of every stored component of that kind. For example, a new `LoadParams` field for vLLM 0.25 would do this.
- A bundle exported today fails verification under a later version of the module.

On top of that, `MANIFEST_VERSION` is a hand-maintained integer salted into every hash. Bumping it invalidates everything; forgetting to bump it hides semantic changes.

This undermines long-term delivery of the cage. It points towards stored canonical bytes as the source of truth, and per-kind schema versions.

### 6. The manifest's own code is an unpinned factor
Several behaviors are defined by code in this module:
- normalization operations;
- segment rendering;
- appendix layout;
- execution order;
- parse and extract heuristics;
- greedy canonicalization.

All of them affect outputs or scores. But bundles don't record which version of the manifest code produced them; only `MANIFEST_VERSION` and `BUNDLE_FORMAT`, both maintained by hand. Changing a regex in `_apply_op` changes outputs without changing any digest.

### 7. Parsing policy has two owners
`ParsePolicy.parse`, `resolve_pointer` and the JSON extraction heuristics are scorer behavior, but they live in the manifest and are pinned by the manifest version. `ScorerSpec.code` already pins the scorer. Changing the parser currently needs a manifest release, and the scorer's identity alone doesn't describe how outputs were scored.

### 8. Sections versus skeleton: two representations, one of them outside the graph
The trial references the resolved skeleton. The sections the UI authors (the "chunks" in the original requirement) are not referenced by any component; they appear only as untrusted context in the bundle.

So the section classes are an authoring layer inside the manifest module. They are neither the business layer's intents nor the frozen request. It's worth deciding explicitly whether sections and `resolve_request` belong in the manifest or in the business layer, with the manifest holding only the skeleton.

### 9. Engine-version knowledge baked into the data layer
- `LoadParams` is typed against vLLM 0.24's flag names.
- `vllm_launch` renders a 0.24 command line.
- `assess_engine` encodes current beta limitations of batch invariance: the FlashInfer sampler and compute capability ≥ 8.0.

This knowledge changes with each vLLM release, but the closure already pins vLLM. So a vLLM upgrade becomes a manifest schema change, which issue 5 makes expensive. Two options: typed load parameters versioned per engine release, or raw argv/env with lint kept outside the data layer.

The tier assessment itself may also be more machinery than the "deliver the cage" goal needs.

### 10. Operational data and an inconsistent sensitivity model in the export
- **Operational data in the export.** `BundleContext` carries endpoint URLs, clearances, lineage and section copies. That is routing, ops and business-layer data inside the manifest's export format. `EndpointBinding.base_url` also repeats `RemoteEngine.base_url`.
- **Inconsistent sensitivity model.** Wire bodies are stored only as HMACs because they contain case text. But completions are stored in plain text in `RunRecord` and travel in bundles, and model outputs can quote the vignette. Governance checks engines, not exports. Moving a bundle to a less-cleared destination isn't covered.

### 11. Minor
- **Identity is syntactic, not semantic.** Greedy sampling is the only canonicalized equivalence. Unset versus explicit default (`generation_config=None` versus `"auto"`, or `temperature` unset under `generation_config="vllm"` versus `1.0`) produces different digests for the same behavior. That's harmless apart from duplicates, but worth stating as a principle.
- **Redundant output schema.** `RequestSkeleton.output_schema` repeats the schema already inside `body` for the native and tool output modes.
- **Unenforced judge seeding.** Judge `epochs` and skeleton `replicates` are linked only by a lint warning.

### 12. The skeleton freezes messages, not the prompt
Requests go to `/v1/chat/completions`, so the server applies the chat template. The token sequence the model actually reads is produced from four things:
- the skeleton;
- the chat template;
- the tokenizer;
- vLLM's rendering code.

The template decides more than formatting:
- role markers, special tokens and BOS;
- system-message handling (Gemma folds the system text into the first user turn);
- how the tool schema is written into the prompt in tool mode;
- thinking on/off, from `chat_template_kwargs` and `reasoning_effort`;
- in some templates, the current date.

How it's pinned today:
- **Local engines:** pinned only indirectly, by `LoadParams.chat_template` if someone fills it in, the model's template file if someone lists it in `ModelArtifact.files` (not enforced), the closure, and the chat-template kwargs.
- **Remote engine:** not pinned at all. Canaries notice a template change only if it happens to change a canary output.

Options:
- **A. Server-side template, tightened.** Require the literal template text or its hash on local engines, and flag templates that read the date. The request shape stays the same everywhere, but the prompt text is still not in the cage.
- **B. Per-request `chat_template`.** Needs `--trust-request-chat-template`. It puts model-specific syntax in request chunks, and the remote engine is likely to ignore or reject it. Not recommended.
- **C. Client-side rendering + `/v1/completions`.** The skeleton holds the literal prompt with slots, produced at compile time

## Proposed amendments

### Hardware and runtime are declared inside the local engine
- **Proposed by:** Claude (agent), 2026-09-29T00:00Z
- **Reason:** A trade-off recorded for decision D6 of the manifest reconciliation. `Hardware` and `Runtime` are plain value objects inside `LocalEngine`; they are not separate content-addressed components.
  - **Gained:** one component per engine and no extra references to resolve. The hardware class and the closure sit next to the load arguments, so each engine digest names a complete cage. This fits "each hardware class is its own cage".
  - **Given up:** hardware and closures can't be listed, picked or reused as standalone rows, for example in the UI or as Postgres foreign keys. The same GPU or closure is repeated in every engine that uses it. Asking "which engines share this closure" means scanning engines instead of following a reference.
  - **Unaffected:** digests stay stable either way, because defaults are omitted from canonical form. Promoting them to components later would change `LocalEngine`'s shape and needs a schema version bump.
  - **Related:** load parameters are raw `argv`/`env` rather than typed per vLLM release. The start-up script owns `--model`, `--served-model-name`, `--chat-template`, `--tokenizer` and `--revision`. Version-specific lints belong outside the data layer.
- **Links:**
  - https://github.com/ahbk/chatddx-remastered/blob/96d15fe9bc511c99113db5bce3c6dfa0a8a1ee2f/src/chatddx/core/manifest/engine.py#L36-L68
  - https://github.com/ahbk/chatddx-remastered/blob/96d15fe9bc511c99113db5bce3c6dfa0a8a1ee2f/agents/manifest-reconciliation.md
