# Catalog stub

## Proposed amendments
- ADD: The catalog is schema `catalog`: append-only, written by `chatddx_writer`, readable by `chatddx_reader`
  (`src/chatddx/store/migrations/0009-t0-catalog.sql`, `0010-t1-catalog-grants.sql`, `0011-t2-catalog-checks.sql`).
- ADD: A thread is the stable handle on an edited component. It has a kind (`src/chatddx/core/catalog.py:THREAD_KINDS`)
  and a sequence of edits, each pointing at one digest of that kind; its head is the edit with the highest id
  (`src/chatddx/store/catalog.py:_HEAD`). A digest may appear in several threads.
- ADD: Recipes are skeleton threads. A skeleton edit names the compilation that produced its skeleton, which holds the
  recipe (`catalog.edit.compilation`); a hand-written skeleton's edits name none.
- ADD: Branching is a new thread whose `forked_from` is an edit of the same kind (`catalog.thread.forked_from`).
- ADD: Threads move only when someone saves an edit; nothing propagates. The portal proposes updates from
  `Catalog.behind(thread)`: the head's references, the recipe's included, whose own thread has a newer head
  (`src/chatddx/store/catalog.py:Catalog.behind`).
- ADD: Configurations and variations are a portal workflow, not a catalog object. A configuration is a set of pinned
  factors; a variation replaces one or more of them. Any digest can be run without a thread, and a variation worth
  keeping becomes an edit or a fork.
- ADD: Names, descriptions, tags, the owner, collaborators and the deleted flag are entries on a thread, a run or a score
  (`catalog.entry`). The latest entry wins per field, per tag and per collaborator, so deleting is reversible
  (`src/chatddx/core/catalog.py:Entry`, `About.of`). A run's owner is who started it.
- ADD: Labels for a scorer's views and resources key on (scorer digest, part, position) (`catalog.label`); the portal
  copies them to the next scorer edit.
- ADD: Cases have no threads (`THREAD_KINDS`); how cases are named is not settled.
