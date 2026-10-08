# Catalog

Components and records never change. Editing a prompt makes a new component with a new digest, and a run's rows are
only ever added to. That is what makes the rig reproducible, but it leaves people nothing to hold on to: a digest is
not a name, and nothing says that one prompt is the next version of another.

The catalog gives people that handle. It names components, families of cases, runs and scores; it keeps the versions
of a component in order; and it remembers who owns what, what was deleted, which language a text is written in, and
what a scorer's views are called. None of it changes an output or a score. No component refers to the catalog, so no
digest depends on it (`docs/factors.md`, "Not factors").

The catalog refers to components by digest, to runs and scores by id, and to people by `identity.person(id)`
(`docs/identity.md`). It holds nothing produced from a vignette, which is the only sensitive data, so
`chatddx_reader` may read all of it.

Code paths are relative to `src/chatddx/catalog/` unless they start with `src/` or `docs/`. The package imports no
other chatddx package than `chatddx.factors`, and holds no SQL. `chatddx.catalog` exports the public names.

## How the catalog is built

### Append-only, and the latest row wins
Nothing in the catalog is updated or deleted. Every change is a new row. For anything that can change (a thread's
current version, a name, a binding, a language, a label), the latest row is what holds now and the earlier rows are
its history. "Latest" means written last, in the order the database numbered the rows; the time a row records (`at`)
is only for display. The rule is written once, in `model.py:latest` and `model.py:About.of`.

Deleting is a row too: a `deleted` entry, which a later entry can undo (see "Deleting"). True deletion is refused by
the database at tier 2 (`docs/store.md`); a garbage collector for unreferenced items is "On the table" in
`docs/chatddx.md`.

### The package and the store
The rules live in this package, and the store reads and writes the rows:
- `read.py:Reader` lists what the rules need to read: components, threads, edits, entries, bindings, languages and
  labels, as rows in the order they were written. A `Reader` never picks the latest row or decides anything.
- `src/chatddx/store/catalog.py:Rows` answers `Reader` from the tables. `src/chatddx/store/catalog.py:Catalog` is what
  callers use: each of its methods reads through `Rows`, calls this package, and writes what was decided in one
  transaction.
- `test/memory.py:Memory` answers `Reader` from memory, so the rules are tested without a database (`test/`). The
  store's tests cover the same behaviour through Postgres (`src/chatddx/store/test/test_catalog.py`).

The dividing line: the store answers which rows exist, and this package decides what they mean. A rule that two
writers at once could break also needs the database to enforce it, at tier 2 (see "Open design issues").

