# Clearance stub

## Vetted endpoints

## Sensitive sources
### Vignettes
A vignette source declares whether it is sensitive (`[source.<name>] sensitive`, true by default.
`src/chatddx/inventory/sources.py:Source.sensitive`.

Nothing enforces it yet: the runner's hard block needs the clearance pipeline (`agents/wip-clearance.md`)."

### Completions derived from vignettes

## Tools
A tool's arguments are written by the model from the case, so a tool that sends them
off the host (a web search) sends case-derived content to a third party, and falls under the hard block like an
engine. The clearance pipeline has to clear tools by their code (`Tool.code`, `entry_point`), with the endpoints
they reach; nothing declares those yet.

## Proposed amendments

- ADD under "Completions derived from vignettes", moved from `docs/ledger.md` ("Policy for case-derived content
  deferred"), which now only declares what is case-derived:
  - Case-derived records are run items and score items (`src/chatddx/ledger/ledger.py:Record.case_derived`):
    completions, the arguments the model passed to tools and what the tools returned, judge responses, and the
    scorer's `detail`.
  - No policy has settled. Options: a database-level read restriction on case-derived tables; a destination check
    when records are exported.
  - Part of it exists: the case-derived tables are in schema `ledger`, and at tier 1 `chatddx_reader` can read
    `factor` but not `ledger` (`src/chatddx/store/migrations/0002-t1-grants.sql`). Who gets that role is open.
