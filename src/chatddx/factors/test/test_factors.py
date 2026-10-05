import hashlib
import json
from typing import Literal

import pytest
from pydantic import HttpUrl, JsonValue, ValidationError

from chatddx.factors.base import (
    Component,
    Fingerprint,
    StructuralError,
    resolve,
    sha256_digest,
)
from chatddx.factors.bundle import Bundle, Registry
from chatddx.factors.cases import (
    Appendix,
    Case,
    Vignette,
    clean,
    prepare_case,
)
from chatddx.factors.engine import (
    FileDigest,
    LocalEngine,
    RemoteEngine,
    check_chat_template,
)
from chatddx.factors.lint import lint
from chatddx.factors.request import (
    AppendixLayout,
    Example,
    FewShot,
    Insert,
    Instructions,
    Message,
    NativeOutput,
    Output,
    OutputContract,
    Prompt,
    Reasoning,
    Recipe,
    Sampling,
    Segment,
    Skeleton,
    Slot,
    TextOutput,
    Tool,
    ToolCall,
    ToolOutput,
    Toolset,
    Translations,
    compile_request,
    next_request,
    render,
    texts,
    tool_calls,
)
from chatddx.factors.scoring import (
    Expectation,
    ExpectationSchema,
    Judge,
    Scorer,
    View,
)
from chatddx.factors.test.sample import (
    KEY,
    RIG,
    SHA,
    fp,
    generation_recipe,
    generation_skeleton,
    local_engine,
    world,
)
from chatddx.factors.trial import (
    Execution,
    Trial,
    suggest_seeds,
)


@pytest.fixture
def reg() -> Registry:
    return Registry()


def test_digest_is_stable_and_order_independent(reg: Registry) -> None:
    a = local_engine(reg)
    b = LocalEngine.model_validate(json.loads(a.model_dump_json()))
    assert a.digest == b.digest
    assert a.digest.startswith("sha256:")
    assert json.loads(a.canonical)["v"] == 1


def test_defaults_are_omitted_from_canonical_form() -> None:
    doc = json.loads(Sampling(temperature=0.5).canonical)
    assert doc == {"kind": "chunk.sampling", "temperature": 0.5, "v": 1}


def test_adding_a_defaulted_field_keeps_digests() -> None:
    class Old(Component):
        kind: Literal["test.evolve.old"] = "test.evolve.old"
        a: int

    class New(Component):
        kind: Literal["test.evolve.new"] = "test.evolve.new"
        a: int
        b: int | None = None

    old, new = json.loads(Old(a=1).canonical), json.loads(New(a=1).canonical)
    assert {k: v for k, v in old.items() if k != "kind"} == {
        k: v for k, v in new.items() if k != "kind"
    }
    del Component.registry["test.evolve.old"], Component.registry["test.evolve.new"]


def test_json_data_keeps_its_order(reg: Registry) -> None:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "diagnosis": {"type": "string"},
            "critical": {"type": "boolean"},
        },
    }
    recipe = Recipe(
        prompt=reg.add(Prompt(segments=(Slot(slot="vignette"),))),
        output=reg.add(Output(contract=NativeOutput(), json_schema=schema)),
        sampling=reg.add(Sampling()),
    )
    skeleton = compile_request(recipe, reg.get)
    stored = Registry().add_raw(skeleton.digest, skeleton.canonical)
    assert isinstance(stored, Skeleton)
    assert json.dumps(stored.output_schema) == json.dumps(schema)
    flipped: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "critical": {"type": "boolean"},
            "diagnosis": {"type": "string"},
        },
    }
    assert Output(contract=NativeOutput(), json_schema=flipped).digest != recipe.output


def test_settings_carry_no_order(reg: Registry) -> None:
    skeleton = generation_skeleton(reg)
    flipped = Skeleton.model_validate(
        {**skeleton.model_dump(), "body": dict(reversed(skeleton.body.items()))}
    )
    assert flipped.digest == skeleton.digest
    kwargs: dict[str, JsonValue] = {"enable_thinking": False, "budget": 1}
    assert (
        Reasoning(chat_template_kwargs=kwargs).digest
        == Reasoning(chat_template_kwargs=dict(reversed(kwargs.items()))).digest
    )


