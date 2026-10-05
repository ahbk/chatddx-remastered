from collections.abc import Iterable
from typing import Literal

from .base import (
    Code,
    Component,
    Digest,
    Finding,
    Frozen,
    StructuralError,
    parse_component,
    sha256_digest,
)


class Registry:
    def __init__(self) -> None:
        self._raw: dict[str, bytes] = {}
        self._parsed: dict[str, Component] = {}

    def add(self, component: Component) -> str:
        _ = self._raw.setdefault(component.digest, component.canonical)
        _ = self._parsed.setdefault(component.digest, component)
        return component.digest

    def add_raw(self, digest: str, raw: bytes) -> Component:
        if sha256_digest(raw) != digest:
            raise StructuralError(f"{digest} does not match its bytes")
        component = parse_component(raw)
        self._raw[digest] = raw
        self._parsed[digest] = component
        return component

    def get(self, digest: str) -> Component:
        try:
            return self._parsed[digest]
        except KeyError:
            raise StructuralError(f"{digest} is not in the registry") from None

    def raw(self, digest: str) -> bytes:
        return self._raw[digest]

    def __contains__(self, digest: object) -> bool:
        return digest in self._parsed

    def __iter__(self):
        return iter(self._parsed)

    def closure(self, roots: Iterable[str]) -> list[str]:
        seen: list[str] = []
        stack = list(roots)
        while stack:
            d = stack.pop()
            if d in seen:
                continue
            seen.append(d)
            stack.extend(site.digest for site in self.get(d).refs())
        return sorted(seen)

    def check(self, digests: Iterable[str] | None = None) -> None:
        problems: list[str] = []
        for d in self if digests is None else digests:
            component = self.get(d)
            refs: list[str] = []
            for site in component.refs():
                if site.digest not in self:
                    refs.append(f"{d}{site.path}: {site.digest} is missing")
                elif (k := self.get(site.digest).kind_name) not in site.kinds:
                    refs.append(f"{d}{site.path}: {k} is not one of {site.kinds}")
            problems.extend(refs)
            # cross_check resolves the component's own references, so they must hold.
            if not refs:
                problems.extend(f"{d}: {p}" for p in component.cross_check(self.get))
        if problems:
            raise StructuralError("\n".join(problems))

    def bundle(self, roots: Iterable[str], generator: Code) -> "Bundle":
        roots = sorted(set(roots))
        digests = self.closure(roots)
        self.check(digests)
        return Bundle(
            generator=generator,
            roots=tuple(roots),
            components={d: self._raw[d].decode() for d in digests},
        )


class Bundle(Frozen):
    format: Literal["chatddx.bundle/1"] = "chatddx.bundle/1"
    generator: Code
    roots: tuple[Digest, ...]
    components: dict[Digest, str]

    def load(self) -> tuple[Registry, list[Finding]]:
        registry = Registry()
        findings: list[Finding] = []
        for digest, text in self.components.items():
            raw = text.encode()
            component = registry.add_raw(digest, raw)
            if component.canonical != raw:
                findings.append(
                    Finding(
                        code="bundle.recanonicalized",
                        message="current code serializes this component differently",
                        subject=digest,
                    )
                )
        missing = [r for r in self.roots if r not in registry]
        if missing:
            raise StructuralError(f"roots missing from bundle: {missing}")
        registry.check()
        return registry, findings
