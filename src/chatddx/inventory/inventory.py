import tomllib
from pathlib import Path
from typing import Literal, Self

from pydantic import Field

from chatddx.factors.base import Frozen

from .sources import DirectorySource, Source


class DirectorySourceSpec(Frozen):
    kind: Literal["directory"] = "directory"
    path: Path
    suffix: str = ".txt"
    sensitive: bool = True


class Inventory(Frozen):
    root: Path
    sources: dict[str, DirectorySourceSpec] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Self:
        with path.open("rb") as f:
            data = tomllib.load(f)
        if set(data) - {"source"}:
            raise ValueError(f"{path}: the inventory has only 'source' tables so far")
        return cls.model_validate(
            {"root": path.parent, "sources": data.get("source", {})}
        )

    def source(self, name: str) -> Source:
        spec = self.sources.get(name)
        if spec is None:
            raise LookupError(f"no source {name!r} in the inventory")
        return DirectorySource(
            name=name,
            path=(self.root / spec.path).resolve(),
            suffix=spec.suffix,
            sensitive=spec.sensitive,
        )