def test_bytes_with_sorted_keys_keep_their_digest(reg: Registry) -> None:
    _ = world(reg)
    for digest in reg:
        doc = json.loads(reg.raw(digest))
        raw = json.dumps(
            doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        assert Registry().add_raw(sha256_digest(raw), raw).canonical == raw


def test_references_come_from_field_types(reg: Registry) -> None:
    ids = world(reg)
    trial = reg.get(ids["trial"])
    paths = {site.path: site.kinds for site in trial.refs()}
    assert paths == {
        "/skeleton": ("skeleton",),
        "/engine": ("engine.local", "engine.remote"),
        "/cases/0": ("case",),
    }
    schema = Trial.model_json_schema()
    assert schema["properties"]["engine"]["x-ref"] == ["engine.local", "engine.remote"]
    appendices = Case.model_json_schema()["properties"]["appendices"]
    assert appendices["items"]["x-ref"] == ["appendix"]


def test_greedy_drops_sampling_noise() -> None:
    assert (
        Sampling(temperature=0, top_p=0.9, top_k=5).digest
        == Sampling(temperature=0).digest
    )
    skeleton = Skeleton(
        messages=(Message(role="user", content=(Slot(slot="vignette"),)),),
        body={"temperature": 0, "min_p": 0.1},
        contract=TextOutput(),
    )
    assert "min_p" not in skeleton.body
    body = render(skeleton, model="m", seed=42, fills={"vignette": "x"})
    assert "seed" not in body


def test_compile_and_render(reg: Registry) -> None:
    skeleton = generation_skeleton(reg)
    assert skeleton.messages[0].content == (
        "You are an emergency physician.\n\nAnswer in JSON.",
    )
    assert skeleton.body["chat_template_kwargs"] == {"enable_thinking": False}
    assert skeleton.body["stop"] == ["</ddx>"]
    assert skeleton.body["max_completion_tokens"] == 1024
    assert skeleton.output_schema == {
        "type": "object",
        "properties": {"ddx": {"type": "array"}},
    }
    appendices = skeleton.appendix_layout.join(["Troponin 80 ng/L."])
    body = render(
        skeleton,
        model="served",
        seed=3,
        fills={"vignette": "{{not a template}}", "appendices": appendices},
    )
    assert body["seed"] == 3
    assert body["return_token_ids"] is True
    assert body["messages"] == [
        {
            "role": "system",
            "content": "You are an emergency physician.\n\nAnswer in JSON.",
        },
        {
            "role": "user",
            "content": "Case:\n{{not a template}}\n\nTroponin 80 ng/L.\n\nDifferential?",
        },
    ]


def test_outputs_show_their_schema(reg: Registry) -> None:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "diagnosis": {"type": "string"},
            "critical": {"type": "boolean"},
        },
    }
    shown = json.dumps(schema, indent=2, ensure_ascii=False)
    guidance = (
        "Answer with JSON matching:\n\n",
        Insert(insert="schema"),
        "\n\nNo prose.",
    )
    prompt = reg.add(Prompt(segments=(Slot(slot="vignette"),)))
    sampling = reg.add(Sampling())

    output = Output(contract=NativeOutput(), json_schema=schema, guidance=guidance)
    assert Registry().add_raw(output.digest, output.canonical) == output
    native = compile_request(
        Recipe(prompt=prompt, output=reg.add(output), sampling=sampling), reg.get
    )
    assert native.messages[0].content == (
        f"Answer with JSON matching:\n\n{shown}\n\nNo prose.",
    )
    assert native.output_schema == schema

    prompted = compile_request(
        Recipe(
            prompt=prompt,
            output=reg.add(
                Output(contract=TextOutput(), json_schema=schema, guidance=guidance)
            ),
            sampling=sampling,
        ),
        reg.get,
    )
    assert prompted.messages[0].content == native.messages[0].content
    assert prompted.body == {}


def test_schema_inserts_are_checked() -> None:
    schema: dict[str, JsonValue] = {"type": "object"}
    with pytest.raises(ValidationError, match="goes in json_schema"):
        _ = Output(contract=TextOutput(json_schema=schema))
    with pytest.raises(ValidationError, match="native contracts need json_schema"):
        _ = Output(contract=NativeOutput())
    with pytest.raises(ValidationError, match="only an output with a schema"):
        _ = Output(contract=TextOutput(), guidance=(Insert(insert="schema"),))
    with pytest.raises(ValidationError, match="used more than once"):
        _ = Output(
            contract=NativeOutput(),
            json_schema=schema,
            guidance=(Insert(insert="schema"), "\n", Insert(insert="schema")),
        )
    text = Output(contract=TextOutput(), guidance="Answer in JSON.")
    split = Output(contract=TextOutput(), guidance=("Answer ", "", "in JSON."))
    assert split.guidance == "Answer in JSON."
    assert split.digest == text.digest
    assert json.loads(text.canonical)["guidance"] == "Answer in JSON."


def test_output_guidance_goes_where_it_is_inserted(reg: Registry) -> None:
    guided = reg.add(Output(contract=TextOutput(), guidance="Answer in JSON."))
    unguided = reg.add(Output(contract=TextOutput()))
    sampling = reg.add(Sampling())
    case = reg.add(Prompt(segments=(Slot(slot="vignette"),)))
    first = reg.add(
        Instructions(text=(Insert(insert="output_guidance", after="\n\n"), "Be terse."))
    )

    def system_and_user(
        instructions: str | None, prompt: str, output: str
    ) -> list[tuple[Segment, ...]]:
        recipe = Recipe(
            instructions=instructions, prompt=prompt, output=output, sampling=sampling
        )
        return [m.content for m in compile_request(recipe, reg.get).messages]

    assert system_and_user(first, case, guided) == [
        ("Answer in JSON.\n\nBe terse.",),
        (Slot(slot="vignette"),),
    ]
    assert system_and_user(first, case, unguided) == [
        ("Be terse.",),
        (Slot(slot="vignette"),),
    ]

    last = reg.add(
        Prompt(
            segments=(
                "Case: ",
                Slot(slot="vignette"),
                Insert(insert="output_guidance", before="\n\n"),
                "\n\nDifferential?",
            )
        )
    )
    terse = reg.add(Instructions(text="Be terse."))
    assert system_and_user(terse, last, guided) == [
        ("Be terse.",),
        ("Case: ", Slot(slot="vignette"), "\n\nAnswer in JSON.\n\nDifferential?"),
    ]
    assert system_and_user(None, last, unguided) == [
        ("Case: ", Slot(slot="vignette"), "\n\nDifferential?"),
    ]
    assert system_and_user(terse, case, guided) == [
        ("Be terse.\n\nAnswer in JSON.",),
        (Slot(slot="vignette"),),
    ]

    with pytest.raises(StructuralError, match="both the instructions and the prompt"):
        _ = system_and_user(first, last, guided)


