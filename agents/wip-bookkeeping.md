# Bookkeeping: work in progress

Material for a future `docs/bookkeeping.md` (or whatever the term becomes, see "Naming"). Checked against 183d066.
"Decided" means the user said so; "Take" is the agent's recommendation and still open.

## Scope
Bookkeeping gives humans a handle on immutable factors and records: a name and labels, revisions, tags, an owner,
collaborators, clearance and delete flags. None of it affects an output or a score, so none of it is in the manifest,
and the manifest never references it (docs/manifest.md, "Scope rule → Excluded"). Bookkeeping references factors by
digest and records by id.

## Decisions

### 1. Append-only, the last entry is the head
Decided: bookkeeping is append-only like the rest of the store. For each thing being tracked, the last entry is the
current state (the head), and the earlier entries are its history, ordered by primary key or timestamp.

Take: order by an identity primary key (`bigint GENERATED ALWAYS AS IDENTITY`), not by timestamp. Two entries can share
a timestamp, and clocks can be skewed, but primary keys are unique and increase with every insert. Keep `at` for display.
"Current" is then `DISTINCT ON (subject) … ORDER BY subject, id DESC`, or a view that does the same. This also lets
bookkeeping tables reuse the tier-2 insert-only triggers and the writer's SELECT/INSERT grants.

### 2. Identity is a small mutable table of its own
Decided: `(id, name)`, mutable, in its own schema, separate from `factor`, `ledger` and bookkeeping.

Take:
- schema `identity`, table `identity.person (id bigint identity PK, name text NOT NULL)`;
- bookkeeping references `identity.person(id)`;
- the writer gets UPDATE on this table only. That's the one mutable table, so tier 2 doesn't attach insert-only
  triggers to it.
- A person who leaves can't be deleted while bookkeeping rows reference them (the foreign key restricts it), which
  keeps the history intact. A later `active` column can hide them in the portal.
- Placeholder names in tests: alice, bob, carol, collaborator-one, admin (AGENTS.md).

### 3. Clearance is its own infrastructure, not yet specified
Decided: it tracks sensitive sources, sensitive derived content and vetted endpoints, both engines and databases.

What the code offers today:
- `Record.case_derived` marks which logs hold derived content (run and score rows; compilations are not).
- Case-derived tables live in schema `ledger`; at tier 1 `chatddx_reader` can't read them.
- `SourceCase.source` names a vignette's source.
- `RemoteEngine.base_url` is the only endpoint in a component. A local engine has no URL anywhere; docs/manifest.md says
  its URL "belongs to bookkeeping". So endpoint routing (engine → URL) is part of this work even though it isn't in
  the list of bookkeeping fields.
- The old module had `EndpointBinding(engine, base_url, clearances)` and `CaseSourcePolicy(source,
  required_clearances)`, with a check that every endpoint reached by case-derived content held the clearances its
  sources require (`git show de843eb:src/chatddx/core/manifest.py`, around L1913 and L2258). That shape still fits:
  sources require clearances, endpoints hold them, and the runner refuses when one is missing.

Take: databases count as endpoints because the ledger *is* case-derived storage. The Postgres instance holding
`ledger` has to be vetted like an engine, and an export target (records leaving for publication) is another endpoint.
Clearance is enforcement data, not bookkeeping in the naming sense; give it its own schema and module (see "Naming").

### 4. What a configuration is, and what its revisions are called
Decided: "configuration" covers every chunk and component that researchers and clinicians edit, except cases. Engines
are excluded; params and the like are not. Revisions are per component, and can be renamed if the word is taken.

By today's kinds (authors from docs/manifest.md):
- In: `chunk.instructions`, `chunk.few_shot`, `chunk.prompt`, `chunk.output`, `chunk.sampling`, `chunk.reasoning`,
  `chunk.passthrough`, `appendix`, `expectation`, `trial`, `scorer`, `judge`, `scoring`, and `skeleton` when it is
  hand-written (judges).
- Out: `case` (decided), `model`, `engine.local`, `engine.remote` (ops), and `expectation_schema`, `canary_set`
  (developers).
- Unclear:
  - a compiled `skeleton`: it is produced from a recipe, so its history is really its recipe's;
  - a `recipe`: it isn't a component and has no id outside a `Compilation` row. Editing a recipe means a new
    compilation, a new skeleton and then a new trial, so a researcher's single edit becomes a chain of new digests.
    Whether bookkeeping tracks the recipe, the skeleton or only the trial needs deciding.
  - the developer-authored kinds and cases may still want names and tags without revisions.

Naming: "revision" is taken, as `ModelArtifact.revision` and `Code.revision`, where it means a commit. "Version"
means the schema version `v`. "Lineage" is what the doc calls compilation provenance.

Take: call the stable handle a **thread** and each step an **edit**.
- A thread has a kind and a sequence of edits, each pointing at one digest; its head is the last edit.
- Branching (docs/manifest.md, "Intended evolution of configurations", step 4) is a new thread whose first entry
  says which thread and edit it forked from.
- Delete flags apply to threads.
- A digest can appear in several threads: two researchers can land on the same chunk independently, and content
  addressing makes them the same factor.

### 5. What names and labels attach to (the user asked for a take)
They can't attach to a digest alone. Every edit makes a new digest, so a name on a digest would disappear on every
change, and two threads sharing a digest would have to share a name.

Take:
- **Names, tags, owner, collaborators, delete flags** attach to the thread, the stable handle. They are entries in the
  same append-only log, and the head entry of each type wins. Tags are additive: a tag entry adds or removes one tag.
  Collaborators work the same way.
