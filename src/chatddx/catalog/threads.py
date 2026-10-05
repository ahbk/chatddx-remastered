from pydantic import JsonValue

from chatddx.factors.base import (
    Component,
    canonical_bytes,
    iter_refs,
    parse_component,
    resolve,
)
from chatddx.factors.request import Compilation, Recipe

from .families import stale_cases
from .model import THREAD_KINDS, Behind, Edit, Subject, Thread, Variation
from .read import Reader, abouts, heads_holding, heads_of


def check_threadable(kind: str) -> None:
    if kind not in THREAD_KINDS:
        raise ValueError(f"{kind} components have no threads")


# src/chatddx/store/migrations/0016-t2-catalog-based-on.sql repeats this check.
def check_based_on(rows: Reader, thread: int, based_on: int) -> None:
    t = rows.thread(thread)
    origin = None if t is None or t.forked_from is None else rows.edit(t.forked_from)
    if origin is None or not any(
        e.id == based_on and e.id >= origin.id for e in rows.edits([origin.thread])
    ):
        raise ValueError(f"edit {based_on} is not in the origin of thread {thread}")


def thread(rows: Reader, id: int) -> Thread:
    found = rows.thread(id)
    if found is None:
        raise LookupError(f"no thread with id {id}")
    return found


def edit(rows: Reader, id: int) -> Edit:
    found = rows.edit(id)
    if found is None:
        raise LookupError(f"no edit with id {id}")
    return found


def history(rows: Reader, thread: int) -> list[Edit]:
    edits = rows.edits([thread])
    if not edits:
        raise LookupError(f"no thread with id {thread}")
    return edits


def head(rows: Reader, thread: int) -> Edit:
    return history(rows, thread)[-1]


def heads(rows: Reader, kind: str, *, deleted: bool = False) -> list[Edit]:
    ids = [t.id for t in rows.threads(kind)]
    found = heads_of(rows, ids)
    known = abouts(rows, [Subject(thread=i) for i in ids])
    return [
        found[i]
        for i in ids
        if i in found and known[Subject(thread=i)].deleted == deleted
    ]


def find(rows: Reader, kind: str, name: str, *, owner: int | None = None) -> list[int]:
    ids = [t.id for t in rows.threads(kind)]
    known = abouts(rows, [Subject(thread=i) for i in ids])
    return [
        i
        for i in ids
        if (a := known[Subject(thread=i)]).name == name
        and not a.deleted
        and (owner is None or a.owner == owner)
    ]


def forks(rows: Reader, thread: int) -> list[int]:
    edits = [e.id for e in rows.edits([thread])]
    return [t.id for t in rows.threads_forked_from(edits)]


def expectations_of(rows: Reader, case: str) -> list[int]:
    return list(heads_holding(rows, rows.expectations([case])))


def recipe(rows: Reader, edit: Edit) -> Recipe | None:
    if edit.compilation is None:
        return None
    return resolve(rows.get, edit.compilation, Compilation).recipe


def behind(rows: Reader, thread: int) -> list[Behind]:
    current = head(rows, thread)
    refs = [(s.path, s.digest) for s in rows.get(current.digest).refs()]
    if (r := recipe(rows, current)) is not None:
        refs += [(s.path, s.digest) for s in iter_refs(r, "/recipe")]
    refs += [
        (path + s.path, s.digest)
        for path, digest in refs
        if rows.kind(digest) == "case"
        for s in rows.get(digest).refs()
    ]
    held: dict[str, set[int]] = {}
    for e in rows.edits_holding({d for _, d in refs}):
        held.setdefault(e.digest, set()).add(e.thread)
    threads = {t for ts in held.values() for t in ts}
    found = heads_of(rows, threads)
    known = abouts(rows, [Subject(thread=t) for t in threads])
    moved = {
        (path, t, digest): Behind(path=path, digest=digest, head=found[t])
        for path, digest in refs
        for t in held.get(digest, ())
        if found[t].digest != digest and not known[Subject(thread=t)].deleted
    }
    ordered = [moved[k] for k in sorted(moved)]
    return sorted([*ordered, *stale_cases(rows, refs)], key=lambda b: b.path)


def variation(rows: Reader, id: int) -> Variation | None:
    forked_from = thread(rows, id).forked_from
    if forked_from is None:
        return None
    edits = history(rows, id)
    based_on = [e.based_on for e in edits if e.based_on is not None]
    base = edit(rows, based_on[-1] if based_on else forked_from)
    fork = edits[-1]
    by_recipe = base.compilation is not None and fork.compilation is not None
    before, after = _shape(rows, base, by_recipe), _shape(rows, fork, by_recipe)
    return Variation(
        base=base,
        origin_head=head(rows, base.thread),
        varies={
            path: (before.get(path), after.get(path))
            for path in sorted(before.keys() | after.keys())
            if before.get(path) != after.get(path)
        },
    )


def proposal(rows: Reader, id: int) -> Recipe | Component | None:
    varied = variation(rows, id)
    if varied is None or not varied.moved:
        return None
    fork = head(rows, id)
    if varied.base.compilation is not None and fork.compilation is not None:
        origin = recipe(rows, varied.origin_head)
        if origin is None:
            return None
        changes = {
            path.removeprefix("/recipe/"): after
            for path, (_, after) in varied.varies.items()
        }
        return Recipe.model_validate({**origin.model_dump(mode="json"), **changes})
    doc = rows.get(varied.origin_head.digest).canonical_doc()
    for path, (_, after) in varied.varies.items():
        if after is None:
            _ = doc.pop(path[1:], None)
        else:
            doc[path[1:]] = after
    return parse_component(canonical_bytes(doc))


def _shape(rows: Reader, edit: Edit, by_recipe: bool) -> dict[str, JsonValue]:
    if by_recipe:
        r = recipe(rows, edit)
        assert r is not None
        return {f"/recipe/{k}": v for k, v in r.model_dump(mode="json").items()}
    doc = rows.get(edit.digest).canonical_doc()
    return {f"/{k}": v for k, v in doc.items() if k not in ("kind", "v")}