def test_inserts_are_checked_per_chunk() -> None:
    with pytest.raises(ValidationError, match="cannot use inserts"):
        _ = Instructions(text=(Insert(insert="schema"),))
    with pytest.raises(ValidationError, match="cannot use inserts"):
        _ = Output(contract=TextOutput(), guidance=(Insert(insert="output_guidance"),))
    with pytest.raises(ValidationError, match="used more than once"):
        _ = Prompt(
            segments=(
                Insert(insert="output_guidance"),
                Slot(slot="vignette"),
                Insert(insert="output_guidance"),
            )
        )
    assert (
        Instructions(text=("Be ", "terse.")).digest
        == Instructions(text="Be terse.").digest
    )


def test_few_shot_developer_role_and_budgets(reg: Registry) -> None:
    recipe = Recipe(
        instructions=reg.add(Instructions(role="developer", text="Be terse.")),
        few_shot=reg.add(
            FewShot(
                messages=(
                    Example(role="user", content="Fever and rash."),
                    Example(role="assistant", content='{"ddx": ["measles"]}'),
                )
            )
        ),
        prompt=reg.add(Prompt(segments=(Slot(slot="vignette"),))),
        output=reg.add(Output(contract=TextOutput())),
        sampling=reg.add(
            Sampling(temperature=0, max_output_tokens=64, max_tokens_key="max_tokens")
        ),
        reasoning=reg.add(Reasoning(effort="low", thinking_token_budget=256)),
    )
    skeleton = compile_request(recipe, reg.get)
    assert [m.role for m in skeleton.messages] == [
        "developer",
        "user",
        "assistant",
        "user",
    ]
    assert skeleton.body == {
        "temperature": 0.0,
        "max_tokens": 64,
        "reasoning_effort": "low",
        "thinking_token_budget": 256,
    }


def test_skeleton_structure_is_enforced() -> None:
    user = Message(role="user", content=(Slot(slot="vignette"),))
    with pytest.raises(ValidationError, match="may not set"):
        _ = Skeleton(messages=(user,), body={"seed": 1}, contract=TextOutput())
    with pytest.raises(ValidationError, match="need slots"):
        _ = Skeleton(
            messages=(Message(role="user", content=("hi",)),), contract=TextOutput()
        )
    with pytest.raises(ValidationError, match="cannot use slots"):
        _ = Skeleton(
            messages=(
                Message(
                    role="user",
                    content=(Slot(slot="vignette"), Slot(slot="completion")),
                ),
            ),
            contract=TextOutput(),
        )
    with pytest.raises(ValidationError, match="native contract"):
        _ = Skeleton(messages=(user,), contract=NativeOutput())
    with pytest.raises(ValidationError, match="exactly one tool"):
        _ = Skeleton(
            messages=(user,),
            body={
                "tools": [{"type": "function", "function": {"name": "a"}}],
                "tool_choice": "required",
            },
            contract=ToolOutput(name="b"),
        )
    with pytest.raises(ValidationError, match="described as"):
        _ = Skeleton(
            messages=(user,),
            body={
                "tools": [{"type": "function", "function": {"name": "a"}}],
                "tool_choice": "required",
            },
            contract=ToolOutput(name="a", description="Answer here."),
        )


def test_malformed_skeleton_bodies_are_validation_errors() -> None:
    user = Message(role="user", content=(Slot(slot="vignette"),))
    with pytest.raises(ValidationError, match="needs a function with a name"):
        _ = Skeleton(
            messages=(user,),
            body={"tools": [{"type": "function"}], "tool_choice": "required"},
            contract=ToolOutput(name="a"),
        )
    with pytest.raises(ValidationError, match="tools must be a list"):
        _ = Skeleton(
            messages=(user,),
            body={"tools": {"name": "a"}, "tool_choice": "required"},
            contract=ToolOutput(name="a"),
        )
    with pytest.raises(ValidationError, match="native contract needs its schema"):
        _ = Skeleton(
            messages=(user,),
            body={"response_format": {"type": "json_object"}},
            contract=NativeOutput(),
        )
    with pytest.raises(ValidationError, match="tool contract needs its schema"):
        _ = Skeleton(
            messages=(user,),
            body={
                "tools": [{"type": "function", "function": {"name": "a"}}],
                "tool_choice": {"type": "function", "function": {"name": "a"}},
            },
            contract=ToolOutput(name="a"),
        )


def test_seeds_are_distinct() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        _ = Judge(skeleton="sha256:" + SHA, engine="sha256:" + SHA, seeds=(1, 1))
    with pytest.raises(ValidationError, match="duplicates"):
        _ = Trial(
            skeleton="sha256:" + SHA,
            engine="sha256:" + SHA,
            cases=("sha256:" + SHA,),
            seeds=(1, 1),
        )


