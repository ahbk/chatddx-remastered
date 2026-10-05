# Store

The store is chatddx's PostgreSQL backend: psycopg 3 and plain SQL migrations, no ORM. Each module that keeps data
has its own schema: `factor` (`docs/factors.md`), `ledger` (`docs/ledger.md`), `identity` (`docs/identity.md`) and
`catalog` (`docs/catalog.md`). Everything but `identity` is append-only, and tier 2 enforces it.

Code paths are relative to `src/chatddx/store/` unless they start with `src/` or `docs/`.

## Code
- `store.py:Store`: components and records. `add(registry, roots)` checks the closure and writes components with
  their reference rows in one transaction; `get` and `load` read them back from `canonical` and verify digests;
  `append(*records)` writes ledger rows in one transaction and never overwrites one; `run` and `score` reassemble
  records from rows; `compilations(skeleton)` finds a skeleton's compilations by their `/skeleton` reference.
- `people.py:People`: people, passwords and sessions.
- `catalog.py:Catalog`: the catalog's writes and reads. Each method reads through `catalog.py:Rows`, which answers
  `src/chatddx/catalog/read.py:Reader` from the tables, lets `chatddx.catalog` decide, and writes the result in one
  transaction. The store answers which rows exist; `chatddx.catalog` decides what they mean.
- `migrate.py`: `migrate(conn, tier)` and `pending(conn, tier)`.

## Tiers
Integrity comes in three tiers, and a deployment applies migrations up to a chosen tier. Each schema has migrations
at every tier:
- Tier 0: schemas, tables, keys, foreign keys, indexes and simple column CHECKs.
- Tier 1: roles and grants.
- Tier 2: CHECKs and triggers that repeat the rules held in Python, and insert-only triggers. CHECKs are over
  app-written columns, not generated columns, so the Python code is the same at every tier.

`migrate(conn, tier)` applies pending migrations up to the tier in file-name order, each in its own transaction, and
records them in `public.migration`. Raising the tier later applies what was skipped; lowering it undoes nothing.
File-name order is not tier order: a later schema's tier 0 runs after an earlier schema's tier 2.

`chatddx migrate [--tier {0,1,2}] [--dry-run]` (`src/chatddx/cli.py`) connects as `DB_ADMIN`, applies pending
migrations up to the tier (default 2) and prints each one; `--dry-run` only lists them.

## Roles and connections
Settings: `src/chatddx/core/settings.py:database(admin=False)`, from `DB_HOST`, `DB_NAME`, `DB_USER` and `DB_ADMIN`
(`.env-example`).
- `DB_ADMIN` (`chatddx`) owns the database and its schemas and runs `chatddx migrate`. It needs CREATEROLE the first
  time tier 1 runs in a cluster, and CREATEDB to run the tests.
- `DB_USER` (`chatddx_writer`) is what the app connects as. Tier 1 creates `chatddx_writer` as NOLOGIN only if it
  doesn't exist, so the login role is created outside the migrations (`setup.sql`).
- Grants don't bind the admin or a superuser, which is why the app doesn't connect as the admin. At tier 0 there are
  no grants, so a tier-0 deployment has to connect as the admin.
- Tier-2 triggers bind the admin too; only a superuser who disables triggers gets around them.
- Roles are cluster-wide, so every database in a cluster shares `chatddx_writer` and `chatddx_reader`.

Agents use the system cluster at `/var/run/postgresql` (setup in `AGENTS.md`); the devShell uses `scripts/dev-db.sh`
and its socket `${REPO_ROOT}/dev-db/pgsock`.

## Tests
`test/`: a migrated template database per session, a fresh copy per test, dropped afterwards. The admin creates and
migrates databases; tests use the writer except where they need the admin (tier-0 writes, tier-2 triggers). They
fail, rather than skip, when Postgres is unreachable.

## Known gaps
- The database doesn't check that a component has all its reference rows, only that the rows it has are right;
  `Store.add` writes them together.
- The database allows a thread without edits; `Catalog.create` writes both in one transaction.
- `jsonb` rejects `\u0000` in strings, so a component or record containing NUL can't be stored.
- `doc` is `jsonb`, which reorders object keys, so a schema's property order survives only in `canonical` and
  `payload`. Components are read from `canonical` (`store.py:Store.get`, `Store.load`); a future ORM or view must not
  rebuild them from `doc`.
- No async API yet; the runner may want one (psycopg 3 has both).
- Per-kind read-only views, for a future ORM, aren't written.

## Migrations
Migrations are `migrations/NNNN-tT-<schema>-<what>.sql`: the next free number, the tier, the schema and a few words
on what it does (the first migration of each schema and tier has none). Below, each schema lists its migrations by
tier; a new migration goes last in its schema and tier's list, with what it changes and the Python code that repeats
it, if any. A new schema gets its own section with all three tiers.

### factor

#### Tier 0
- `0001-t0-factor.sql`: schema `factor`.
  - `component (digest, kind, v, canonical, doc)`: every component kind in one table. `canonical` is the source of
    truth; `doc` is its `jsonb` copy.
  - `component_ref (src, path, dst, kinds)`: one row per typed reference (`Component.refs()`), with deferred foreign
    keys on `src` and `dst`. This gives references inside lists, and references that allow several kinds, a
    foreign key each.

