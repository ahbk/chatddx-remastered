# Store

This document describes the postgresql backend for chatddx called store.
It includes roles and owner (admin) setup, migrations and tests.

## Basics
- psycopg 3 and plain SQL migrations (no ORM).
- One component table plus reference edges, not one table per kind.
- Integrity in three tiers. Each tier is its own migration and a deployment applies them up to a chosen tier.
- DB tests fail, rather than skip, when Postgres is unreachable.
- `dev-db` scripts to setup a local postgres instance for development

## Layout

- Schema `factor`, not case-derived:
  - `component (digest, kind, v, canonical, doc)`: every component kind in one table. `canonical` is the source of
    truth; `doc` is its `jsonb` copy.
  - `component_ref (src, path, dst, kinds)`: one row per typed reference (`Component.refs()`), with a deferred foreign
    key on `dst`. This covers references inside lists and references that allow several kinds.
  - `compilation (digest, skeleton, payload, doc)`: keyed by the digest of the record's canonical bytes, so writing
    it again is a no-op.

- Schema `ledger`, case-derived: `run_stage (run, stage)`, `run_item (run, case, replicate)`,
  `canary_call (run, phase, probe)`, `score_stage (score, stage)`, `score_item (score, case, replicate, view)`.
  Item rows reference their log's started row through a constant `stage` column. `payload` holds
  `Record.canonical`; `doc` is its `jsonb` copy.

- Schema `identity`, not case-derived and mutable: `person (id, name)`, `id` generated as identity

- schema `identity`: `person` gains `login` (unique), `roles` and `active`; new tables
  `credential (person, hash)` and `session (token_digest, person, created, expires)`

- Schema `catalog`, not case-derived, append-only: `thread (id, kind, forked_from, by, at)`,
  `edit (id, thread, kind, digest, compilation, based_on, by, at)`,
  `entry (id, thread | run | score, field, value, person, present, by, at)` and
  `label (id, scorer, part, position, value, by, at)`.
  `based_on` is an edit of the same kind in the thread's origin, at or after `forked_from`.

- schema `catalog` gains `family (id, by, at)` and `binding (id, family, source, source_id,
  vignette, by, at)`, and `entry` gains a `family` subject.

## Tiers
Migrations apply in file-name order, not tier order, so a later schema's tier-0 file (`0004-t0-…`)
runs after earlier tier-1 and tier-2 files (`src/chatddx/store/migrate.py`, `pending`).

### Tier 0

migrations:
- `0001-t0-tables.sql`
- `0004-t0-identity.sql`
- `0006-t0-identity-auth.sql`
- `0009-t0-catalog.sql`
- `0012-t0-catalog-families.sql`
- `0015-t0-catalog-based-on.sql` (the column and its same-kind foreign key).

Endowes:
- tables, keys, foreign keys.
- `UNIQUE (digest, kind)` to `factor.component` and
- `UNIQUE (digest, skeleton)` to `factor.compilation`, as targets for the catalog's composite foreign keys.

### Tier 1

migration:
- `0002-t1-grants.sql`
- `0005-t1-identity-grants.sql`
- `0007-t1-identity-auth-grants.sql`
- `0010-t1-catalog-grants.sql`

Endowes:
- roles `chatddx_writer` (SELECT, INSERT on both schemas) and `chatddx_reader`
- (SELECT on `factor` only).
- PUBLIC loses all access.
- Default privileges cover tables the admin creates later.

- `chatddx_writer` gets SELECT, INSERT, UPDATE on `identity.person` and no DELETE, so people
  are never removed; `chatddx_reader` gets SELECT. Each identity table is granted explicitly, without default
  privileges, because tables there may be mutable.

- `chatddx_writer` gets SELECT, INSERT, UPDATE on `identity.credential` and SELECT, INSERT,
  DELETE on `identity.session` (sessions are deleted on logout, expiry and deactivation); `chatddx_reader` gets neither.

- `chatddx_writer` gets SELECT, INSERT and `chatddx_reader` gets SELECT on `catalog`, with default privileges like `factor`.

### Tier 2

migrations:
- `0003-t2-integrity.sql`
- `0008-t2-identity-checks.sql`
- `0011-t2-catalog-checks.sql`
- `0013-t2-catalog-families.sql`
- `0014-t2-catalog-kinds.sql`
- `0016-t2-catalog-based-on.sql` (a trigger that keeps `based_on` in the origin thread, repeated in `Catalog.edit`).

Endowes:
- CHECKs that digests match canonical text, that `doc` and every key column match
  the payload, a deferred constraint trigger that each reference row points at an allowed kind and matches the value
  at its path, and triggers that refuse UPDATE, DELETE and TRUNCATE.
