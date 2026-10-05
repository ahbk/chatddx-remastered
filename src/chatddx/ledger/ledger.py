import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Annotated, ClassVar, Literal, Self, cast, override
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, Field, JsonValue, model_validator

from chatddx.factors.base import (
    Code,
    Digest,
    Finding,
    Fingerprint,
    Frozen,
    StructuralError,
    canonical_bytes,
    distinct,
    resolve,
    sha256_digest,
    sorted_keys,
)
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Case, CaseRef
from chatddx.factors.engine import LocalEngine, RemoteEngine
from chatddx.factors.request import Skeleton, Tool, ToolOutput, tool_calls
from chatddx.factors.scoring import Judge, JudgeRef, Scorer, Scoring, ScoringRef
from chatddx.factors.select import reaches
from chatddx.factors.trial import (
    CanarySet,
    CanarySetRef,
    Execution,
    Trial,
    TrialRef,
)

Phase = Literal["start", "end"]
PHASES: tuple[Phase, ...] = ("start", "end")


def _utc(at: datetime) -> datetime:
    return at.astimezone(UTC)


# The offset is part of the canonical bytes, so without this one instant would seal
# differently depending on the writer's time zone.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_utc)]


def _in_order(started: datetime, finished: datetime) -> None:
    if finished < started:
        raise ValueError("finished before it started")


def _phase_order(phases: tuple[Phase, ...]) -> tuple[Phase, ...]:
    return tuple(sorted(distinct(phases), key=PHASES.index))


class Record(Frozen):
    case_derived: ClassVar[bool] = True
    # Bump when an existing field's meaning or default changes; additive fields don't.
    schema_version: ClassVar[int] = 1

    def canonical_doc(self) -> dict[str, JsonValue]:
        doc = cast(
            dict[str, JsonValue],
            self.model_dump(mode="json", context={"canonical": True}),
        )
        return sorted_keys({**doc, "v": type(self).schema_version})

    @property
    def canonical(self) -> bytes:
        return canonical_bytes(self.canonical_doc())

    @classmethod
    def parse(cls, data: bytes | str) -> Self:
        loaded: object = json.loads(data)
        if not isinstance(loaded, dict):
            raise StructuralError(f"{cls.__name__} is not a JSON object")
        doc = cast(dict[str, object], loaded)
        v = doc.pop("v", None)
        if v != cls.schema_version:
            raise StructuralError(
                f"{cls.__name__} v{v} is not readable by v{cls.schema_version}"
            )
        return cls.model_validate(doc)


class Call(Frozen):
    request: Fingerprint
    # With retries, from the first attempt's start to the last attempt's end.
    started_at: UtcDatetime
    finished_at: UtcDatetime
    status: int | None = None
    response: dict[str, JsonValue] | None = None
    prompt_tokens: Fingerprint | None = None
    attempts: int = Field(default=1, ge=1)
    error: str | None = None

    @model_validator(mode="after")
    def _times(self) -> "Call":
        _in_order(self.started_at, self.finished_at)
        return self

    @property
    def returned_model(self) -> str | None:
        model = (self.response or {}).get("model")
        return model if isinstance(model, str) else None

    @property
    def system_fingerprint(self) -> str | None:
        fp = (self.response or {}).get("system_fingerprint")
        return fp if isinstance(fp, str) else None


def fingerprint_prompt_tokens(
    response: dict[str, JsonValue], key: tuple[str, bytes] | None = None
) -> tuple[dict[str, JsonValue], Fingerprint | None]:
    # prompt_token_ids encode the case text, so only their fingerprint may be stored.
    kept = {k: v for k, v in response.items() if k != "prompt_token_ids"}
    ids = response.get("prompt_token_ids")
    if not isinstance(ids, list):
        return kept, None
    return kept, Fingerprint.of(canonical_bytes(ids), key)


def fingerprint_request(
    body: dict[str, JsonValue], key: tuple[str, bytes] | None = None
) -> Fingerprint:
    return Fingerprint.of(canonical_bytes(sorted_keys(body)), key)


# One tool the runner ran for a tool call in a response: `result` is the text the model
# read back, `error` why the tool failed. A call naming no tool of the skeleton fails.
class ToolRun(Frozen):
    id: str
    name: str
    started_at: UtcDatetime
    finished_at: UtcDatetime
    result: str
    error: str | None = None

    @model_validator(mode="after")
    def _times(self) -> "ToolRun":
        _in_order(self.started_at, self.finished_at)
        return self


# A tool round: the tools run for the previous response's calls, and the next call.
class Turn(Frozen):
    tools: tuple[ToolRun, ...] = Field(min_length=1)
    call: Call


class ItemKey(Frozen):
    case: CaseRef
    replicate: int = Field(ge=0)

    @override
    def __hash__(self) -> int:
        return hash((self.case, self.replicate))


# Runs and scores are append-only: one row per stage, plus item rows.


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


