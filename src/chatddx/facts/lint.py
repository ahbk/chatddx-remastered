from collections.abc import Iterable

from chatddx.factors.base import Finding, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.lint import Reasons
from chatddx.factors.request import Skeleton
from chatddx.factors.scoring import Judge
from chatddx.factors.trial import Trial

from .facts import ContractFact, Facts, ModelFacts, Refusal, model_name


def reasons(facts: Facts, registry: Registry) -> Reasons:
    def by_default(engine: str) -> bool | None:
        model = facts.about(registry.get(engine), registry)
        return None if model is None else model.reasons_by_default()

    return by_default


def _model(subject: str, skeleton: Skeleton, model: ModelFacts) -> Iterable[Finding]:
    body = skeleton.body
    asked = (body.get("reasoning_effort"), body.get("chat_template_kwargs") or {})
    known = [
        (w.effort, w.chat_template_kwargs) for w in model.reasoning.realized().values()
    ]
    if asked != (None, {}) and asked not in known:
        yield Finding(
            code="facts.reasoning_unmatched",
            message="the skeleton's reasoning settings match none of the model's "
            + "reasoning levels",
            subject=subject,
        )
    if "thinking_token_budget" in body and isinstance(model.reasoning.budget, Refusal):
        yield Finding(
            code="facts.budget_refused",
            message=model.reasoning.budget.refused,
            subject=subject,
        )
    match model.output.fact(skeleton.contract.kind):
        case Refusal(refused=reason):
            yield Finding(code="facts.output_refused", message=reason, subject=subject)
        case ContractFact(note=str() as note):
            yield Finding(
                level="info", code="facts.output_note", message=note, subject=subject
            )
        case _:
            pass


def _pair(c: Trial | Judge, registry: Registry, facts: Facts) -> Iterable[Finding]:
    engine = registry.get(c.engine)
    model = facts.about(engine, registry)
    if model is None:
        yield Finding(
            level="info",
            code="facts.missing",
            message=f"no facts about {model_name(engine, registry)!r}, so "
            + "model-level checks were skipped",
            subject=c.digest,
        )
        return
    yield from _model(c.digest, resolve(registry.get, c.skeleton, Skeleton), model)


def lint(
    registry: Registry, facts: Facts, digests: Iterable[str] | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    for d in registry if digests is None else digests:
        match registry.get(d):
            case Trial() | Judge() as c:
                findings.extend(_pair(c, registry, facts))
            case _:
                pass
    return findings
