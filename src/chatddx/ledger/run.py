from collections.abc import Sequence
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, Field, model_validator

from chatddx.factors.base import (
    Code,
    Digest,
    Finding,
    Fingerprint,
    Frozen,
    StructuralError,
    distinct,
    resolve,
)
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Case
from chatddx.factors.engine import LocalEngine, RemoteEngine
from chatddx.factors.request import Skeleton, Tool, ToolOutput, ToolRef, tool_calls
from chatddx.factors.trial import CanarySet, CanarySetRef, Execution, Trial, TrialRef

from .call import Call, Turn, check_execution
from .record import (
    ItemKey,
    Record,
    UtcDatetime,
    check_log,
    check_seal,
    code_name,
    ran_pinned,
    seal_rows,
)

Phase = Literal["start", "end"]
PHASES: tuple[Phase, ...] = ("start", "end")


def _phase_order(phases: tuple[Phase, ...]) -> tuple[Phase, ...]:
    return tuple(sorted(distinct(phases), key=PHASES.index))


# The code a tool ran with, as the runner loaded it.
class ToolCode(Frozen):
    tool: ToolRef
    code: Code


def _by_tool(entries: tuple[ToolCode, ...]) -> tuple[ToolCode, ...]:
    _ = distinct(tuple(e.tool for e in entries))
    return tuple(sorted(entries, key=lambda e: e.tool))


# The inventory's endpoint a run's calls went to.
class Endpoint(Frozen):
    name: str
    url: str


class RunStarted(Record):
    case_derived: ClassVar[bool] = False
    stage: Literal["started"] = "started"
    run: UUID
    at: UtcDatetime
    rig: Code
    trial: TrialRef
    execution: Execution = Execution()
    canaries: CanarySetRef | None = None
    verify_at: Annotated[tuple[Phase, ...], AfterValidator(_phase_order)] = Field(
        default=PHASES, min_length=1
    )
    tool_code: Annotated[tuple[ToolCode, ...], AfterValidator(_by_tool)] = ()
    endpoint: Endpoint | None = None

    @model_validator(mode="after")
    def _verify_at(self) -> "RunStarted":
        if self.canaries is None and self.verify_at != PHASES:
            raise ValueError("verify_at goes with a canary set")
        return self


class RunFinished(Record):
    case_derived: ClassVar[bool] = False
    stage: Literal["finished"] = "finished"
    run: UUID
    at: UtcDatetime
    findings: tuple[Finding, ...] = ()
    seal: Digest


RunStage = Annotated[RunStarted | RunFinished, Field(discriminator="stage")]
RUN_STAGES = ("started", "finished")


class RunItem(Record):
    run: UUID
    key: ItemKey
    vignette: Fingerprint
    call: Call
    turns: tuple[Turn, ...] = ()

    @property
    def calls(self) -> tuple[Call, ...]:
        return (self.call, *(t.call for t in self.turns))


class CanaryCall(Record):
    case_derived: ClassVar[bool] = False
    run: UUID
    phase: Phase
    canary: int = Field(ge=0)
    call: Call


class Run(Frozen):
    stages: tuple[RunStage, ...] = Field(min_length=1)
    items: tuple[RunItem, ...] = ()
    canaries: tuple[CanaryCall, ...] = ()

    @model_validator(mode="after")
    def _log(self) -> "Run":
        rows = (*self.stages, *self.items, *self.canaries)
        check_log(self.stages, RUN_STAGES, [r.run for r in rows])
        return self

    @property
    def started(self) -> RunStarted:
        stage = self.stages[0]
        assert isinstance(stage, RunStarted)
        return stage

    @property
    def finished(self) -> RunFinished | None:
        return next((s for s in self.stages if isinstance(s, RunFinished)), None)

    def seal(self) -> str:
        return seal_rows(self.started, items=self.items, canaries=self.canaries)

    def finish(
        self, at: AwareDatetime, findings: Sequence[Finding] = ()
    ) -> RunFinished:
        return RunFinished(
            run=self.started.run, at=at, findings=tuple(findings), seal=self.seal()
        )


