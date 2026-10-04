# Catalog

The catalog is schema `catalog`: append-only, written by `chatddx_writer`, readable by `chatddx_reader`
(`src/chatddx/store/migrations/0009-t0-catalog.sql`, `0010-t1-catalog-grants.sql`, `0011-t2-catalog-checks.sql`).

## Threads and edits
A thread is the stable handle on an edited component. It has a kind (`src/chatddx/core/catalog.py:THREAD_KINDS`)
and a sequence of edits, each pointing at one digest of that kind; its head is the edit with the highest id
(`src/chatddx/store/catalog.py:_HEAD`). A digest may appear in several threads.

`Catalog.behind` looks through cases, which have no threads: a trial's or expectation's
case is behind when one of its appendices has a newer head (`src/chatddx/store/catalog.py:Catalog.behind`).

every kind has threads except `case`, including the ones ops and developers author
(models, engines, expectation schemas, canary sets), so threads are where names attach for everything but cases
(`src/chatddx/core/catalog.py:THREAD_KINDS`, `src/chatddx/store/migrations/0014-t2-catalog-kinds.sql`).

## Recipies
Recipes are skeleton threads. A skeleton edit names the compilation that produced its skeleton, which holds the
recipe (`catalog.edit.compilation`); a hand-written skeleton's edits name none.

Branching is a new thread whose `forked_from` is an edit of the same kind (`catalog.thread.forked_from`).
Threads move only when someone saves an edit; nothing propagates. The portal proposes updates from
`Catalog.behind(thread)`: the head's references, the recipe's included, whose own thread has a newer head
(`src/chatddx/store/catalog.py:Catalog.behind`).

## A note on the "evolution of configurations" workflow"
Configurations and variations are a portal workflow, not a catalog object. A configuration is a named set of pinned
factors; a variation replaces one or more of them. Any digest can be run without a thread, and a variation worth
keeping becomes an edit or a fork.

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

## Labels
Labels for a scorer's views and resources key on (scorer digest, part, position) (`catalog.label`); the portal
copies them to the next scorer edit.

## Cases
Cases have no threads; they are named through a family (`catalog.family`). A family's binding log
(`catalog.binding`) records where its vignette is, as (source, id, vignette fingerprint), and the latest binding is
current. A case belongs to the family with a binding matching its own source, id and fingerprint
(`src/chatddx/store/catalog.py:Catalog.family`). `Catalog.adopt` creates a family for a case, and refuses one that
would give an existing family's vignette new content or a new name; those are repairs. Names, tags and the rest are
entries on the family; the current binding's id, the file name, can root the displayed name.

a repair handles a content change or a file-name change, never both: the unchanged half of the
binding, (source, id) or the fingerprint, identifies the family. It appends a binding, re-binds the appendices as
new edits on their threads, and builds the new cases. When both change, the vignette is a new family. Repair
helpers are not implemented.

A case's language is a `language` entry on its family. It holds a language tag such as `sv` or `pt-BR`; it can
be replaced but not removed, and the latest one wins (`About.language`). It describes the vignette and never
reaches a request.

## Proposed amendments