The modules:
- `model.py`: the types, the lists of kinds and fields, and "latest wins";
- `read.py`: `Reader`, and helpers that read and pick the latest in one step (a thread's head, what a subject's
  entries say, a scorer's labels);
- `threads.py`: threads, edits, forks, variations, proposals and what has moved on;
- `families.py`: families of cases and their bindings: adopting, surveying and repairing;
- `language.py`: languages;
- `titles.py`: titles.

## Threads and edits
A *thread* is the handle on a component that people edit: the versions of one prompt, one trial, one engine. It has
a kind, and a sequence of *edits*, each pointing at one stored component of that kind (`model.py:Thread`,
`model.py:Edit`). The latest edit is the thread's *head*, its current version; the others are its history, in order.

Every kind of component has threads except two (`model.py:THREAD_KINDS`):
- `case`: cases are named through families instead (see "Families and repairs");
- `compilation`: a compilation records how a skeleton was made, and nobody edits it. Skeleton edits point at it (see
  "Recipes").

That includes the kinds ops and developers write, such as models, engines, expectation schemas and canary sets. They
are rarely edited, but threads are where names attach for everything except cases.

The same digest may appear in several threads, and more than once in one: reverting is an edit that points back at
an earlier version.

Writing:
- `Catalog.create(digest, by, compilation=, forked_from=, name=, owner=)` writes a thread, its first edit and its
  owner entry, plus its name when one is given, in one transaction. The owner is whoever creates the thread, unless
  `owner` names someone else. It refuses a digest that isn't stored (`LookupError`) and a kind without threads
  (`ValueError`, `threads.py:check_threadable`).
- `Catalog.edit(thread, digest, by, compilation=, based_on=)` adds an edit, so the thread moves to `digest`. The
  database refuses a digest that isn't stored or isn't of the thread's kind.

Reading (`threads.py`):
- `Catalog.thread(id)`, `Catalog.history(thread)` and `Catalog.head(thread)`. They raise `LookupError` for a thread
  that doesn't exist.
- `Catalog.heads(kind, deleted=False)`: the heads of the live threads of a kind, or of the deleted ones.
- `Catalog.find(kind, name, owner=)`: the live threads of a kind whose current name is `name`, owned by `owner` if
  given. Names aren't unique, so it may return several.
- `Catalog.forks(thread)`: the threads forked from any edit of a thread.
- `Catalog.expectations_of(case)`: the expectation threads whose head expects a case.
- `Catalog.containing(digest)`: every edit, in any thread, that points at a digest.

A thread moves only when someone saves an edit, and nothing moves along with it. When a prompt's thread moves on, the
skeletons, trials and scorings built on the old prompt stay as they are; `behind` tells which of them could be
updated (see "What has moved on").

## Configurations and variations
Researchers refine their configurations by trying variations, keeping what works, and forking new configurations
from it (`docs/chatddx.md`, "Intended evolution of configurations"). The catalog supports that workflow without having
objects of its own for it.

A *configuration* is a skeleton thread. A *variation* is a fork, of any kind, together with what it changes from the
edit it came from. Trying a variation costs nothing in the catalog, since any digest can be run without a thread. A
variation worth keeping becomes an edit of its thread, or a fork. Configurations are organised by their lineage
(forks and `based_on`), the recipe parts they use, their entries and their runs, not by their names, which are
optional.

### Recipes
A recipe has no thread of its own; its history is kept on the skeleton thread it compiles to. Each edit of a skeleton
thread can name the compilation that produced its skeleton (`Edit.compilation`), and the compilation holds the recipe
(`docs/factors.md`, "Compilation"). The database checks that the compilation is one of that very skeleton, and that
only skeleton edits name one. A hand-written skeleton has no compilation, so its edits name none.
`Catalog.recipe(thread)` reads the recipe of a thread's head.

### Forks
A *fork* is a new thread started from an edit of another thread of the same kind (`Thread.forked_from`). The thread
it came from is its *origin*.

`Catalog.variation(thread)` (`threads.py:variation`) compares a fork with its *base*: the edit it was forked from, or
the origin edit it was last re-applied onto (`Edit.based_on`, below). It returns `model.py:Variation`:
- `varies`: what the fork's head changes from the base. When both have a compilation, that is the recipe parts the
  fork replaced (`/recipe/output`, …). Otherwise, as for a chunk or a hand-written skeleton, it is the component's
  top-level fields that differ (`/max_output_tokens`, …);
- `origin_head`: the origin's head now, and `moved`: whether it has moved on from the base.

It returns `None` for a thread that isn't a fork.

When the origin has moved on, `Catalog.proposal(thread)` offers the origin's head with the fork's changes on top: a
recipe to compile when the fork is compared by recipe, a component otherwise. It gives `None` when nothing has moved,
and when the fork is compared by recipe but the origin's new head has no compilation. Nothing is stored.

Accepting a proposal is an ordinary edit of the fork, with `based_on` naming the origin edit it was re-applied onto.
`based_on` must be an edit of the origin at or after the one the fork came from (`threads.py:check_based_on`, and at
tier 2). From then on, `variation` compares the fork with that edit. The old chatddx let a configuration extend
another and follow it automatically; here a fork follows its origin only when someone accepts a proposal.

### What has moved on
`Catalog.behind(thread)` (`threads.py:behind`) lists the references of a thread's head that could be updated. It
looks at:
- the head's own references, such as a trial's skeleton, engine and cases;
- for a skeleton thread, the references of its recipe (`/recipe/instructions`, …);
- the appendices of each case the head references (`/cases/0/appendices/0`, or `/case/appendices/0` for an
  expectation), since cases have no threads of their own.

A reference is behind when a live thread that held its digest has since moved on to another. Its entry
(`model.py:Behind`) has the path, the digest and that thread's head. A digest can be held by several threads, so one
path gets an entry for each thread that moved on: for example, when a chunk was forked and both threads moved on.
Deleted threads aren't proposed.

A case is also behind when its family has a newer binding (see "Families and repairs"). That entry has the binding
instead of a head, and the case that replaces the old one (`Behind.replacement`): the same appendix texts, bound to
the family's current vignette. Cases without appendices are flagged this way too.

`behind` goes one level at a time. A chunk edit makes its skeleton threads behind; saving one of those makes its
trial threads behind. A fork makes nothing behind until it moves on.

## Entries
Names, and everything else people say about a subject, are *entries* (`model.py:Entry`, table `catalog.entry`). The
subject of an entry is exactly one thread, family, run or score (`model.py:Subject`). An entry on a run or a score
points at its started row, so a run has to have started before anything can be said about it.

`Catalog.note(subject, entry, by)` writes an entry, and `Catalog.about(subject)` reads what a subject's entries say
now (`model.py:About`). The latest entry wins for each field, and for tags and collaborators, for each tag and each
collaborator. A subject without entries gives an empty `About`.

| Field | Holds | Removed by | Read by the code |
| --- | --- | --- | --- |
| `name` | a text, not empty | an entry without a value; the title takes over | titles, `find` |
| `description` | a text | nothing: it can only be replaced | no |
| `tag` | one tag, not empty; a subject has a set of them | an entry with `present` false | no |
| `owner` | a person | nothing: it can only be replaced | `find` |
| `collaborator` | a person; a subject has a set of them | an entry with `present` false | no |
| `deleted` | a flag, set by the entry | an entry with `present` false, which restores the subject | see "Deleting" |
| `language` | a language tag, only on a family | nothing: it can only be replaced | see "Languages" |

Every field except `language` is accepted on every subject. The code reads some of them; the rest are for people and
the portal.

### Names
A name is for people. None is required, and names needn't be unique: a thread without one is shown by its title (see
"Titles"). Names can still be used to look threads up. `Catalog.find` does, and the sample loader uses it to match its
records on a re-run, by kind, name and owner (`src/chatddx/seed/write.py`).

