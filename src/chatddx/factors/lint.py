import re
from collections.abc import Callable, Iterable, Sequence

from jsonschema import ValidationError, validators
from jsonschema.exceptions import SchemaError, relevance
from jsonschema.protocols import Validator
from pydantic import JsonValue
from referencing.exceptions import Unresolvable
from referencing.jsonschema import UnknownDialect, specification_with

from .base import Component, Finding, resolve
from .bundle import Registry
from .engine import LocalEngine, ModelArtifact, flag_names
from .request import NativeOutput, Skeleton, ToolOutput
from .scoring import Expectation, ExpectationSchema, Judge, Scorer
from .select import reaches
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


def _scorer(c: Scorer, registry: Registry) -> Iterable[Finding]:
    if c.code.revision is None:
        yield Finding(
            code="scorer.revision",
            message="scorer code has no revision",
            subject=c.digest,
        )
    schema = resolve(registry.get, c.expectation_schema, ExpectationSchema).json_schema
    for i, view in enumerate(c.views):
        if not reaches(schema, view.expectation):
            yield Finding(
                code="view.unreachable",
                message=f"view {i}'s expectation selector {view.expectation!r} "
                + "reaches nothing in the expectation schema",
                subject=c.digest,
            )


def _where(error: ValidationError | SchemaError) -> str:
    path: Sequence[str | int] = error.absolute_path
    if not path:
        return "at the root"
    tokens = (str(p).replace("~", "~0").replace("/", "~1") for p in path)
    return "at /" + "/".join(tokens)


def _known_draft(draft: JsonValue) -> bool:
    if not isinstance(draft, str):
        return False
    try:
        _ = specification_with(draft)
    except UnknownDialect:
        return False
    return True


def _validator(schema: dict[str, JsonValue]) -> type[Validator] | str:
    draft = schema.get("$schema", "https://json-schema.org/draft/2020-12/schema")
    if not _known_draft(draft):
        return f"$schema {draft!r} names no draft that can be checked"
    cls = validators.validator_for(schema)
    try:
        cls.check_schema(schema)
    except SchemaError as e:
        return f"{_where(e)}: {e.message}"
    return cls


def _expectation_schema(c: ExpectationSchema, _: Registry) -> Iterable[Finding]:
    match _validator(c.json_schema):
        case str() as message:
            yield Finding(
                code="expectation_schema.invalid", message=message, subject=c.digest
            )
        case _:
            pass


def _expectation(c: Expectation, registry: Registry) -> Iterable[Finding]:
    schema = resolve(registry.get, c.expectation_schema, ExpectationSchema).json_schema
    cls = _validator(schema)
    if isinstance(cls, str):
        return
    try:
        errors = list(cls(schema).iter_errors(c.data))
    except Unresolvable as e:
        yield Finding(
            code="expectation.unchecked",
            message=f"a $ref in the schema can't be resolved: {e.ref}",
            subject=c.digest,
        )
        return
    error = max(errors, key=relevance, default=None)
    if error is not None:
        more = f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""
        yield Finding(
            code="expectation.invalid",
            message=f"{_where(error)}: {error.message}{more}",
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


def _choice(choice: JsonValue) -> str:
    match choice:
        case {"function": {"name": str() as name}}:
            return repr(name)
        case str():
            return choice
        case _:
            return "auto"


def _asks_to_reason(body: dict[str, JsonValue]) -> bool | None:
    kwargs = body.get("chat_template_kwargs")
    thinking = kwargs.get("enable_thinking") if isinstance(kwargs, dict) else None
    effort = body.get("reasoning_effort")
    if thinking is False or effort == "none":
        return False
    if thinking is True or effort is not None or "thinking_token_budget" in body:
        return True
    return None


def _runtime(
    subject: str, skeleton: Skeleton, engine: Component, by_default: bool | None
) -> Iterable[Finding]:
    constrained = isinstance(skeleton.contract, NativeOutput | ToolOutput)
    if not (
        isinstance(engine, LocalEngine)
        and engine.runtime.server == "vllm"
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
    if "tools" in skeleton.body and not (
        {"--enable-auto-tool-choice", "--tool-call-parser"} <= flags
    ):
        constrained = False
        yield Finding(
            code="vllm.tools_refused",
            message="vLLM 0.24 refuses tool_choice "
            + f"{_choice(skeleton.body.get('tool_choice'))} without "
            + "--enable-auto-tool-choice and --tool-call-parser; for harmony and "
            + "Mistral models it goes out unconstrained instead",
            subject=subject,
        )
    if isinstance(skeleton.contract, NativeOutput) and skeleton.tools:
        yield Finding(
            code="vllm.native_tools_uncallable",
            message="with tool_choice auto, vLLM 0.24 constrains the whole answer to "
            + "response_format, so no tool can be called",
            subject=subject,
        )
    if constrained and "--reasoning-parser" not in flags:
        reasons = _asks_to_reason(skeleton.body)
        if reasons is None and "--default-chat-template-kwargs" not in flags:
            reasons = by_default
        if reasons is not False:
            yield Finding(
                level="warning" if reasons else "info",
                code="vllm.grammar_before_reasoning",
                message="without --reasoning-parser, vLLM 0.24 constrains the "
                + "answer from its first token, so a model can't reason first",
                subject=subject,
            )


Languages = Callable[[str], str | None]
# Whether an engine's model reasons when a request doesn't say, by the engine's digest.
Reasons = Callable[[str], bool | None]


def _languages(c: Trial, languages: Languages) -> Iterable[Finding]:
    request = languages(c.skeleton)
    if request is None:
        yield Finding(
            level="info",
            code="language.unknown",
            message="the request's language is unknown",
            subject=c.digest,
        )
        return
    cases = [languages(case) for case in c.cases]
    if unknown := cases.count(None):
        yield Finding(
            level="info",
            code="language.unknown",
            message=f"the language of {unknown} of {len(cases)} cases is unknown",
            subject=c.digest,
        )
    other = [lang for lang in cases if lang is not None and lang != request]
    if other:
        verb = "is" if len(other) == 1 else "are"
        yield Finding(
            code="language.mixed",
            message=f"the request is {request}, but {len(other)} of {len(cases)} "
            + f"cases {verb} {', '.join(sorted(set(other)))}",
            subject=c.digest,
        )


def _pair(
    c: Trial | Judge, registry: Registry, reasons: Reasons | None
) -> Iterable[Finding]:
    skeleton = resolve(registry.get, c.skeleton, Skeleton)
    by_default = None if reasons is None else reasons(c.engine)
    yield from _runtime(c.digest, skeleton, registry.get(c.engine), by_default)


def lint(
    registry: Registry,
    digests: Iterable[str] | None = None,
    languages: Languages | None = None,
    reasons: Reasons | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for d in registry if digests is None else digests:
        match registry.get(d):
            case ModelArtifact() as c:
                findings.extend(_model(c, registry))
            case LocalEngine() as c:
                findings.extend(_engine(c, registry))
            case Scorer() as c:
                findings.extend(_scorer(c, registry))
            case ExpectationSchema() as c:
                findings.extend(_expectation_schema(c, registry))
            case Expectation() as c:
                findings.extend(_expectation(c, registry))
            case Trial() as c:
                findings.extend(_pair(c, registry, reasons))
                if languages is not None:
                    findings.extend(_languages(c, languages))
            case Judge() as c:
                findings.extend(_pair(c, registry, reasons))
            case _:
                pass
    return findings
