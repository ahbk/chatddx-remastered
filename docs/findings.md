# Findings
A list of all findings and what they mean.

## Ledger
The ledger emits findings as they come. [They might benefit from having a separate mechanism for this (what linting is for factors),
but nothing is planned or decided.]

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

- `case.drift`:
the vignette read at the source differs from the case's fingerprint (`prepare_case`, `check_run`).

## Factors
Factors are "linted" in one sweep by passing a registry (of factors) to `lint(registry, digests, facts=)`

- `vllm.temperature_clamped`:
the skeleton sets a temperature between 0 and 0.01, which vLLM 0.24 raises to 0.01.

- `vllm.tool_unconstrained`:
a `tool` contract without `--enable-auto-tool-choice` and `--tool-call-parser` in the engine's argv goes out unconstrained.

- `vllm.thinking_budget_refused`:
`thinking_token_budget` without `--reasoning-parser` or `--reasoning-config` is refused.

- `vllm.grammar_before_reasoning`:
takes its level from the model's default reasoning.
a `native` contract, or a constrained `tool` one, without `--reasoning-parser` is constrained from the first token,
so the model can't reason first. It's a warning when the skeleton asks for reasoning
(`reasoning_effort`, `thinking_token_budget`, `enable_thinking: true`), and `info` when it leaves reasoning to the model.
Nothing is reported when it turns reasoning off.

- `model.revision`:

- `engine.closure`:

- `scorer.revision`:

- `expectation_schema.invalid`:

- `expectation.invalid`:

- `expectation.unchecked`:

- `schema.ref_unverified`:

- `facts.missing`:

- `facts.reasoning_unmatched`:

- `facts.budget_refused`:

- `facts.output_refused`:

- `facts.output_note`:

## Proposed amendments

### G18: language findings
Under **Factors**:
- `language.mixed`: some of a trial's cases are in another language than its request (`lint`, with
  `languages=`).
- `language.unknown` (info): the request's language, or some of a trial's cases', is unknown, because a label
  is missing or the labels disagree (`lint`, with `languages=`).