### Owners
`Catalog.create` and `Catalog.adopt` record whoever creates the thread or family as its owner, unless they name
someone else. This is the catalog's owner, which authorization may later use to decide what to show
(`docs/identity.md`). It is a different thing from the roles' sense of owning, as in "clinicians own the vignettes"
(`docs/chatddx.md`, "Roles mentioned").

A run's owner should be whoever started it. That is a rule for the runner, which doesn't exist yet: nothing writes
it, and a run's started row records no person (`docs/ledger.md`, "RunStarted").

Every row in the catalog records who wrote it and when (`by`, `at`), but `About` keeps only what holds now: the API
doesn't show who changed what.

### Deleting
A deleted thread or family is still there, and so are its digests. What deleting hides depends on the method:
- skipped: `heads` (unless asked for deleted threads), `find`, and `behind`, which proposes no deleted thread and
  flags no case of a deleted family;
- ranked last: `title_of` turns to deleted threads only when the live ones give it nothing to show;
- included: `history`, `forks`, `expectations_of`, `containing`, `survey`, and `repair`, which rebinds deleted
  threads too.
- `Catalog.involving(person)` gives the subjects whose entries have ever named a person as owner or collaborator,
  for `about` to tell what holds now. `chatddx wipe-data` deletes those the person owns and unshares those they
  collaborate on, writing an entry only where it changes something, and `init-data --giftbag` restores a deleted
  fork rather than forking anew.

