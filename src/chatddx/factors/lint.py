import re
from collections.abc import Iterable

from pydantic import JsonValue

from .base import Component, Finding, resolve
from .bundle import Registry
from .engine import LocalEngine, ModelArtifact, flag_names
from .request import NativeOutput, Skeleton, ToolOutput
from .scoring import Judge, Scorer
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


def _has_ref(node: JsonValue) -> bool:
    match node:
        case {"$ref": str()}:
            return True
        case dict():
            return any(_has_ref(v) for v in node.values())
        case list():
            return any(_has_ref(v) for v in node)
        case _:
            return False


def _asks_to_reason(body: dict[str, JsonValue]) -> bool | None:
    kwargs = body.get("chat_template_kwargs")
    thinking = kwargs.get("enable_thinking") if isinstance(kwargs, dict) else None
    effort = body.get("reasoning_effort")
    if thinking is False or effort == "none":
        return False
    if thinking is True or effort is not None or "thinking_token_budget" in body:
        return True
    return None


def _pair(subject: str, skeleton: Skeleton, engine: Component) -> Iterable[Finding]:
    constrained = isinstance(skeleton.contract, NativeOutput | ToolOutput)
    if not (
        isinstance(engine, LocalEngine)
        and engine.runtime.engine == "vllm"
        and engine.runtime.version.startswith("0.24.")
    ):
        if constrained and _has_ref(skeleton.output_schema):
            yield Finding(
                code="schema.ref_unverified",
                message="the schema has $ref, which this engine isn't known to "
                + "resolve; inline_refs@1 removes them",
                subject=subject,
            )
        return

    flags = flag_names(engine.argv)
    t = skeleton.body.get("temperature")
    if isinstance(t, int | float) and 0 < t < 0.01:
        yield Finding(
            code="vllm.temperature_clamped",
            message=f"vLLM 0.24 raises temperature {t} to 0.01",
            subject=subject,
        )
    if "thinking_token_budget" in skeleton.body and not flags & {
        "--reasoning-parser",
        "--reasoning-config",
    }:
        yield Finding(
            code="vllm.thinking_budget_refused",
            message="vLLM 0.24 refuses thinking_token_budget without "
            + "--reasoning-parser or --reasoning-config",
            subject=subject,
        )
    if isinstance(skeleton.contract, ToolOutput) and not (
        {"--enable-auto-tool-choice", "--tool-call-parser"} <= flags
    ):
        constrained = False
        yield Finding(
            code="vllm.tool_unconstrained",
            message="vLLM 0.24 leaves a named tool unconstrained without "
            + "--enable-auto-tool-choice and --tool-call-parser",
            subject=subject,
        )
    if constrained and "--reasoning-parser" not in flags:
        reasons = _asks_to_reason(skeleton.body)
        if reasons is not False:
            yield Finding(
                level="warning" if reasons else "info",
                code="vllm.grammar_before_reasoning",
                message="without --reasoning-parser, vLLM 0.24 constrains the "
                + "answer from its first token, so a model can't reason first",
                subject=subject,
            )


def _trial(c: Trial, registry: Registry) -> Iterable[Finding]:
    skeleton = resolve(registry.get, c.skeleton, Skeleton)
    yield from _pair(c.digest, skeleton, registry.get(c.engine))


def _judge(c: Judge, registry: Registry) -> Iterable[Finding]:
    skeleton = resolve(registry.get, c.skeleton, Skeleton)
    yield from _pair(c.digest, skeleton, registry.get(c.engine))


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
            case Judge() as c:
                findings.extend(_judge(c, registry))
            case _:
                pass
    return findings
