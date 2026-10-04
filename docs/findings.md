# Findings
A list of all findings and what they mean.

- `attestation.model`:
the engine returned a different model name than declared: the engine digest for a local engine, the requested model for a remote one (`check_run`).

- `attestation.prompt_tokens`:
some run items have no prompt-token fingerprint, for example because the engine didn't return token ids (`check_run`).

- `attestation.prompt_tokens_drift`:
an item read different prompt tokens than the same item in another run (`compare_prompt_tokens`).

- `run.incomplete`:
a finished run lacks some of the trial's items (`check_run`).

- `ledger.seal`:
a run's or score's rows no longer match the seal in its finished row (`check_run`, `check_score`).

- `engine.chat_template` and `engine.chat_template_date`:
the chat-template file doesn't match the engine's declared digest, or reads the current date (`check_chat_template`).

- `bundle.recanonicalized`:
the current code would serialize a bundled component differently from its stored bytes, which remain authoritative (`Bundle.load`).

- `model.revision`, `engine.closure`, `scorer.revision`:
kand the pair rules for trials and judges

`vllm.temperature_clamped`:

`vllm.tool_unconstrained`:

`vllm.thinking_budget_refused`:

`vllm.grammar_before_reasoning`:

`schema.ref_unverified`:

`facts.missing`:

`facts.reasoning_unmatched`:

`facts.budget_refused`:

`facts.output_refused`:

`facts.output_note`:

`case.drift`:
the vignette read at the source differs from the case's fingerprint (`prepare_case`, `check_run`).

`model.revision`:

`engine.closure`:

`scorer.revision`:

`expectation_schema.invalid`:

`expectation.invalid`:

`expectation.unchecked`:

## Proposed amendments
