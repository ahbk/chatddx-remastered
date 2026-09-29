# Manifest reconciliation: old single-file manifest vs the new package

The old module was `src/chatddx/core/manifest.py`, removed in D1. It is still readable with
`git show de843eb:src/chatddx/core/manifest.py`.

The new package was written from `docs/manifest.md` without reading the old module first. This file tracks the
differences. Each one gets a decision, and the decision is recorded here.

Layout of the new package, now at `chatddx.core.manifest`:
- `identity.py`: canonical bytes, digests, typed refs, `Component` registry
- `engine.py`, `request.py`, `cases.py`, `scoring.py`, `trial.py`: factor components
- `bundle.py`: `Registry` and `Bundle`
- `ledger.py`: records, which are not components
- `governance.py`: clearance

## Surprises found along the way
- `docs/manifest.md` stops mid-sentence in issue 12, option C.
- `pyproject.toml` requires `pydantic>=2.13.5`; the doc says 2.13.4.
- The old code contradicts the doc's "Misc notes" in two places:
  - its appendices are reusable across cases, where the doc says they are bound to an HMAC;
  - its expectations are keyed by source case only, where the doc says source case + appendices.

## Assumptions to verify against vLLM 0.24.0
- With `return_token_ids: true`, a chat-completions response carries the prompt's ids in a top-level
  `prompt_token_ids` list; `seal_prompt_tokens` relies on this. The repo has no fake vLLM yet to pin it.
- Temperatures in (0, 0.01) are raised to 0.01 (carried over from the old module's lint).

## Decision points

| # | Topic | Old | New | Decision |
|---|-------|-----|-----|----------|
| D1 | Location | single file `chatddx.core.manifest` | package `chatddx.manifest` | package replaces the old module at `chatddx.core.manifest` |
| D2 | Digest / schema evolution (#5) | `model_dump()` incl. defaults, salted with `MANIFEST_VERSION`; bundle stores objects | canonical form omits defaults, per-kind `schema_version`, bundle stores canonical text and verifies bytes | new: defaults omitted, per-kind versions |
| D3 | References (#1) | hand-written `references()` | `Annotated[Digest, RefTo(kind…)]`, generic walker, `x-ref` in JSON Schema | typed refs; dotted kind names |
| D4 | Records (#2, #4) | `RunRecord`/`ScoreRecord` are Components | `Record` base outside the union, UUID id + `seal()`, `ItemKey(case, replicate)`, `check_run`/`check_score` | new: records outside the component graph |
| D5 | Verification plan (#3) | canaries referenced only by run | `RunPlan{trial, verification}`, `Verification{canaries, at}`; canaries are literal bodies | new, as is |
| D6 | Engine (#9) | typed `LoadParams` (0.24 flags), Hardware/Closure as components, `assess_engine`, `vllm_launch` | inline Hardware/Runtime, raw `argv`/`env` with owned flags forbidden, required chat-template digest, no tier assessment | raw argv/env; hardware and runtime stay inline (trade-off proposed as a doc amendment) |
| D7 | Request composition (#6, #8) | sections + generic `fills`, few-shot, lineage in `BundleContext` | chunk components + `RequestSpec` → `compile_request` → `Skeleton`; lineage as `Compilation` record; only output guidance → system | fixed slots + few-shot chunk, developer role, stop, max_tokens_key, thinking_token_budget |
| D8 | Seeds / replicates | seeds in sampling section / skeleton | seeds on `Trial` / `Judge`; skeleton reusable; greedy → seed not sent | new + `suggest_seeds` |
| D9 | Cases | reusable titled appendices, `Normalization` per case input, `CaseSource` component, expectations keyed by source case, case set sorted | appendices bound to (case, vignette HMAC), normalization on `CaseSet`, expectations keyed by `CaseInput`, case set ordered | (a) appendices bound to vignette HMAC, (b) expectations keyed by case+appendices, (c)/(d) no CaseSet: `Trial.cases` + `Trial.normalization`, order from `Trial.order` |
| D10 | Scoring (#7) | `ParsePolicy`, typed metrics per view, judge per view, `resources` | no parsing in manifest, `View.metric: str`, `Judge` component on `Scoring` | new + `Scorer.resources`, `View.judge`; judges derived from views |
| D11 | Governance / export (#10) | `BundleContext` (endpoints, clearance labels, lineage, sections) | no context; `Clearance` by URL origin outside bundle; `case_derived` flag per kind | URL-origin clearance; only cases and case-derived outputs (records) are sensitive; appendices and expectations are not (doc amendment) |
| D12 | Chat template (#12) | optional `LoadParams.chat_template` | required `LocalEngine.chat_template` file digest; remote unpinned | A + D: `check_chat_template` (digest + date use); `render` sends `return_token_ids`, `seal_prompt_tokens` keeps only an HMAC, `compare_prompt_tokens` across runs |
| D13 | Lint | rich per-component `lint()` | only structural errors + run attestation | `lint.py`: model revision, closure path, scorer revision, vLLM 0.24 temperature clamp |
| D14 | Execution policy | order incl. shuffled, concurrency, retries, timeout | `Trial.order`, `Trial.concurrency` | A: `RunPlan.execution` (order incl. shuffled, concurrency, timeout, retries); trial identity is intent only; `Call.attempts` |

## Table classification follow-ups
- `expectation_set` dropped: `Scoring.expectations` lists expectations directly.
- Views and scorer resources are referred to by position; labels belong to bookkeeping.
- Open: whether `request`, `skeleton`, `verification`, `run_plan` and `canary_set` need tables; HMAC vs plain sha256 fingerprints; how staged run records are written.
