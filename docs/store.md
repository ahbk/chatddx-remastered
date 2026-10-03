# Store

This document describes the postgresql backend for chatddx called store.
It includes roles and owner setup, migrations and tests.

## Basics
- psycopg 3 and plain SQL migrations (no ORM).
- One component table plus reference edges, not one table per kind.
- Integrity in three tiers. Each tier is its own migration and a deployment applies them up to a chosen tier.
- DB tests fail, rather than skip, when Postgres is unreachable.
- `dev-db` scripts to setup a local postgres instance for development

## Layout
`src/chatddx/store/migrations/0001-t0-tables.sql`
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

## Tiers
- Tier 0 (`0001-t0-tables.sql`): tables, keys, foreign keys.
- Tier 1 (`0002-t1-grants.sql`): roles `chatddx_writer` (SELECT, INSERT on both schemas) and `chatddx_reader`
  (SELECT on `factor` only). PUBLIC loses all access. Default privileges cover tables the owner creates later.
- Tier 2 (`0003-t2-integrity.sql`): CHECKs that digests match canonical text, that `doc` and every key column match
  the payload, a deferred constraint trigger that each reference row points at an allowed kind and matches the value
  at its path, and triggers that refuse UPDATE, DELETE and TRUNCATE.
- Tier 2 uses CHECKs over app-written columns rather than generated columns, so the Python code is the same at every
  tier.
- `migrate(conn, tier)` records applied migrations in `public.migration`. Raising the tier later applies what was
  skipped; lowering it undoes nothing.

## Roles and connections
Settings: `chatddx.core.settings.database(owner=False)`, from `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_OWNER`
(`.env-example`).
- `DB_OWNER` (`chatddx`) owns the database and its schemas and runs `chatddx migrate`. It needs CREATEROLE the first
  time tier 1 runs in a cluster, and CREATEDB to run the tests.
- `DB_USER` (`chatddx_writer`) is what the app connects as. Tier 1 creates `chatddx_writer` as NOLOGIN only if it
  doesn't exist, so the login role has to be created (or altered to LOGIN) outside the migrations.
- Grants don't bind the owner or a superuser, which is why the app doesn't connect as the owner. At tier 0 there are
  no grants, so a tier-0 deployment has to connect as the owner.
- Tier-2 triggers bind the owner too; only a superuser who disables triggers gets around them.
- Roles are cluster-wide, so every database in a cluster shares `chatddx_writer` and `chatddx_reader`.

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
`chatddx migrate [--tier {0,1,2}] [--dry-run]` (`src/chatddx/cli.py`) connects as `DB_OWNER`, applies pending
migrations up to the tier (default 2) and prints each one; `--dry-run` only lists them.

## Store API (`src/chatddx/store/store.py`)
- `add(registry, roots)`: checks the closure and inserts components and their reference rows in one transaction.
- `get(digest)`, `load(roots)`: read back and verify digests. `load` follows `component_ref` with a recursive query and
  returns a `Registry`, so `check`/`bundle` work unchanged.
- `append(*records)`: one transaction. Ledger rows are never overwritten; a duplicate key raises.
- `run(id)`, `score(id)`, `compilations(skeleton)`: reassemble from rows; seals don't depend on row order.

## Tests
`src/chatddx/store/test/`: a migrated template database per session, a fresh copy per test, dropped afterwards.
The owner creates and migrates databases; tests use the writer except where they need the owner (tier-0 writes,
tier-2 triggers).

## Known gaps
- The database doesn't check that a component has all its reference rows, only that the rows it has are right;
  `Store.add` writes them together.
- `jsonb` rejects `\u0000` in strings, so a component or record containing NUL can't be stored.
- No async API yet; the runner may want one (psycopg 3 has both).
- Per-kind read-only views, for a future ORM, aren't written.
