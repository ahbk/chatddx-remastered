# Manifest reconciliation: `chatddx.core.manifest` (old) vs `chatddx.manifest` (new)

The new package was written from `docs/manifest.md` without reading the old module first. This file tracks the
differences. Each one gets a decision, and the decision is recorded here.

Layout of the new package:
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

## Decision points

| # | Topic | Old | New | Decision |
|---|-------|-----|-----|----------|
| D1 | Location | single file `chatddx.core.manifest` | package `chatddx.manifest` | open |
| D2 | Digest / schema evolution (#5) | `model_dump()` incl. defaults, salted with `MANIFEST_VERSION`; bundle stores objects | canonical form omits defaults, per-kind `schema_version`, bundle stores canonical text and verifies bytes | open |
| D3 | References (#1) | hand-written `references()` | `Annotated[Digest, RefTo(kind…)]`, generic walker, `x-ref` in JSON Schema | open |
| D4 | Records (#2, #4) | `RunRecord`/`ScoreRecord` are Components | `Record` base outside the union, UUID id + `seal()`, `ItemKey(case, replicate)`, `check_run`/`check_score` | open |
| D5 | Verification plan (#3) | canaries referenced only by run | `RunPlan{trial, verification}`, `Verification{canaries, at}`; canaries are literal bodies | open |
| D6 | Engine (#9) | typed `LoadParams` (0.24 flags), Hardware/Closure as components, `assess_engine`, `vllm_launch` | inline Hardware/Runtime, raw `argv`/`env` with owned flags forbidden, required chat-template digest, no tier assessment | open |
| D7 | Request composition (#6, #8) | sections + generic `fills`, few-shot, lineage in `BundleContext` | chunk components + `RequestSpec` → `compile_request` → `Skeleton`; lineage as `Compilation` record; only output guidance → system | open |
| D8 | Seeds / replicates | seeds in sampling section / skeleton | seeds on `Trial` / `Judge`; skeleton reusable; greedy → seed not sent | open |
| D9 | Cases | reusable titled appendices, `Normalization` per case input, `CaseSource` component, expectations keyed by source case, case set sorted | appendices bound to (case, vignette HMAC), normalization on `CaseSet`, expectations keyed by `CaseInput`, case set ordered | open |
| D10 | Scoring (#7) | `ParsePolicy`, typed metrics per view, judge per view, `resources` | no parsing in manifest, `View.metric: str`, `Judge` component on `Scoring` | open |
| D11 | Governance / export (#10) | `BundleContext` (endpoints, clearance labels, lineage, sections) | no context; `Clearance` by URL origin outside bundle; `case_derived` flag per kind | open |
| D12 | Chat template (#12) | optional `LoadParams.chat_template` | required `LocalEngine.chat_template` file digest; remote unpinned | open |
| D13 | Lint | rich per-component `lint()` | only structural errors + run attestation | open |
| D14 | Execution policy | order incl. shuffled, concurrency, retries, timeout | `Trial.order`, `Trial.concurrency` | open |