def test_schema_ops_inline_refs(reg: Registry) -> None:
    authored: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "diagnoses": {"type": "array", "items": {"$ref": "#/$defs/Diagnosis"}},
            "worst": {"$ref": "#/$defs/Diagnosis", "description": "The worst one."},
            "odd": {"$ref": "#/$defs/a~1b"},
            "first": {"$ref": "#/properties/diagnoses/items"},
        },
        "$defs": {
            "Diagnosis": {
                "type": "object",
                "description": "A diagnosis.",
                "properties": {"name": {"type": "string"}},
            },
            "a/b": {"type": "string"},
        },
    }
    diagnosis: dict[str, JsonValue] = {
        "type": "object",
        "description": "A diagnosis.",
        "properties": {"name": {"type": "string"}},
    }
    inlined: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "diagnoses": {"type": "array", "items": diagnosis},
            "worst": {**diagnosis, "description": "The worst one."},
            "odd": {"type": "string"},
            "first": diagnosis,
        },
    }
    ops: tuple[Literal["inline_refs@1"], ...] = ("inline_refs@1",)
    prompt = reg.add(Prompt(segments=(Slot(slot="vignette"),)))
    sampling = reg.add(Sampling())

    def compiled(output: Output) -> Skeleton:
        return compile_request(
            Recipe(prompt=prompt, output=reg.add(output), sampling=sampling), reg.get
        )

    shown = (Insert(insert="schema"),)
    for contract in (NativeOutput(), ToolOutput(name="final_result")):
        skeleton = compiled(
            Output(
                contract=contract,
                json_schema=authored,
                guidance=shown,
                schema_ops=ops,
            )
        )
        assert json.dumps(skeleton.output_schema) == json.dumps(inlined)
        assert skeleton.messages[0].content == (
            json.dumps(inlined, indent=2, ensure_ascii=False),
        )
    text = compiled(
        Output(
            contract=TextOutput(),
            json_schema=authored,
            guidance=shown,
            schema_ops=ops,
        )
    )
    assert json.dumps(text.output_schema) == json.dumps(inlined)
    kept = compiled(Output(contract=NativeOutput(), json_schema=authored))
    assert kept.output_schema == authored


def test_schema_ops_are_checked() -> None:
    ops: tuple[Literal["inline_refs@1"], ...] = ("inline_refs@1",)

    def output(schema: dict[str, JsonValue]) -> Output:
        return Output(contract=NativeOutput(), json_schema=schema, schema_ops=ops)

    with pytest.raises(ValidationError, match="refers to itself"):
        _ = output(
            {
                "type": "object",
                "properties": {"next": {"$ref": "#/$defs/Node"}},
                "$defs": {"Node": {"properties": {"next": {"$ref": "#/$defs/Node"}}}},
            }
        )
    with pytest.raises(ValidationError, match="outside the schema"):
        _ = output({"$ref": "https://example.org/schema.json"})
    with pytest.raises(ValidationError, match="refers to nothing"):
        _ = output({"$ref": "#/$defs/Missing"})
    with pytest.raises(ValidationError, match="need a schema"):
        _ = Output(contract=TextOutput(), schema_ops=ops)
    with pytest.raises(ValidationError, match="duplicates"):
        _ = Output(
            contract=NativeOutput(),
            json_schema={"type": "object"},
            schema_ops=("inline_refs@1", "inline_refs@1"),
        )
    plain = Output(contract=NativeOutput(), json_schema={"type": "object"})
    assert "schema_ops" not in json.loads(plain.canonical)


def test_tool_contracts_carry_a_description(reg: Registry) -> None:
    schema: dict[str, JsonValue] = {"type": "object"}
    described = ToolOutput(name="final_result", description="Answer by calling this.")
    skeleton = compile_request(
        Recipe(
            prompt=reg.add(Prompt(segments=(Slot(slot="vignette"),))),
            output=reg.add(
                Output(contract=described, json_schema=schema, guidance="Use the tool.")
            ),
            sampling=reg.add(Sampling()),
        ),
        reg.get,
    )
    assert json.dumps(skeleton.body["tools"]) == json.dumps(
        [
            {
                "type": "function",
                "function": {
                    "name": "final_result",
                    "description": "Answer by calling this.",
                    "parameters": schema,
                },
            }
        ]
    )
    assert skeleton.contract == described
    assert json.loads(ToolOutput(name="final_result").model_dump_json()) == {
        "kind": "tool",
        "name": "final_result",
        "description": None,
    }
    assert (
        "description"
        not in json.loads(
            Output(contract=ToolOutput(name="a"), json_schema=schema).canonical
        )["contract"]
    )
    with pytest.raises(ValidationError, match="at least 1 character"):
        _ = ToolOutput(name="a", description="")


def test_translations_apply_at_compile_time(reg: Registry) -> None:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "title": "Plan",
        "properties": {
            "urgency": {
                "type": "string",
                "enum": ["high", "low"],
                "description": "How urgent.",
            },
            "description": {"type": "string", "description": "Free text."},
        },
    }
    recipe = Recipe(
        instructions=reg.add(
            Instructions(
                text=(
                    "You are an emergency physician.",
                    Insert(insert="output_guidance", before="\n\n"),
                )
            )
        ),
        few_shot=reg.add(
            FewShot(
                messages=(
                    Example(role="user", content="Chest pain."),
                    Example(role="assistant", content="ACS."),
                )
            )
        ),
        prompt=reg.add(Prompt(segments=("Case:\n", Slot(slot="vignette")))),
        output=reg.add(
            Output(
                contract=ToolOutput(name="plan", description="The plan."),
                json_schema=schema,
                guidance=("Answer with:\n\n", Insert(insert="schema")),
            )
        ),
        sampling=reg.add(Sampling(temperature=0)),
        appendix_layout=AppendixLayout(before="\n\nLabs:\n"),
    )
    assert texts(recipe, reg.get) == {
        "instructions": ("You are an emergency physician.",),
        "few_shot": ("Chest pain.", "ACS."),
        "prompt": ("Case:\n",),
        "output": (
            "Answer with:\n\n",
            "Plan",
            "How urgent.",
            "Free text.",
            "The plan.",
        ),
        "appendix_layout": ("\n\nLabs:\n",),
    }
    swedish = {
        "You are an emergency physician.": "Du är akutläkare.",
        "Chest pain.": "Bröstsmärta.",
        "ACS.": "AKS.",
        "Case:\n": "Fall:\n",
        "Answer with:\n\n": "Svara med:\n\n",
        "Plan": "Vårdplan",
        "How urgent.": "Hur brådskande.",
        "Free text.": "Fritext.",
        "The plan.": "Planen.",
        "\n\nLabs:\n": "\n\nLabb:\n",
    }
    translated = recipe.model_copy(
        update={"translations": reg.add(Translations(entries=swedish))}
    )
    skeleton = compile_request(translated, reg.get)
    system, user, assistant, prompt = skeleton.messages
    assert system.content[0] == (
        "Du är akutläkare.\n\nSvara med:\n\n"
        + json.dumps(skeleton.output_schema, indent=2, ensure_ascii=False)
    )
    assert (user.content, assistant.content) == (("Bröstsmärta.",), ("AKS.",))
    assert prompt.content == ("Fall:\n", Slot(slot="vignette"))
    assert skeleton.appendix_layout.before == "\n\nLabb:\n"
    assert skeleton.output_schema == {
        "type": "object",
        "title": "Vårdplan",
        "properties": {
            "urgency": {
                "type": "string",
                "enum": ["high", "low"],
                "description": "Hur brådskande.",
            },
            "description": {"type": "string", "description": "Fritext."},
        },
    }
    tools = skeleton.body["tools"]
    assert isinstance(tools, list) and isinstance(tools[0], dict)
    assert tools[0]["function"] == {
        "name": "plan",
        "description": "Planen.",
        "parameters": skeleton.output_schema,
    }

    partial = {k: v for k, v in swedish.items() if k not in ("Plan", "ACS.")}
    with pytest.raises(StructuralError, match="no translation for 'ACS.', 'Plan'"):
        _ = compile_request(
            recipe.model_copy(
                update={"translations": reg.add(Translations(entries=partial))}
            ),
            reg.get,
        )
    for entries in ({}, {"  ": "x"}, {"Plan": ""}):
        with pytest.raises(ValidationError):
            _ = Translations(entries=entries)


