import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import HttpUrl, JsonValue, ValidationError

from chatddx.core.manifest.bundle import Bundle, Registry
from chatddx.core.manifest.cases import (
    Appendix,
    CaseInput,
    SourceCase,
    normalize,
)
from chatddx.core.manifest.engine import (
    FileDigest,
    LocalEngine,
    RemoteEngine,
    check_chat_template,
)
from chatddx.core.manifest.identity import (
    Component,
    Fingerprint,
    StructuralError,
)
from chatddx.core.manifest.ledger import (
    Call,
    CanaryCall,
    ItemKey,
    JudgeCall,
    Run,
    RunItem,
    RunStarted,
    Score,
    ScoreItem,
    ScoreStarted,
    check_run,
    check_score,
    compare_prompt_tokens,
    fingerprint_prompt_tokens,
    fingerprint_request,
)
from chatddx.core.manifest.lint import lint
from chatddx.core.manifest.request import (
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
from chatddx.core.manifest.scoring import (
    Judge,
)
from chatddx.core.manifest.test.sample import (
    KEY,
    NOW,
    RIG,
    SHA,
    fp,
    generation_skeleton,
    local_engine,
    world,
)
from chatddx.core.manifest.trial import (
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


def test_run_and_score_checks(reg: Registry) -> None:
    ids = world(reg)
    engine = reg.get(ids["engine"])
    assert isinstance(engine, LocalEngine)
    run_id = uuid4()

    def call(model: str, tokens: list[JsonValue] | None = None) -> Call:
        response, prompt_tokens = fingerprint_prompt_tokens(
            {"model": model, "prompt_token_ids": tokens or [2, 106, 1645]}
        )
        return Call(
            request=fp("body"),
            started_at=NOW,
            finished_at=NOW,
            status=200,
            response=response,
            prompt_tokens=prompt_tokens,
        )

    def item(replicate: int, c: Call) -> RunItem:
        return RunItem(
            run=run_id,
            key=ItemKey(case=ids["case"], replicate=replicate),
            vignette=fp("v"),
            call=c,
        )

    started = RunStarted(
        run=run_id, at=NOW, rig=RIG, trial=ids["trial"], canaries=ids["canaries"]
    )
    items = (item(0, call(engine.served_model_name)), item(1, call("other")))
    canaries = (CanaryCall(run=run_id, phase="start", probe=0, call=call("c")),)
    open_run = Run(stages=(started,), items=items, canaries=canaries)
    run = Run(stages=(started, open_run.finish(NOW)), items=items, canaries=canaries)
    assert [f.code for f in check_run(run, reg)] == ["attestation.model"]
    assert run.items[0].call.response == {"model": engine.served_model_name}

    tampered = run.model_copy(update={"items": items[:1]})
    assert "ledger.seal" in [f.code for f in check_run(tampered, reg)]

    rerun = Run(
        stages=(started,),
        items=(
            item(0, call(engine.served_model_name, [2, 106, 9])),
            item(1, items[1].call.model_copy(update={"prompt_tokens": None})),
        ),
    )
    assert [f.code for f in compare_prompt_tokens(rerun, run)] == [
        "attestation.prompt_tokens_drift"
    ]
    assert "attestation.prompt_tokens" in [f.code for f in check_run(rerun, reg)]

    with pytest.raises(StructuralError, match="outside the trial"):
        _ = check_run(Run(stages=(started,), items=(*items, item(2, call("m")))), reg)
    with pytest.raises(StructuralError, match="not planned"):
        bad_canary = CanaryCall(run=run_id, phase="start", probe=1, call=call("c"))
        _ = check_run(Run(stages=(started,), canaries=(bad_canary,)), reg)
    with pytest.raises(ValidationError, match="different logs"):
        _ = Run(
            stages=(started,), items=(items[0].model_copy(update={"run": uuid4()}),)
        )
    with pytest.raises(ValidationError, match="do not follow"):
        _ = Run(stages=(started, started))

    score_id = uuid4()

    def score(*score_items: ScoreItem) -> Score:
        return Score(
            stages=(
                ScoreStarted(
                    score=score_id,
                    run=run_id,
                    at=NOW,
                    rig=RIG,
                    scorer_code=RIG,
                    scoring=ids["scoring"],
                ),
            ),
            items=score_items,
        )

    ok = ScoreItem(
        score=score_id,
        key=ItemKey(case=ids["case"], replicate=0),
        view=1,
        value=1.0,
        judge_calls=(JudgeCall(judge=ids["judge"], seed_index=0, call=call("j")),),
    )
    assert check_score(score(ok), run, reg) == []
    with pytest.raises(StructuralError, match="seed index 1 out of range"):
        _ = check_score(
            score(
                ok.model_copy(
                    update={
                        "judge_calls": (
                            JudgeCall(judge=ids["judge"], seed_index=1, call=call("j")),
                        )
                    }
                )
            ),
            run,
            reg,
        )
    with pytest.raises(StructuralError, match="view 2 is out of range"):
        _ = check_score(score(ok.model_copy(update={"view": 2})), run, reg)


def test_seal_survives_a_storage_roundtrip() -> None:
    run_id = uuid4()
    local = datetime(2026, 9, 29, 2, tzinfo=timezone(timedelta(hours=2)))
    c = Call(request=fp("body"), started_at=local, finished_at=local)
    started = RunStarted(run=run_id, at=local, rig=RIG, trial="sha256:" + "0" * 64)
    items = tuple(
        RunItem(
            run=run_id,
            key=ItemKey(case="sha256:" + d * 64, replicate=r),
            vignette=fp("v"),
            call=c,
        )
        for d in "12"
        for r in range(2)
    )
    canaries = tuple(
        CanaryCall(run=run_id, phase=p, probe=0, call=c) for p in ("start", "end")
    )
    run = Run(stages=(started,), items=items, canaries=canaries)
    assert started.at == NOW and started.at.utcoffset() == timedelta(0)

    stored = Run(
        stages=(RunStarted.parse(started.canonical),),
        items=tuple(RunItem.parse(i.canonical) for i in reversed(items)),
        canaries=tuple(CanaryCall.parse(c.canonical) for c in reversed(canaries)),
    )
    assert stored.seal() == run.seal()
    assert "attempts" not in json.loads(items[0].canonical)["call"]


def test_records_carry_their_schema_version() -> None:
    started = RunStarted(run=uuid4(), at=NOW, rig=RIG, trial="sha256:" + "0" * 64)
    doc = json.loads(started.canonical)
    assert doc["v"] == 1 and doc["stage"] == "started"
    doc["v"] = 2
    with pytest.raises(StructuralError, match="RunStarted v2 is not readable by v1"):
        _ = RunStarted.parse(json.dumps(doc))
    with pytest.raises(StructuralError, match="not a JSON object"):
        _ = RunStarted.parse("[]")


def test_request_fingerprint_is_over_canonical_bytes() -> None:
    a = fingerprint_request({"model": "m", "messages": [], "temperature": 0})
    b = fingerprint_request({"temperature": 0, "messages": [], "model": "m"})
    assert a == b == Fingerprint.of(b'{"messages":[],"model":"m","temperature":0}')


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
