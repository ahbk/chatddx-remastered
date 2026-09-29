import json
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import HttpUrl, ValidationError

from chatddx.manifest.bundle import Bundle, Registry
from chatddx.manifest.cases import Appendix, CaseInput, CaseSet, SourceCase, normalize
from chatddx.manifest.engine import (
    FileDigest,
    Hardware,
    LocalEngine,
    ModelArtifact,
    RemoteEngine,
    Runtime,
)
from chatddx.manifest.governance import Clearance, ClearanceError, require_clearance
from chatddx.manifest.identity import Code, Component, Hmac, StructuralError
from chatddx.manifest.ledger import (
    Call,
    ItemKey,
    JudgeCall,
    RunItem,
    RunRecord,
    ScoreItem,
    ScoreRecord,
    check_run,
    check_score,
)
from chatddx.manifest.request import (
    Instructions,
    Message,
    NativeOutput,
    Output,
    Prompt,
    Reasoning,
    RequestSpec,
    Sampling,
    Skeleton,
    Slot,
    TextOutput,
    ToolOutput,
    compile_request,
    render,
)
from chatddx.manifest.scoring import (
    Expectation,
    ExpectationSchema,
    ExpectationSet,
    Judge,
    Scorer,
    Scoring,
    View,
)
from chatddx.manifest.trial import Canary, CanarySet, RunPlan, Trial, Verification

KEY = b"test-key"
RIG = Code(distribution="chatddx", version="0.0.0+dev", revision="abc123")
NOW = datetime(2026, 9, 29, tzinfo=UTC)
SHA = "0" * 64


def hmac_of(text: str) -> Hmac:
    return Hmac.of("k1", KEY, text.encode())


@pytest.fixture
def reg() -> Registry:
    return Registry()


def local_engine(reg: Registry) -> LocalEngine:
    model = reg.add(
        ModelArtifact(
            repo="google/gemma-3-12b-it",
            revision="deadbeef",
            files=(FileDigest(path="model.safetensors", sha256=SHA),),
        )
    )
    return LocalEngine(
        hardware=Hardware(
            gpu="RTX 5090", compute_capability=(12, 0), vram_mib=32607, driver="575.64"
        ),
        runtime=Runtime(version="0.24.0", closure="/nix/store/xxx-vllm-container"),
        model=model,
        chat_template=FileDigest(path="chat_template.jinja", sha256=SHA),
        argv=("--max-model-len", "8192"),
        env={"VLLM_BATCH_INVARIANT": "1"},
    )


def generation_skeleton(reg: Registry) -> Skeleton:
    spec = RequestSpec(
        instructions=reg.add(Instructions(text="You are an emergency physician.")),
        prompt=reg.add(
            Prompt(
                segments=(
                    "Case:\n",
                    Slot(slot="case"),
                    Slot(slot="appendices"),
                    "\n\nDifferential?",
                )
            )
        ),
        output=reg.add(
            Output(
                contract=NativeOutput(),
                json_schema={
                    "type": "object",
                    "properties": {"ddx": {"type": "array"}},
                },
                guidance="Answer in JSON.",
            )
        ),
        sampling=reg.add(Sampling(temperature=0.7, top_p=0.9, max_tokens=1024)),
        reasoning=reg.add(Reasoning(chat_template_kwargs={"enable_thinking": False})),
    )
    _ = reg.add(spec)
    return compile_request(spec, reg.get)


