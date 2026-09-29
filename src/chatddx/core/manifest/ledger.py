from datetime import datetime
from typing import Annotated, ClassVar, Literal, override
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue

from .bundle import Registry
from .cases import CaseInputRef, CaseSet
from .engine import LocalEngine, RemoteEngine
from .identity import (
    Code,
    Digest,
    Finding,
    Frozen,
    Hmac,
    RefTo,
    StructuralError,
    canonical_bytes,
    sha256_digest,
)
from .request import SkeletonRef
from .scoring import Judge, JudgeRef, Scoring, ScoringRef
from .trial import RunPlan, RunPlanRef, Trial


class Record(Frozen):
    case_derived: ClassVar[bool] = True

    def seal(self) -> str:
        return sha256_digest(canonical_bytes(self.model_dump(mode="json")))


class Call(Frozen):
    request: Hmac
    started_at: AwareDatetime
    finished_at: AwareDatetime
    status: int | None = None
    response: dict[str, JsonValue] | None = None
    error: str | None = None

    @property
    def returned_model(self) -> str | None:
        model = (self.response or {}).get("model")
        return model if isinstance(model, str) else None

    @property
    def system_fingerprint(self) -> str | None:
        fp = (self.response or {}).get("system_fingerprint")
        return fp if isinstance(fp, str) else None


class ItemKey(Frozen):
    case: CaseInputRef
    replicate: int = Field(ge=0)

    @override
    def __hash__(self) -> int:
        return hash((self.case, self.replicate))


class RunItem(Frozen):
    key: ItemKey
    vignette: Hmac
    call: Call


class CanaryCall(Frozen):
    phase: Literal["start", "end"]
    probe: int = Field(ge=0)
    call: Call


class RunRecord(Record):
    id: UUID
    plan: RunPlanRef
    rig: Code
    started_at: AwareDatetime
    finished_at: AwareDatetime
    canaries: tuple[CanaryCall, ...] = ()
    items: tuple[RunItem, ...]
    findings: tuple[Finding, ...] = ()


class JudgeCall(Frozen):
    judge: JudgeRef
    seed_index: int = Field(ge=0)
    call: Call


class ScoreItem(Frozen):
    key: ItemKey
    view: str
    value: float | None
    detail: JsonValue = None
    judge_calls: tuple[JudgeCall, ...] = ()


class ScoreRecord(Record):
    id: UUID
    run: UUID
    scoring: ScoringRef
    rig: Code
    scorer_observed: Code
    created_at: AwareDatetime
    items: tuple[ScoreItem, ...]
    findings: tuple[Finding, ...] = ()


class Compilation(Record):
    case_derived: ClassVar[bool] = False
    request: Annotated[Digest, RefTo("request")]
    skeleton: SkeletonRef
    compiler: Code
    compiled_at: datetime


def _trial(run: RunRecord, get: Registry) -> Trial:
    plan = get.get(run.plan)
    assert isinstance(plan, RunPlan)
    trial = get.get(plan.trial)
    assert isinstance(trial, Trial)
    return trial


def check_run(run: RunRecord, registry: Registry) -> list[Finding]:
    trial = _trial(run, registry)
    case_set = registry.get(trial.cases)
    assert isinstance(case_set, CaseSet)
    expected = {
        ItemKey(case=c, replicate=r)
        for c in case_set.cases
        for r in range(len(trial.seeds))
    }
    keys = [i.key for i in run.items]
    if stray := set(keys) - expected:
        raise StructuralError(f"run items outside the trial: {sorted(map(str, stray))}")
    if len(set(keys)) != len(keys):
        raise StructuralError("duplicate run items")

    findings: list[Finding] = []
    if missing := expected - set(keys):
        findings.append(
            Finding(code="run.incomplete", message=f"{len(missing)} items missing")
        )
    engine = registry.get(trial.engine)
    assert isinstance(engine, LocalEngine | RemoteEngine)
    want = engine.served_model_name if isinstance(engine, LocalEngine) else engine.model
    for item in run.items:
        got = item.call.returned_model
        if got is not None and got != want:
            findings.append(
                Finding(
                    code="attestation.model",
                    message=f"engine returned model {got!r}, declared {want!r}",
                    subject=str(item.key),
                )
            )
    return findings


def check_score(
    score: ScoreRecord, run: RunRecord, registry: Registry
) -> list[Finding]:
    if score.run != run.id:
        raise StructuralError("score does not belong to this run")
    scoring = registry.get(score.scoring)
    assert isinstance(scoring, Scoring)
    run_keys = {i.key for i in run.items}
    judges = {j: registry.get(j) for j in scoring.judges}
    for item in score.items:
        if item.key not in run_keys:
            raise StructuralError(f"score item {item.key} is not in the run")
        for jc in item.judge_calls:
            judge = judges.get(jc.judge)
            if judge is None:
                raise StructuralError(f"judge {jc.judge} is not part of the scoring")
            assert isinstance(judge, Judge)
            if jc.seed_index >= len(judge.seeds):
                raise StructuralError(f"judge seed index {jc.seed_index} out of range")
    return []