# Canonical rows keep old seals valid when a defaulted field is added, and sorting
# makes the seal independent of the order storage returns rows in.
def _seal(started: Record, **rows: Iterable[Record]) -> str:
    doc: dict[str, JsonValue] = {
        name: sorted((r.canonical_doc() for r in group), key=canonical_bytes)
        for name, group in rows.items()
    }
    doc["started"] = started.canonical_doc()
    return sha256_digest(canonical_bytes(sorted_keys(doc)))


class Run(Frozen):
    stages: tuple[RunStage, ...] = Field(min_length=1)
    items: tuple[RunItem, ...] = ()
    canaries: tuple[CanaryCall, ...] = ()

    @model_validator(mode="after")
    def _log(self) -> "Run":
        _check_log(
            self.stages, RUN_STAGES, self.stages[0].run, [*self.items, *self.canaries]
        )
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
        return _seal(self.started, items=self.items, canaries=self.canaries)

    def finish(
        self, at: AwareDatetime, findings: Sequence[Finding] = ()
    ) -> RunFinished:
        return RunFinished(
            run=self.started.run, at=at, findings=tuple(findings), seal=self.seal()
        )


class JudgeCall(Frozen):
    judge: JudgeRef
    seed_index: int = Field(ge=0)
    call: Call


class ScoreStarted(Record):
    case_derived: ClassVar[bool] = False
    stage: Literal["started"] = "started"
    score: UUID
    run: UUID
    at: UtcDatetime
    rig: Code
    scorer_code: Code
    scoring: ScoringRef
    execution: Execution = Execution()


class ScoreFinished(Record):
    case_derived: ClassVar[bool] = False
    stage: Literal["finished"] = "finished"
    score: UUID
    at: UtcDatetime
    findings: tuple[Finding, ...] = ()
    seal: Digest


ScoreStage = Annotated[ScoreStarted | ScoreFinished, Field(discriminator="stage")]
SCORE_STAGES = ("started", "finished")


class ScoreItem(Record):
    score: UUID
    key: ItemKey
    view: int = Field(ge=0)
    value: float | None = Field(allow_inf_nan=False)
    detail: JsonValue = None
    judge_calls: tuple[JudgeCall, ...] = ()


class Score(Frozen):
    stages: tuple[ScoreStage, ...] = Field(min_length=1)
    items: tuple[ScoreItem, ...] = ()

    @model_validator(mode="after")
    def _log(self) -> "Score":
        _check_log(self.stages, SCORE_STAGES, self.stages[0].score, self.items)
        return self

    @property
    def started(self) -> ScoreStarted:
        stage = self.stages[0]
        assert isinstance(stage, ScoreStarted)
        return stage

    @property
    def finished(self) -> ScoreFinished | None:
        return next((s for s in self.stages if isinstance(s, ScoreFinished)), None)

    def seal(self) -> str:
        return _seal(self.started, items=self.items)

    def finish(
        self, at: AwareDatetime, findings: Sequence[Finding] = ()
    ) -> ScoreFinished:
        return ScoreFinished(
            score=self.started.score, at=at, findings=tuple(findings), seal=self.seal()
        )


type _Row = (
    RunStarted
    | RunFinished
    | RunItem
    | CanaryCall
    | ScoreStarted
    | ScoreFinished
    | ScoreItem
)


def _log_id(row: _Row) -> UUID:
    match row:
        case ScoreStarted() | ScoreFinished() | ScoreItem():
            return row.score
        case RunStarted() | RunFinished() | RunItem() | CanaryCall():
            return row.run


def _check_log(
    stages: Sequence[RunStarted | RunFinished | ScoreStarted | ScoreFinished],
    order: tuple[str, ...],
    log: UUID,
    rows: Sequence[_Row],
) -> None:
    names = [s.stage for s in stages]
    if names != list(order[: len(names)]):
        raise StructuralError(f"stages {names} do not follow {order}")
    for later in stages[1:]:
        _in_order(stages[0].at, later.at)
    if any(_log_id(r) != log for r in [*stages, *rows]):
        raise StructuralError("rows belong to different logs")


def _sealed(log: Run | Score, subject: str) -> list[Finding]:
    finished = log.finished
    if finished is not None and finished.seal != log.seal():
        return [
            Finding(
                code="ledger.seal",
                message="rows changed after the log was sealed",
                subject=subject,
            )
        ]
    return []


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

    findings = _sealed(run, str(started.run))
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
    engine = registry.get(trial.engine)
    assert isinstance(engine, LocalEngine | RemoteEngine)
    want = engine.served_model_name if isinstance(engine, LocalEngine) else engine.model
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
    return findings + _executed(started.execution, trial, calls, sent)