#### Tier 1
- `0002-t1-factor.sql`: creates `chatddx_writer` and `chatddx_reader` (NOLOGIN) unless they exist; every schema's
  tier 1 grants to them. PUBLIC loses all access to `factor`; the writer gets SELECT, INSERT and the reader SELECT,
  with default privileges for tables the admin creates later.

#### Tier 2
- `0003-t2-factor.sql`: a CHECK that `digest` matches `canonical` and that `doc`, `kind` and `v` match it; a
  deferred constraint trigger (`factor.check_ref`) that each reference row points at one of its allowed kinds and
  matches the value at its path; `factor.refuse_change()`, which every schema's insert-only triggers call; and
  insert-only triggers (UPDATE, DELETE, TRUNCATE) on both tables.

### ledger

#### Tier 0
- `0004-t0-ledger.sql`: schema `ledger`, the run and score logs: `run_stage (run, stage)`,
  `run_item (run, case, replicate)`, `canary_call (run, phase, canary)`, `score_stage (score, stage)` and
  `score_item (score, case, replicate, view)`. `payload` holds `Record.canonical`; `doc` is its `jsonb` copy. Item
  rows and canary calls reference their log's started row through a constant `stage` column, and a score's started
  row references its run's. Trials, scorings and cases reference `factor.component`.

#### Tier 1
- `0005-t1-ledger.sql`: PUBLIC loses all access; the writer gets SELECT, INSERT, with default privileges. The reader
  gets nothing: `run_item` and `score_item` hold case-derived records
  (`src/chatddx/ledger/record.py:Record.case_derived`), and the stage rows and canary calls sit beside them so that
  each log stays in one schema.

#### Tier 2
- `0006-t2-ledger.sql`: CHECKs that `doc` matches `payload` and that every key column matches `doc`; insert-only
  triggers on every table.

### identity

#### Tier 0
- `0007-t0-identity.sql`: schema `identity`, the one mutable schema.
  - `person (id, name, login, roles, active)`: `id` generated as identity, `login` unique.
  - `credential (person, hash)`.
  - `session (token_digest, person, created, expires)`: only a digest of the token is stored.

#### Tier 1
- `0008-t1-identity.sql`: PUBLIC loses all access. Each table is granted explicitly, without default privileges,
  because tables here may be mutable. The writer gets SELECT, INSERT, UPDATE on `person` and `credential`, and no
  DELETE, so people are never removed; and SELECT, INSERT, DELETE on `session` (sessions are deleted on logout,
  expiry and deactivation). The reader gets SELECT on `person` only.

#### Tier 2
- `0009-t2-identity.sql`: CHECKs on `person.login` (lowercase pattern), `person.roles` (known role names) and
  `session` expiry. Repeated by `src/chatddx/identity/identity.py:LOGIN` and `Role`, and `Role` is tested against
  the check (`test/test_identity.py`). No insert-only triggers.

### catalog

#### Tier 0
- `0010-t0-catalog.sql`: schema `catalog`, append-only.
  - `UNIQUE (digest, kind)` on `factor.component` and `UNIQUE (src, path, dst)` on `factor.component_ref`, as
    targets for the composite foreign keys below.
  - `thread (id, kind, forked_from, by, at)`: `forked_from` is an edit of the same kind.
  - `edit (id, thread, kind, digest, compilation, compilation_kind, compilation_path, based_on, by, at)`: `digest`
    is a component of the thread's kind. `compilation_kind` and `compilation_path` are constants, so that two
    composite foreign keys can require a (skeleton) edit's compilation to be a `compilation` component whose
    `/skeleton` reference row points at the edit's digest. `based_on` is an edit of the same kind.
  - `family (id, by, at)` and `binding (id, family, source, source_id, fingerprint, by, at)`.
  - `entry (id, thread | family | run | score, field, value, person, present, by, at)`: exactly one subject; runs
    and scores through their started rows.
  - `label (id, scorer, kind, part, position, value, by, at)`: `scorer` is a `scorer` component.
  - `language (id, digest, kind, value, by, at)`: a component's language, kept on its digest; `(digest, kind)`
    references `factor.component`.

#### Tier 1
- `0011-t1-catalog.sql`: PUBLIC loses all access; the writer gets SELECT, INSERT and the reader SELECT, with
  default privileges.

#### Tier 2
- `0012-t2-catalog.sql`: repeats the rules of `chatddx.catalog` that concurrent writers could break.
  - Thread kinds, entry fields and language kinds, mirrored by `src/chatddx/catalog/model.py` and tested against it
    (`test/test_catalog.py`).
  - Entry shapes (`src/chatddx/catalog/model.py:Entry._shape`): a name can be removed (no value, `present` false),
    language tags follow `model.py:LANGUAGE`, NULL values are ruled out explicitly (a NULL check passes), and a
    `language` entry is only on a family (`src/chatddx/catalog/language.py:check_entry`).
  - Label positions within the scorer's views or resources (`src/chatddx/catalog/model.py:check_label`).
  - `based_on` is in the thread's origin, at or after `forked_from` (`src/chatddx/catalog/threads.py:check_based_on`).
  - A new binding keeps the previous one's source, and its id or its fingerprint
    (`src/chatddx/catalog/families.py:plan_repair`).
  - Language tags and kinds on `catalog.language`, and no language for a skeleton already known to be compiled
    (`src/chatddx/catalog/language.py:check_language`).
  - Insert-only triggers on every table.

## Proposed amendments
