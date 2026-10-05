import json
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue, ValidationError

from chatddx.factors.base import Code, Component
from chatddx.factors.bundle import Registry
from chatddx.factors.request import (
    FewShot,
    Instructions,
    Output,
    Passthrough,
    Prompt,
    Reasoning,
    Recipe,
    Sampling,
    Tool,
    Toolset,
    Translations,
    compile_request,
)
from chatddx.factors.scoring import ExpectationSchema
from chatddx.facts.facts import INTENTS, Effort, Facts, Refused
from chatddx.ledger.ledger import Compilation

# The sample's tables, in the order they're planned: a table may reference only those before it.
TABLES: dict[str, type[Component]] = {
    "instructions": Instructions,
    "few_shot": FewShot,
    "prompt": Prompt,
    "output": Output,
    "sampling": Sampling,
    "reasoning": Reasoning,
    "passthrough": Passthrough,
    "translations": Translations,
    "tool": Tool,
    "toolset": Toolset,
    "expectation_schema": ExpectationSchema,
}
# A recipe's references, by field; each names a record of the table of the same name.
RECIPE_PARTS = (
    "instructions",
    "few_shot",
    "prompt",
    "output",
    "sampling",
    "reasoning",
    "passthrough",
    "translations",
    "toolset",
)
SAMPLE = Path(__file__).parent.parent / "data" / "sample"


@dataclass(frozen=True)
class Planned:
    table: str
    name: str
    kind: str
    digest: str
    tags: tuple[str, ...] = ()
    description: str | None = None
    fork_of: str | None = None
    compilation: Compilation | None = None


@dataclass
class Plan:
    registry: Registry
    records: list[Planned] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def named(self, table: str, name: str) -> Planned:
        for r in self.records:
            if (r.table, r.name) == (table, name):
                return r
        raise LookupError(f"no {table} named {name!r} in the plan")


# A record per model, or one for all (None).
Variants = dict[str | None, Planned]


def _named(name: str, model: str | None) -> str:
    return name if model is None else f"{name} ({model})"


def _with_files(node: JsonValue, root: Path) -> JsonValue:
    match node:
        case dict():
            out: dict[str, JsonValue] = {}
            for k, v in node.items():
                if k == "json_schema_path":
                    assert isinstance(v, str)
                    with (root / v).open() as f:
                        out["json_schema"] = json.load(f)
                else:
                    out[k] = _with_files(v, root)
            return out
        case list():
            return [_with_files(v, root) for v in node]
        case _:
            return node


def _component(
    table: str, body: dict[str, JsonValue], model: str | None, facts: Facts
) -> Component:
    from_facts = body.pop("from_facts", None)
    if from_facts is None:
        return TABLES[table].model_validate(body)
    assert model is not None and isinstance(from_facts, dict)
    model_facts = facts.models[model]
    options = dict(from_facts)
    effort = options.pop("effort", "default")
    if effort not in (*INTENTS, "default"):
        raise ValueError(f"{table}: {effort!r} is no reasoning level")
    level: Effort = effort
    match table:
        case "sampling":
            return model_facts.sampling_chunk(level, **options, **body)
        case "reasoning":
            budget = options.pop("budget", None)
            if options or body or not (budget is None or isinstance(budget, int)):
                raise ValueError(
                    f"{table}: from_facts takes an effort and a budget only"
                )
            return model_facts.reasoning_chunk(level, budget=budget)
        case _:
            raise ValueError(f"{table} records can't come from facts")


def _tags(value: JsonValue) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError(f"tags must be a list of strings, not {value!r}")
    return tuple(str(t) for t in value)