def web_search(reg: Registry) -> str:
    return reg.add(
        Tool(
            name="web_search",
            description="Search the web.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up."}
                },
                "required": ["query"],
            },
            code=RIG,
            entry_point="chatddx_tools.web:search",
        )
    )


def test_tools_compile_into_the_request(reg: Registry) -> None:
    web = web_search(reg)
    tools = reg.add(Toolset(tools=(web,), guidance="Search when unsure.", max_rounds=3))
    base = generation_recipe(reg).model_copy(update={"toolset": tools})
    web_function: dict[str, JsonValue] = {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web.",
            "parameters": resolve(reg.get, web, Tool).parameters,
        },
    }

    native = compile_request(base, reg.get)
    assert (native.tools, native.max_rounds) == ((web,), 3)
    assert native.messages[0].content == (
        "You are an emergency physician.\n\nAnswer in JSON.\n\nSearch when unsure.",
    )
    assert native.body["tools"] == [web_function]
    assert native.body["tool_choice"] == "auto"
    assert "response_format" in native.body

    answer = reg.add(
        Output(
            contract=ToolOutput(name="answer", description="Give the answer."),
            json_schema={"type": "object"},
        )
    )
    tool = compile_request(base.model_copy(update={"output": answer}), reg.get)
    tool_list = tool.body["tools"]
    assert isinstance(tool_list, list)
    assert tool_list[0] == web_function
    assert tool.body["tool_choice"] == "required"
    assert tool.output_schema == {"type": "object"}

    text = reg.add(Output(contract=TextOutput()))
    plain = compile_request(base.model_copy(update={"output": text}), reg.get)
    assert (plain.body["tools"], plain.body["tool_choice"]) == ([web_function], "auto")

    placed = reg.add(
        Prompt(
            segments=(
                "Case:\n",
                Slot(slot="vignette"),
                Insert(insert="tool_guidance", before="\n\n"),
            )
        )
    )
    inserted = compile_request(base.model_copy(update={"prompt": placed}), reg.get)
    assert inserted.messages[-1].content == (
        "Case:\n",
        Slot(slot="vignette"),
        "\n\nSearch when unsure.",
    )
    assert "Search when unsure." not in str(inserted.messages[0].content)
    bare = compile_request(
        generation_recipe(reg).model_copy(update={"prompt": placed}), reg.get
    )
    assert bare.messages[-1].content == ("Case:\n", Slot(slot="vignette"))

    clash = reg.add(
        Output(
            contract=ToolOutput(name="web_search", description="Answer."),
            json_schema={"type": "object"},
        )
    )
    with pytest.raises(StructuralError, match="web_search"):
        _ = compile_request(base.model_copy(update={"output": clash}), reg.get)

    assert texts(base, reg.get)["toolset"] == (
        "Search when unsure.",
        "Search the web.",
        "What to look up.",
    )
    twice = Toolset(tools=(web, web))
    _ = reg.add(twice)
    with pytest.raises(StructuralError, match="more than once"):
        reg.check([twice.digest])
    with pytest.raises(ValidationError, match="max_rounds"):
        _ = Skeleton.model_validate({**native.model_dump(), "max_rounds": None})
    with pytest.raises(ValidationError, match="auto"):
        _ = Skeleton.model_validate(
            {**plain.model_dump(), "body": {**plain.body, "tool_choice": "required"}}
        )


