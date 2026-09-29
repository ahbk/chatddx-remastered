from typing import Annotated, Literal, override

from pydantic import Field, JsonValue, field_validator

from .cases import CaseInputRef
from .engine import EngineRef
from .identity import Code, Component, Digest, Frozen, JsonPointer, RefTo, Resolver
from .request import Skeleton, SkeletonRef


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


class ExpectationSet(Component):
    kind: Literal["expectation_set"] = "expectation_set"
    json_schema: ExpectationSchemaRef
    items: tuple[ExpectationRef, ...] = Field(min_length=1)

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        problems: list[str] = []
        cases: set[str] = set()
        for ref in self.items:
            e = get(ref)
            assert isinstance(e, Expectation)
            if e.json_schema != self.json_schema:
                problems.append(f"expectation {ref} uses another schema")
            if e.case in cases:
                problems.append(f"case {e.case} has more than one expectation")
            cases.add(e.case)
        return problems


ExpectationSetRef = Annotated[Digest, RefTo("expectation_set")]


class Judge(Component):
    kind: Literal["judge"] = "judge"
    skeleton: SkeletonRef
    engine: EngineRef
    seeds: tuple[int, ...] = Field(min_length=1)

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        skeleton = get(self.skeleton)
        assert isinstance(skeleton, Skeleton)
        if skeleton.purpose != "judge":
            return [f"judge uses a {skeleton.purpose} skeleton"]
        return []


JudgeRef = Annotated[Digest, RefTo("judge")]


class View(Frozen):
    name: str
    output: JsonPointer = ""
    expectation: JsonPointer = ""
    metric: str
    judge: JudgeRef | None = None
    params: dict[str, JsonValue] = Field(default_factory=dict)


class Scorer(Component):
    kind: Literal["scorer"] = "scorer"
    code: Code
    consumes: ExpectationSchemaRef
    views: tuple[View, ...] = Field(min_length=1)
    resources: dict[str, str] = Field(default_factory=dict)
    params: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def judges(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(v.judge for v in self.views if v.judge is not None))

    @field_validator("views")
    @classmethod
    def _unique_names(cls, views: tuple[View, ...]) -> tuple[View, ...]:
        names = [v.name for v in views]
        if len(set(names)) != len(names):
            raise ValueError("duplicate view names")
        return views


ScorerRef = Annotated[Digest, RefTo("scorer")]


class Scoring(Component):
    kind: Literal["scoring"] = "scoring"
    scorer: ScorerRef
    expectations: ExpectationSetRef

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        scorer = get(self.scorer)
        expectations = get(self.expectations)
        assert isinstance(scorer, Scorer)
        assert isinstance(expectations, ExpectationSet)
        if scorer.consumes != expectations.json_schema:
            return [
                "scorer consumes a different expectation schema than the set provides"
            ]
        return []


ScoringRef = Annotated[Digest, RefTo("scoring")]
