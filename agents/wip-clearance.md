# Clearance: work in progress

Material for `docs/clearance.md`. Split out of the former `agents/wip-bookkeeping.md`; checked against d10c96d.
"Decided" means the user said so; "Take" is the agent's recommendation and still open.

## Scope
Decided: clearance tracks sensitive sources, sensitive derived content and vetted endpoints, both engines and
databases. It is enforcement, not curation: sending case-derived content to an engine that isn't cleared, judge
engines included, is the system's one hard block (`docs/chatddx.md`, "Findings and errors"). The factors and the
ledger provide the means; the runner enforces.

## What the code offers
- `Record.case_derived` (`src/chatddx/ledger/ledger.py:45`) marks which logs hold derived content: run and score rows
  are case-derived, compilations are not (`:306`).
- Case-derived tables live in schema `ledger`; at tier 1 `chatddx_reader` can't read them
  (`src/chatddx/store/migrations/0002-t1-grants.sql`).
- `SourceCase.source` (`src/chatddx/factors/cases.py:12`) names a vignette's source.
- Sources are declared in the inventory (`src/chatddx/inventory/inventory.py`), and each one says whether it is
  sensitive (`Source.sensitive`, true by default). `prepare_case` produces the case-derived fills the hard block
  would guard.
- `RemoteEngine.base_url` (`src/chatddx/factors/engine.py:76`) is the only endpoint in a factor, and it is part of the
  engine's digest. A local engine has no URL; by the glossary that is an inventory fact (`docs/factors.md`, "Local
  engines have no endpoint…").
- `Call` (`src/chatddx/ledger/ledger.py`) records no URL, so the ledger can't show which endpoint received
  case-derived content.
- The old module had `EndpointBinding(engine, base_url, clearances)` and `CaseSourcePolicy(source,
  required_clearances)`, with a check that every endpoint reached by case-derived content held the clearances its
  sources require (`git show de843eb:src/chatddx/core/manifest.py`, around L1913 and L2258). That shape still fits:
  sources require clearances, endpoints hold them, and the runner refuses when one is missing.

## Takes
- Databases count as endpoints, because the ledger *is* case-derived storage. The Postgres instance holding `ledger`
  has to be vetted like an engine, and an export target (records leaving for publication) is another endpoint.
- Storage: schema `clearance`, append-only like the catalog (SELECT, INSERT for the writer; t2 insert-only triggers).
  Granting clearance is the action the hard block depends on, so possibly only a separate role
  (`chatddx_clearance`) may write it. That is the one place a new database role seems justified.
- Record the endpoint on each `Call` (or once per run in `RunStarted`, if one run talks to one endpoint per engine), so
  the clearance check can be audited from the ledger.

## Open
1. **What a grant keys on.** This blocks the schema. Endpoints now live in the inventory: ops-authored TOML, mutable,
   no ids, and no code yet. Candidates:
   - the URL (simple, but a local host serves different engines over time, and one engine may have several hosts);
   - an inventory entry name (stable for humans, but the inventory isn't in the database, so no foreign key);
   - the engine digest plus URL (what the old `EndpointBinding` did).
   Related: whether `RemoteEngine.base_url` stays in the digest or moves to the inventory, so that local and remote
   engines are bound the same way.
2. **Where vetting is declared.** In the inventory (ops write it alongside the endpoint) or in the database (granted
   through the portal and logged). The second gives an audit trail and fits the append-only store; the first keeps
   all location facts in one place.
3. **Who may grant clearance**, and whether that needs its own role.
4. **Binding check before sending.** A local engine's `served_model_name` is its digest, and `/v1/models` lists it,
   so the runner can confirm an inventory binding before sending case-derived content. Whether that check is part of
   the hard block is undecided.
5. **Sources.** Sources are now listed in the inventory with a `sensitive` flag (G11 in
   `agents/wip-sample-data.md`). Still unspecified: what clearance each requires, beyond sensitive or not.

## Tools (G8)
- A tool's arguments are written by the model from the case. A tool that sends them off the host (the old
  `web_search` goes to DuckDuckGo) sends case-derived content to a third party, so the hard block covers tools as
  it covers engines and judges.
- Tools are pinned by code (`Tool.code`, `entry_point`), so the clearance pipeline can clear a tool by its code.
  Nothing declares which endpoints a tool reaches; that belongs to clearance, not to the factor.
- Tool results come back from the third party and are stored in the ledger (`ToolRun.result`), next to the
  responses.