What the portal hides is open (see "Open design issues").

## Titles
Anything without a name is shown by a *title*, built when asked and never stored (`titles.py`).

`Catalog.title(thread)` is the thread's name. Without one, a fork is shown by its base's title and what it varies
(`plan, output: management-plan-shown`), or as `a fork of plan` when it varies nothing; any other thread is shown by
what its head holds.

`Catalog.title_of(digest)` works for any digest, in a thread or not, so a run of a variation nobody saved can be shown
too. It prefers, in order:
1. a live thread whose head is the digest: its name, or else its title;
2. a live thread with a name that held the digest earlier: `plan (earlier)`;
3. the same two among deleted threads;
4. what the digest holds.

A case is shown by its family's name, or without one by the family's current place (`registry/c1`), followed by its
appendices: `chest pain with "Troponin 80 ng/L."`. A case from before a repair says so:
`chest pain (earlier) with …`. A case without a family is shown by its own source and id.

What a component holds is described according to its kind (`titles.py:describe`):
- a configuration by its recipe's parts, translations and toolset included:
  `ddx · case · management-plan · recommended · off`. A judge's recipe starts with `judge:`, and a skeleton
  without a compilation is shown as `generation skeleton 3f9a1c`;
- a chunk by its values or the opening words of its text. A translations chunk is shown by its language, when it has
  one, and its size: `sv translations, 12 texts`;
- a local engine by its model, server and GPU: `google/gemma-3-12b-it on vllm 0.24.0 (RTX 5090)`; a remote engine by
  its model and host;
- a trial by its skeleton, its engine and how many cases and seeds it has; an expectation by its case:
  `expectation for chest pain with …`;
- anything else by its kind and the start of its digest.

A thread's title uses the compilation its head names; `title_of` of a skeleton with several compilations uses the
first by digest.

Titles aren't unique: two unnamed components can be described alike, and names aren't unique either. Titles change
when the names they're built from change, so an export for publication should freeze the titles it uses.

## Scorer labels
A scorer refers to its views and resources by position (`docs/factors.md`, "Scorer"). Their *labels*
(`catalog.label`) are keyed by the scorer's digest, the part (`view` or `resource`) and the position, and the latest
label wins for each (`Catalog.label`, `Catalog.labels`). The position is checked against the scorer, in Python
(`model.py:check_label`) and at tier 2.

A position only means something within one scorer, so a new scorer edit starts without labels. Copying them over is
the portal's job, since it knows how the positions moved; no portal exists yet.

"Label" means only this. A text's language is not called a label (see "Languages").

## Families and repairs
A case is a component: a vignette plus its appendices (`docs/factors.md`, "Case"). It has no thread, because changing
its appendices makes another case, not a new version of the same one. The vignette can also change at its source,
under the same file name or another. A *family* is the handle that follows one vignette through all of that. Names,
tags and the rest are entries on the family, and every case of the vignette belongs to it.

A family's *bindings* record where its vignette is (`catalog.binding`, `model.py:Binding`): the source, the
vignette's id there (for a directory, the file name) and the fingerprint of its content. The latest binding is the
current one (`Catalog.bindings(family)`). In the table, the column `fingerprint` holds the fingerprint alone; in
Python, `Binding.vignette` holds all three.

A case belongs to the family with a binding, current or earlier, that matches its vignette exactly, and to the latest
such family when several match (`Catalog.family`, `families.py:family_of`). So cases from before a repair keep their
family.

`Catalog.adopt(case, by, owner=)` returns the case's family, or creates one, binds it to the case's vignette and
records its owner. It refuses a case whose vignette would give another family's current binding new content (the
same id) or a new name (the same content): that calls for a repair, not a new family
(`families.py:check_adoptable`).