def world(reg: Registry) -> dict[str, str]:
    case = SourceCase(source="registry", id="c1")
    vignette = hmac_of("A 54-year-old with chest pain.")
    appendix = reg.add(Appendix(case=case, vignette=vignette, text="Troponin 80 ng/L."))
    case_input = reg.add(
        CaseInput(case=case, vignette=vignette, appendices=(appendix,))
    )
    case_set = reg.add(
        CaseSet(normalization=("newlines.lf@1", "strip@1"), cases=(case_input,))
    )
    engine = reg.add(local_engine(reg))
    skeleton = reg.add(generation_skeleton(reg))
    trial = reg.add(
        Trial(skeleton=skeleton, engine=engine, cases=case_set, seeds=(1, 2))
    )
    canaries = reg.add(
        CanarySet(
            probes=(Canary(messages=({"role": "user", "content": "2+2?"},), seed=0),)
        )
    )
    plan = reg.add(
        RunPlan(trial=trial, verification=reg.add(Verification(canaries=canaries)))
    )
    schema = reg.add(ExpectationSchema(json_schema={"type": "object"}))
    expectation = reg.add(
        Expectation(case=case_input, json_schema=schema, data={"ddx": ["ACS"]})
    )
    judge_skeleton = reg.add(
        Skeleton(
            purpose="judge",
            messages=(
                Message(role="user", content=("Grade: ", Slot(slot="completion"))),
            ),
            body={"temperature": 0},
            contract=TextOutput(),
        )
    )
    judge = reg.add(Judge(skeleton=judge_skeleton, engine=engine, seeds=(7,)))
    scoring = reg.add(
        Scoring(
            scorer=reg.add(
                Scorer(
                    code=RIG,
                    consumes=schema,
                    views=(
                        View(
                            name="top1",
                            output="/ddx/0",
                            expectation="/ddx",
                            metric="match",
                        ),
                    ),
                )
            ),
            expectations=reg.add(
                ExpectationSet(json_schema=schema, items=(expectation,))
            ),
            judges=(judge,),
        )
    )
    return {
        "plan": plan,
        "case": case_input,
        "scoring": scoring,
        "judge": judge,
        "engine": engine,
    }


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
    trial = reg.get(reg.get(ids["plan"]).refs()[0].digest)
    paths = {site.path: site.kinds for site in trial.refs()}
    assert paths == {
        "/skeleton": ("skeleton",),
        "/engine": ("engine.local", "engine.remote"),
        "/cases": ("case_set",),
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
    bundle = reg.bundle([ids["plan"], ids["scoring"]], generator=RIG)
    assert bundle.case_derived
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
        skeleton=ids["engine"], engine=ids["engine"], cases=ids["case"], seeds=(1,)
    )
    _ = reg.add(bad)
    with pytest.raises(StructuralError, match="is not one of"):
        reg.check([bad.digest])
    dangling = RunPlan(trial="sha256:" + SHA)
    _ = reg.add(dangling)
    with pytest.raises(StructuralError, match="is missing"):
        reg.check([dangling.digest])


def test_cross_checks(reg: Registry) -> None:
    case = SourceCase(source="registry", id="c1")
    other = reg.add(
        Appendix(
            case=SourceCase(source="registry", id="c2"), vignette=hmac_of("v"), text="x"
        )
    )
    ci = CaseInput(case=case, vignette=hmac_of("v"), appendices=(other,))
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

    def call(model: str) -> Call:
        return Call(
            request=hmac_of("body"),
            started_at=NOW,
            finished_at=NOW,
            status=200,
            response={"model": model},
        )

    run = RunRecord(
        id=uuid4(),
        plan=ids["plan"],
        rig=RIG,
        started_at=NOW,
        finished_at=NOW,
        items=(
            RunItem(
                key=ItemKey(case=ids["case"], replicate=0),
                vignette=hmac_of("v"),
                call=call(engine.served_model_name),
            ),
            RunItem(
                key=ItemKey(case=ids["case"], replicate=1),
                vignette=hmac_of("v"),
                call=call("other"),
            ),
        ),
    )
    assert [f.code for f in check_run(run, reg)] == ["attestation.model"]
    assert run.seal().startswith("sha256:")

    stray = run.model_copy(
        update={
            "items": (
                *run.items,
                run.items[0].model_copy(
                    update={"key": ItemKey(case=ids["case"], replicate=2)}
                ),
            )
        }
    )
    with pytest.raises(StructuralError, match="outside the trial"):
        _ = check_run(stray, reg)

    def score(*items: ScoreItem) -> ScoreRecord:
        return ScoreRecord(
            id=uuid4(),
            run=run.id,
            scoring=ids["scoring"],
            rig=RIG,
            scorer_observed=RIG,
            created_at=NOW,
            items=items,
        )

    ok = ScoreItem(
        key=ItemKey(case=ids["case"], replicate=0),
        view="top1",
        value=1.0,
        judge_calls=(JudgeCall(judge=ids["judge"], seed_index=0, call=call("j")),),
    )
    assert check_score(score(ok), run, reg) == []
    with pytest.raises(StructuralError, match="out of range"):
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


def test_remote_engine_identity() -> None:
    a = RemoteEngine(base_url=HttpUrl("https://a100.example.org/v1"), model="gemma")
    assert (
        a.digest
        != RemoteEngine(
            base_url=HttpUrl("https://a100.example.org/v1"), model="gemma-2"
        ).digest
    )


def test_clearance_is_a_hard_block() -> None:
    cleared = [Clearance(origin=HttpUrl("https://a100.example.org"), case_derived=True)]
    require_clearance("https://a100.example.org/v1/chat/completions", cleared)
    with pytest.raises(ClearanceError):
        require_clearance("https://elsewhere.example.org/v1", cleared)
