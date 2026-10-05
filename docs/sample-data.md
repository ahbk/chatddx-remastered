# Sample data

The sample data in `src/chatddx/data/sample/` holds:
  - `factors.toml`: chunks and recipes;
  - `facts.toml`: the two sample models' facts;
  - `cases.toml`: each case's tags, language and targets;
  - `schemas/`: the output schemas and the hand-written targets schema.

  The vignettes aren't in it: they're a fake-sensitive source, located by a World inventory or given as a
  directory.

  `chatddx init-data USER (--vignettes DIR | --world FILE) [--giftbag] [--data DIR]` seeds it for the `archive`
  person, created if missing, and shares it with USER, who must exist (`chatddx person add`).
  - **Inputs:** `--data` is what gets seeded (the old command's `--inventory`), the package's sample data by
    default. The vignettes come from `--vignettes`, a directory of `<id>.txt` files such as the old checkout's
    `src/chatddx/data/cases`, or from `--world`, a World inventory whose `[source.<name>]` table locates them.
    `--source` names the source (`sample`), which the cases are keyed by. A path that holds none of the sample's
    cases is refused before anything is written.
  - **Chunks and recipes:** each record becomes a thread named after it and owned by the archive. Recipes are
    compiled, and their compilations recorded. A model-dependent chunk (`from_facts`, such as `recommended`
    sampling) is written from each model's facts, so the recipes using it come once per model, named
    `plan (Qwen/Qwen3-8B-AWQ)`. A refused combination is skipped and reported. A `fork_of` record is seeded as a
    fork, so the catalog knows what it varies.
  - **Cases:** each case is read from the inventory's source, adopted into a family (named by its id, with its
    tags and language), and given an expectation thread.
  - **Re-runs:** a record is matched by kind, name and the archive as owner. It's validated when unchanged and gets
    a new edit when changed, so nothing is duplicated. A vignette that is missing or has changed at the source is
    reported; a changed one needs a repair (`docs/catalog.md`).
  - **Sharing:** USER becomes a collaborator on every archive thread and family.
  - **`--giftbag`:** USER also gets their own fork of every chunk, recipe and expectation thread, so the catalog's
    variations and proposals follow the archive when it moves. Existing forks are kept.

  Compilations record the running code as their compiler (`src/chatddx/core/rig.py`): version from the package,
  revision from `CHATDDX_REVISION` when set."

### init-data
- **Command**, append: "`chatddx init-data USER (--vignettes DIR | --world FILE) [--source NAME] [--giftbag]
  [--data DIR] [--facts PATH …]` connects as `DB_USER` and seeds the sample data in one transaction (`docs/chatddx.md`,
  "Sample data"). It prints one line per record: created, validated, updated, skipped, missing, needs repair,
  forked or kept."
- **Store API**, `Catalog`: add `find(kind, name, owner=)`, `forks(thread)`, `expectations_of(case)`.

## Proposed amendments
- REMOVE `docs/sample-data.md`: everything it says, the `init-data` command included, is folded into
  `agents/wip-sample-data.md` ("The sample data now"), and no doc refers to it.