def test_tool_rounds_continue_the_request(reg: Registry) -> None:
    web = web_search(reg)
    tools = reg.add(Toolset(tools=(web,)))
    skeleton = compile_request(
        generation_recipe(reg).model_copy(update={"toolset": tools}), reg.get
    )
    body = render(
        skeleton, model="m", seed=1, fills={"vignette": "Chest pain.", "appendices": ""}
    )
    called: dict[str, JsonValue] = {
        "role": "assistant",
        "content": None,
        "reasoning": "Let me check.",
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"query": "ACS"}'},
            }
        ],
    }
    response: dict[str, JsonValue] = {"choices": [{"message": called}]}
    assert tool_calls(response) == (
        ToolCall(id="c1", name="web_search", arguments='{"query": "ACS"}'),
    )
    assert tool_calls({"choices": [{"message": {"content": "ACS"}}]}) == ()
    follow_up = next_request(body, response, {"c1": "1. ACS - Wikipedia"})
    messages = follow_up["messages"]
    assert isinstance(messages, list) and isinstance(body["messages"], list)
    assert messages[: len(body["messages"])] == body["messages"]
    assert messages[len(body["messages"]) :] == [
        {"role": "assistant", "content": None, "tool_calls": called["tool_calls"]},
        {"role": "tool", "tool_call_id": "c1", "content": "1. ACS - Wikipedia"},
    ]
    assert {k: v for k, v in follow_up.items() if k != "messages"} == {
        k: v for k, v in body.items() if k != "messages"
    }
    with pytest.raises(StructuralError, match="c1"):
        _ = next_request(body, response, {})


def test_engine_argv_cannot_override_manifest(reg: Registry) -> None:
    engine = local_engine(reg)
    for argv in (["--served-model-name=x"], ["--chat_template", "t.jinja"]):
        with pytest.raises(ValidationError, match="may not set"):
            _ = LocalEngine.model_validate({**engine.model_dump(), "argv": argv})
    for argv in (["--config", "serve.yaml"], ["--config=serve.yaml"]):
        with pytest.raises(ValidationError, match="config"):
            _ = LocalEngine.model_validate({**engine.model_dump(), "argv": argv})
    assert engine.served_model_name == engine.digest


def test_bundle_roundtrip_and_tamper(reg: Registry) -> None:
    ids = world(reg)
    bundle = reg.bundle([ids["trial"], ids["canaries"], ids["scoring"]], generator=RIG)
    loaded, findings = Bundle.model_validate_json(bundle.model_dump_json()).load()
    assert findings == []
    assert ids["case"] in loaded
    digest, text = next(iter(bundle.components.items()))
    tampered = bundle.model_copy(
        update={"components": {**bundle.components, digest: text + " "}}
    )
    with pytest.raises(StructuralError, match="does not match"):
        _ = tampered.load()


def test_dangling_and_mistyped_refs(reg: Registry) -> None:
    ids = world(reg)
    bad = Trial(
        skeleton=ids["engine"], engine=ids["engine"], cases=(ids["case"],), seeds=(1,)
    )
    _ = reg.add(bad)
    with pytest.raises(StructuralError, match="is not one of"):
        reg.check([bad.digest])
    dangling = Judge(skeleton="sha256:" + SHA, engine=ids["engine"], seeds=(1,))
    _ = reg.add(dangling)
    with pytest.raises(StructuralError, match="is missing"):
        reg.check([dangling.digest])


def test_cross_checks(reg: Registry) -> None:
    other = reg.add(
        Appendix(
            vignette=Vignette(source="registry", id="c2", fingerprint=fp("v")),
            text="x",
        )
    )
    case = Case(
        vignette=Vignette(source="registry", id="c1", fingerprint=fp("v")),
        appendices=(other,),
    )
    _ = reg.add(case)
    with pytest.raises(StructuralError, match="bound to another vignette"):
        reg.check([case.digest])


def test_each_component_is_checked_on_its_own(reg: Registry) -> None:
    dangling = reg.add(
        Judge(skeleton="sha256:" + SHA, engine="sha256:" + SHA, seeds=(1,))
    )
    other = reg.add(
        Appendix(
            vignette=Vignette(source="registry", id="c2", fingerprint=fp("v")),
            text="x",
        )
    )
    misbound = reg.add(
        Case(
            vignette=Vignette(source="registry", id="c1", fingerprint=fp("v")),
            appendices=(other,),
        )
    )
    with pytest.raises(StructuralError) as raised:
        reg.check([dangling, misbound])
    assert "is missing" in str(raised.value)
    assert "bound to another vignette" in str(raised.value)


def test_prepare_case(reg: Registry) -> None:
    raw = b"A 54-year-old\r\nwith chest pain.\n"
    vignette = Vignette(source="registry", id="c1", fingerprint=Fingerprint.of(raw))
    appendices = tuple(
        reg.add(Appendix(vignette=vignette, text=text))
        for text in ("Troponin 80 ng/L.", "ECG: ST elevation.")
    )
    case = Case(vignette=vignette, appendices=appendices)
    prepared = prepare_case(
        case, raw, reg.get, AppendixLayout(), ("newlines.lf@1", "strip@1")
    )
    assert prepared.fills == {
        "vignette": "A 54-year-old\nwith chest pain.",
        "appendices": "\n\nTroponin 80 ng/L.\n\nECG: ST elevation.",
    }
    assert (prepared.fingerprint, prepared.findings) == (vignette.fingerprint, ())

    edited = prepare_case(case, b"Edited at the source.", reg.get, AppendixLayout())
    assert edited.fingerprint == Fingerprint.of(b"Edited at the source.")
    assert [f.code for f in edited.findings] == ["case.drift"]

    keyed = Case(
        vignette=vignette.model_copy(
            update={"fingerprint": Fingerprint.of(raw, ("k1", b"secret"))}
        )
    )
    with pytest.raises(StructuralError, match="k1"):
        _ = prepare_case(keyed, raw, reg.get, AppendixLayout())
    assert not prepare_case(
        keyed, raw, reg.get, AppendixLayout(), key=("k1", b"secret")
    ).findings
    with pytest.raises(UnicodeDecodeError):
        _ = prepare_case(case, b"\xff", reg.get, AppendixLayout())


