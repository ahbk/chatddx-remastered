# Catalog review (at 1dad5d1)

Working checklist for reconciling the catalog with `docs/catalog.md`; the result goes to `agents/catalog.md`, a
drop-in replacement for `docs/catalog.md`. Each item has an id, a default (the agent's recommendation) and a status:
open, decided (the user said so), done.

## Where the catalog is today
- Models: `src/chatddx/core/catalog.py` (`THREAD_KINDS`, `Entry`, `About`, `Thread`, `Edit`, `Variation`, `Binding`,
  `Behind`, `Survey`, `Repair`, `Subject`).
- Titles: `src/chatddx/core/titles.py` (pure functions over components); tests in `src/chatddx/core/test/`.
- Store access: `src/chatddx/store/catalog.py` (`Catalog`, ~780 lines, mostly SQL); tests in
  `src/chatddx/store/test/test_catalog.py`.
- Schema: migrations `0009`–`0021` in `src/chatddx/store/migrations/`.
- Callers: `src/chatddx/seed/write.py` (sample loader), `src/chatddx/factors/lint.py` via `languages=`
  (`Catalog.language_of`).
- Depends on: `chatddx.factors`, `chatddx.store.store` (`Store`, `Connection`); `identity.person` and
  `ledger.run_stage`/`score_stage` by foreign key only.

## P: before the catalog
- P1 identity: coupled only through the package layout (A1) and owner semantics (T7). Default: move identity in the
  same step as the catalog if A1 says so; leave rewriting `docs/identity.md` for its own session. open
- P2 facts: no coupling found. Loose end: `agents/wip-facts.md` says the old `tags` went to the catalog, but nothing
  puts model or engine threads (or tags) in the catalog. Default: not blocking. open

## A: where the catalog lives
- A1 package. Options: (a) `chatddx/catalog/` holds models, titles and `Catalog`; migrations stay in store;
  (b) as (a), plus `catalog/migrations/` collected by `store/migrate.py`; (c) pure/SQL split like factors/ledger.
  Default: (a), revisit (b) with `docs/store.md`. open

## D: the doc says something the code doesn't do
- D1 "every kind has threads except `case`": `compilation` has none either
  (`src/chatddx/store/test/test_catalog.py:747`). open
- D2 "nothing looks a thread up by [name]": `Catalog.find` does, and the seed relies on it
  (`src/chatddx/store/catalog.py:196`, `src/chatddx/seed/write.py:90`). open
- D3 `Catalog.repair(…, vignette=…)`: the keyword is `fingerprint=` (`src/chatddx/store/catalog.py:509`). open
- D4 "`behind` proposes the new case" after a content repair: it reports the family's newer binding; nothing maps an
  old case to its repaired case once `repair` has returned (`Repair.cases` isn't stored). open
- D5 `variation` compares top-level fields "for other kinds": in fact whenever either side lacks a compilation,
  hand-written skeletons included (`src/chatddx/store/catalog.py:276`); `proposal` returns `None` when the origin's
  new head has no compilation (`:294-296`). open
- D6 `language_of`'s text-bearing chunks "(instructions, few-shot, prompt, output)": the toolset counts too
  (`src/chatddx/factors/request.py:721`). open
- D7 "A run's owner is who started it": nothing writes it; `RunStarted` has no person. A rule for a future runner.
  open
- D8 "the portal copies [labels] to the next scorer edit": a requirement on a portal that doesn't exist yet. open
- D9 stale pointers: `0014-t2-catalog-kinds.sql` (the list is in `0021`), `docs/chatddx.md:Evolution of
  configurations` (heading is "Intended evolution of configurations"), `docs/factors.md` "Linting" (it's "Lints"),
  `src/chatddx/core/catalog.py:42` names `0019` for the field list (it's `0017`); two stray `"`. open
- D10 "the current binding's id … can root the displayed name": titles use the case's own vignette id, so a case
  from before a rename shows the old id; nothing uses the current binding. open

## B: code that looks wrong, or gaps (B1-B5 confirmed with a probe)
- B1 titles leave out translations and toolset: an English and a Swedish configuration get identical titles
  (`src/chatddx/core/titles.py:92`). Default: add both. open
- B2 engine titles leave out hardware: the 3070 and 5090 engines of one model and runtime collide
  (`src/chatddx/core/titles.py:177`). Default: add the GPU. open
- B3 every case of a family has the family's name as its title, whatever its appendices
  (`src/chatddx/store/catalog.py:326-330`). Default: family name plus its appendices' titles. open
- B4 an unlabelled thread holding a digest vetoes its language: a plain fork (as the giftbag makes) turns a labelled
  chunk's language into `None` (`src/chatddx/store/catalog.py:634-649`). Languages are per thread, so relabelling a
  thread relabels every digest it ever held. Default: unlabelled threads abstain. open
- B5 `behind` gives one entry per moved thread that ever held the referenced digest, so one path can get several.
  Default: document. open
- B6 `language_of`: a trial follows its skeleton, a judge doesn't (it falls to its own thread's entry). Language
  entries on compiled skeleton, trial or judge threads are accepted and ignored. Default: judge follows its
  skeleton; document what's ignored. open
- B7 a skeleton with several compilations: `language_of` and titles use the first by digest. Default: language needs
  all to agree; titles keep the first. open
- B8 `repair(id=…)` doesn't check that the new place is free, so two families can end up bound to one vignette,
  which `adopt` refuses. The tier-2 binding trigger lets a binding change source when the fingerprint stays; Python
  never does (`0018-t2-catalog-bindings.sql:9`). Default: refuse both. open
- B9 comments that explain what the code does (AGENTS.md "Docs"): `src/chatddx/core/titles.py:1`,
  `src/chatddx/store/catalog.py:195,217,228,354`. Default: remove. open

## T: ambiguities and terms
- T1 "label" means scorer view/resource labels (`catalog.label`) and language entries (`Catalog._label`, "reads …
  from these labels", `docs/factors.md` "language labels"). Default: keep "label" for scorers only. open
- T2 "Recipes are skeleton threads": a recipe's history is kept on a skeleton thread. open
- T3 fork vs branch. Default: fork. open
- T4 configuration (a skeleton thread, `docs/chatddx.md`) vs variation (a fork of any kind). open
- T5 `Variation.head` is the origin's head, not the fork's. Default: rename it. open
- T6 `catalog.binding.vignette` holds a fingerprint; `Binding.vignette` is (source, id, fingerprint). Default:
  clarify in the doc, leave the column. open
- T7 owner: is a thread's creator its owner? Nothing writes an owner on `create`; the seed does it by hand. Also
  "owner" in the roles' sense (`docs/chatddx.md`, "Roles mentioned"). open
- T8 any field is accepted on any subject; removal differs per field (name: no value; tag, collaborator:
  `present = false`; deleted: `present = false` restores; description: replaced only; owner, language: replaced
  only). Default: document; restrict nothing. open
- T9 what "deleted" hides, per method: `find`, `heads`, `behind`, `title_of`, languages skip or rank down deleted
  threads; `forks`, `expectations_of`, `containing`, `history`, `repair` include them. Default: document. open
- T10 "the old `extends`" means nothing to a new reader. open
- T11 titles aren't unique. Default: say so. open

## S: structure of the doc
- S1 no statement of what the catalog is for. open
- S2 `behind` is used before it's defined; repairs are described twice. open
- S3 undocumented: `history`, `head`, `heads(deleted=)`, `containing`, `bindings`, `note`/`about`, `by`/`at`, and
  that the API folds entries without exposing who changed what. open
- S4 no Terms table and no "Open design issues", unlike `docs/factors.md` and `docs/ledger.md`; the known gaps in
  `agents/wip-catalog.md` belong there. open
