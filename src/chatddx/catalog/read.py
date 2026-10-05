from collections.abc import Collection
from typing import Protocol

from chatddx.factors.base import Component
from chatddx.factors.cases import Vignette
from chatddx.factors.request import Compilation

from .model import About, Binding, Edit, Entry, Part, Subject, Thread, latest


# Rows come back in the order they were written; nothing here picks the latest. The store
# implements this over its tables (src/chatddx/store/catalog.py:Rows).
class Reader(Protocol):
    def get(self, digest: str) -> Component: ...

    def kind(self, digest: str) -> str | None: ...

    def compilations(self, skeleton: str) -> list[Compilation]: ...

    def thread(self, id: int) -> Thread | None: ...

    def threads(self, kind: str) -> list[Thread]: ...

    def threads_forked_from(self, edits: Collection[int]) -> list[Thread]: ...

    def edit(self, id: int) -> Edit | None: ...

    def edits(self, threads: Collection[int]) -> list[Edit]: ...

    def edits_holding(self, digests: Collection[str]) -> list[Edit]: ...

    def entries(self, subjects: Collection[Subject]) -> list[tuple[Subject, Entry]]: ...

    def bindings(self, family: int) -> list[Binding]: ...

    def bindings_in(self, source: str) -> list[Binding]: ...

    def bound_to(self, vignette: Vignette, kind: str) -> list[str]: ...

    def expectations(self, cases: Collection[str]) -> list[str]: ...

    def languages(self, digests: Collection[str]) -> list[tuple[str, str]]: ...

    def labels(self, scorer: str) -> list[tuple[Part, int, str]]: ...


def heads_of(rows: Reader, threads: Collection[int]) -> dict[int, Edit]:
    found = latest((e.thread, e) for e in rows.edits(threads))
    return {t: found[t] for t in sorted(found)}


def holders(rows: Reader, digests: Collection[str]) -> dict[int, Edit]:
    return heads_of(rows, {e.thread for e in rows.edits_holding(digests)})


def heads_holding(rows: Reader, digests: Collection[str]) -> dict[int, Edit]:
    wanted = set(digests)
    return {t: h for t, h in holders(rows, wanted).items() if h.digest in wanted}


def abouts(rows: Reader, subjects: Collection[Subject]) -> dict[Subject, About]:
    entries: dict[Subject, list[Entry]] = {s: [] for s in subjects}
    for subject, entry in rows.entries(subjects):
        entries[subject].append(entry)
    return {s: About.of(e) for s, e in entries.items()}


def about(rows: Reader, subject: Subject) -> About:
    return abouts(rows, [subject])[subject]


def labels(rows: Reader, scorer: str) -> dict[tuple[Part, int], str]:
    return latest(
        ((part, position), value) for part, position, value in rows.labels(scorer)
    )
