"""Transient titles for unnamed things: built from what they hold, never stored."""

import json
import re
from collections.abc import Callable, Iterable

from pydantic import JsonValue

from chatddx.factors.base import Component
from chatddx.factors.cases import Appendix, Case
from chatddx.factors.engine import LocalEngine, ModelArtifact, RemoteEngine
from chatddx.factors.request import (
    FewShot,
    Insert,
    Instructions,
    Output,
    Passthrough,
    Prompt,
    Reasoning,
    Recipe,
    Sampling,
    Skeleton,
    Slot,
    Tool,
    Toolset,
    Translations,
)
from chatddx.factors.scoring import (
    Expectation,
    ExpectationSchema,
    Judge,
    Scorer,
    Scoring,
)
from chatddx.factors.trial import CanarySet, Trial

Title = Callable[[str], str]

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAMPLED = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "presence_penalty",
    "frequency_penalty",
    "repetition_penalty",
    "max_output_tokens",
)


def short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:6]


def snippet(text: str, limit: int = 40) -> str:
    words = " ".join(text.split())
    if len(words) > limit:
        words = words[: limit + 1].rsplit(" ", 1)[0] + "…"
    return f'"{words}"'


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _text(parts: str | Iterable[str | Slot | Insert]) -> str:
    if isinstance(parts, str):
        return parts
    return "".join(
        p
        if isinstance(p, str)
        else "{" + (p.slot if isinstance(p, Slot) else p.insert) + "}"
        for p in parts
    )


def _value(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _schema(schema: dict[str, JsonValue] | None) -> str | None:
    match schema:
        case {"title": str() as title}:
            return title
        case {"properties": dict() as properties} if properties:
            return ", ".join(properties)
        case _:
            return None


def describe_recipe(recipe: Recipe, title: Title) -> str:
    parts = (
        recipe.instructions,
        recipe.few_shot,
        recipe.prompt,
        recipe.output,
        recipe.sampling,
        recipe.reasoning,
        recipe.passthrough,
        recipe.translations,
        recipe.toolset,
    )
    described = " · ".join(title(p) for p in parts if p is not None)
    return described if recipe.purpose == "generation" else f"judge: {described}"


def describe_change(path: str, after: JsonValue, title: Title) -> str:
    label = path.removeprefix("/recipe/").removeprefix("/")
    match after:
        case None:
            return f"no {label}"
        case str() if _DIGEST.match(after):
            return f"{label}: {title(after)}"
        case str():
            return f"{label}: {snippet(after)}"
        case _:
            return f"{label}: {_value(after)}"


def describe(component: Component, title: Title) -> str:
    match component:
        case Instructions(text=text):
            return snippet(_text(text))
        case FewShot(messages=messages):
            return f"few-shot, {_count(len(messages), 'message')}"
        case Prompt(segments=segments):
            return snippet(_text(segments))
        case Output(contract=contract, json_schema=schema):
            kind = f"tool {contract.name}" if contract.kind == "tool" else contract.kind
            described = _schema(schema)
            return f"{kind} output" + ("" if described is None else f": {described}")
        case Sampling():
            values = component.model_dump(include=set(_SAMPLED), exclude_none=True)
            if not values:
                return "default sampling"
            return ", ".join(f"{k} {_value(v)}" for k, v in values.items())
        case Reasoning(effort=effort, thinking_token_budget=budget):
            parts = [] if effort is None else [f"effort {effort}"]
            parts += [
                f"{k} {_value(v)}" for k, v in component.chat_template_kwargs.items()
            ]
            parts += [] if budget is None else [f"budget {budget}"]
            return ", ".join(parts) or "default reasoning"
        case Tool(name=name):
            return name
        case Toolset(tools=tools):
            return "with " + ", ".join(title(t) for t in tools)
        case Translations(entries=entries):
            return f"translations, {_count(len(entries), 'text')}"
        case Passthrough(body=body):
            return "passthrough: " + ", ".join(body)
        case Skeleton(purpose=purpose):
            return f"{purpose} skeleton {short(component.digest)}"
        case Trial(skeleton=skeleton, engine=engine, cases=cases, seeds=seeds):
            return (
                f"{title(skeleton)} on {title(engine)}, "
                + f"{_count(len(cases), 'case')}, {_count(len(seeds), 'seed')}"
            )
        case Judge(skeleton=skeleton, engine=engine):
            return f"judge {title(skeleton)} on {title(engine)}"
        case Scorer(code=code):
            return f"scorer {code.distribution} {code.version}"
        case Scoring(scorer=scorer, expectations=expectations):
            return f"{title(scorer)} against {_count(len(expectations), 'expectation')}"
        case Appendix(text=text):
            return snippet(text)
        case Expectation(case=case):
            return f"expectation for {title(case)}"
        case Case(vignette=vignette, appendices=appendices):
            described = f"{vignette.source}/{vignette.id}"
            if appendices:
                described += (
                    f" with {_count(len(appendices), 'appendix', 'appendices')}"
                )
            return described
        case ModelArtifact(repo=repo):
            return repo
        case LocalEngine(model=model, runtime=runtime, hardware=hardware):
            runs = f"{runtime.server} {runtime.version} ({hardware.gpu})"
            return f"{title(model)} on {runs}"
        case RemoteEngine(model=model, base_url=url):
            return f"{model} at {url.host}"
        case ExpectationSchema(json_schema=schema):
            return _schema(schema) or f"expectation schema {short(component.digest)}"
        case CanarySet(canaries=canaries):
            return _count(len(canaries), "canary", "canaries")
        case _:
            return f"{component.kind_name} {short(component.digest)}"