def test_cleanup_ops_are_pinned() -> None:
    text = "\r\n  line one\r\n\r\n\r\nline two  \n"
    ops = ("newlines.lf@1", "blank_lines.collapse@1", "strip@1")
    assert clean(text, ops) == "line one\n\nline two"


def test_remote_engine_identity() -> None:
    a = RemoteEngine(base_url=HttpUrl("https://a100.example.org/v1"), model="gemma")
    assert (
        a.digest
        != RemoteEngine(
            base_url=HttpUrl("https://a100.example.org/v1"), model="gemma-2"
        ).digest
    )


def test_chat_template_check(reg: Registry) -> None:
    template = b"{{ bos_token }}{% for m in messages %}{{ m.content }}{% endfor %}"
    engine = local_engine(reg).model_copy(
        update={
            "chat_template": FileDigest(
                path="chat_template.jinja", sha256=hashlib.sha256(template).hexdigest()
            )
        }
    )
    assert check_chat_template(engine, template) == []
    dated = template + b'{{ strftime_now("%d %b %Y") }}'
    assert [f.code for f in check_chat_template(engine, dated)] == [
        "engine.chat_template",
        "engine.chat_template_date",
    ]


def test_suggested_seeds_are_distinct_31_bit() -> None:
    seeds = suggest_seeds(8)
    assert len(set(seeds)) == 8
    assert all(0 <= s < 2**31 for s in seeds)


def test_lint(reg: Registry) -> None:
    ids = world(reg)
    codes = {f.code for f in lint(reg)}
    assert codes == {"model.revision", "engine.closure"}
    skeleton = reg.add(
        Skeleton(
            messages=(Message(role="user", content=(Slot(slot="vignette"),)),),
            body={"temperature": 0.005},
            contract=TextOutput(),
        )
    )
    trial = reg.add(
        Trial(skeleton=skeleton, engine=ids["engine"], cases=(ids["case"],), seeds=(1,))
    )
    assert [f.code for f in lint(reg, [trial])] == ["vllm.temperature_clamped"]


def test_expectations_are_linted_against_their_schema(reg: Registry) -> None:
    ids = world(reg)
    ddx = reg.add(
        ExpectationSchema(
            json_schema={
                "type": "object",
                "properties": {"ddx": {"type": "array", "items": {"type": "string"}}},
                "required": ["ddx"],
            }
        )
    )

    def findings(data: JsonValue, schema: str = ddx) -> list[tuple[str, str]]:
        expectation = reg.add(
            Expectation(case=ids["case"], expectation_schema=schema, data=data)
        )
        return [(f.code, f.message) for f in lint(reg, [expectation])]

    assert findings({"ddx": ["ACS"]}) == []
    assert findings({"ddx": ["ACS", 3]}) == [
        ("expectation.invalid", "at /ddx/1: 3 is not of type 'string'")
    ]
    assert findings({"ddx": [1, 2]}) == [
        ("expectation.invalid", "at /ddx/1: 2 is not of type 'string' (and 1 more)")
    ]
    assert findings([]) == [
        ("expectation.invalid", "at the root: [] is not of type 'object'")
    ]

    def schema_findings(json_schema: dict[str, JsonValue]) -> list[tuple[str, str]]:
        schema = reg.add(ExpectationSchema(json_schema=json_schema))
        return [(f.code, f.message) for f in lint(reg, [schema])]

    tuple_items: dict[str, JsonValue] = {"items": [{"type": "string"}]}
    assert schema_findings(tuple_items) == [
        (
            "expectation_schema.invalid",
            "at /items: [{'type': 'string'}] is not of type 'object', 'boolean'",
        )
    ]
    draft7 = "http://json-schema.org/draft-07/schema#"
    assert schema_findings({"$schema": draft7, **tuple_items}) == []
    assert schema_findings({"$schema": "https://example.org/draft"}) == [
        (
            "expectation_schema.invalid",
            "$schema 'https://example.org/draft' names no draft that can be checked",
        )
    ]

    broken = reg.add(ExpectationSchema(json_schema=tuple_items))
    assert findings(["anything"], broken) == []
    dangling = reg.add(
        ExpectationSchema(json_schema={"properties": {"ddx": {"$ref": "#/$defs/ddx"}}})
    )
    assert schema_findings({"properties": {"ddx": {"$ref": "#/$defs/ddx"}}}) == []
    assert findings({"ddx": []}, dangling) == [
        (
            "expectation.unchecked",
            "a $ref in the schema can't be resolved: /$defs/ddx",
        )
    ]
    assert findings({}, dangling) == []


def test_views_are_checked_against_the_expectation_schema(reg: Registry) -> None:
    closed = reg.add(
        ExpectationSchema(
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"ddx": {"type": "array"}},
            }
        )
    )
    scorer = reg.add(
        Scorer(
            code=RIG,
            expectation_schema=closed,
            views=(
                View(expectation="$.ddx[*]", metric="m"),
                View(expectation="$.targets", metric="m"),
            ),
        )
    )
    assert [(f.code, f.message) for f in lint(reg, [scorer])] == [
        (
            "view.unreachable",
            "view 1's expectation selector '$.targets' reaches nothing in the "
            + "expectation schema",
        )
    ]


