from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from chatddx.factors.base import Code, Digest, Finding, Frozen, StructuralError, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.request import Skeleton
from chatddx.factors.scoring import Judge, JudgeRef, Scorer, Scoring, ScoringRef
from chatddx.factors.select import reaches
from chatddx.factors.trial import Execution, Trial

from .call import Call, check_execution
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
from .run import Run


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
        check_log(
            self.stages, SCORE_STAGES, [r.score for r in (*self.stages, *self.items)]
        )
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
        return seal_rows(self.started, items=self.items)

    def finish(
        self, at: AwareDatetime, findings: Sequence[Finding] = ()
    ) -> ScoreFinished:
        return ScoreFinished(
            score=self.started.score, at=at, findings=tuple(findings), seal=self.seal()
        )


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
    if not ran_pinned(pinned, ran):
        findings.append(
            Finding(
                code="score.scorer_code",
                message=f"the scorer code that ran ({code_name(ran)}) isn't the code "
                + f"the scorer pins ({code_name(pinned)})",
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
    findings.extend(check_seal(score, str(started.score)))
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
    return findings + check_execution(started.execution, trial, calls, sent)


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
