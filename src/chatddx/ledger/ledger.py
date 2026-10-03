import json
from collections.abc import Iterable, Sequence
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
    resolve,
    sha256_digest,
)
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import CaseInputRef
from chatddx.factors.engine import LocalEngine, RemoteEngine
from chatddx.factors.request import Recipe, SkeletonRef
from chatddx.factors.scoring import Judge, JudgeRef, Scorer, Scoring, ScoringRef
from chatddx.factors.trial import (
    CanarySet,
    CanarySetRef,
    Execution,
    Trial,
    TrialRef,
)

Phase = Literal["start", "end"]


def _utc(at: datetime) -> datetime:
    return at.astimezone(UTC)


# timestamptz reads back in UTC, and the offset is part of the sealed bytes.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_utc)]


class Record(Frozen):
    case_derived: ClassVar[bool] = True
    # Bump when an existing field's meaning or default changes; additive fields don't.
    schema_version: ClassVar[int] = 1

    def canonical_doc(self) -> dict[str, JsonValue]:
        doc = cast(
            dict[str, JsonValue],
            self.model_dump(mode="json", context={"canonical": True}),
        )
        doc["v"] = type(self).schema_version
        return doc

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
    started_at: UtcDatetime
    finished_at: UtcDatetime
    status: int | None = None
    response: dict[str, JsonValue] | None = None
    prompt_tokens: Fingerprint | None = None
    attempts: int = Field(default=1, ge=1)
    error: str | None = None

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
    return Fingerprint.of(canonical_bytes(body), key)


class ItemKey(Frozen):
    case: CaseInputRef
    replicate: int = Field(ge=0)

    @override
    def __hash__(self) -> int:
        return hash((self.case, self.replicate))


# Runs and scores are append-only: one row per stage, plus item rows.


class RunStarted(Record):
    stage: Literal["started"] = "started"
    run: UUID
    at: UtcDatetime
    rig: Code
    trial: TrialRef
    execution: Execution = Execution()
    canaries: CanarySetRef | None = None
    verify_at: tuple[Phase, ...] = ("start", "end")


class RunFinished(Record):
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


class CanaryCall(Record):
    run: UUID
    phase: Phase
    probe: int = Field(ge=0)
    call: Call


# Canonical rows keep old seals valid when a defaulted field is added, and sorting
# makes the seal independent of the order storage returns rows in.
def _seal(started: Record, **rows: Iterable[Record]) -> str:
    doc: dict[str, JsonValue] = {
        name: sorted((r.canonical_doc() for r in group), key=canonical_bytes)
        for name, group in rows.items()
    }
    doc["started"] = started.canonical_doc()
    return sha256_digest(canonical_bytes(doc))


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
    stage: Literal["started"] = "started"
    score: UUID
    run: UUID
    at: UtcDatetime
    rig: Code
    scorer_code: Code
    scoring: ScoringRef


class ScoreFinished(Record):
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
    value: float | None
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
    if any(_log_id(r) != log for r in [*stages, *rows]):
        raise StructuralError("rows belong to different logs")


class Compilation(Record):
    case_derived: ClassVar[bool] = False
    recipe: Recipe
    skeleton: SkeletonRef
    compiler: Code
    at: UtcDatetime


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
    if started.canaries is not None:
        canaries = resolve(registry.get, started.canaries, CanarySet)
        for c in run.canaries:
            if c.phase not in started.verify_at or c.probe >= len(canaries.probes):
                raise StructuralError(f"canary call {c.phase}/{c.probe} is not planned")
    elif run.canaries:
        raise StructuralError("canary calls in a run without canaries")

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
    if run.finished is not None and (missing := expected - set(keys)):
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
    if unfingerprinted := sum(1 for i in run.items if i.call.prompt_tokens is None):
        findings.append(
            Finding(
                code="attestation.prompt_tokens",
                message=f"{unfingerprinted} items have no prompt token fingerprint",
            )
        )
    return findings


def compare_prompt_tokens(a: Run, b: Run) -> list[Finding]:
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
    run_keys = {i.key for i in run.items}
    for item in score.items:
        if item.view >= len(scorer.views):
            raise StructuralError(f"score item view {item.view} is out of range")
        if item.key not in run_keys:
            raise StructuralError(f"score item {item.key} is not in the run")
        for jc in item.judge_calls:
            judge = judges.get(jc.judge)
            if judge is None:
                raise StructuralError(f"judge {jc.judge} is not part of the scoring")
            if jc.seed_index >= len(judge.seeds):
                raise StructuralError(f"judge seed index {jc.seed_index} out of range")
    return _sealed(score, str(started.score))
