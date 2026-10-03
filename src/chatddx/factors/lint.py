import re
from collections.abc import Iterable

from .bundle import Registry
from .engine import LocalEngine, ModelArtifact
from .identity import Finding
from .request import Skeleton
from .scoring import Scorer
from .trial import Trial

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_NIX_STORE = re.compile(r"^/nix/store/[0-9a-z]{32}-.+$")


def _model(c: ModelArtifact, _: Registry) -> Iterable[Finding]:
    if not _COMMIT.match(c.revision):
        yield Finding(
            code="model.revision",
            message=f"revision {c.revision!r} is not a commit and can move",
            subject=c.digest,
        )


def _engine(c: LocalEngine, _: Registry) -> Iterable[Finding]:
    if not _NIX_STORE.match(c.runtime.closure):
        yield Finding(
            code="engine.closure",
            message=f"closure {c.runtime.closure!r} is not a Nix store path",
            subject=c.digest,
        )


def _scorer(c: Scorer, _: Registry) -> Iterable[Finding]:
    if c.code.revision is None:
        yield Finding(
            code="scorer.revision",
            message="scorer code has no revision",
            subject=c.digest,
        )


def _trial(c: Trial, registry: Registry) -> Iterable[Finding]:
    engine = registry.get(c.engine)
    skeleton = registry.get(c.skeleton)
    assert isinstance(skeleton, Skeleton)
    if not isinstance(engine, LocalEngine):
        return
    if engine.runtime.engine == "vllm" and engine.runtime.version.startswith("0.24."):
        t = skeleton.body.get("temperature")
        if isinstance(t, int | float) and 0 < t < 0.01:
            yield Finding(
                code="vllm.temperature_clamped",
                message=f"vLLM 0.24 raises temperature {t} to 0.01",
                subject=c.digest,
            )


def lint(registry: Registry, digests: Iterable[str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for d in registry if digests is None else digests:
        match registry.get(d):
            case ModelArtifact() as c:
                findings.extend(_model(c, registry))
            case LocalEngine() as c:
                findings.extend(_engine(c, registry))
            case Scorer() as c:
                findings.extend(_scorer(c, registry))
            case Trial() as c:
                findings.extend(_trial(c, registry))
            case _:
                pass
    return findings