def test_languages_are_linted_per_trial(reg: Registry) -> None:
    ids = world(reg)
    trial = resolve(reg.get, ids["trial"], Trial)

    def findings(languages: dict[str, str]) -> list[tuple[str, str, str]]:
        return [
            (f.code, f.level, f.message)
            for f in lint(reg, [ids["trial"]], languages=languages.get)
            if f.code.startswith("language.")
        ]

    case = trial.cases[0]
    assert findings({trial.skeleton: "sv", case: "sv"}) == []
    assert findings({trial.skeleton: "sv", case: "en"}) == [
        ("language.mixed", "warning", "the request is sv, but 1 of 1 cases is en")
    ]
    assert findings({case: "en"}) == [
        ("language.unknown", "info", "the request's language is unknown")
    ]
    assert findings({trial.skeleton: "sv"}) == [
        ("language.unknown", "info", "the language of 1 of 1 cases is unknown")
    ]
    assert [f.code for f in lint(reg, [ids["trial"]])] == []


def test_skeleton_and_engine_compatibility(reg: Registry) -> None:
    ids = world(reg)
    engine = local_engine(reg)

    def local(*argv: str) -> str:
        return reg.add(
            LocalEngine.model_validate({**engine.model_dump(), "argv": argv})
        )

    bare = local()
    parsers = local(
        "--enable-auto-tool-choice",
        "--tool_call_parser=hermes",
        "--reasoning-parser",
        "qwen3",
    )
    remote = reg.add(
        RemoteEngine(base_url=HttpUrl("https://example.org/v1"), model="gemma")
    )
    user = Message(role="user", content=(Slot(slot="vignette"),))
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/A"}},
        "$defs": {"A": {"type": "string"}},
    }
    response_format: dict[str, JsonValue] = {
        "type": "json_schema",
        "json_schema": {"name": "output", "schema": schema},
    }

    def skeleton(contract: OutputContract, **body: JsonValue) -> str:
        return reg.add(Skeleton(messages=(user,), body=body, contract=contract))

    native = skeleton(NativeOutput(), response_format=response_format)
    thinking = skeleton(
        NativeOutput(),
        response_format=response_format,
        chat_template_kwargs={"enable_thinking": True},
    )
    silent = skeleton(
        NativeOutput(),
        response_format=response_format,
        chat_template_kwargs={"enable_thinking": False},
    )
    tool = skeleton(
        ToolOutput(name="f"),
        tools=[{"type": "function", "function": {"name": "f", "parameters": schema}}],
        tool_choice={"type": "function", "function": {"name": "f"}},
    )
    budget = skeleton(TextOutput(), thinking_token_budget=512)

    def findings(skeleton: str, engine: str) -> dict[str, str]:
        trial = reg.add(
            Trial(skeleton=skeleton, engine=engine, cases=(ids["case"],), seeds=(1,))
        )
        return {f.code: f.level for f in lint(reg, [trial])}

    assert findings(native, bare) == {"vllm.grammar_before_reasoning": "info"}
    assert findings(thinking, bare) == {"vllm.grammar_before_reasoning": "warning"}
    assert findings(silent, bare) == {}
    assert findings(native, parsers) == {}
    assert findings(tool, bare) == {"vllm.tools_refused": "warning"}
    assert findings(tool, parsers) == {}
    with_tools = generation_recipe(reg).model_copy(
        update={"toolset": reg.add(Toolset(tools=(web_search(reg),)))}
    )
    native_tools = reg.add(compile_request(with_tools, reg.get))
    text = reg.add(Output(contract=TextOutput()))
    text_tools = reg.add(
        compile_request(with_tools.model_copy(update={"output": text}), reg.get)
    )
    assert findings(native_tools, parsers) == {
        "vllm.native_tools_uncallable": "warning"
    }
    assert findings(text_tools, bare) == {"vllm.tools_refused": "warning"}
    assert findings(text_tools, parsers) == {}
    assert findings(budget, bare) == {"vllm.thinking_budget_refused": "warning"}
    assert findings(budget, local("--reasoning-config", "{}")) == {}
    assert findings(native, remote) == {"schema.ref_unverified": "warning"}
    assert findings(tool, remote) == {"schema.ref_unverified": "warning"}
    assert findings(budget, remote) == {}

    judged = reg.add(
        Skeleton(
            purpose="judge",
            messages=(Message(role="user", content=(Slot(slot="completion"),)),),
            body={"response_format": response_format},
            contract=NativeOutput(),
        )
    )
    judge = reg.add(Judge(skeleton=judged, engine=remote, seeds=(1,)))
    assert [f.code for f in lint(reg, [judge])] == ["schema.ref_unverified"]


def test_execution_schedule() -> None:
    cases = ["a", "b"]
    assert Execution().schedule(cases, 2) == [("a", 0), ("a", 1), ("b", 0), ("b", 1)]
    assert Execution(order="replicate_major@1").schedule(cases, 2) == [
        ("a", 0),
        ("b", 0),
        ("a", 1),
        ("b", 1),
    ]
    shuffled = Execution(order="shuffled@1", shuffle_seed=7)
    assert shuffled.schedule(cases, 2) == shuffled.schedule(cases, 2)
    assert sorted(shuffled.schedule(cases, 2)) == Execution().schedule(cases, 2)
    with pytest.raises(ValidationError, match="shuffle_seed"):
        _ = Execution(shuffle_seed=7)


def test_fingerprint_algorithms() -> None:
    plain = Fingerprint.of(b"vignette")
    assert (
        plain.alg == "sha256" and plain.hex == hashlib.sha256(b"vignette").hexdigest()
    )
    keyed = Fingerprint.of(b"vignette", ("k1", KEY))
    assert (
        keyed.alg == "hmac-sha256" and keyed.key_id == "k1" and keyed.hex != plain.hex
    )
    with pytest.raises(ValidationError, match="key_id"):
        _ = Fingerprint(key_id="k1", hex=plain.hex)
