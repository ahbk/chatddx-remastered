# Identity stub

## Proposed amendments
- ADD: people are `identity.person (id, login, name, roles, active)`; `id` is what other schemas reference, `login` is
  unique and lowercase, `name` is for display. People are never deleted; `active = false` disables them and ends their
  sessions (`src/chatddx/store/migrations/0004-t0-identity.sql`, `0006-t0-identity-auth.sql`,
  `src/chatddx/store/people.py:People.update`).
- ADD: roles are defined in code (`src/chatddx/core/identity.py:Role`: admin, clinician, developer, ops, researcher)
  and stored as strings on the person; tier 2 restricts the column to those names
  (`src/chatddx/store/migrations/0008-t2-identity-checks.sql`).
- ADD: authentication is owned by the app: argon2id password hashes in `identity.credential`, server-side sessions in
  `identity.session` holding only a sha256 of the token, lifetime `settings.SESSION_TTL`
  (`src/chatddx/core/identity.py`, `src/chatddx/store/people.py`). Neither table is readable by `chatddx_reader`.
- ADD: authorization is a convenience, not a protection: it decides what is relevant to show. First by role (which
  kinds a role sees), later also by the catalog's owner and collaborators. Sensitive data is protected by clearance
  and database grants, not by authorization.
- ADD: the deployment will eventually sit behind Keycloak. `login` follows Keycloak's lowercase usernames so people
  carry over; how Keycloak hands the app a login is not decided.
- ADD: people are managed with `chatddx person add LOGIN NAME [--role ROLE …]` and `chatddx person password LOGIN`
  (`src/chatddx/cli.py`), connecting as `DB_USER`.
