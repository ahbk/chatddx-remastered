# Identity: work in progress

Material for `docs/identity.md`. Split out of the former `agents/wip-bookkeeping.md`; checked against d10c96d.
"Decided" means the user said so; "Take" is the agent's recommendation and still open.

## Scope
`docs/factors.md` ("Not factors") assigns people, roles, authentication and authorization to identity. Identity is
referenced by the catalog (owners, collaborators, who started a run) and possibly by clearance (who granted it).

## Done
- Migrations `src/chatddx/store/migrations/0004-t0-identity.sql` (schema and `person`) and `0005-t1-identity-grants.sql`
  (writer SELECT, INSERT, UPDATE; reader SELECT; no DELETE for anyone but the admin role), tested in
  `src/chatddx/store/test/test_identity.py`. No t2 migration: nothing in `identity` is insert-only.
- Not done: `core/identity.py` (no Python model or `Store` API yet), `active` column.

## Decisions

### 1. People are a small mutable table of their own
Decided: `(id, name)`, mutable, in its own schema, separate from `factor`, `ledger` and the catalog.

Take:
- schema `identity`, table `identity.person (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name text NOT NULL)`;
- the catalog references `identity.person(id)`;
- the writer gets SELECT, INSERT, UPDATE on this table only. It's the one mutable table, so its t2 migration attaches
  no insert-only triggers to it;
- a person who leaves can't be deleted while catalog rows reference them (the foreign key restricts it), which keeps
  the history intact. A later `active` column can hide them in the portal;
- code goes in `src/chatddx/core/identity.py`. `core/` holds only `settings.py` today, and the user's view was that
  the identity model is what belongs in `core`.

### 2. Not sensitive
Decided: only vignettes and data derived from them are sensitive. Names are not, so `chatddx_reader` may read
`identity`.

## Open
- **Roles.** `docs/chatddx.md` ("Roles mentioned") lists clinicians, researchers, developers and ops, a person may hold
  several. Are they only descriptive (who authors what), or do they grant permissions in the portal (only clinicians
  edit expectations, only ops edit engines)? If they grant, `identity.person_role (person, role)` is the obvious
  shape, and it is mutable like `person`.
- **Authentication.** Nothing exists: no portal, no login. Whether people log in through an external provider (the
  orchestrator runs in Kubernetes), and whether `person` stores that provider's subject id, is unspecified.
- **Authorization** beyond roles: per-thread owner and collaborators live in the catalog (`agents/wip-catalog.md`); who
  may edit a thread is then a rule over identity and catalog together. Where that rule lives is open.
- **Naming clash with the database owner**: resolved, the database side is now `DB_ADMIN` / `database(admin=…)`.
