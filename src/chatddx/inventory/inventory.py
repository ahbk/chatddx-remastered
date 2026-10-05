import tomllib
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, HttpUrl, model_validator

from chatddx.factors.base import Digest, Frozen, Sha256Hex

from .sources import DirectorySource, Source


class DirectorySourceSpec(Frozen):
    kind: Literal["directory"] = "directory"
    path: Path
    suffix: str = ".txt"
    sensitive: bool = True


# Where an engine is served: the engine by digest, which is also the name it's served
# under, and how to reach it. A local engine's endpoint names the host that runs it;
# a remote engine's URL is its base_url, so its endpoint leaves the URL out.
class EndpointSpec(Frozen):
    engine: Digest
    url: HttpUrl | None = None
    host: str | None = None
    max_jobs: int = Field(default=1, ge=1)
    credential: str | None = None


# Where a host keeps what its engines pin: model artifacts by digest, chat templates by
# their SHA-256. Locations are the host's own, given as the start-up script reads them.
class HostSpec(Frozen):
    bind: str = "0.0.0.0"
    models: dict[Digest, str] = Field(default_factory=dict)
    templates: dict[Sha256Hex, str] = Field(default_factory=dict)


class Inventory(Frozen):
    root: Path
    sources: dict[str, DirectorySourceSpec] = Field(default_factory=dict)
    endpoints: dict[str, EndpointSpec] = Field(default_factory=dict)
    hosts: dict[str, HostSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _hosts_exist(self) -> Self:
        for name, endpoint in self.endpoints.items():
            if endpoint.host is not None and endpoint.host not in self.hosts:
                raise ValueError(f"endpoint {name!r} names no host {endpoint.host!r}")
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        with path.open("rb") as f:
            data = tomllib.load(f)
        if stray := set(data) - {"source", "endpoint", "host"}:
            raise ValueError(
                f"{path}: the inventory has only 'source', 'endpoint' and 'host' "
                + f"tables, not {sorted(stray)}"
            )
        return cls.model_validate(
            {
                "root": path.parent,
                "sources": data.get("source", {}),
                "endpoints": data.get("endpoint", {}),
                "hosts": data.get("host", {}),
            }
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

    def endpoint(self, name: str) -> EndpointSpec:
        spec = self.endpoints.get(name)
        if spec is None:
            raise LookupError(f"no endpoint {name!r} in the inventory")
        return spec
