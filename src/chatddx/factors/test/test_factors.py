import hashlib
import json
from typing import Literal

import pytest
from pydantic import HttpUrl, JsonValue, ValidationError

from chatddx.factors.base import (
    Component,
    Fingerprint,
    StructuralError,
    sha256_digest,
)
from chatddx.factors.bundle import Bundle, Registry
from chatddx.factors.cases import (
    Appendix,
    CaseInput,
    SourceCase,
    normalize,
)
from chatddx.factors.engine import (
    FileDigest,
    LocalEngine,
    RemoteEngine,
    check_chat_template,
)
from chatddx.factors.lint import lint
from chatddx.factors.request import (
    Example,
    FewShot,
    Instructions,
    Message,
    NativeOutput,
    Output,
    Prompt,
    Reasoning,
    Recipe,
    Sampling,
    Skeleton,
    Slot,
    TextOutput,
    ToolOutput,
    compile_request,
    render,
)
from chatddx.factors.scoring import (
    Judge,
)
from chatddx.factors.test.sample import (
    KEY,
    RIG,
    SHA,
    fp,
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
        prompt=reg.add(Prompt(segments=(Slot(slot="case"),))),
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
    appendices = CaseInput.model_json_schema()["properties"]["appendices"]
    assert appendices["items"]["x-ref"] == ["appendix"]


def test_greedy_drops_sampling_noise() -> None:
    assert (
        Sampling(temperature=0, top_p=0.9, top_k=5).digest
        == Sampling(temperature=0).digest
    )
    skeleton = Skeleton(
        messages=(Message(role="user", content=(Slot(slot="case"),)),),
        body={"temperature": 0, "min_p": 0.1},
        contract=TextOutput(),
    )
    assert "min_p" not in skeleton.body
    body = render(skeleton, model="m", seed=42, fills={"case": "x"})
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
        fills={"case": "{{not a template}}", "appendices": appendices},
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


def test_few_shot_developer_role_and_budgets(reg: Registry) -> None:
    spec = Recipe(
        instructions=reg.add(Instructions(role="developer", text="Be terse.")),
        few_shot=reg.add(
            FewShot(
                messages=(
                    Example(role="user", content="Fever and rash."),
                    Example(role="assistant", content='{"ddx": ["measles"]}'),
                )
            )
        ),
        prompt=reg.add(Prompt(segments=(Slot(slot="case"),))),
        output=reg.add(Output(contract=TextOutput())),
        sampling=reg.add(
            Sampling(temperature=0, max_output_tokens=64, max_tokens_key="max_tokens")
        ),
        reasoning=reg.add(Reasoning(effort="low", thinking_token_budget=256)),
    )
    skeleton = compile_request(spec, reg.get)
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
    user = Message(role="user", content=(Slot(slot="case"),))
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
                    role="user", content=(Slot(slot="case"), Slot(slot="completion"))
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


def test_engine_argv_cannot_override_manifest(reg: Registry) -> None:
    engine = local_engine(reg)
    with pytest.raises(ValidationError, match="served-model-name"):
        _ = LocalEngine.model_validate(
            {**engine.model_dump(), "argv": ["--served-model-name=x"]}
        )
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
    case = SourceCase(source="registry", id="c1")
    other = reg.add(
        Appendix(
            case=SourceCase(source="registry", id="c2"), vignette=fp("v"), text="x"
        )
    )
    ci = CaseInput(case=case, vignette=fp("v"), appendices=(other,))
    _ = reg.add(ci)
    with pytest.raises(StructuralError, match="bound to another case"):
        reg.check([ci.digest])


def test_normalization_ops_are_pinned() -> None:
    text = "\r\n  line one\r\n\r\n\r\nline two  \n"
    ops = ("newlines.lf@1", "blank_lines.collapse@1", "strip@1")
    assert normalize(text, ops) == "line one\n\nline two"


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
            messages=(Message(role="user", content=(Slot(slot="case"),)),),
            body={"temperature": 0.005},
            contract=TextOutput(),
        )
    )
    trial = reg.add(
        Trial(skeleton=skeleton, engine=ids["engine"], cases=(ids["case"],), seeds=(1,))
    )
    assert [f.code for f in lint(reg, [trial])] == ["vllm.temperature_clamped"]


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