### Noticing changes
`Catalog.survey(source, listing)` compares a source's listing (id → fingerprint, which can be built from the cases a
source lists, `src/chatddx/inventory/sources.py:Source.cases`) with the current bindings of that source's families.
It sorts them into:
- unchanged;
- changed: the same id with new content;
- renamed: the same content under an id the listing has, while the old id is gone;
- new, and gone. A vignette whose name and content both changed shows up as gone plus new.

A run notices a changed vignette too, as `case.drift` (`docs/factors.md`, "Preparing a case").

### Repairing
`Catalog.repair(family, by, id=…)` or `fingerprint=…` (`families.py:plan_repair`) moves a family to its vignette's
new place. It changes exactly one half of the binding, the id or the fingerprint, and keeps the source; the half that
stays the same is what identifies the family. It refuses a place that another family already holds, as `adopt` does.
Then, in one transaction, it:
1. appends the new binding;
2. builds every stored case of the old binding anew: the same appendix texts, bound to the new vignette;
3. gives each appendix thread whose head belongs to the old binding an edit with its rebound appendix;
4. for a rename only, gives each expectation thread of those cases an edit that expects the new case. A rename can't
   change the right answer, but new content can, so after a content change the expectations stay as they are, and
   `behind` reports their case with its replacement for a clinician to confirm.

It returns what it wrote: the binding, the old and new cases and appendices, and the edits (`model.py:Repair`).

When both the name and the content changed, nothing identifies the family, so there is no repair: the vignette
becomes a new family through `adopt`, and its names are carried over by hand. At tier 2, consecutive bindings of a
family must keep the source and either the id or the fingerprint.

## Languages
The rig never tells a model which language a request is in; what is sent is simply in a language. The catalog records
which one, so that lints can check that a trial doesn't mix languages (`docs/factors.md`, "Lints").

A language is a tag such as `sv` or `pt-BR` (`model.py:LANGUAGE`). It can be replaced but not removed, and the latest
wins. It is kept in one of two places:
- A vignette's language is a `language` entry on its family. A `language` entry on anything else is refused
  (`language.py:check_entry`, and at tier 2).
- A component's language is kept on its digest (`catalog.language`, `Catalog.language(digest, value, by)`), so it
  belongs to the text itself: forking a chunk keeps it, and changing one digest's language touches no other. A new
  edit's digest has no language until someone gives it one; the portal can offer the previous head's.

Only the kinds whose language is read from their own digest can have one (`model.py:LANGUAGE_KINDS`): the chunks that
hold text sent to a model (instructions, few-shot, prompt, output, toolset, and translations, whose language is the
one they translate into) and skeletons that aren't compiled. Any other kind is refused, and so is a skeleton already
known to have a compilation, since its language comes from its recipe (`language.py:check_language`, and at tier 2).

`Catalog.language_of(digest)` (`language.py:language_of`) works out the language of any component:
- a case's is its family's, and an expectation's is its case's;
- a trial's and a judge's are their skeleton's;
- a compiled skeleton's comes from its recipes, which vote. A recipe with translations votes for the translations'
  language. A recipe without translations votes for the language that all its text-bearing chunks share, toolset
  included and appendix layout not. A recipe whose chunks lack a language or disagree doesn't vote. When the votes
  disagree, the skeleton has no language;
- a chunk's and a hand-written skeleton's are their own.

An unknown or disagreeing language gives none.

Vignettes are never translated: a case runs in the language it was written in. So the request follows the case (a
Swedish case runs under a Swedish-translated configuration), and comparing languages means comparing case sets.

