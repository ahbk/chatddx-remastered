import json
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import JsonValue, ValidationError

from chatddx.core.rig import entry
from chatddx.factors.base import Code, Component
from chatddx.factors.bundle import Registry
from chatddx.factors.engine import LocalEngine, ModelArtifact, RemoteEngine
from chatddx.factors.request import (
    Compilation,
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
from chatddx.factors.scoring import ExpectationSchema, Scorer
from chatddx.facts.facts import INTENTS, Effort, Facts, Refused
from chatddx.scorers.scorer import function

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
    "scorer": Scorer,
    "model": ModelArtifact,
    "local_engine": LocalEngine,
    "remote_engine": RemoteEngine,
}
# References outside recipes, by table and field: each names records of another table.
REFERENCES: dict[str, dict[str, str]] = {
    "toolset": {"tools": "tool"},
    "scorer": {"expectation_schema": "expectation_schema"},
    "local_engine": {"model": "model"},
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
    compilation: str | None = None
    # a scorer's view labels, by position
    labels: tuple[str | None, ...] = ()


@dataclass
class Plan:
    registry: Registry
    facts: Facts
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


def _labels(table: str, body: dict[str, JsonValue]) -> tuple[str | None, ...]:
    views = body.get("views")
    if table != "scorer" or not isinstance(views, list):
        return ()
    labels: list[str | None] = []
    for view in views:
        label = view.pop("label", None) if isinstance(view, dict) else None
        if label is not None and not isinstance(label, str):
            raise ValueError(f"a view's label must be a string, not {label!r}")
        labels.append(label)
    return tuple(labels)


def _check_runs(component: Component) -> None:
    match component:
        case Tool():
            if not callable(entry(component.code, component.entry_point)):
                raise TypeError(f"{component.entry_point} isn't a function")
        case Scorer():
            _ = function(component)
        case _:
            pass


def _tags(value: JsonValue) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError(f"tags must be a list of strings, not {value!r}")
    return tuple(str(t) for t in value)


def _load(path: Path, root: Path) -> dict[str, dict[str, JsonValue]]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    if stray := set(data) - set(TABLES) - {"recipe"}:
        raise ValueError(f"{path}: unknown tables {sorted(stray)}")
    return {
        table: {name: _with_files(raw, root) for name, raw in records.items()}
        for table, records in data.items()
    }


# `more` are factors files planned with the first, such as a World's imported engines;
# each resolves its files against its own directory.
def plan_factors(
    path: Path,
    facts: Facts,
    compiler: Code,
    root: Path | None = None,
    more: Sequence[Path] = (),
) -> Plan:
    data = _load(path, root or path.parent)
    for extra in more:
        for table, records in _load(extra, extra.parent).items():
            known = data.setdefault(table, {})
            if twice := sorted(set(known) & set(records)):
                raise ValueError(f"{extra}: {table} {twice} are named before it too")
            known.update(records)
    plan = Plan(Registry(), facts)
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

    for table, kind in TABLES.items():
        for name, raw in data.get(table, {}).items():
            assert isinstance(raw, dict)
            body = dict(raw)
            tags = _tags(body.pop("tags", []))
            # A tool's description is a field, what the model reads; it's the entry too.
            if "description" in kind.model_fields:
                description = body.get("description")
            else:
                description = body.pop("description", None)
            fork_of = body.pop("fork_of", None)
            for key, target in REFERENCES.get(table, {}).items():
                match body.get(key):
                    case str() as named:
                        body[key] = plan.named(target, named).digest
                    case list() as names:
                        body[key] = [plan.named(target, str(n)).digest for n in names]
                    case _:
                        pass
            # Tools and scorers pin the code that runs them: the running chatddx, which
            # compilations record as their compiler, unless they name other code.
            if table in ("tool", "scorer") and "code" not in body:
                body["code"] = compiler.model_dump(mode="json")
            labels = _labels(table, body)
            variants: Variants = {}
            for model in models if "from_facts" in body else [None]:
                try:
                    component = _component(table, dict(body), model, facts)
                except Refused as e:
                    plan.skipped.append(f"{table} {_named(name, model)}: {e}")
                    continue
                except ValidationError as e:
                    raise ValueError(f"{table}.{name}: {e}") from None
                if getattr(component, "code", None) == compiler:
                    try:
                        _check_runs(component)
                    except (LookupError, TypeError) as e:
                        raise ValueError(f"{table}.{name}: {e}") from None
                variants[model] = Planned(
                    table=table,
                    name=_named(name, model),
                    kind=component.kind_name,
                    digest=plan.registry.add(component),
                    tags=tags,
                    description=None if description is None else str(description),
                    fork_of=base(table, fork_of, model),
                    labels=labels,
                )
            add(table, name, variants)

    for name, raw in data.get("recipe", {}).items():
        assert isinstance(raw, dict)
        body = dict(raw)
        tags = _tags(body.pop("tags", []))
        description = body.pop("description", None)
        fork_of = body.pop("fork_of", None)
        parts = {p: planned[(p, str(body.pop(p)))] for p in RECIPE_PARTS if p in body}
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
                compilation=plan.registry.add(
                    Compilation(recipe=recipe, skeleton=skeleton, compiler=compiler)
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