- CHECKs over app-written columns rather than generated columns, so the Python code is the same at every tier.
- `migrate(conn, tier)` records applied migrations in `public.migration`. Raising the tier later applies what was
  skipped; lowering it undoes nothing.
- `identity` gets no insert-only triggers; it is the one mutable schema.
- CHECKs on `identity.person.login` (lowercase pattern), `identity.person.roles` (known role
  names, mirrored by `src/chatddx/core/identity.py:Role` and tested against it) and `identity.session` expiry.
- thread kinds and entry fields (mirrored by `src/chatddx/core/catalog.py` and tested against it),
  entry shapes, label positions, and insert-only triggers reusing `factor.refuse_change()`.
- insert-only triggers for `catalog.family` and `catalog.binding`.
- `0014-t2-catalog-kinds.sql`: replaces the thread-kind check so that every kind but `case` has threads.

## Roles and connections
Settings: `chatddx.core.settings.database(admin=False)`, from `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_ADMIN`
(`.env-example`).
- `DB_ADMIN` (`chatddx`) owns the database and its schemas and runs `chatddx migrate`. It needs CREATEROLE the first
  time tier 1 runs in a cluster, and CREATEDB to run the tests.
- `DB_USER` (`chatddx_writer`) is what the app connects as. Tier 1 creates `chatddx_writer` as NOLOGIN only if it
  doesn't exist, so the login role has to be created (or altered to LOGIN) outside the migrations.
- Grants don't bind the admin or a superuser, which is why the app doesn't connect as the admin. At tier 0 there are
  no grants, so a tier-0 deployment has to connect as the admin.
- Tier-2 triggers bind the admin too; only a superuser who disables triggers gets around them.
- Roles are cluster-wide, so every database in a cluster shares `chatddx_writer` and `chatddx_reader`.

Agents use the system cluster at /var/run/postgresql, while the devShell uses dev-db and its socket `${REPO_ROOT}/dev-db/pgsock`.
Local setup used by in the agent container (Unix socket, peer auth mapped from the OS user):
```
CREATE ROLE chatddx LOGIN CREATEDB CREATEROLE;
CREATE ROLE chatddx_writer LOGIN;
CREATE DATABASE chatddx OWNER chatddx;
-- pg_ident.conf: chatddx <os-user> chatddx / chatddx <os-user> chatddx_writer
-- pg_hba.conf:   local all all peer map=chatddx
```
then `chatddx migrate`.

## Command
`chatddx migrate [--tier {0,1,2}] [--dry-run]` (`src/chatddx/cli.py`) connects as `DB_ADMIN`, applies pending
migrations up to the tier (default 2) and prints each one; `--dry-run` only lists them.

## Store API (`src/chatddx/store/store.py`)
- `add(registry, roots)`: checks the closure and inserts components and their reference rows in one transaction.
- `get(digest)`, `load(roots)`: read back and verify digests. `load` follows `component_ref` with a recursive query and
  returns a `Registry`, so `check`/`bundle` work unchanged.
- `append(*records)`: one transaction. Ledger rows are never overwritten; a duplicate key raises.
- `run(id)`, `score(id)`, `compilations(skeleton)`: reassemble from rows; seals don't depend on row order.
- `People` (`src/chatddx/store/people.py`): `add`, `get`, `find`, `update`, `set_password`,
  `authenticate`, `open_session`, `session`, `close_session`, `purge_sessions`.
- `Catalog` (`src/chatddx/store/catalog.py`): `create`, `edit`, `thread`, `history`, `head`,
  `heads`, `containing`, `behind`, `note`, `about`, `label`, `labels`, `recipe`, `variation` and `proposal`.
- `Catalog.adopt`, `Catalog.family`, `Catalog.bindings`.

## Tests
`src/chatddx/store/test/`: a migrated template database per session, a fresh copy per test, dropped afterwards.
The admin creates and migrates databases; tests use the writer except where they need the admin (tier-0 writes,
tier-2 triggers).

## Known gaps
- The database doesn't check that a component has all its reference rows, only that the rows it has are right;
  `Store.add` writes them together.
- `jsonb` rejects `\u0000` in strings, so a component or record containing NUL can't be stored.
- No async API yet; the runner may want one (psycopg 3 has both).
- Per-kind read-only views, for a future ORM, aren't written.
- the database allows a thread without edits; `Catalog.create` writes both in one transaction.
- "`doc` is `jsonb`, which reorders object keys, so a schema's property order survives only in
  `canonical` and `payload`. Components are read from `canonical` (`src/chatddx/store/store.py:Store.get`,
  `Store.load`); a future ORM or view must not rebuild them from `doc`." (`src/chatddx/factors/base.py:46`)

## Proposed amendments
