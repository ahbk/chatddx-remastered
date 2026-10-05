# Catalog: work in progress

Superseded by `agents/catalog.md` (the doc) and `agents/wip-catalog-review.md` (the decisions since); kept for
its history.

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
chunk.* ─(recipe, only in compilations)──────▶ skeleton ─▶ trial ◀─ engine, seeds, cleanup
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
compilation that produced it, which holds the recipe; hand-written skeletons have edits without one. So
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
- Also in, decided later: `model`, `engine.local`, `engine.remote`, `expectation_schema`, `canary_set`. They are
  rarely edited, but threads are the one place names attach for everything but cases (migration
  `0014-t2-catalog-kinds.sql`).
- Out: `case` (decided; see decision 7). A test pins `THREAD_KINDS` to every registered kind except `case`.
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

Implemented later, for variations (G9 in `agents/wip-sample-data.md`): a nullable `catalog.edit.based_on`. It names
the origin edit a fork was re-applied onto, so `Catalog.variation` and `Catalog.proposal` compare a fork with the
right base. It's narrower than `implied_by`: it only says what a fork is based on, not why an edit was made.

### 5. What names and labels attach to
Implemented (was a take):
- **Names, tags, descriptions, owner, collaborators, delete flags** are entries in one log, `catalog.entry`, on a
  thread, a case family (decision 7), a run or a score. The latest entry wins per field, per tag and per
  collaborator (`About.of`).
- **Labels for views and resources** attach to (scorer digest, `view` or `resource`, position) in `catalog.label`.
  Positions are checked against the scorer, in Python and by a tier-2 trigger. Copying labels to the next scorer
  edit is left to the portal, which knows how positions moved.
- **Records**: entries on a run or score point at its started row through a constant stage column, as
  `ledger.score_stage` does. "Who started a run" is the run's owner entry.
- The "owner" field name is used, now that the database role is "admin".

### 6. Sensitivity
Decided: only vignettes and data derived from them are sensitive; source files, names and tags are not. So the catalog
lives outside `ledger` and `chatddx_reader` may read it.

### 7. Cases are named through families (C2)
Decided: option C2. Cases get no threads, so "Out" stands.

Implemented, with one refinement: names attach to a catalog **family**, not directly to `SourceCase (source, id)`.
With the user's suggestion that names start from the vignette's file name, a renamed file would otherwise lose them.
- `catalog.family` is the handle. `catalog.binding` logs where the family's vignette is, as (source, id, vignette
  fingerprint); the latest binding is current. This is also the record of the current fingerprint that C2 lacked.
- A case belongs to the family with a binding, current or earlier, that matches its (source, id, fingerprint), so
  cases from before a repair still find their family (`Catalog.family`).
- `Catalog.adopt(case)` returns the case's family or creates one. It refuses a case whose (source, id) is another
  family's current binding with other content, or whose fingerprint is another family's current binding under
  another id: those need a repair, not a new family.
- Names, tags and the rest are entries on the family (`Subject(family=)`). The current binding's id (the file name)
  can be the root of the displayed name, qualified by a name entry or the appendices' names; that's for the portal.
- `behind` looks through cases, since they have no threads: a trial's or expectation's case is behind when one of
  its appendices has a newer head (paths like `/cases/0/appendices/0` and `/case/appendices/0`). The portal proposes
  the new case and, for expectations, the re-key edit, which a clinician confirms because an appendix may change
  the answer.

Decided: helpers that repair either a content change or a file-name change, never both; not implemented now. C2 with
the binding log allows them, because each repair keeps one half of the binding as its anchor:
- **Content changed, same file name.** The anchor is (source, id), the family's current binding. The helper appends
  a binding with the new fingerprint, re-binds each appendix head to it (same text, new digest, a new edit on the same
  appendix thread) and builds the new cases; `behind` then flags trials and expectations. Expectations need a
  clinician to confirm, since the vignette changed and the answer may have.
- **File renamed, same content.** The anchor is (source, fingerprint), the family's current binding. The helper
  appends a binding with the new id, re-binds appendices to the new `SourceCase` (same text and fingerprint) and
  builds the new cases. Names stay on the family; a name rooted in the file name follows the rename. Expectation data
  can't be affected, so the helper may write the re-key edits itself.
- **Both changed.** No anchor, so no repair: it's a new family through `adopt`, and names are carried over by hand.
  For the helpers, consecutive bindings of a family must share the id or the fingerprint; a tier-2 trigger can
  enforce that when they land.
- Detection: the import script's listing of a source (id → fingerprint) compared with current bindings, or the
  case-drift check (`docs/factors.md`, "Misc", not implemented).

Implemented later (G13 in `agents/wip-sample-data.md`): `Catalog.survey` for detection, `Catalog.repair`, the
"keep the id or the vignette" trigger (`0018-t2-catalog-bindings.sql`), and `behind` reporting cases whose family
has a newer binding (`Behind.binding`).

## Implemented
- `src/chatddx/core/catalog.py`: `THREAD_KINDS`, `EntryField`, `Entry` (shape rules), `Subject`, `About.of`,
  `Thread`, `Edit`, `Behind`, `Binding`.
- `src/chatddx/store/catalog.py`: `Catalog` with `create` (optionally `forked_from`), `edit`, `thread`, `history`,
  `head`, `heads(kind, deleted=)`, `containing(digest)`, `behind`, `adopt`, `family`, `bindings`, `note`,
  `about`, `label`, `labels`.
- Migrations: `0009-t0-catalog.sql` (tables, composite foreign keys, and `UNIQUE (digest, kind)` on
  `factor.component` and `UNIQUE (src, path, dst)` on `factor.component_ref` as their targets),
  `0010-t1-catalog-grants.sql` (writer SELECT, INSERT; reader SELECT), `0011-t2-catalog-checks.sql` (kind and field
  lists mirrored from code, entry shapes, label positions, insert-only triggers reusing `factor.refuse_change()`),
  `0012-t0-catalog-families.sql` (family, binding, entries on families), `0013-t2-catalog-families.sql`
  (insert-only triggers), `0014-t2-catalog-kinds.sql` (ops and developer kinds get threads).
- Compilations are components (`src/chatddx/factors/request.py:Compilation`); `catalog.edit` reaches them through
  `factor.component` and `factor.component_ref` (`0009-t0-catalog.sql`). `factor.compilation` is gone.
- Tests: `src/chatddx/store/test/test_catalog.py`.

## Open

### 1. What deleting a thread hides
Everywhere, or only pickers, while runs that used it stay visible. A portal decision: `heads(kind, deleted=)` and
`behind` give it what it needs.

### 2. Known gaps
- Names are optional and unnamed threads are shown by `Catalog.title` (G17 in `agents/wip-sample-data.md`). A
  title is rebuilt from mutable names, and costs a query per reference it visits.
- SQL allows a thread without edits; `Catalog.create` writes both in one transaction. A deferred constraint
  trigger, as `0003` uses for reference rows, could close this at tier 2.
- A skeleton edit may omit its compilation even when one exists; nothing can require it.
- `about()` of a subject with no entries, or no such subject, is an empty `About`.
- Two concurrent `adopt` calls for the same vignette can create two families; nothing makes current bindings unique.
- Two identical vignettes under different ids in one source look like a rename to `adopt`, which refuses the second.
- If bindings of several families match a case, `family` returns the one with the latest binding.
