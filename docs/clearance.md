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
