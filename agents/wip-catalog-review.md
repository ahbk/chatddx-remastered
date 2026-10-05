# Catalog review (at 1dad5d1)

Working checklist for reconciling the catalog with `docs/catalog.md`; the result goes to `agents/catalog.md`, a
drop-in replacement for `docs/catalog.md`. Each item has an id, a default (the agent's recommendation) and a status:
open, decided (the user said so), held (waits for the language items), done.

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
  same step as the catalog if A1 says so; leave rewriting `docs/identity.md` for its own session. decided: identity moves in step 3.
- P2 facts: no coupling found. Loose end: `agents/wip-facts.md` says the old `tags` went to the catalog, but nothing
  puts model or engine threads (or tags) in the catalog. Default: not blocking. decided: not blocking.

## A: where the catalog lives
- A1 package. Decided: (c), like the ledger. `chatddx.catalog` holds the models and every decision, and imports
  only `chatddx.factors`. `src/chatddx/store/catalog.py:Catalog` stays as a thin layer with the same public
  methods: it reads rows, calls `chatddx.catalog`, and writes them back in one transaction. All SQL stays in
  `store`. `core/identity.py` moves to `chatddx/identity/` in the same step. decided
- A2 "latest wins" (heads, current entries, current bindings, labels). Decided: computed in Python from raw rows;
  `store` returns rows and decides nothing. The catalog is written by hand, so loading the rows is cheap. decided
- A3 boundary rule for the docs: "store answers which rows exist; `chatddx.catalog` decides what they mean." Rules
  that concurrent writers could break still need tier-2 backing. decided: goes in the docs.

## Plan
1. Decide the items below.
2. Fix them in the current layout, in small commits pinned by tests.
3. Refactor to A1 without changing behaviour: the `Catalog` tests stay as they are apart from imports; the pure
   package gets its own tests, which need no Postgres.
4. Write `agents/catalog.md` against the final paths.

## D: the doc says something the code doesn't do
- D1 "every kind has threads except `case`": `compilation` has none either
  (`src/chatddx/store/test/test_catalog.py:747`). decided: doc.
- D2 "nothing looks a thread up by [name]": `Catalog.find` does, and the seed relies on it
  (`src/chatddx/store/catalog.py:196`, `src/chatddx/seed/write.py:90`). decided: keep `find`, doc.
- D3 `Catalog.repair(…, vignette=…)`: the keyword is `fingerprint=` (`src/chatddx/store/catalog.py:509`). decided: doc.
- D4 "`behind` proposes the new case" after a content repair: it reports the family's newer binding; nothing maps an
  old case to its repaired case once `repair` has returned (`Repair.cases` isn't stored). done (4b9cf86).
- D5 `variation` compares top-level fields "for other kinds": in fact whenever either side lacks a compilation,
  hand-written skeletons included (`src/chatddx/store/catalog.py:276`); `proposal` returns `None` when the origin's
  new head has no compilation (`:294-296`). decided: doc.
- D6 `language_of`'s text-bearing chunks "(instructions, few-shot, prompt, output)": the toolset counts too
  (`src/chatddx/factors/request.py:721`). held: language.
- D7 "A run's owner is who started it": nothing writes it; `RunStarted` has no person. A rule for a future runner.
  decided: doc, as a rule for the runner.
- D8 "the portal copies [labels] to the next scorer edit": a requirement on a portal that doesn't exist yet. decided: doc, as a rule for the portal.
- D9 stale pointers: `0014-t2-catalog-kinds.sql` (the list is in `0021`), `docs/chatddx.md:Evolution of
  configurations` (heading is "Intended evolution of configurations"), `docs/factors.md` "Linting" (it's "Lints"),
  `src/chatddx/core/catalog.py:42` names `0019` for the field list (it's `0017`); two stray `"`. decided: doc.
- D10 "the current binding's id … can root the displayed name": titles use the case's own vignette id, so a case
  from before a rename shows the old id; nothing uses the current binding. done (decb023).

## B: code that looks wrong, or gaps (B1-B5 confirmed with a probe)
- B1 titles leave out translations and toolset: an English and a Swedish configuration get identical titles
  (`src/chatddx/core/titles.py:92`). Default: add both. done (6b79f40); a translations chunk's title names its language: done (56ace55).
- B2 engine titles leave out hardware: the 3070 and 5090 engines of one model and runtime collide
  (`src/chatddx/core/titles.py:177`). Default: add the GPU. done (6b79f40), the GPU in parentheses.
- B3 every case of a family has the family's name as its title, whatever its appendices
  (`src/chatddx/store/catalog.py:326-330`). Default: family name plus its appendices' titles. done (decb023).
- B4 an unlabelled thread holding a digest vetoes its language: a plain fork (as the giftbag makes) turns a labelled
  chunk's language into `None` (`src/chatddx/store/catalog.py:634-649`). Languages are per thread, so relabelling a
  thread relabels every digest it ever held. Default: unlabelled threads abstain. Decided: option 4, a component's
  language is kept on its digest (`catalog.language`, `Catalog.language`); a language entry only on a family. done
  (e37bdf5, migrations 0023, 0024).
- B5 `behind` gives one entry per moved thread that ever held the referenced digest, so one path can get several.
  Default: document. decided: doc.
- B6 `language_of`: a trial follows its skeleton, a judge doesn't (it falls to its own row). Since B4, a language
  can be written on any stored digest, but `language_of` ignores it on compiled skeletons, trials, expectations
  and cases, whose language comes from elsewhere. Decided: a judge follows its skeleton; a language is refused
  except on `LANGUAGE_KINDS` (text-bearing chunks, skeletons), and on a skeleton already known to be compiled. done
  (857147a). Residual, for "Open design issues": a language written on a skeleton that is compiled later is
  ignored.
- B7 a skeleton with several compilations: `language_of` and titles use the first by digest. Default: language needs
  all to agree; titles keep the first. held: language.
- B8 `repair(id=…)` doesn't check that the new place is free, so two families can end up bound to one vignette,
  which `adopt` refuses. The tier-2 binding trigger lets a binding change source when the fingerprint stays; Python
  never does (`0018-t2-catalog-bindings.sql:9`). Default: refuse both. done (acc19d7, migration 0022).
- B9 comments that explain what the code does (AGENTS.md "Docs"): `src/chatddx/core/titles.py:1`,
  `src/chatddx/store/catalog.py:195,217,228,354`. Default: remove. decided: step 3.

## T: ambiguities and terms
- T1 "label" means scorer view/resource labels (`catalog.label`) and language entries (`Catalog._label`, "reads …
  from these labels", `docs/factors.md` "language labels"). Default: keep "label" for scorers only. done: `_language`, stray locals renamed, `docs/factors.md` amendment.
- T2 "Recipes are skeleton threads": a recipe's history is kept on a skeleton thread. decided: doc.
- T3 fork vs branch. Default: fork. decided: doc.
- T4 configuration (a skeleton thread, `docs/chatddx.md`) vs variation (a fork of any kind). decided: doc.
- T5 `Variation.head` is the origin's head, not the fork's. Default: rename it. done (f1e397f): `origin_head`.
- T6 `catalog.binding.vignette` holds a fingerprint; `Binding.vignette` is (source, id, fingerprint). Default:
  clarify in the doc, leave the column. Reopened: no deployed data, so the column could be renamed `fingerprint`.
  done (d22a11e, migrations 0025, 0026).
- T7 owner: is a thread's creator its owner? Nothing writes an owner on `create`; the seed does it by hand. Also
  "owner" in the roles' sense (`docs/chatddx.md`, "Roles mentioned"). done (0e56ad2): `Catalog.create(owner=)`, the creator by default.
- T8 any field is accepted on any subject; removal differs per field (name: no value; tag, collaborator:
  `present = false`; deleted: `present = false` restores; description: replaced only; owner, language: replaced
  only). Default: document; restrict nothing. decided: doc. Since B4, a language entry is refused except on a family.
- T9 what "deleted" hides, per method: `find`, `heads`, `behind`, `title_of`, languages skip or rank down deleted
  threads; `forks`, `expectations_of`, `containing`, `history`, `repair` include them. Default: document. decided: doc.
- T10 "the old `extends`" means nothing to a new reader. decided: doc.
- T11 titles aren't unique. Default: say so. decided: doc.

## S: structure of the doc
- S1 no statement of what the catalog is for. decided: doc.
- S2 `behind` is used before it's defined; repairs are described twice. decided: doc.
- S3 undocumented: `history`, `head`, `heads(deleted=)`, `containing`, `bindings`, `note`/`about`, `by`/`at`, and
  that the API folds entries without exposing who changed what. decided: doc.
- S5 found while fixing: a case stored at an old vignette after a repair gets a replacement that nothing stored;
  `behind` names it, and the portal would have to build it. For "Open design issues". decided: doc.
- T12 found while fixing: `adopt` writes no owner for the family it creates, unlike `Catalog.create`. done
  (3be11e3): `Catalog.adopt(owner=)`, the adopter by default.
- S4 no Terms table and no "Open design issues", unlike `docs/factors.md` and `docs/ledger.md`; the known gaps in
  `agents/wip-catalog.md` belong there. decided: doc.
