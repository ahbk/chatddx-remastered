# Catalog: work in progress

Material for `docs/catalog.md`. Split out of the former `agents/wip-bookkeeping.md` (the word "bookkeeping" is retired;
its parts are now catalog, `agents/wip-identity.md` and `agents/wip-clearance.md`).
"Decided" means the user said so; "Take" is the agent's recommendation and still open; "Implemented" means the code
follows it (`src/chatddx/core/catalog.py`, `src/chatddx/store/catalog.py`, migrations `0009`–`0011`).

## Scope
The catalog gives humans a handle on immutable factors and records: names, labels, tags, descriptions, owners,
collaborators, version history and delete flags (`docs/factors.md`, "Not factors"). None of it affects an output or a
score, so no factor references it. It references factors by digest, records by id and people by
`identity.person(id)`.

## Landscape
Factors reference each other by digest, so an edit anywhere makes new digests all the way up:

```
chunk.* ─(recipe, only in factor.compilation)─▶ skeleton ─▶ trial ◀─ engine, seeds, cleanup
                                                    └─────▶ judge ─▶ scorer ─▶ scoring
appendix ─┐                                                                      ▲
vignette ─┴▶ case ─▶ trial                                                       │
              └────▶ expectation ────────────────────────────────────────────────┘
```

People edit the bottom (chunks, appendices, expectation data); runs and scores reference the top (trials, scorings).
A recipe assembles chunks the way a case assembles appendices on a vignette, but a case is a component with a family
key already in its content (`SourceCase`), while a recipe is neither.

## Decisions

### 1. Append-only, the last entry is the head
Decided: the catalog is append-only like the rest of the store. For each thing being tracked, the last entry is the
current state (the head), and the earlier entries are its history.

Implemented (was a take): ordered by identity primary keys, not timestamps; `at` is kept for display.

### 2. Delete is a flag
Decided: deleting is a `deleted` entry in the catalog. True deletion is blocked at tier 2 (`docs/store.md`); a garbage
collector for non-referenced items is "On the table" in `docs/chatddx.md`.

Implemented: a later entry with `present = false` restores the thread.

### 3. Threads, and what a configuration is
Decided: every chunk and component that researchers and clinicians edit has threads, except cases. Engines are
excluded; params and the like are not. (This was first worded as what "configuration" covers; the word now means the
UX entity below.)

Decided (option A2): recipes are skeleton threads. A skeleton thread's edits point at a skeleton digest and at the
`factor.compilation` row that produced it, which holds the recipe; hand-written skeletons have edits without one. So
`skeleton` is in unconditionally, and "a compiled skeleton's history is really its recipe's" is answered: the thread
is the recipe's history.

Decided: a configuration is a set of pinned factors, and a variation is a configuration where one or more factors are
replaced. The user keeps a handful of configurations; variations allow controlled experiments without the
combinatorial explosion. It is a UX entity with no exact representation in the code, only a supported workflow:
- any digest can be run without a thread, so trying a variation costs nothing in the catalog;
- a variation worth keeping becomes an edit of its thread or a fork (`thread.forked_from`);
- `Catalog.behind` shows which pinned factors have moved on.

Implemented (was a take), by today's kinds (`THREAD_KINDS`):
- In: the seven `chunk.*` kinds, `skeleton`, `trial`, `judge`, `scorer`, `scoring`, `appendix`, `expectation`.
- Out: `case` (decided), `model`, `engine.local`, `engine.remote`, `expectation_schema`, `canary_set`.
- A thread has a kind and a sequence of edits, each pointing at one digest of that kind (composite foreign keys).
  A digest can appear in several threads. A fork is a new thread whose `forked_from` is an edit of the same kind.

Naming: "revision" is taken (`ModelArtifact.revision`, `Code.revision`), "version" means the schema version `v`, and
"lineage" is what `docs/ledger.md` calls compilation provenance; hence **thread** and **edit**.

### 4. Downstream threads move only when someone saves them
Decided (option B1). Nothing propagates. `Catalog.behind(thread)` lists the head's references, the recipe's
included, whose own thread has a newer head, so the portal can propose the update; deleted threads are not proposed.
One level at a time: a chunk edit makes its skeleton threads behind; saving one of those makes its trial threads
behind. A fork doesn't make anything behind, since it doesn't contain the digest it forked from.

Not implemented: storing what caused an edit (option B3). It can be derived by diffing consecutive edits' references,
and a nullable `implied_by` column can be added later.

