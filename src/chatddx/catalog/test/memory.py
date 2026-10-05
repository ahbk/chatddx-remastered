from collections.abc import Collection

from chatddx.catalog import Binding, Edit, Entry, Part, Subject, Thread
from chatddx.factors.base import Component
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Appendix, Case, Vignette
from chatddx.factors.request import Compilation, Recipe, compile_request
from chatddx.factors.scoring import Expectation
from chatddx.factors.test.sample import NOW, RIG

BY = 1


def compiled(registry: Registry, recipe: Recipe) -> Compilation:
    skeleton = registry.add(compile_request(recipe, registry.get))
    compilation = Compilation(recipe=recipe, skeleton=skeleton, compiler=RIG)
    _ = registry.add(compilation)
    return compilation


class Memory:
    def __init__(self, registry: Registry) -> None:
        self.registry: Registry = registry
        self._threads: list[Thread] = []
        self._edits: list[Edit] = []
        self._entries: list[tuple[Subject, Entry]] = []
        self._bindings: list[Binding] = []
        self._languages: list[tuple[str, str]] = []
        self._labels: list[tuple[str, Part, int, str]] = []

    def start(
        self,
        digest: str,
        *,
        compilation: str | None = None,
        forked_from: int | None = None,
    ) -> Edit:
        thread = Thread(
            id=len(self._threads) + 1,
            kind=self.registry.get(digest).kind_name,
            forked_from=forked_from,
            by=BY,
            at=NOW,
        )
        self._threads.append(thread)
        return self.save(thread.id, digest, compilation=compilation)

    def save(
        self,
        thread: int,
        digest: str,
        *,
        compilation: str | None = None,
        based_on: int | None = None,
    ) -> Edit:
        edit = Edit(
            id=len(self._edits) + 1,
            thread=thread,
            digest=digest,
            compilation=compilation,
            based_on=based_on,
            by=BY,
            at=NOW,
        )
        self._edits.append(edit)
        return edit

    def note(self, subject: Subject, entry: Entry) -> None:
        self._entries.append((subject, entry))

    def bind(self, family: int, vignette: Vignette) -> Binding:
        binding = Binding(
            id=len(self._bindings) + 1, family=family, vignette=vignette, by=BY, at=NOW
        )
        self._bindings.append(binding)
        return binding

    def set_language(self, digest: str, value: str) -> None:
        self._languages.append((digest, value))

    def add_label(self, scorer: str, part: Part, position: int, value: str) -> None:
        self._labels.append((scorer, part, position, value))

    def get(self, digest: str) -> Component:
        return self.registry.get(digest)

    def kind(self, digest: str) -> str | None:
        return self.registry.get(digest).kind_name if digest in self.registry else None

    def compilations(self, skeleton: str) -> list[Compilation]:
        return [c for _, c in self._stored(Compilation) if c.skeleton == skeleton]

    def thread(self, id: int) -> Thread | None:
        return next((t for t in self._threads if t.id == id), None)

    def threads(self, kind: str) -> list[Thread]:
        return [t for t in self._threads if t.kind == kind]

    def threads_forked_from(self, edits: Collection[int]) -> list[Thread]:
        return [t for t in self._threads if t.forked_from in edits]

    def edit(self, id: int) -> Edit | None:
        return next((e for e in self._edits if e.id == id), None)

    def edits(self, threads: Collection[int]) -> list[Edit]:
        return [e for e in self._edits if e.thread in threads]

    def edits_holding(self, digests: Collection[str]) -> list[Edit]:
        return [e for e in self._edits if e.digest in digests]

    def entries(self, subjects: Collection[Subject]) -> list[tuple[Subject, Entry]]:
        return [(s, e) for s, e in self._entries if s in subjects]

    def bindings(self, family: int) -> list[Binding]:
        return [b for b in self._bindings if b.family == family]

    def bindings_in(self, source: str) -> list[Binding]:
        return [b for b in self._bindings if b.vignette.source == source]

    def bound_to(self, vignette: Vignette, kind: str) -> list[str]:
        return [
            d
            for d, c in [*self._stored(Case), *self._stored(Appendix)]
            if c.kind_name == kind and c.vignette == vignette
        ]

    def expectations(self, cases: Collection[str]) -> list[str]:
        return [d for d, e in self._stored(Expectation) if e.case in cases]

    def languages(self, digests: Collection[str]) -> list[tuple[str, str]]:
        return [(d, v) for d, v in self._languages if d in digests]

    def labels(self, scorer: str) -> list[tuple[Part, int, str]]:
        return [(p, i, v) for s, p, i, v in self._labels if s == scorer]

    def _stored[C: Component](self, cls: type[C]) -> list[tuple[str, C]]:
        found: list[tuple[str, C]] = []
        for digest in sorted(self.registry):
            if isinstance(component := self.registry.get(digest), cls):
                found.append((digest, component))
        return found
