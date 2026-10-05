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
a finished run lacks some of the trial's items, or some planned canary calls (`check_run`).

- `ledger.seal`:
a run's or score's rows no longer match the seal in its finished row (`check_run`, `check_score`).

- `tools.unanswered`:
an item's last response still calls tools, after its rounds ran out or the run stopped (`check_run`).

- `judge.incomplete`:
a scored item's judge was called with fewer seeds than the judge has (`check_score`);

- `score.incomplete`:
a finished score lacks items for some pairs of run item and view (`check_score`).

## Factors
Factors are "linted" in one sweep by passing a registry (of factors) to `lint(registry, digests, facts=)`

- `bundle.recanonicalized`:
the current code would serialize a bundled component differently from its stored bytes, which remain authoritative (`Bundle.load`).

- `engine.chat_template` and `engine.chat_template_date`:
the chat-template file doesn't match the engine's declared digest, or reads the current date (`check_chat_template`).

- `vllm.temperature_clamped`:
the skeleton sets a temperature between 0 and 0.01, which vLLM 0.24 raises to 0.01.

- `vllm.thinking_budget_refused`:
`thinking_token_budget` without `--reasoning-parser` or `--reasoning-config` is refused.

- `vllm.grammar_before_reasoning`:
takes its level from the model's default reasoning.
a `native` contract, or a constrained `tool` one, without `--reasoning-parser` is constrained from the first token,
so the model can't reason first. It's a warning when the skeleton asks for reasoning
(`reasoning_effort`, `thinking_token_budget`, `enable_thinking: true`), and `info` when it leaves reasoning to the model.
Nothing is reported when it turns reasoning off.

- `vllm.tools_refused`:
a body with tools on an engine without `--enable-auto-tool-choice` and `--tool-call-parser`;
vLLM 0.24 refuses it, or sends it unconstrained for harmony and Mistral models.

- `vllm.native_tools_uncallable`: a `native` contract with tools; on vLLM 0.24 the schema constrains the whole
  answer, so no tool can be called.

- `model.revision`, `engine.closure`, `scorer.revision`:
kand the pair rules for trials and judges

- `expectation_schema.invalid`:

- `expectation.invalid`:

- `expectation.unchecked`:

- `schema.ref_unverified`:

- `facts.missing`:

- `facts.reasoning_unmatched`:

- `facts.budget_refused`:

- `facts.output_refused`:

- `facts.output_note`:

- `language.mixed`:
some of a trial's cases are in another language than its request (`lint`, with `languages=`).

- `language.unknown` (info):
the request's language, or some of a trial's cases', is unknown, because a label is missing or
the labels disagree (`lint`, with `languages=`).

## Both
- `view.unreachable`:
a view's selector can't pick anything from documents that follow the schema: the
expectation schema for its expectation selector (`lint`, on scorers), or the run's output schema for its output
selector (`check_score`).

- `case.drift`:
the vignette read at the source differs from the case's fingerprint (`prepare_case`, `check_run`).

## Proposed amendments

- keep "Ledger" to the codes of `check_run`, `check_score` and `compare_prompt_tokens` (`src/chatddx/ledger/ledger.py`):
- The ledger doc now lists its codes with their subjects (`agents/ledger.md`, "Findings", the replacement for
  `docs/ledger.md`); "Ledger" here could point there instead of repeating them.
- ADD to "Ledger" (`src/chatddx/ledger/ledger.py:check_run`, `_executed`):
  - `execution.retries`: some calls took more attempts than the run's `retries` allows;
  - `execution.order`: some items were sent before items the run's order schedules ahead of them;
  - `execution.concurrency`: more calls were in flight at once than the run's `concurrency` allows, canary calls
    included.
- ADD to "Ledger": `score.scorer_code`: the scorer code that ran isn't the code the scorer pins; the revision counts
  only when the scorer pins one (`src/chatddx/ledger/ledger.py:check_score`).
- CHANGE the `execution.*` codes proposed above: `check_score` reports them too, for a score's judge calls against
  `ScoreStarted.execution` (`src/chatddx/ledger/ledger.py:check_score`).
- CHANGE `attestation.model`: "the engine returned a different model name than declared …" → "… or a response gave
  no model name; calls without a response are skipped" (`src/chatddx/ledger/ledger.py:check_run`).
