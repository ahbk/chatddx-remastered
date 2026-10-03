# Identity

Assigns people, roles, authentication and authorization to identity. Identity is
referenced by the catalog (owners, collaborators, who started a run) and possibly by clearance (who granted it).

Names are not sensitive, so `chatddx_reader` may read
`identity.person` (not credentials or sessions, which are secrets rather than sensitive data).

## People
people are `identity.person (id, login, name, roles, active)`; `id` is what other schemas reference, `login` is
unique and lowercase, `name` is for display. People are never deleted; `active = false` disables them and ends their
sessions (`src/chatddx/store/migrations/0004-t0-identity.sql`, `0006-t0-identity-auth.sql`,
`src/chatddx/store/people.py:People.update`).

## Roles
roles are defined in code (`src/chatddx/core/identity.py:Role`: admin, clinician, developer, ops, researcher)
and stored as strings on the person; tier 2 restricts the column to those names
(`src/chatddx/store/migrations/0008-t2-identity-checks.sql`).

## Authentication
authentication is owned by the app: argon2id password hashes in `identity.credential`, server-side sessions in
`identity.session` holding only a sha256 of the token, lifetime `settings.SESSION_TTL`
(`src/chatddx/core/identity.py`, `src/chatddx/store/people.py`). Neither table is readable by `chatddx_reader`.

## Authorization
Authentication is owned by the app: passwords and sessions in the database. No IdP today. The deployment
will eventually sit behind Keycloak; know it, don't implement it.

authorization is a convenience, not a protection: it decides what is relevant to show. First by role (which
kinds a role sees), later also by the catalog's owner and collaborators. Sensitive data is protected by clearance
and database grants, not by authorization.

the deployment will eventually sit behind Keycloak. `login` follows Keycloak's lowercase usernames so people
carry over; how Keycloak hands the app a login is not decided.
people are managed with `chatddx person add LOGIN NAME [--role ROLE …]` and `chatddx person password LOGIN`
  (`src/chatddx/cli.py`), connecting as `DB_USER`.

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
