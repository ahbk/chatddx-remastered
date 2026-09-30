# Triage of "Possible design issues" in docs/manifest.md

Done at 034193f, before the Postgres implementation. The question for each item: does it have to be settled before rows
exist in Postgres? Rows are insert-only and outlive the code, so anything that changes digests, seals or what a stored
row means is cheapest now.

Permalink base: https://github.com/ahbk/chatddx-remastered/blob/034193f742e0fe0eac9da5b02494923b1920ef22/

## A. Blocks Postgres: fix first

A1–A4 are done: seals hash sorted canonical rows, record datetimes are normalized to UTC, and `fingerprint_request`
is defined. Record schema versions (end of A2) are done too: `Record.canonical` carries `v`, `Record.parse` checks it.

These are new; they are not in the doc's list. All three were reproduced with a scratch script.

- **A1. Seals depend on item order.** `Run.seal()` / `Score.seal()` hash items in the order given
  (`src/chatddx/core/manifest/ledger.py#L135`, `#L210`). A table read without `ORDER BY` reassembles the log in any
  order, and `check_run` then reports `ledger.seal` on untouched rows. Fix: seal over items sorted by their natural key
  (`(case, replicate)`, `(phase, probe)`, `(key, view)`), so storage needs no sequence column.
- **A2. Seals hash the full dump, not the canonical form.** The seal uses `model_dump(mode="json")`, defaults
  included. Adding a defaulted field to `Call`, `RunItem` or any record changes the dump of every old row, so every old
  seal breaks. Components solved this with canonical form (omit defaults); records should reuse it
  (`context={"canonical": True}`). Records also carry no schema version, so a payload column can't tell record
  versions apart; worth adding the same `v` the components have.
- **A3. Seals depend on the timezone offset.** `AwareDatetime` serializes with its offset. A `timestamptz` column
  returns UTC, so a row with `+02:00` round-trips to a different seal. Fix: normalize record datetimes to UTC in a
  validator (or keep them only inside the payload text).

Also decide before the first rows:

- **A4. Request fingerprint definition** (doc: "Call.request is never defined"). Stored `Call.request` values are only
  comparable across runs if the definition is fixed before the first run: `Fingerprint.of(canonical_bytes(body))`.
  One helper, no decision needed.
- **A5. Case-derived tables** (doc: "Policy for case-derived content deferred"). The policy can stay deferred, but the
  cheap part of it is a layout choice: put `run_*`, `canary_call` and `score_*` tables in their own Postgres schema so a
  read restriction can be granted later without moving data. `compilation` stays out (`Compilation.case_derived` is
  false).
- **A6. Greedy trials hash their seeds** (doc: "Greedy sampling and seeds"). Recommendation: keep as is. On an engine
  that isn't batch invariant, greedy replicates still measure nondeterminism, and the seeds are what name the
  replicates. Two greedy trials differing only in seeds is then a real difference in replicate identity, not
  noise. Same answer for "Greedy judges make extra seeds pointless". If the answer is instead "greedy trials take a
  replicate count", it changes `Trial`'s shape and should be done before trials are stored.

## B. Settled by evidence: propose closing

- **Unverified vLLM 0.24 assumptions.** Both hold at tag v0.24.0 (ee0da84ab9e04ac7610e28580af62c365e898389):
  - `ChatCompletionResponse.prompt_token_ids` is a top-level `list[int] | None`, set only when
    `request.return_token_ids` is true:
    https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L129 and
    https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L1070-L1072
  - `0 < temperature < 1e-2` is logged and raised to `1e-2` (`_MAX_TEMP`):
    https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/sampling_params.py#L428-L438
  - Extra fact: greedy is `temperature < 1e-5`, checked after the clamp, so only an exact 0 is greedy; that matches
    `Skeleton.greedy`.

  The fake vLLM should still pin these (AGENTS.md); the Postgres work doesn't depend on it.
- **Vignette fingerprint: raw or normalized?** The "Case" section already answers it: raw, as fetched. What's left is
  pinning the bytes (UTF-8 of the fetched text), which belongs in the prepare-case helper (C5).
- **Should text cleanup apply to appendices?** Recommendation: no. Appendices are authored in the portal, so the
  portal can normalize on save. Browser textareas submit CRLF line endings, so the portal should at least apply
  `newlines.lf@1` before an appendix is digested.

## C. Small code fixes: no decision needed, don't block Postgres

1. Case drift: `check_run` compares `RunItem.vignette` with the `CaseInput.vignette`, new code `case.drift`.
2. `check_score` warns when `ScoreStarted.scorer_code != Scorer.code` (`scorer.code`).
3. `check_score` warns on run cases with no expectation, and on score items whose case has none (`score.no_expectation`).
4. One method for the model name an engine answers to. It's used by `render` callers and `check_run`
   (`ledger.py#L310`) and would be used by judge attestation (next item).
5. Judge calls get the same attestation as run items (returned model, prompt-token fingerprint).
6. Helpers: `prepare_case` (fetch → drift check → cleanup → join appendices), canary → request body, request
   fingerprint (A4). A case-slot judge reuses `prepare_case`.
7. `Scorer.resources` typed `tuple[Digest, ...]` instead of `tuple[str, ...]` (`scoring.py#L60`). Validation only; stored
   strings don't change.
8. New bug: `Registry.check` stops running `cross_check` for every component after the first problem
   (`bundle.py#L69-L70`), so one error hides the rest.
9. New: `ScoreItem.value` accepts NaN, which `canonical_bytes` (`allow_nan=False`) then refuses when sealing. Reject it
   at construction.

## D. Needs a human decision, doesn't block Postgres

- **Canary drift.** What counts as "the same output"? Options: (a) digest of each choice's message (the old
  `compare_canaries` compared output digests), (b) the prompt-token fingerprint only (it catches template or tokenizer
  change, not weight change), (c) both. Recommendation: (c), with (b) as a separate code. Needs no new fields:
  everything is in `CanaryCall.call`.
- **Skeleton provenance.** Recommendation: allow hand-written skeletons. Provenance is "has a `compilation` row", which
  is a query, and can be surfaced by a lint once the ledger is queryable.
- **Re-binding after vignette drift.** This is a portal/bookkeeping workflow. The manifest side already works: a
  rebinding script creates new appendices, a new case and new expectations with unchanged text.
- **Judge engines not verified by canaries** (D5 option B): stays deferred. If adopted, it goes on `ScoreStarted`
  (`canaries`, `verify_at`) the way it did on `RunStarted`. That's a defaulted field, so it's additive.
- **New: `render` always adds `return_token_ids`** (`request.py#L374-L375`). A non-vLLM OpenAI-compatible remote may
  reject unknown keys with a 400. Callers can pass `return_token_ids=False` for remote engines. Should that be the
  engine's decision (for example a field on `RemoteEngine`, which changes the digest) or the runner's?

## E. Defer: can be added later without breaking stored digests

Each of these is a defaulted field, a new `Literal` member or new code, so canonical form keeps old digests:
`Hardware.gpu_count` (default 1), the extra cleanup ops (`nbsp`, zero-width, Unicode line breaks, trailing
whitespace), checking metric names against scorer code, comparing `system_fingerprint`, records in bundles.

## Postgres (chatddx.core.store)
Decisions by the user: psycopg 3 + plain SQL migrations, one component table + ref edges, three integrity tiers as
separate migrations (`NNNN-tK-name.sql`, `migrate(conn, tier)` applies up to tier K), DB tests fail without Postgres.
Open: the portal/bookkeeping layer, async access, per-kind read-only views for a future ORM.
