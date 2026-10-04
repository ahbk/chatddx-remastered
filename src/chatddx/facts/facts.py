import tomllib
from pathlib import Path
from typing import Annotated, Literal, Self, get_args

from pydantic import Field, JsonValue, StringConstraints, model_validator

from chatddx.factors.base import Component, Frozen, Settings, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.engine import LocalEngine, ModelArtifact, RemoteEngine
from chatddx.factors.request import Reasoning, Sampling

Intent = Literal["off", "on", "minimal", "low", "medium", "high", "xhigh"]
Effort = Intent | Literal["default"]
INTENTS: tuple[Intent, ...] = get_args(Intent)


class Refused(ValueError):
    pass


class Refusal(Frozen):
    refused: Annotated[str, StringConstraints(min_length=1)]


class Writes(Frozen):
    effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    chat_template_kwargs: Settings = Field(default_factory=dict)


ReasoningFact = Intent | Refusal | Writes


class ReasoningFacts(Frozen):
    default: Intent | None = None
    off: ReasoningFact | None = None
    on: ReasoningFact | None = None
    minimal: ReasoningFact | None = None
    low: ReasoningFact | None = None
    medium: ReasoningFact | None = None
    high: ReasoningFact | None = None
    xhigh: ReasoningFact | None = None
    budget: Literal["thinking_token_budget"] | Refusal | None = None

    def fact(self, intent: Intent) -> ReasoningFact | None:
        fact: ReasoningFact | None = getattr(self, intent)
        return fact

    def land(self, intent: Intent) -> tuple[Intent, Refusal | Writes | None]:
        path: list[Intent] = []
        while True:
            if intent in path:
                raise ValueError(
                    f"{' -> '.join([*path, intent])}: the collapses go round"
                )
            path.append(intent)
            fact = self.fact(intent)
            if not isinstance(fact, str):
                return intent, fact
            intent = fact

    def resolve(self, effort: Effort) -> tuple[Intent, Writes]:
        intent = self.default if effort == "default" else effort
        if intent is None:
            raise Refused("no default reasoning level is known")
        landed, fact = self.land(intent)
        match fact:
            case None:
                raise Refused(f"nothing is known about reasoning {landed!r}")
            case Refusal(refused=reason):
                raise Refused(reason)
            case Writes():
                return landed, fact

    def realized(self) -> dict[Intent, Writes]:
        return {i: f for i in INTENTS if isinstance(f := self.fact(i), Writes)}

    @model_validator(mode="after")
    def _collapses_land(self) -> Self:
        collapses: dict[str, Intent] = {
            i: f for i in INTENTS if isinstance(f := self.fact(i), str)
        }
        if self.default is not None:
            collapses["default"] = self.default
        for name, target in collapses.items():
            if self.land(target)[1] is None:
                raise ValueError(
                    f"{name!r} collapses into {target!r}, which ends in no fact"
                )
        return self


class SamplingValues(Frozen):
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    repetition_penalty: float | None = None


class SamplingFacts(Frozen):
    recommended: dict[Intent, SamplingValues] = Field(default_factory=dict)
    generation_config: SamplingValues | None = None


class ContractFact(Frozen):
    note: str | None = None


class OutputFacts(Frozen):
    default: Literal["native", "tool", "text"] | None = None
    native: ContractFact | Refusal | None = None
    tool: ContractFact | Refusal | None = None
    text: ContractFact | Refusal | None = None

    def fact(
        self, kind: Literal["native", "tool", "text"]
    ) -> ContractFact | Refusal | None:
        fact: ContractFact | Refusal | None = getattr(self, kind)
        return fact


class ModelFacts(Frozen):
    family: str | None = None
    parameters_b: float | None = None
    active_parameters_b: float | None = None
    quantization: str | None = None
    context_length: int | None = None
    licence: str | None = None
    reasoning: ReasoningFacts = ReasoningFacts()
    sampling: SamplingFacts = SamplingFacts()
    output: OutputFacts = OutputFacts()

    def reasoning_chunk(
        self, effort: Effort = "default", budget: int | None = None
    ) -> Reasoning:
        _, writes = self.reasoning.resolve(effort)
        if budget is not None:
            match self.reasoning.budget:
                case None:
                    raise Refused("nothing is known about a thinking budget")
                case Refusal(refused=reason):
                    raise Refused(reason)
                case _:
                    pass
        return Reasoning(
            effort=writes.effort,
            chat_template_kwargs=writes.chat_template_kwargs,
            thinking_token_budget=budget,
        )

    def sampling_chunk(
        self, effort: Effort = "default", **overrides: JsonValue
    ) -> Sampling:
        intent, _ = self.reasoning.resolve(effort)
        values = self.sampling.recommended.get(intent)
        if values is None:
            raise Refused(f"no sampling is recommended for reasoning {intent!r}")
        return Sampling.model_validate(
            {**values.model_dump(exclude_none=True), **overrides}
        )

    def reasons_by_default(self) -> bool | None:
        try:
            intent, _ = self.reasoning.resolve("default")
        except Refused:
            return None
        return intent != "off"


def model_name(engine: Component, registry: Registry) -> str | None:
    match engine:
        case LocalEngine():
            return resolve(registry.get, engine.model, ModelArtifact).repo
        case RemoteEngine():
            return engine.model
        case _:
            return None


class Facts(Frozen):
    models: dict[str, ModelFacts] = Field(default_factory=dict)

    @classmethod
    def load(cls, *paths: Path) -> Self:
        models: dict[str, object] = {}
        for path in paths:
            with path.open("rb") as f:
                data = tomllib.load(f)
            if set(data) - {"model"}:
                raise ValueError(f"{path}: facts have only 'model' tables")
            for name, values in data.get("model", {}).items():
                if name in models:
                    raise ValueError(f"{name!r} has facts in more than one file")
                models[name] = values
        return cls.model_validate({"models": models})

    def about(self, engine: Component, registry: Registry) -> ModelFacts | None:
        name = model_name(engine, registry)
        return None if name is None else self.models.get(name)
