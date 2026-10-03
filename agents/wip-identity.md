# Identity: work in progress

Material for `docs/identity.md`. Split out of the former `agents/wip-bookkeeping.md`; checked against d10c96d.
"Decided" means the user said so; "Take" is the agent's recommendation and still open.

## Scope
`docs/factors.md` ("Not factors") assigns people, roles, authentication and authorization to identity. Identity is
referenced by the catalog (owners, collaborators, who started a run) and possibly by clearance (who granted it).

## Done
- Migrations `0004-t0-identity.sql`, `0005-t1-identity-grants.sql` (person, grants), `0006-t0-identity-auth.sql`,
  `0007-t1-identity-auth-grants.sql` (login, roles, active, credential, session) and `0008-t2-identity-checks.sql`
  (login pattern, role names, session expiry), all in `src/chatddx/store/migrations/`.
- `src/chatddx/core/identity.py`: `Role`, `Person`, argon2id hashing (`argon2-cffi`), session tokens.
- `src/chatddx/store/people.py`: `People`, the store API for people, passwords and sessions.
- `chatddx person add` / `chatddx person password` (`src/chatddx/cli.py`).
- Tests: `src/chatddx/store/test/test_identity.py`, `test_cli.py`.
- `DB_OWNER` renamed to `DB_ADMIN`, freeing "owner" for the catalog.

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
`identity.person` (not credentials or sessions, which are secrets rather than sensitive data).

### 3. Authentication, roles and authorization
Decided:
- Authentication is owned by the app (option B): passwords and sessions in the database. No IdP today. The deployment
  will eventually sit behind Keycloak; know it, don't implement it.
- Roles live in code and are stored as strings on the person (R1).
- Authorization is a convenience that decides what is relevant to show, not a protection: by role first (Z1), then
  also by catalog owner and collaborators (Z2).

Take, for when Keycloak arrives: Keycloak (or a proxy in front, such as oauth2-proxy) authenticates and the app maps
its username onto `person.login`, which is why logins are lowercase like Keycloak's. Password login can stay as the
development path. Roles could then come from Keycloak's realm roles instead of the column; not decided.

## Open
- **Web framework.** No portal exists, so nothing sets or reads a session cookie yet. Cookie flags (`HttpOnly`,
  `Secure`, `SameSite`), CSRF protection and login rate limiting belong to that layer.
- **Z1's map from role to kinds** isn't written; it needs the portal's views to mean anything.
- **Who may administer people.** `Role.ADMIN` exists, but nothing checks it; the CLI connects as `DB_USER`, so anyone
  with database access can add people.
- **Expired sessions** are only removed by `People.purge_sessions()`; nothing calls it on a schedule.
