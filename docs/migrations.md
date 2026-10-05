# Migrations
Migrations are `migrations/NNNN-tT-<schema>-<what>.sql`: the next free number, the tier, the schema and a few words
on what it does (the first migration of each schema and tier has none). Below, each schema lists its migrations by
tier; a new migration goes last in its schema and tier's list, with what it changes and the Python code that repeats
it, if any. A new schema gets its own section with all three tiers.

## factor

### Tier 0
- `0001-t0-factor.sql`: schema `factor`.
  - `component (digest, kind, v, canonical, doc)`: every component kind in one table. `canonical` is the source of
    truth; `doc` is its `jsonb` copy.
  - `component_ref (src, path, dst, kinds)`: one row per typed reference (`Component.refs()`), with deferred foreign
    keys on `src` and `dst`. This gives references inside lists, and references that allow several kinds, a
    foreign key each.

### Tier 1
- `0002-t1-factor.sql`: creates `chatddx_writer` and `chatddx_reader` (NOLOGIN) unless they exist; every schema's
  tier 1 grants to them. PUBLIC loses all access to `factor`; the writer gets SELECT, INSERT and the reader SELECT,
  with default privileges for tables the admin creates later.

### Tier 2
- `0003-t2-factor.sql`: a CHECK that `digest` matches `canonical` and that `doc`, `kind` and `v` match it; a
  deferred constraint trigger (`factor.check_ref`) that each reference row points at one of its allowed kinds and
  matches the value at its path; `factor.refuse_change()`, which every schema's insert-only triggers call; and
  insert-only triggers (UPDATE, DELETE, TRUNCATE) on both tables.

## ledger

### Tier 0
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

## identity

### Tier 0
- `0007-t0-identity.sql`: schema `identity`, the one mutable schema.
  - `person (id, name, login, roles, active)`: `id` generated as identity, `login` unique.
  - `credential (person, hash)`.
  - `session (token_digest, person, created, expires)`: only a digest of the token is stored.

### Tier 1
- `0008-t1-identity.sql`: PUBLIC loses all access. Each table is granted explicitly, without default privileges,
  because tables here may be mutable. The writer gets SELECT, INSERT, UPDATE on `person` and `credential`, and no
  DELETE, so people are never removed; and SELECT, INSERT, DELETE on `session` (sessions are deleted on logout,
  expiry and deactivation). The reader gets SELECT on `person` only.

### Tier 2
- `0009-t2-identity.sql`: CHECKs on `person.login` (lowercase pattern), `person.roles` (known role names) and
  `session` expiry. Repeated by `src/chatddx/identity/identity.py:LOGIN` and `Role`, and `Role` is tested against
  the check (`test/test_identity.py`). No insert-only triggers.

## catalog

### Tier 0
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

### Tier 1
- `0011-t1-catalog.sql`: PUBLIC loses all access; the writer gets SELECT, INSERT and the reader SELECT, with
  default privileges.

### Tier 2
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
