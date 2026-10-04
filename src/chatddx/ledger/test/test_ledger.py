import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import JsonValue, ValidationError

from chatddx.factors.base import Fingerprint, StructuralError, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import CaseInput
from chatddx.factors.engine import LocalEngine
from chatddx.factors.request import Output, Tool, ToolOutput, Toolset, compile_request
from chatddx.factors.test.sample import (
    NOW,
    RIG,
    fp,
    generation_recipe,
    world,
)
from chatddx.factors.trial import Trial
from chatddx.ledger.ledger import (
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
    ToolRun,
    Turn,
    check_run,
    check_score,
    compare_prompt_tokens,
    fingerprint_prompt_tokens,
    fingerprint_request,
)


@pytest.fixture
def reg() -> Registry:
    return Registry()


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

    vignette = resolve(reg.get, ids["case"], CaseInput).vignette

    def item(replicate: int, c: Call, observed: Fingerprint = vignette) -> RunItem:
        return RunItem(
            run=run_id,
            key=ItemKey(case=ids["case"], replicate=replicate),
            vignette=observed,
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
    drifted = Run(
        stages=(started,),
        items=(item(0, call(engine.served_model_name), fp("edited at the source")),),
    )
    assert [(f.code, f.subject) for f in check_run(drifted, reg)] == [
        ("case.drift", str(drifted.items[0].key))
    ]

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
    authored = fingerprint_request(
        {"response_format": {"properties": {"a": 1, "b": 2}}}
    )
    flipped = fingerprint_request({"response_format": {"properties": {"b": 2, "a": 1}}})
    assert authored != flipped


def test_tool_rounds_are_checked(reg: Registry) -> None:
    ids = world(reg)
    engine = reg.get(ids["engine"])
    assert isinstance(engine, LocalEngine)
    web = reg.add(
        Tool(
            name="web_search",
            description="Search the web.",
            parameters={"type": "object"},
            code=RIG,
            entry_point="chatddx_tools.web:search",
        )
    )
    answer = reg.add(
        Output(
            contract=ToolOutput(name="answer", description="Answer."),
            json_schema={"type": "object"},
        )
    )
    recipe = generation_recipe(reg).model_copy(
        update={"output": answer, "tools": reg.add(Toolset(tools=(web,), max_rounds=2))}
    )
    skeleton = reg.add(compile_request(recipe, reg.get))
    trial = reg.add(
        Trial(skeleton=skeleton, engine=ids["engine"], cases=(ids["case"],), seeds=(1,))
    )
    run_id = uuid4()
    started = RunStarted(run=run_id, at=NOW, rig=RIG, trial=trial)
    vignette = resolve(reg.get, ids["case"], CaseInput).vignette

    def call(*names: str, model: str = engine.served_model_name) -> Call:
        calls: list[JsonValue] = [
            {
                "id": f"c{i}",
                "type": "function",
                "function": {"name": n, "arguments": "{}"},
            }
            for i, n in enumerate(names)
        ]
        message: dict[str, JsonValue] = {"role": "assistant", "tool_calls": calls}
        response, prompt_tokens = fingerprint_prompt_tokens(
            {"model": model, "choices": [{"message": message}], "prompt_token_ids": [1]}
        )
        return Call(
            request=fp("body"),
            started_at=NOW,
            finished_at=NOW,
            status=200,
            response=response,
            prompt_tokens=prompt_tokens,
        )

    def ran(*ids_: str, name: str = "web_search") -> tuple[ToolRun, ...]:
        return tuple(
            ToolRun(id=i, name=name, started_at=NOW, finished_at=NOW, result="r")
            for i in ids_
        )

    def check(first: Call, *turns: Turn) -> list[str]:
        item = RunItem(
            run=run_id,
            key=ItemKey(case=ids["case"], replicate=0),
            vignette=vignette,
            call=first,
            turns=turns,
        )
        return [f.code for f in check_run(Run(stages=(started,), items=(item,)), reg)]

    assert check(call("answer")) == []
    assert check(call("web_search"), Turn(tools=ran("c0"), call=call("answer"))) == []
    searched = Turn(tools=ran("c0"), call=call("web_search"))
    assert check(call("web_search"), searched, searched) == ["tools.unanswered"]
    assert check(call("web_search"), searched) == ["tools.unanswered"]
    drifted = Turn(tools=ran("c0"), call=call("answer", model="other"))
    assert check(call("web_search"), drifted) == ["attestation.model"]
    with pytest.raises(StructuralError, match="more than 2 rounds"):
        _ = check(call("web_search"), searched, searched, searched)
    with pytest.raises(StructuralError, match="no tool named 'fetch'"):
        _ = check(call("web_search"), Turn(tools=ran("c0", name="fetch"), call=call()))
    with pytest.raises(StructuralError, match="didn't make"):
        _ = check(call("web_search"), Turn(tools=ran("c9"), call=call("answer")))

    plain = RunItem(
        run=run_id,
        key=ItemKey(case=ids["case"], replicate=0),
        vignette=vignette,
        call=call(),
        turns=(Turn(tools=ran("c0"), call=call()),),
    )
    plain_run = Run(
        stages=(RunStarted(run=run_id, at=NOW, rig=RIG, trial=ids["trial"]),),
        items=(plain,),
    )
    with pytest.raises(StructuralError, match="no tools"):
        _ = check_run(plain_run, reg)
    assert (
        "turns"
        not in RunItem.model_validate(
            {**plain.model_dump(), "turns": ()}
        ).canonical_doc()
    )