### 5. What names and labels attach to
Implemented (was a take):
- **Names, tags, descriptions, owner, collaborators, delete flags** are entries in one log, `catalog.entry`, on a
  thread, a run or a score. The latest entry wins per field, per tag and per collaborator (`About.of`).
- **Labels for views and resources** attach to (scorer digest, `view` or `resource`, position) in `catalog.label`.
  Positions are checked against the scorer, in Python and by a tier-2 trigger. Copying labels to the next scorer
  edit is left to the portal, which knows how positions moved.
- **Records**: entries on a run or score point at its started row through a constant stage column, as
  `ledger.score_stage` does. "Who started a run" is the run's owner entry.
- The "owner" field name is used, now that the database role is "admin".

### 6. Sensitivity
Decided: only vignettes and data derived from them are sensitive; source files, names and tags are not. So the catalog
lives outside `ledger` and `chatddx_reader` may read it.

## Implemented
- `src/chatddx/core/catalog.py`: `THREAD_KINDS`, `EntryField`, `Entry` (shape rules), `Subject`, `About.of`,
  `Thread`, `Edit`, `Behind`.
- `src/chatddx/store/catalog.py`: `Catalog` with `create` (optionally `forked_from`), `edit`, `thread`, `history`,
  `head`, `heads(kind, deleted=)`, `containing(digest)`, `behind`, `note`, `about`, `label`, `labels`.
- Migrations: `0009-t0-catalog.sql` (tables, composite foreign keys, and `UNIQUE (digest, kind)` on
  `factor.component` and `UNIQUE (digest, skeleton)` on `factor.compilation` as their targets),
  `0010-t1-catalog-grants.sql` (writer SELECT, INSERT; reader SELECT), `0011-t2-catalog-checks.sql` (kind and field
  lists mirrored from code, entry shapes, label positions, insert-only triggers reusing `factor.refuse_change()`).
- `Compilation.digest`, the key of `factor.compilation`, so the catalog and `Store.append` share it.
- Tests: `src/chatddx/store/test/test_catalog.py`.

## Open

### 1. Cases (next)
Nobody edits a case, but its digest changes when an appendix is edited (even a typo), added or removed (a clinician
editing its composition by hand), or when the vignette drifts. The earlier note that "a case is replaced, never
edited" holds for the vignette only. Each change also needs a new expectation with the same data, because
`Expectation.case` is the case digest (`src/chatddx/factors/scoring.py:21`); without one, the new case is scored
against nothing, silently (`docs/factors.md:199`). Re-keying automatically would defeat why expectations key on the
case (an appendix may change the answer), which B1 already rules out.

Options:
- **C1. Name the case digest.** Cheap; the name is lost on every appendix edit and drift.
- **C2. Implied family.** Names and tags attach to `SourceCase (source, id)`; cases get no threads, so "Out" stands.
  A case shows as its family name plus its appendices' thread names; the portal builds the current case from the
  family, the appendix heads and the current fingerprint, and content addressing finds it if it exists. The attach
  key has no foreign key (sources aren't rows), and "current fingerprint" needs an import record that doesn't exist.
- **C3. Case threads.** Reverses "Out"; nearly every case edit is forced by an appendix edit or drift, so the portal
  keeps many threads current.

Take: C2, with expectations keeping their threads and taking re-key edits the portal proposes.

Left undone until this is decided:
- `case` is not in `THREAD_KINDS` (consistent with "Out"; C3 would add it to the code list and the tier-2 check).
- No attach point for case families (C2) or case digests (C1); `catalog.entry` subjects are threads, runs and scores.
- `behind` never reports cases, since they have no threads. Under C2, a trial's or expectation's case is stale when
  its appendices aren't heads or its fingerprint isn't current, which is a different query.
- The expectation re-key flow after an appendix edit or drift.
- Names for developer and ops kinds (models, engines, expectation schemas, canary sets): one-edit threads or naming
  digests directly. Same choice as C1 vs threads, so it rides with this question.

### 2. What deleting a thread hides
Everywhere, or only pickers, while runs that used it stay visible. A portal decision: `heads(kind, deleted=)` and
`behind` give it what it needs.

### 3. Known gaps
- SQL allows a thread without edits; `Catalog.create` writes both in one transaction.
- A skeleton edit may omit its compilation even when one exists; nothing can require it.
- `about()` of a subject with no entries, or no such subject, is an empty `About`.
