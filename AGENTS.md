# Agent instructions
This document contains project-wide instructions written by humans for agents. Agents may append proposed amendments and should always report surprises.

## Environment
* `.env-example` is complete and should match your environment as is.
* Human devenv is declared in flake.nix's devShell
* `store` uses PostgreSQL 16:
 - pg_ident.conf: chatddx <os-user> chatddx / chatddx <os-user> chatddx_writer
 - pg_hba.conf:   local all all peer map=chatddx
 - Setup database and roles with `src/chatddx/store/setup.sql`
* Base the fake vLLM on 0.24.0, extend/amend continuously as new facts are discovered, questions arise or sample data is needed.
* `chatddx.core.settings` is the project-wide source of truth.

## Typechecking
* Errors and warnings that indicate issues beyond the scope of the current task should remain, never hide them.

## Testing
* Baseline is `pytest`

## Docs
For docs in (`docs/*`), the same rule applies as to this document: Humans write, agents propose.
* Proposals are always added at the end under a section "Proposed amendments", create the section if it doesn't exist, move it to the end if it appears elsewhere.
* For each proposal write ADD/REMOVE/CHANGE: [instruction], point to source files (e.g. "src/chatddx/core/settings.py:3") or permalinks relevant to the proposal.
* Docs refer to the code, the code speaks for itself. Always remove docstrings or comments that reference docs or explain what the code does before committing.
* Important details that can't be learned by reading the code+tests may deserve a comment iff it is relevant to exactly one line or section in the code, otherwise it should be added as a proposed amendment referring to the parts of the code affected by it.
* If a section of code is related to another section of code elsewhere such that reading both make them easier to understand, a path in a comment can act as a link for agents and humans alike. A comment linking two or more pieces of code this way with a short explanation is allowed.
* `agents/*` are exempt from the "Humans write, agents propose` rule and agents are free to use it for whatever purpose they deem fit.

## Misc
* For placeholder usernames in docs and tests, use alice, bob, carol... or semantic names (archive, guest, nobody, other, collaborator-one, admin).

## Proposed amendments
* ADD to "Environment": in a fresh cloud container PostgreSQL 16 is installed but stopped, and `pg_hba.conf` /
  `pg_ident.conf` lack the `chatddx` map. Setup: add `map=chatddx` to the `local all all peer` line, append
  `chatddx root chatddx` and `chatddx root chatddx_writer` to `pg_ident.conf`, `service postgresql start`, then
  `su postgres -c "psql -f src/chatddx/store/setup.sql"` and `chatddx migrate` (`src/chatddx/store/setup.sql`).
