from typing import Annotated, Literal, override

from pydantic import Field, JsonValue, field_validator

from .cases import CaseSetRef
from .engine import EngineRef
from .identity import Component, Digest, Frozen, RefTo, Resolver
from .request import RUNTIME_KEYS, Skeleton, SkeletonRef

# How the runner walks (case, replicate) pairs; matters only where batching is variant.
Order = Literal["case_major@1", "replicate_major@1"]


class Trial(Component):
    kind: Literal["trial"] = "trial"
    skeleton: SkeletonRef
    engine: EngineRef
    cases: CaseSetRef
    seeds: tuple[int, ...] = Field(min_length=1)
    order: Order = "case_major@1"
    concurrency: int = Field(default=1, ge=1)

    @field_validator("seeds")
    @classmethod
    def _unique(cls, seeds: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(seeds)) != len(seeds):
            raise ValueError("duplicate seeds")
        return seeds

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        skeleton = get(self.skeleton)
        assert isinstance(skeleton, Skeleton)
        if skeleton.purpose != "generation":
            return [f"trial uses a {skeleton.purpose} skeleton"]
        return []


TrialRef = Annotated[Digest, RefTo("trial")]


class Canary(Frozen):
    messages: tuple[dict[str, JsonValue], ...] = Field(min_length=1)
    body: dict[str, JsonValue] = Field(default_factory=dict)
    seed: int | None = None

    @field_validator("body")
    @classmethod
    def _no_runtime_keys(cls, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if owned := body.keys() & RUNTIME_KEYS:
            raise ValueError(f"canary body may not set {sorted(owned)}")
        return body


class CanarySet(Component):
    kind: Literal["canary_set"] = "canary_set"
    probes: tuple[Canary, ...] = Field(min_length=1)


CanarySetRef = Annotated[Digest, RefTo("canary_set")]


class Verification(Component):
    kind: Literal["verification"] = "verification"
    canaries: CanarySetRef
    at: tuple[Literal["start", "end"], ...] = ("start", "end")


VerificationRef = Annotated[Digest, RefTo("verification")]


class RunPlan(Component):
    kind: Literal["run_plan"] = "run_plan"
    trial: TrialRef
    verification: VerificationRef | None = None


RunPlanRef = Annotated[Digest, RefTo("run_plan")]