## Storage
The catalog is the schema `catalog` (`docs/store.md`), with the tables `thread`, `edit`, `entry`, `label`, `family`,
`binding` and `language`. It is written by `chatddx_writer` and readable by `chatddx_reader`. Composite foreign keys
keep an edit on its thread's kind and on a stored component of that kind, a fork on an edit of the same kind, an
edit's compilation on one of that very skeleton, and a language on a stored component of the kind it records. At
tier 2, every table refuses updates and deletes, and checks repeat the rules above. The lists of thread kinds, entry
fields and language kinds are written both in `model.py` and in the migrations, and a test checks that they agree
(`src/chatddx/store/test/test_catalog.py`).

The catalog's migrations are listed under "catalog" in `docs/migrations.md`": the migrations are squashed into one
per schema and tier, and the list there stays current as migrations are added.

## Terms

| Term | Meaning |
| --- | --- |
| Catalog | People's handles on components and records: names, versions, owners, deletions, languages and labels. Nothing in it changes an output. |
| Thread | The versions of one component that people edit, in order. |
| Edit | One version on a thread: a digest. |
| Head | A thread's latest edit, its current version. |
| Fork | A thread started from an edit of another thread of the same kind, its origin. |
| Base | The edit a fork is compared with: the one it came from, or the one it was last re-applied onto. |
| Configuration | A skeleton thread. |
| Variation | A fork, and what it changes from its base. |
| Proposal | The origin's head with a fork's changes on top. |
| Behind | A reference whose thread has moved on, or a case whose family has a newer binding. |
| Entry | Something said about a thread, a family, a run or a score: a name, description, tag, owner, collaborator, deleted flag or language. |
| Title | How something without a name is shown, built from what it holds. |
| Label | The name of a scorer's view or resource. |
| Family | The handle on one vignette through renames and edits at its source. |
| Binding | Where a family's vignette is: source, id and fingerprint. The latest is current. |
| Repair | Moving a family to its vignette's new id or content, and rebinding what depends on it. |
| Replacement | The case a stale case becomes under its family's current binding. |
| Latest wins | Of the rows about one thing, the one written last is what holds. |

## Open design issues

### Concurrent writers
The rules are checked in Python, on rows read inside the transaction that writes. That holds with one writer at a
time; with two, only what tier 2 enforces is sure to hold. For example, two `adopt` calls for one vignette at the same
moment can create two families, since nothing makes current bindings unique.

### Smaller issues
- **Identical vignettes.** Two vignettes with the same content under different ids in one source look like a rename
  to `adopt`, which refuses the second.
- **Several matching families.** When bindings of several families match a case, `family_of` takes the latest.
- **Current bindings are read per source.** Surveys and the checks in `adopt` and `repair` read one source's
  bindings, which relies on a family never changing source. Tier 2 enforces that, and the Python never does it.
- **A thread without edits** is possible in SQL; `Catalog.create` writes both in one transaction. A deferred
  constraint trigger could rule it out at tier 2.
- **A skeleton edit may leave out its compilation** even when one exists, and nothing can require it.
- **A language on a skeleton that is compiled later** is ignored from then on, since the skeleton's language comes
  from its recipe.
- **A late case has no stored replacement.** A case stored at a vignette's old place after a repair gets a
  replacement from `behind` that nothing has stored; the portal would have to build it.
- **What deleting hides in the portal**: everywhere, or only in pickers, while the runs that used it stay visible.
- **Who changed what isn't exposed.** Every row records who wrote it and when, but the API only returns what holds
  now.
- **Why an edit was made isn't stored.** It can be worked out by comparing consecutive edits' references.
- **Titles take a query for every reference they visit,** are rebuilt from names that change, and aren't unique.
- **A run's owner isn't recorded, and scorer labels aren't carried to the next scorer edit.** Both wait for the
  runner and the portal.
- **Judges aren't checked for language.** A judge's language could be compared with the cases its scoring grades,
  the way `language.mixed` checks a trial. An English judge grading Swedish answers may be intended, so it would be a
  notice rather than a warning.

## Proposed amendments