- **Labels for views and resources** attach to (scorer digest, `view` or `resource`, position). Positions only mean
  something inside one scorer digest; the next edit of the scorer may reorder them. The portal copies the labels
  forward when it creates the next edit, and they are history from then on.
- **Records** are their own handles: run and score UUIDs already exist. "Who started a run" and a run's name or tags
  are entries keyed by run id. A foreign key to a run has to point at `ledger.run_stage (run, 'started')`, using the
  constant-`stage` pattern the item tables use, unless a `ledger.run` id table is added.
- Cases: not configurations, but clinicians will want to name them. A case can get a thread with no edits after the
  first, or a separate naming table keyed by digest; this is the one place where naming a digest directly is
  harmless, because a case never changes, it is replaced.

### 6. Sensitivity of bookkeeping data
Decided: only vignettes and data derived from them are sensitive. Source files, names and tags are not.

Consequence: bookkeeping lives outside `ledger` and is readable by `chatddx_reader`. Derived data covers completions,
judge calls (their prompts may contain case text) and score `detail`, all of which are already in `ledger`.

### 7. Separate schema and role, if necessary
Decided: yes, if needed.

Take: three new schemas, no new database role.
- `identity`: mutable; the writer gets SELECT, INSERT, UPDATE.
- `bookkeeping` (or `catalog`): append-only; the writer gets SELECT, INSERT; tier-2 insert-only triggers; the reader
  gets SELECT.
- `clearance`: append-only like bookkeeping. Possibly only a separate role can write it, because granting clearance is
  the action the hard block depends on. That is the one place a new role (`chatddx_clearance`) seems justified.
- Tier 1 grants and default privileges today cover only `factor` and `ledger`
  (`src/chatddx/core/store/migrations/0002-t1-grants.sql`), and the tier-2 triggers attach to an explicit table list
  (`0003-t2-integrity.sql`). New schemas need their own `t1` and `t2` migrations next to the `t0` that creates them.

## Naming: is "bookkeeping" the right term?
It covers three different things:
- curation (names, tags, threads, edits, owner, collaborators, delete flags), which is mutable in effect and harmless;
- identity (people), which is mutable;
- clearance (sources, endpoints, what may flow where), which is enforcement and safety-critical.

Lumping clearance in with tags under one word hides that difference.

Take:
- Use **catalog** for the curation part. It is a catalog of immutable items, and the word doesn't clash with anything
  in the code; `Registry` is taken.
- Use **identity** for people. This clashes with `chatddx/core/manifest/identity.py`, which holds digests and canonical
  form, so that module should be renamed (see below).
- Use **clearance** for the enforcement part.
- "Bookkeeping" can remain the umbrella word in prose, as the manifest doc uses it, but not as a module or schema
  name.

Other terms in use:
- **owner**: already the database owner role (`DB_OWNER`, `settings.database(owner=True)`, the `owner` test
  fixture). Either call the catalog field something else (`steward`?) or rename the database side (`DB_ADMIN`). The
  database side is cheaper to rename.
- **label**: the doc's "labels for views and resources" are positional; "name" is the thread's. Keep the two words
  for those two things.

## File organisation (not bookkeeping, recorded here as asked)
The user's view: only the identity model really belongs in `core`; manifest and store should be at the package root;
the manifest may split into ledger and components.

Checked: components don't depend on the ledger. The imports run one way:
- `ledger.py` imports `bundle`, `cases`, `engine`, `request`, `scoring`, `trial`;
- none of those import `ledger`;
- everything imports `identity.py` (`Frozen`, canonical bytes, digests, `Fingerprint`, `Code`, `Finding`,
  `StructuralError`);
- `bundle.py` (`Registry`) is used by `ledger`, `lint` and the store.

So the split is clean. Take:

```
src/chatddx/
  manifest/           # one unit, since docs/manifest.md says it may move to its own repo
    canon.py          # today's identity.py: Frozen, canonical bytes, digests, Fingerprint, Code, Finding
    components/       # cases, engine, request, scoring, trial, plus bundle (Registry, Bundle) and lint
    ledger.py         # records; imports components, never the reverse
  store/              # Postgres: migrations, Store, migrate
  core/
    identity.py       # people (the new mutable model)
    settings.py
  catalog/ clearance/ # when they exist
  cli.py
```

Points to weigh:
- **Keep `manifest` as one package with a `components`/`ledger` split inside**, rather than two root packages. The doc
  treats the manifest as one importable unit for the orchestrator, runner, scorer and start-up script, and the
  start-up script needs only components. A second-level split lets it import `chatddx.manifest.components` without
  pulling in the ledger. Two root packages would also be fine; it only changes import paths.
- **Rename `identity.py` to `canon.py`** (or `canonical.py`) whatever else happens, so "identity" means people.
- **`chatddx.core.settings`** is named as the project's source of truth in AGENTS.md. If `core` shrinks to identity,
  either settings stays in `core` (it's core in every other sense), or AGENTS.md needs a human edit.
- **The move is mechanical but wide:** imports in every module and test, the `migrations` path inside `store`, and the
  `defined in:` lines in docs/manifest.md, which use bare file names (`ledger.py:RunItem`) and so mostly survive.
  It's best done as one commit before any bookkeeping code exists.

## Open
- Thread granularity for compiled skeletons, recipes and trials (section 4).
- Whether cases get threads or a plain naming table (section 5).
- Who may grant clearance, and whether that needs its own role (section 7).
- The field name for the catalog's "owner" (Naming).
- Whether deleting a thread hides it everywhere, or only from pickers, while runs that used it stay visible.
