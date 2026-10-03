# Catalog: work in progress

Material for `docs/catalog.md`. Split out of the former `agents/wip-bookkeeping.md` (the word "bookkeeping" is retired;
its parts are now catalog, `agents/wip-identity.md` and `agents/wip-clearance.md`); checked against d10c96d.
"Decided" means the user said so; "Take" is the agent's recommendation and still open.

## Scope
The catalog gives humans a handle on immutable factors and records: names, labels, tags, descriptions, owners,
collaborators, version history and delete flags (`docs/factors.md`, "Not factors"). None of it affects an output or a
score, so no factor references it. It references factors by digest, records by id and people by
`identity.person(id)`.

## Decisions

### 1. Append-only, the last entry is the head
Decided: the catalog is append-only like the rest of the store. For each thing being tracked, the last entry is the
current state (the head), and the earlier entries are its history, ordered by primary key or timestamp.

Take: order by an identity primary key (`bigint GENERATED ALWAYS AS IDENTITY`), not by timestamp. Two entries can share
a timestamp and clocks can be skewed, but primary keys are unique and increase with every insert. Keep `at` for display.
"Current" is then `DISTINCT ON (subject) … ORDER BY subject, id DESC`, or a view that does the same.

### 2. Delete is a flag
Decided: deleting is a `deleted` entry in the catalog. True deletion is blocked at tier 2 (`docs/store.md`); a garbage
collector for non-referenced items is "On the table" in `docs/chatddx.md`.

### 3. What a configuration is, and what its revisions are called
Decided: "configuration" covers every chunk and component that researchers and clinicians edit, except cases. Engines
are excluded; params and the like are not. Revisions are per component, and can be renamed if the word is taken.

By today's kinds (principal authors from `docs/factors.md`):
- In: `chunk.instructions`, `chunk.few_shot`, `chunk.prompt`, `chunk.output`, `chunk.sampling`, `chunk.reasoning`,
  `chunk.passthrough`, `appendix`, `expectation`, `trial`, `scorer`, `judge`, `scoring`, and `skeleton` when it is
  hand-written (judges).
- Out: `case` (decided), `model`, `engine.local`, `engine.remote` (ops), and `expectation_schema`, `canary_set`
  (developers).

Naming: "revision" is taken (`ModelArtifact.revision`, `Code.revision`, meaning a commit), "version" means the schema
version `v`, and "lineage" is what `docs/ledger.md` calls compilation provenance.

Take: call the stable handle a **thread** and each step an **edit**.
- A thread has a kind and a sequence of edits, each pointing at one digest; its head is the last edit.
- Branching (`docs/chatddx.md`, "Intended evolution of configurations", step 4) is a new thread whose first entry
  says which thread and edit it forked from.
- Delete flags apply to threads.
- A digest can appear in several threads: two researchers can land on the same chunk independently, and content
  addressing makes them the same factor.

### 4. What names and labels attach to
They can't attach to a digest alone: every edit makes a new digest, so a name on a digest would disappear on every
change, and two threads sharing a digest would have to share a name.

Take:
- **Names, tags, descriptions, owner, collaborators, delete flags** attach to the thread. They are entries in the same
  append-only log, and the head entry of each type wins. Tags and collaborators are additive: an entry adds or removes
  one.
- **Labels for views and resources** attach to (scorer digest, `view` or `resource`, position). Positions only mean
  something inside one scorer digest; the next edit may reorder them. The portal copies labels forward when it creates
  the next edit.
- **Records** are their own handles: run and score UUIDs already exist. "Who started a run" and a run's name or tags
  are entries keyed by run id. A foreign key to a run points at `ledger.run_stage (run, 'started')` through a
  constant `stage` column, the pattern `ledger.score_stage` already uses for its run
  (`src/chatddx/store/migrations/0001-t0-tables.sql:72`), unless a `ledger.run` id table is added.

### 5. Sensitivity
Decided: only vignettes and data derived from them are sensitive; source files, names and tags are not. So the catalog
lives outside `ledger` and `chatddx_reader` may read it.

## Store readiness
- Schema `catalog`, append-only: the writer gets SELECT, INSERT; the reader gets SELECT.
- Grants (`src/chatddx/store/migrations/0002-t1-grants.sql`) and insert-only triggers
  (`0003-t2-integrity.sql`) name their schemas and tables explicitly, so the catalog needs its own `t0`, `t1` and `t2`
  migrations. Numbering them `0004-t0-…` onward works: `migrate` applies pending files in name order up to the tier
  (`src/chatddx/store/migrate.py`).
- `factor.refuse_change()` can be reused, but lives in schema `factor`; moving it to `public` would stop the catalog
  depending on `factor` for anything but foreign keys.
- `Store` (`src/chatddx/store/store.py`) has no catalog API; whether it grows one or a separate class is open.

## Open
1. **Thread granularity for the request side.** This blocks the main table. Researchers edit chunks, a recipe is not a
   component and exists only inside a `Compilation` row, and a trial references the compiled skeleton. One edit to a
   chunk therefore means a new compilation, a new skeleton and a new trial: a chain of new digests. Does a thread follow
   the chunk, the recipe, the skeleton or the trial? A compiled skeleton's history is really its recipe's.
2. **Cases** are not configurations, but clinicians will want to name them: a thread with a single edit, or a plain
   naming table keyed by digest. Naming a digest directly is harmless here, because a case is replaced, never edited.
   Developer-authored kinds may want names and tags without threads too.
3. **The "owner" field name** clashes with the database owner (`agents/wip-identity.md`, "Open").
4. **What deleting a thread hides:** everywhere, or only pickers, while runs that used it stay visible.