def plan_factors(
    path: Path, facts: Facts, compiler: Code, at: datetime, root: Path | None = None
) -> Plan:
    with path.open("rb") as f:
        data = tomllib.load(f)
    if stray := set(data) - set(TABLES) - {"recipe"}:
        raise ValueError(f"{path}: unknown tables {sorted(stray)}")
    root = root or path.parent
    plan = Plan(Registry())
    planned: dict[tuple[str, str], Variants] = {}
    models = sorted(facts.models)

    def add(table: str, name: str, variants: Variants) -> None:
        planned[(table, name)] = variants
        plan.records.extend(variants.values())

    def base(table: str, fork_of: object, model: str | None) -> str | None:
        if fork_of is None:
            return None
        assert isinstance(fork_of, str)
        variants = planned.get((table, fork_of))
        if variants is None:
            raise ValueError(f"{table}.{fork_of} must come before its forks")
        found = variants.get(model) or variants.get(None)
        return None if found is None else found.name

    for table in TABLES:
        for name, raw in data.get(table, {}).items():
            body = _with_files(raw, root)
            assert isinstance(body, dict)
            tags = _tags(body.pop("tags", []))
            description = body.pop("description", None)
            fork_of = body.pop("fork_of", None)
            if table == "toolset" and isinstance(tools := body.get("tools"), list):
                body["tools"] = [plan.named("tool", str(t)).digest for t in tools]
            variants: Variants = {}
            for model in models if "from_facts" in body else [None]:
                try:
                    component = _component(table, dict(body), model, facts)
                except Refused as e:
                    plan.skipped.append(f"{table} {_named(name, model)}: {e}")
                    continue
                except ValidationError as e:
                    raise ValueError(f"{table}.{name}: {e}") from None
                variants[model] = Planned(
                    table=table,
                    name=_named(name, model),
                    kind=component.kind_name,
                    digest=plan.registry.add(component),
                    tags=tags,
                    description=None if description is None else str(description),
                    fork_of=base(table, fork_of, model),
                )
            add(table, name, variants)

    for name, raw in data.get("recipe", {}).items():
        body = dict(raw)
        tags = _tags(body.pop("tags", []))
        description = body.pop("description", None)
        fork_of = body.pop("fork_of", None)
        parts = {p: planned[(p, body.pop(p))] for p in RECIPE_PARTS if p in body}
        per_model = any(None not in v for v in parts.values())
        variants = {}
        for model in models if per_model else [None]:
            refs = {p: v.get(model) or v.get(None) for p, v in parts.items()}
            if missing := sorted(p for p, r in refs.items() if r is None):
                plan.skipped.append(
                    f"recipe {_named(name, model)}: the facts refuse its {', '.join(missing)}"
                )
                continue
            digests = {p: r.digest for p, r in refs.items() if r is not None}
            try:
                recipe = Recipe.model_validate({**body, **digests})
            except ValidationError as e:
                raise ValueError(f"recipe.{name}: {e}") from None
            skeleton = plan.registry.add(compile_request(recipe, plan.registry.get))
            variants[model] = Planned(
                table="recipe",
                name=_named(name, model),
                kind="skeleton",
                digest=skeleton,
                tags=tags,
                description=None if description is None else str(description),
                fork_of=base("recipe", fork_of, model),
                compilation=Compilation(
                    recipe=recipe, skeleton=skeleton, compiler=compiler, at=at
                ),
            )
        add("recipe", name, variants)
    return plan


@dataclass(frozen=True)
class SampleCase:
    tags: tuple[str, ...]
    language: str | None
    targets: JsonValue


def load_cases(path: Path) -> dict[str, SampleCase]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    if set(data) - {"case"}:
        raise ValueError(f"{path}: the sample's cases have only 'case' tables")
    cases: dict[str, SampleCase] = {}
    for id, case in data.get("case", {}).items():
        if stray := set(case) - {"tags", "language", "targets"}:
            raise ValueError(f"case.{id}: unknown keys {sorted(stray)}")
        cases[id] = SampleCase(
            tags=tuple(case.get("tags", ())),
            language=case.get("language"),
            targets=case.get("targets"),
        )
    return cases
