from pydantic import HttpUrl

from chatddx.catalog import Entry, EntryField, Subject, titles
from chatddx.catalog.test.memory import Memory
from chatddx.catalog.titles import (
    Title,
    describe,
    describe_change,
    describe_recipe,
    snippet,
)
from chatddx.factors.base import resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Case
from chatddx.factors.engine import RemoteEngine
from chatddx.factors.request import (
    Output,
    Reasoning,
    Sampling,
    Tool,
    ToolOutput,
    Toolset,
    Translations,
    texts,
)
from chatddx.factors.scoring import ExpectationSchema
from chatddx.factors.test.sample import RIG, generation_recipe, world


def titler(reg: Registry) -> Title:
    def title(digest: str) -> str:
        return describe(reg.get(digest), title)

    return title


def test_snippets_are_short() -> None:
    assert snippet("You  are an\nemergency physician.") == (
        '"You are an emergency physician."'
    )
    assert snippet("one two three four five six seven eight nine ten", 20) == (
        '"one two three four…"'
    )


def test_unnamed_components_are_described_by_what_they_hold() -> None:
    reg = Registry()
    ids = world(reg)
    title = titler(reg)
    recipe = generation_recipe(reg)
    assert describe_recipe(recipe, title) == (
        '"You are an emergency physician." · "Case: {vignette}{appendices}…"'
        + " · native output: ddx · temperature 0.7, top_p 0.9, max_output_tokens 1024"
        + " · enable_thinking false"
    )
    assert title(ids["engine"]) == "google/gemma-3-12b-it on vllm 0.24.0 (RTX 5090)"
    assert title(ids["case"]) == 'registry/c1 with "Troponin 80 ng/L."'
    assert title(ids["trial"]).endswith(
        " on google/gemma-3-12b-it on vllm 0.24.0 (RTX 5090), 1 case, 2 seeds"
    )
    assert title(ids["canaries"]) == "1 canary"
    assert title(reg.add(Sampling())) == "default sampling"
    assert title(reg.add(Reasoning(effort="low", thinking_token_budget=512))) == (
        "effort low, budget 512"
    )
    tool = Output(
        contract=ToolOutput(name="plan", description="The plan."),
        json_schema={"type": "object", "properties": {"steps": {}, "disposition": {}}},
    )
    assert title(reg.add(tool)) == "tool plan output: steps, disposition"
    schema = reg.add(ExpectationSchema(json_schema={"title": "Targets"}))
    assert title(schema) == "Targets"
    remote = reg.add(
        RemoteEngine(base_url=HttpUrl("https://example.org/v1"), model="gpt-x")
    )
    assert title(remote) == "gpt-x at example.org"


def test_recipes_are_described_by_every_part() -> None:
    reg = Registry()
    _ = world(reg)
    title = titler(reg)
    recipe = generation_recipe(reg)
    needed = [t for part in texts(recipe, reg.get).values() for t in part]
    swedish = reg.add(Translations(entries={t: f"[sv] {t}" for t in needed}))
    search = reg.add(
        Tool(
            name="web_search",
            description="Search the web.",
            parameters={"type": "object"},
            code=RIG,
            entry_point="chatddx_tools.web:search",
        )
    )
    tools = reg.add(Toolset(tools=(search,)))
    english = describe_recipe(recipe, title)
    translated = recipe.model_copy(update={"translations": swedish})
    assert describe_recipe(translated, title) == (
        english + f" · translations, {len(needed)} texts"
    )
    assert (
        describe(reg.get(swedish), title, "sv")
        == f"sv translations, {len(needed)} texts"
    )
    with_tools = recipe.model_copy(update={"toolset": tools})
    assert describe_recipe(with_tools, title) == english + " · with web_search"


def test_changes_are_described_by_path_and_value() -> None:
    reg = Registry()
    title = titler(reg)
    shown = reg.add(Sampling(temperature=0.2))
    assert describe_change("/recipe/sampling", shown, title) == (
        "sampling: temperature 0.2"
    )
    assert describe_change("/recipe/reasoning", None, title) == "no reasoning"
    assert describe_change("/max_output_tokens", 4096, title) == (
        "max_output_tokens: 4096"
    )
    assert describe_change("/text", "Be brief and kind to the patient.", title) == (
        'text: "Be brief and kind to the patient."'
    )


def test_titles_prefer_the_names_of_threads_that_hold_a_digest() -> None:
    reg = Registry()
    rows = Memory(reg)
    cool, greedy = reg.add(Sampling(temperature=0.5)), reg.add(Sampling(temperature=0))
    assert titles.title_of(rows, cool) == "temperature 0.5"
    named = rows.start(cool)
    rows.note(Subject(thread=named.thread), Entry(field=EntryField.NAME, value="cool"))
    assert titles.title_of(rows, cool) == "cool"
    _ = rows.save(named.thread, greedy)
    assert titles.title_of(rows, cool) == "cool (earlier)"
    assert titles.title_of(rows, greedy) == "cool"

    fork = rows.start(cool, forked_from=named.id)
    assert titles.title(rows, fork.thread) == "a fork of cool"
    assert titles.title_of(rows, cool) == "a fork of cool"
    rows.note(Subject(thread=fork.thread), Entry(field=EntryField.DELETED))
    assert titles.title_of(rows, cool) == "cool (earlier)"


def test_cases_are_titled_by_family_and_appendices() -> None:
    reg = Registry()
    ids = world(reg)
    rows = Memory(reg)
    vignette = resolve(reg.get, ids["case"], Case).vignette
    troponin = '"Troponin 80 ng/L."'
    assert titles.title_of(rows, ids["case"]) == f"registry/c1 with {troponin}"
    _ = rows.bind(1, vignette)
    rows.note(Subject(family=1), Entry(field=EntryField.NAME, value="chest pain"))
    assert titles.title_of(rows, ids["case"]) == f"chest pain with {troponin}"
    _ = rows.bind(1, vignette.model_copy(update={"id": "c1-renamed"}))
    assert titles.title_of(rows, ids["case"]) == f"chest pain (earlier) with {troponin}"


def test_translations_are_titled_by_their_language() -> None:
    reg = Registry()
    rows = Memory(reg)
    hello = reg.add(Translations(entries={"Hello": "Hej"}))
    assert titles.title_of(rows, hello) == "translations, 1 text"
    rows.set_language(hello, "sv")
    assert titles.title_of(rows, hello) == "sv translations, 1 text"
