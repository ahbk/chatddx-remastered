# Store

The store is chatddx's PostgreSQL backend: psycopg 3 and plain SQL migrations, no ORM. Each module that keeps data
has its own schema: `factor` (`docs/factors.md`), `ledger` (`docs/ledger.md`), `identity` (`docs/identity.md`) and
`catalog` (`docs/catalog.md`). Everything but `identity` is append-only, and tier 2 enforces it.

Code paths are relative to `src/chatddx/store/` unless they start with `src/` or `docs/`.

Migrations are documented in `docs/migrations.md`.

## Code
- `store.py:Store`: components and records.
  - `add(registry, roots)` checks the closure and writes components with their reference rows in one transaction,
    skipping those already stored; `get` and `load` read them back from `canonical` and verify digests;
  - `append(*records)` writes ledger rows in one transaction and raises on a duplicate key, so a row is never
    overwritten.
- `people.py:People`: people, passwords and sessions.
- `catalog.py:Catalog`: the catalog's writes and reads. Each method reads through `catalog.py:Rows`, which answers
  `src/chatddx/catalog/read.py:Reader` from the tables, lets `chatddx.catalog` decide, and writes the result in one
  transaction. The store answers which rows exist; `chatddx.catalog` decides what they mean.
- `migrate.py`: `migrate(conn, tier)` and `pending(conn, tier)`.
- `load` returns a `Registry` without checking it, so `Registry.check` and `Registry.bundle` work on it as on any other.

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
- A log can be written that can't be read back. `Store.append` checks only keys and foreign keys, and a finished
  row has no foreign key to its started row, so a finished row alone is accepted, and `Store.run` then fails with
  `StructuralError: RunStarted vNone is not readable by v1`. `Run` and `Score` validate a log only when it is read.
- `RunStarted.canaries` has no foreign key, so a run can name a canary set that isn't stored, and `check_run` then
  can't resolve it." (`migrations/0004-t0-ledger.sql`, `src/chatddx/ledger/run.py:RunStarted`, `check_run`)
- Runs and scores can't be found: `Store.run` and `Store.score` take an id, and nothing lists the runs of a trial
  or the scores of a run.
- A schema-version bump strands stored rows. `Store.get`, `load`, `run` and `score` parse every row, and parsing
  refuses another version (`docs/factors.md`, "Versions"; `docs/ledger.md`, "Canonical form and versions"), so
  after a bump the store can't return rows of the old version, nor load a closure that holds one. The rows can't
  be rewritten either: the tables are insert-only, and digests and seals depend on the bytes.
  `docs/factors.md`, "Splitting expectations", already considers a bump of `Scorer.schema_version`.

## Proposed amendments
- CHANGE in "Tests", "`test/`: a migrated template database per session" → "`src/chatddx/conftest.py`: a migrated
  template database per session, shared by every package's tests" (`src/chatddx/conftest.py`)
- CHANGE in "Known gaps", "No async API yet; the runner may want one (psycopg 3 has both)." → "No async API. The
  runner is synchronous, and wants a connection in autocommit mode so that each row lands as it's written: on a
  connection that isn't, a statement outside a transaction block (`Catalog.note`, `People.find`) opens a transaction
  only the caller commits, and `Store.append`'s transactions become savepoints inside it."
  (`src/chatddx/runner/run.py:Runner`, `src/chatddx/store/store.py:Store.append`)
