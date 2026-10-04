from typing import Annotated, Literal, override

from pydantic import AfterValidator, Field, JsonValue

from .base import (
    Code,
    Component,
    Digest,
    Frozen,
    RefTo,
    Resolver,
    Settings,
    distinct,
)
from .cases import CaseInputRef
from .engine import EngineRef
from .request import Skeleton, SkeletonRef
from .select import SplitOp, check_selector, select, split


class ExpectationSchema(Component):
    kind: Literal["expectation_schema"] = "expectation_schema"
    json_schema: dict[str, JsonValue]


ExpectationSchemaRef = Annotated[Digest, RefTo("expectation_schema")]


class Expectation(Component):
    kind: Literal["expectation"] = "expectation"
    case: CaseInputRef
    json_schema: ExpectationSchemaRef
    data: JsonValue


ExpectationRef = Annotated[Digest, RefTo("expectation")]


class Judge(Component):
    kind: Literal["judge"] = "judge"
    skeleton: SkeletonRef
    engine: EngineRef
    seeds: Annotated[tuple[int, ...], AfterValidator(distinct)] = Field(min_length=1)

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        skeleton = get(self.skeleton)
        assert isinstance(skeleton, Skeleton)
        if skeleton.purpose != "judge":
            return [f"judge uses a {skeleton.purpose} skeleton"]
        return []


JudgeRef = Annotated[Digest, RefTo("judge")]


# A JSON Pointer or a JSONPath query from the subset in select.py.
Selector = Annotated[str, AfterValidator(check_selector)]


class View(Frozen):
    output: Selector = ""
    expectation: Selector = ""
    split: SplitOp | None = None
    metric: str
    judge: JudgeRef | None = None
    params: Settings = Field(default_factory=dict)

    def output_items(self, answer: JsonValue) -> list[JsonValue]:
        items = select(answer, self.output)
        return items if self.split is None else split(self.split, items)

    def expectation_items(self, data: JsonValue) -> list[JsonValue]:
        return select(data, self.expectation)


class Scorer(Component):
    kind: Literal["scorer"] = "scorer"
    code: Code
    consumes: ExpectationSchemaRef
    views: tuple[View, ...] = Field(min_length=1)
    resources: tuple[str, ...] = ()
    params: Settings = Field(default_factory=dict)

    @property
    def judges(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(v.judge for v in self.views if v.judge is not None))


ScorerRef = Annotated[Digest, RefTo("scorer")]


class Scoring(Component):
    kind: Literal["scoring"] = "scoring"
    scorer: ScorerRef
    expectations: tuple[ExpectationRef, ...] = Field(min_length=1)

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        scorer = get(self.scorer)
        assert isinstance(scorer, Scorer)
        problems: list[str] = []
        cases: set[str] = set()
        for ref in self.expectations:
            e = get(ref)
            assert isinstance(e, Expectation)
            if e.json_schema != scorer.consumes:
                problems.append(f"expectation {ref} is not in the scorer's schema")
            if e.case in cases:
                problems.append(f"case {e.case} has more than one expectation")
            cases.add(e.case)
        return problems


ScoringRef = Annotated[Digest, RefTo("scoring")]