def check_run(run: Run, registry: Registry) -> list[Finding]:
    started = run.started
    trial = resolve(registry.get, started.trial, Trial)
    planned: set[tuple[Phase, int]] = set()
    if started.canaries is not None:
        canary_set = resolve(registry.get, started.canaries, CanarySet)
        planned = {
            (p, i) for p in started.verify_at for i in range(len(canary_set.canaries))
        }
    elif run.canaries:
        raise StructuralError("canary calls in a run without canaries")
    probes = [(c.phase, c.canary) for c in run.canaries]
    if stray := set(probes) - planned:
        names = sorted(f"{p}/{c}" for p, c in stray)
        raise StructuralError(f"canary calls not planned: {names}")
    if len(set(probes)) != len(probes):
        raise StructuralError("duplicate canary calls")

    expected = {
        ItemKey(case=c, replicate=r)
        for c in trial.cases
        for r in range(len(trial.seeds))
    }
    keys = [i.key for i in run.items]
    if stray := set(keys) - expected:
        raise StructuralError(f"run items outside the trial: {sorted(map(str, stray))}")
    if len(set(keys)) != len(keys):
        raise StructuralError("duplicate run items")

    findings = check_seal(run, str(started.run))
    if run.finished is not None:
        for missing, what in (
            (expected - set(keys), "items"),
            (planned - set(probes), "canary calls"),
        ):
            if missing:
                findings.append(
                    Finding(
                        code="run.incomplete", message=f"{len(missing)} {what} missing"
                    )
                )
    for item in run.items:
        case = resolve(registry.get, item.key.case, Case)
        if item.vignette != case.vignette.fingerprint:
            findings.append(
                Finding(
                    code="case.drift",
                    message="the vignette read differs from the case's",
                    subject=str(item.key),
                )
            )
    skeleton = resolve(registry.get, trial.skeleton, Skeleton)
    for item in run.items:
        findings.extend(_rounds(item, skeleton, registry))
    findings.extend(_tool_code(started, skeleton, registry))
    engine = registry.get(trial.engine)
    assert isinstance(engine, LocalEngine | RemoteEngine)
    want = engine.served_model_name
    for item in run.items:
        returned = {c.returned_model for c in item.calls if c.response is not None}
        for got in sorted(m for m in returned - {want} if m is not None):
            findings.append(
                Finding(
                    code="attestation.model",
                    message=f"engine returned model {got!r}, declared {want!r}",
                    subject=str(item.key),
                )
            )
        if None in returned:
            findings.append(
                Finding(
                    code="attestation.model",
                    message=f"engine returned no model name, declared {want!r}",
                    subject=str(item.key),
                )
            )
    if unfingerprinted := sum(
        1 for i in run.items if any(c.prompt_tokens is None for c in i.calls)
    ):
        findings.append(
            Finding(
                code="attestation.prompt_tokens",
                message=f"{unfingerprinted} items have no prompt token fingerprint",
            )
        )
    calls = [*(c for i in run.items for c in i.calls), *(c.call for c in run.canaries)]
    sent = {i.key: i.call.started_at for i in run.items}
    findings.extend(check_execution(started.execution, trial, calls, sent))
    return findings + _bracketed(run)


# Canaries measure the engine before and after the items, so they mustn't overlap them.
def _bracketed(run: Run) -> list[Finding]:
    item_calls = [c for i in run.items for c in i.calls]
    if not item_calls:
        return []
    first = min(c.started_at for c in item_calls)
    last = max(c.finished_at for c in item_calls)
    early = sum(
        1 for c in run.canaries if c.phase == "start" and c.call.finished_at > first
    )
    late = sum(1 for c in run.canaries if c.phase == "end" and c.call.started_at < last)
    return [
        Finding(code="canary.phase", message=message)
        for count, message in (
            (
                early,
                f"{early} start canary calls hadn't finished when the first item was sent",
            ),
            (
                late,
                f"{late} end canary calls started before the last item call finished",
            ),
        )
        if count
    ]


def _rounds(item: RunItem, skeleton: Skeleton, registry: Registry) -> list[Finding]:
    if item.turns and not skeleton.tools:
        raise StructuralError(f"{item.key}: tool rounds, but the skeleton has no tools")
    if not skeleton.tools:
        return []
    assert skeleton.max_rounds is not None
    if len(item.turns) > skeleton.max_rounds:
        raise StructuralError(f"{item.key}: more than {skeleton.max_rounds} rounds")
    names = {resolve(registry.get, t, Tool).name for t in skeleton.tools}
    answer = (
        skeleton.contract.name if isinstance(skeleton.contract, ToolOutput) else None
    )

    def pending(call: Call) -> set[tuple[str, str]]:
        return {
            (c.id, c.name) for c in tool_calls(call.response or {}) if c.name != answer
        }

    previous = item.call
    for turn in item.turns:
        for r in turn.tools:
            if r.name not in names and r.error is None:
                raise StructuralError(f"{item.key}: no tool named {r.name!r}")
        if {(r.id, r.name) for r in turn.tools} != pending(previous):
            raise StructuralError(
                f"{item.key}: a round runs tools for calls the response didn't make"
            )
        previous = turn.call
    if left := sorted({name for _, name in pending(previous)}):
        return [
            Finding(
                code="tools.unanswered",
                message=f"the last response still calls {', '.join(left)} after "
                + f"{len(item.turns)} of {skeleton.max_rounds} rounds",
                subject=str(item.key),
            )
        ]
    return []


def _tool_code(
    started: RunStarted, skeleton: Skeleton, registry: Registry
) -> list[Finding]:
    recorded = {e.tool: e.code for e in started.tool_code}
    if stray := sorted(recorded.keys() - set(skeleton.tools)):
        raise StructuralError(
            f"tool code recorded for tools the skeleton lacks: {stray}"
        )
    findings: list[Finding] = []
    for ref in skeleton.tools:
        tool = resolve(registry.get, ref, Tool)
        ran = recorded.get(ref)
        if ran is None:
            message = f"the code tool {tool.name!r} ran with isn't recorded"
        elif not ran_pinned(tool.code, ran):
            message = (
                f"tool {tool.name!r} ran with {code_name(ran)}, not the code it pins "
                + f"({code_name(tool.code)})"
            )
        else:
            continue
        findings.append(Finding(code="tools.code", message=message, subject=ref))
    return findings


def compare_prompt_tokens(a: Run, b: Run, registry: Registry) -> list[Finding]:
    ta, tb = (resolve(registry.get, r.started.trial, Trial) for r in (a, b))
    if differ := [
        f for f in ("skeleton", "engine", "cleanup") if getattr(ta, f) != getattr(tb, f)
    ]:
        raise StructuralError(
            f"the runs build their prompts from different {', '.join(differ)}"
        )
    theirs = {i.key: i.call.prompt_tokens for i in b.items}
    return [
        Finding(
            code="attestation.prompt_tokens_drift",
            message="the engine read different prompt tokens for the same item",
            subject=str(item.key),
        )
        for item in a.items
        if item.call.prompt_tokens is not None
        and (other := theirs.get(item.key)) is not None
        and other != item.call.prompt_tokens
    ]
