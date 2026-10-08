# Clearance stub

## Vetted endpoints
Until clearance has a database grant, a World inventory's `[cleared]` table says which sensitive sources each
endpoint may receive case-derived content from. It is read from the inventory's own file, never an included one, so
importing endpoints again clears nothing. Before a run writes anything, the runner refuses an endpoint serving
another engine than the trial's, a sensitive source the endpoint isn't cleared for, tools on a sensitive source,
and an endpoint whose `/v1/models` doesn't list its engine.

## Sensitive sources
### Vignettes
A vignette source declares whether it is sensitive (`[source.<name>] sensitive`, true by default.
`src/chatddx/inventory/sources.py:Source.sensitive`.

The runner enforces it against the inventory's `[cleared]` table (see "Vetted endpoints") until the clearance
pipeline replaces the table.

### Completions derived from vignettes
- Case-derived records are run items and score items (`src/chatddx/ledger/record.py:Record.case_derived`):
  completions, the arguments the model passed to tools and what the tools returned, judge responses, and the
  scorer's `detail`.
- No policy has settled. Options: a database-level read restriction on case-derived tables; a destination check
  when records are exported.
- Part of it exists: the case-derived tables are in schema `ledger`, and at tier 1 `chatddx_reader` can read
  `factor` but not `ledger`. Who gets that role is open.

## Tools
A tool's arguments are written by the model from the case, so a tool that sends them
off the host (a web search) sends case-derived content to a third party, and falls under the hard block like an
engine. The clearance pipeline has to clear tools by their code (`Tool.code`, `entry_point`), with the endpoints
they reach; nothing declares those yet.

## Proposed amendments
