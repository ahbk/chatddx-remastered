from abc import ABC, abstractmethod
from pathlib import Path
from typing import override

from chatddx.factors.base import Fingerprint, Frozen
from chatddx.factors.cases import Case, Vignette


class Source(Frozen, ABC):
    name: str
    sensitive: bool = True

    @abstractmethod
    def ids(self) -> list[str]: ...

    @abstractmethod
    def fetch(self, id: str) -> bytes: ...

    def cases(self) -> list[Case]:
        return [
            Case(
                vignette=Vignette(
                    source=self.name, id=id, fingerprint=Fingerprint.of(self.fetch(id))
                )
            )
            for id in self.ids()
        ]

    def _missing(self, id: str) -> LookupError:
        return LookupError(f"no case {id!r} in source {self.name!r}")


class DirectorySource(Source):
    path: Path
    suffix: str = ".txt"

    @override
    def ids(self) -> list[str]:
        return sorted(
            p.name.removesuffix(self.suffix)
            for p in self.path.iterdir()
            if p.is_file() and p.name.endswith(self.suffix)
        )

    @override
    def fetch(self, id: str) -> bytes:
        if "/" in id or "\\" in id or id.startswith("."):
            raise LookupError(f"case id {id!r} points outside source {self.name!r}")
        try:
            return (self.path / f"{id}{self.suffix}").read_bytes()
        except FileNotFoundError:
            raise self._missing(id) from None


class MemorySource(Source):
    vignettes: dict[str, bytes]

    @override
    def ids(self) -> list[str]:
        return sorted(self.vignettes)

    @override
    def fetch(self, id: str) -> bytes:
        try:
            return self.vignettes[id]
        except KeyError:
            raise self._missing(id) from None