# `sent` holds when each item was sent: for a run its first call, for a score its first
# judge call.
def _executed(
    execution: Execution,
    trial: Trial,
    calls: Sequence[Call],
    sent: Mapping[ItemKey, datetime],
) -> list[Finding]:
    findings: list[Finding] = []
    allowed = execution.retries + 1
    if over := sum(1 for c in calls if c.attempts > allowed):
        findings.append(
            Finding(
                code="execution.retries",
                message=f"{over} calls took more than the {allowed} attempts allowed",
            )
        )

    position = {
        ItemKey(case=c, replicate=r): n
        for n, (c, r) in enumerate(execution.schedule(trial.cases, len(trial.seeds)))
    }
    early = 0
    latest: datetime | None = None
    for key in sorted(sent, key=position.__getitem__):
        at = sent[key]
        if latest is not None and at < latest:
            early += 1
        latest = at if latest is None else max(latest, at)
    if early:
        findings.append(
            Finding(
                code="execution.order",
                message=f"{early} items were sent before items scheduled ahead of them",
            )
        )

    # Ends sort before starts at the same instant: a call ending as another starts
    # doesn't overlap it.
    events = sorted(
        [(c.started_at, 1) for c in calls] + [(c.finished_at, -1) for c in calls]
    )
    in_flight = peak = 0
    for _, step in events:
        in_flight += step
        peak = max(peak, in_flight)
    if peak > execution.concurrency:
        findings.append(
            Finding(
                code="execution.concurrency",
                message=f"{peak} calls were in flight at once, declared "
                + f"{execution.concurrency}",
            )
        )
    return findings


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


def check_score(score: Score, run: Run, registry: Registry) -> list[Finding]:
    started = score.started
    if started.run != run.started.run:
        raise StructuralError("score does not belong to this run")
    scoring = resolve(registry.get, started.scoring, Scoring)
    scorer = resolve(registry.get, scoring.scorer, Scorer)
    judges = {j: resolve(registry.get, j, Judge) for j in scorer.judges}
    trial = resolve(registry.get, run.started.trial, Trial)
    schema = resolve(registry.get, trial.skeleton, Skeleton).output_schema
    findings = [
        Finding(
            code="view.unreachable",
            message=f"view {i}'s output selector {view.output!r} reaches nothing in "
            + "the run's output schema",
            subject=scoring.scorer,
        )
        for i, view in enumerate(scorer.views)
        if not reaches(schema, view.output)
    ]
    run_keys = {i.key for i in run.items}
    scored = [(i.key, i.view) for i in score.items]
    if len(set(scored)) != len(scored):
        raise StructuralError("duplicate score items")
    for item in score.items:
        if item.view >= len(scorer.views):
            raise StructuralError(f"score item view {item.view} is out of range")
        if item.key not in run_keys:
            raise StructuralError(f"score item {item.key} is not in the run")
        findings.extend(_judged(item, scorer.views[item.view].judge, judges))
    pinned, ran = scorer.code, started.scorer_code
    if (ran.distribution, ran.version) != (pinned.distribution, pinned.version) or (
        pinned.revision is not None and ran.revision != pinned.revision
    ):
        findings.append(
            Finding(
                code="score.scorer_code",
                message=f"the scorer code that ran ({_named(ran)}) isn't the code the "
                + f"scorer pins ({_named(pinned)})",
                subject=str(started.score),
            )
        )
    if (ended := run.finished) is None or ended.at > started.at:
        findings.append(
            Finding(
                code="score.run_unfinished",
                message="the run hasn't finished"
                if ended is None
                else "the score started before the run finished",
                subject=str(started.score),
            )
        )
    findings.extend(_sealed(score, str(started.score)))
    expected = {(k, v) for k in run_keys for v in range(len(scorer.views))}
    if score.finished is not None and (missing := expected - set(scored)):
        findings.append(
            Finding(code="score.incomplete", message=f"{len(missing)} items missing")
        )
    calls = [jc.call for i in score.items for jc in i.judge_calls]
    sent: dict[ItemKey, datetime] = {}
    for item in score.items:
        for jc in item.judge_calls:
            at = jc.call.started_at
            sent[item.key] = min(sent.get(item.key, at), at)
    return findings + _executed(started.execution, trial, calls, sent)


def _named(code: Code) -> str:
    revision = "" if code.revision is None else f" {code.revision}"
    return f"{code.distribution} {code.version}{revision}"


def _judged(
    item: ScoreItem, judge_ref: str | None, judges: dict[str, Judge]
) -> list[Finding]:
    seeds: list[int] = []
    for jc in item.judge_calls:
        if jc.judge != judge_ref:
            raise StructuralError(f"judge {jc.judge} is not view {item.view}'s judge")
        if jc.seed_index >= len(judges[jc.judge].seeds):
            raise StructuralError(f"judge seed index {jc.seed_index} out of range")
        seeds.append(jc.seed_index)
    if len(set(seeds)) != len(seeds):
        raise StructuralError(f"{item.key}: duplicate judge calls for view {item.view}")
    if judge_ref is None or item.value is None:
        return []
    if (called := len(seeds)) < (declared := len(judges[judge_ref].seeds)):
        return [
            Finding(
                code="judge.incomplete",
                message=f"view {item.view}'s value rests on {called} of {declared} "
                + "judge seeds",
                subject=str(item.key),
            )
        ]
    return []
