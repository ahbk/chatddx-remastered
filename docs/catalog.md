# Catalog

The catalog is schema `catalog`: append-only, written by `chatddx_writer`, readable by `chatddx_reader`
(`src/chatddx/store/migrations/0009-t0-catalog.sql`, `0010-t1-catalog-grants.sql`, `0011-t2-catalog-checks.sql`).

## Threads and edits
A thread is the stable handle on an edited component. It has a kind (`src/chatddx/core/catalog.py:THREAD_KINDS`)
and a sequence of edits, each pointing at one digest of that kind; its head is the edit with the highest id
(`src/chatddx/store/catalog.py:_HEAD`). A digest may appear in several threads.

`Catalog.behind` looks through cases, which have no threads: a trial's or expectation's
case is behind when one of its appendices has a newer head (`src/chatddx/store/catalog.py:Catalog.behind`).

Every kind has threads except `case`, including the ones ops and developers author
(models, engines, expectation schemas, canary sets), so threads are where names attach for everything but cases
(`src/chatddx/core/catalog.py:THREAD_KINDS`, `src/chatddx/store/migrations/0014-t2-catalog-kinds.sql`).

## Recipes
Recipes are skeleton threads. A skeleton edit names the compilation that produced its skeleton, which holds the
recipe (`catalog.edit.compilation`); a hand-written skeleton's edits name none.

Branching is a new thread whose `forked_from` is an edit of the same kind (`catalog.thread.forked_from`).
Threads move only when someone saves an edit; nothing propagates. The portal proposes updates from
`Catalog.behind(thread)`: the head's references, the recipe's included, whose own thread has a newer head
(`src/chatddx/store/catalog.py:Catalog.behind`).

## A note on the "Evolution of configurations" workflow
Configurations and variations are a portal workflow, not a catalog object. See `docs/chatddx.md:Evolution of configurations`.

Configurations are organized by their lineage (forks and `based_on`), the recipe parts they use,
their entries (tags, owner, collaborators, description) and their runs, not by their names, which are optional.

A variation kept as a fork remembers what it varies:
  - `Catalog.recipe(thread)` reads a skeleton thread's recipe from its head's compilation.
  - `Catalog.variation(thread)` compares a fork with its base: the edit it was forked from, or the origin edit it was
    last re-applied onto (`catalog.edit.based_on`). It returns the recipe parts the fork replaced (`/recipe/…`), or
    for other kinds the top-level fields it changed. It also tells whether the origin has moved on since.
  - When it has, `Catalog.proposal(thread)` offers the origin's head with the fork's own changes on top: a recipe
    to compile for skeleton threads, a component otherwise.
  - Accepting a proposal is an ordinary edit of the fork, with `based_on` naming the origin edit it was re-applied
    onto. Nothing propagates by itself: this is the old `extends`, on request."

Names, descriptions, tags, a language, the owner, collaborators and the deleted flag are entries on a thread, a family, a
run or a score (`catalog.entry`). The latest entry wins per field, per tag and per collaborator, so deleting
is reversible (`src/chatddx/core/catalog.py:Entry`, `About.of`). A run's owner is who started it.

A name is only for people: nothing looks a thread up by it, so none is required, and names needn't be unique.
`Catalog.create(…, name=)` writes the thread, its first edit and its name in one transaction.
A name is removed by a `name` entry without a value, and the title takes over (`src/chatddx/core/catalog.py:Entry`).

## Titles
Anything unnamed is shown by a title, built when asked and never stored
(`src/chatddx/store/catalog.py:Catalog.title`, `Catalog.title_of`, `src/chatddx/core/titles.py`).

`title(thread)` is the thread's name; else, for a fork, its base's title and what it varies (`plan, output:
management-plan-shown`), or `a fork of …` when nothing varies; else what its head holds. `title_of(digest)`
works for any digest, threaded or not, so a run of an unsaved variation can be shown too. It prefers a live
thread whose head the digest is, named first; then a named thread that held it earlier (`plan (earlier)`); then
deleted threads the same way; then what it holds.

A case is shown by its family's name. What a component holds is described per kind: a configuration by its
recipe's parts (`ddx · case · management-plan · recommended · off`), a chunk by its values or the opening words
of its text, an engine by its model and runtime, a trial by its skeleton, engine and counts, and anything else
by its kind and a short digest. Titles change when the names they're built from change, so an export for
publication should freeze the titles it uses.

## Labels
Labels for a scorer's views and resources key on (scorer digest, part, position) (`catalog.label`); the portal
copies them to the next scorer edit.

## Cases
Cases have no threads; they are named through a family (`catalog.family`). A family's binding log
(`catalog.binding`) records where its vignette is, as (source, id, vignette fingerprint), and the latest binding is
current.

A case belongs to the family with a binding matching its own source, id and fingerprint
(`src/chatddx/store/catalog.py:Catalog.family`). `Catalog.adopt` creates a family for a case, and refuses one that
would give an existing family's vignette new content or a new name; those are repairs. Names, tags and the rest are
entries on the family; the current binding's id, the file name, can root the displayed name.

A repair handles a content change or a file-name change, never both: the unchanged half of the
binding, (source, id) or the fingerprint, identifies the family. It appends a binding, re-binds the appendices as
new edits on their threads, and builds the new cases. When both change, the vignette is a new family.

The following helpers assist re-mapping changes in vignette sources:

- `Catalog.survey(source, listing)` compares a source's listing (id → fingerprint, e.g. from `Source.cases()`)
  with the current bindings of that source's families. It sorts them into unchanged, changed (same id, new
  content), renamed (same content under an id the listing has, while the old id is gone), new, and gone. A
  vignette whose name and content both changed shows up as gone plus new.
- `Catalog.repair(family, by, id=…)` or `vignette=…` changes exactly one half of the binding. It appends a
  binding, gives the appendix threads at the old binding a re-bound edit (same text), and builds the family's
  cases anew. A rename also re-keys the expectation threads of those cases, whose data a rename can't affect. A
  content change leaves them, and `behind` proposes the new case for a clinician to confirm.
- `Catalog.behind` reports a case whose family has a newer binding (`Behind.binding` instead of `Behind.head`),
  so cases without appendices are flagged too.
- Consecutive bindings must keep the id or the vignette (tier 2).

Languages are `language` entries: on a family, the language its vignette is written in; on a chunk thread, the
language its texts are written in; on a translations thread, the language it translates into. Each holds a
language tag such as `sv` or `pt-BR`; it can be replaced but not removed, and the latest one wins
(`About.language`). No tag ever reaches a request: what's sent is in a language, it isn't told one.

`Catalog.language_of(digest)` reads any component's language from these labels: a case's from its family, an
expectation's from its case, a trial's from its skeleton, and a compiled skeleton's from its recipe: the
translations' label when it has translations, else the label its text-bearing chunks (instructions, few-shot,
prompt, output) agree on. A hand-written skeleton, a chunk or a translations chunk takes the label of the live
threads that hold it, when they agree. Unlabelled or disagreeing gives none. Lints use it to check that a trial
is in one language (`docs/factors.md`, "Linting").

Vignettes are never translated: a case runs in the language it was written in, so the request follows the case
(a Swedish case runs under a Swedish-translated configuration), and comparing languages means comparing case
sets."

## Proposed amendments
