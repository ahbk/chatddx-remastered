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
    # The sensitive sources each endpoint may receive case-derived content from. Only
    # the inventory's own file says so, never an included one, so importing endpoints
    # again can't clear anything.
    cleared: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _hosts_exist(self) -> Self:
        for name, endpoint in self.endpoints.items():
            if endpoint.host is not None and endpoint.host not in self.hosts:
                raise ValueError(f"endpoint {name!r} names no host {endpoint.host!r}")
        return self

    @model_validator(mode="after")
    def _cleared_exist(self) -> Self:
        for name, sources in self.cleared.items():
            if name not in self.endpoints:
                raise ValueError(f"cleared names no endpoint {name!r}")
            if unknown := sorted(set(sources) - set(self.sources)):
                raise ValueError(
                    f"endpoint {name!r} is cleared for no source {', '.join(unknown)}"
                )
        return self

    # `include` names files, relative to this one, whose endpoints and hosts are this
    # inventory's too, such as what `chatddx import-engine` writes.
    @classmethod
    def load(cls, path: Path) -> Self:
        with path.open("rb") as f:
            data = tomllib.load(f)
        if stray := set(data) - {"include", "source", "endpoint", "host", "cleared"}:
            raise ValueError(
                f"{path}: the inventory has only 'include', 'source', 'endpoint', "
                + f"'host' and 'cleared', not {sorted(stray)}"
            )
        tables = {t: dict(data.get(t, {})) for t in ("source", "endpoint", "host")}
        for name in data.get("include", []):
            included = path.parent / name
            with included.open("rb") as f:
                more = tomllib.load(f)
            if stray := set(more) - {"endpoint", "host"}:
                raise ValueError(
                    f"{included}: an included inventory has only 'endpoint' and "
                    + f"'host' tables, not {sorted(stray)}"
                )
            for table, records in more.items():
                if twice := sorted(set(tables[table]) & set(records)):
                    raise ValueError(f"{included}: {table} {twice} are in {path} too")
                tables[table] |= records
        return cls.model_validate(
            {
                "root": path.parent,
                "sources": tables["source"],
                "endpoints": tables["endpoint"],
                "hosts": tables["host"],
                "cleared": data.get("cleared", {}),
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

    def cleared_for(self, endpoint: str) -> frozenset[str]:
        return frozenset(self.cleared.get(endpoint, ()))
