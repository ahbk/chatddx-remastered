import json
import math
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import JsonValue, ValidationError

from chatddx.factors.base import Fingerprint, StructuralError, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Case
from chatddx.factors.engine import LocalEngine
from chatddx.factors.request import Output, Tool, ToolOutput, Toolset, compile_request
from chatddx.factors.scoring import Scorer, Scoring, View
from chatddx.factors.test.sample import (
    NOW,
    RIG,
    fp,
    generation_recipe,
    world,
)
from chatddx.factors.trial import Execution, Trial
from chatddx.ledger import (
    Call,
    CanaryCall,
    ItemKey,
    JudgeCall,
    Run,
    RunFinished,
    RunItem,
    RunStarted,
    Score,
    ScoreFinished,
    ScoreItem,
    ScoreStarted,
    ToolCode,
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

    vignette = resolve(reg.get, ids["case"], Case).vignette.fingerprint

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
    canaries = tuple(
        CanaryCall(run=run_id, phase=p, canary=0, call=call("c"))
        for p in ("start", "end")
    )
    open_run = Run(stages=(started,), items=items, canaries=canaries)
    run = Run(stages=(started, open_run.finish(NOW)), items=items, canaries=canaries)
    assert [f.code for f in check_run(run, reg)] == ["attestation.model"]
    unprobed = Run(stages=(started,), items=items, canaries=canaries[:1])
    assert [
        f.message
        for f in check_run(
            unprobed.model_copy(update={"stages": (started, unprobed.finish(NOW))}),
            reg,
        )
        if f.code == "run.incomplete"
    ] == ["1 canary calls missing"]
    assert run.items[0].call.response == {"model": engine.served_model_name}
    unnamed = items[0].call.model_copy(update={"response": {"choices": []}})
    silent = Run(
        stages=(started,), items=(items[0].model_copy(update={"call": unnamed}),)
    )
    assert [(f.code, f.message) for f in check_run(silent, reg)] == [
        (
            "attestation.model",
            f"engine returned no model name, declared {engine.served_model_name!r}",
        )
    ]
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
    assert [f.code for f in compare_prompt_tokens(rerun, run, reg)] == [
        "attestation.prompt_tokens_drift"
    ]
    trial = resolve(reg.get, ids["trial"], Trial)

    def run_of(**changes: object) -> Run:
        other = reg.add(Trial.model_validate({**trial.model_dump(), **changes}))
        moved = started.model_copy(update={"trial": other})
        return Run(stages=(moved,), items=rerun.items)

    reseeded = run_of(seeds=(9, 10))
    assert [f.code for f in compare_prompt_tokens(reseeded, run, reg)] == [
        "attestation.prompt_tokens_drift"
    ]
    with pytest.raises(StructuralError, match="different cleanup"):
        _ = compare_prompt_tokens(run_of(cleanup=("strip@1",)), run, reg)
    assert "attestation.prompt_tokens" in [f.code for f in check_run(rerun, reg)]

    with pytest.raises(StructuralError, match="outside the trial"):
        _ = check_run(Run(stages=(started,), items=(*items, item(2, call("m")))), reg)
    with pytest.raises(StructuralError, match="not planned"):
        bad_canary = CanaryCall(run=run_id, phase="start", canary=1, call=call("c"))
        _ = check_run(Run(stages=(started,), canaries=(bad_canary,)), reg)
    with pytest.raises(StructuralError, match="duplicate canary calls"):
        _ = check_run(Run(stages=(started,), canaries=canaries[:1] * 2), reg)
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
    assert [(f.code, f.message) for f in check_score(score(ok), open_run, reg)] == [
        ("score.run_unfinished", "the run hasn't finished")
    ]
    late = Run(
        stages=(started, open_run.finish(NOW + timedelta(seconds=1))),
        items=items,
        canaries=canaries,
    )
    assert [(f.code, f.message) for f in check_score(score(ok), late, reg)] == [
        ("score.run_unfinished", "the score started before the run finished")
    ]
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
    other_code = RIG.model_copy(update={"version": "0.0.1"})
    elsewhere = score(ok).started.model_copy(update={"scorer_code": other_code})
    assert [
        (f.code, f.message)
        for f in check_score(Score(stages=(elsewhere,), items=(ok,)), run, reg)
    ] == [
        (
            "score.scorer_code",
            "the scorer code that ran (chatddx 0.0.1 abc123) isn't the code the scorer "
            + "pins (chatddx 0.0.0+dev abc123)",
        )
    ]
    with pytest.raises(StructuralError, match="view 2 is out of range"):
        _ = check_score(score(ok.model_copy(update={"view": 2})), run, reg)
    with pytest.raises(StructuralError, match="duplicate score items"):
        _ = check_score(score(ok, ok), run, reg)
    with pytest.raises(StructuralError, match="is not view 0's judge"):
        _ = check_score(score(ok.model_copy(update={"view": 0})), run, reg)
    with pytest.raises(StructuralError, match="duplicate judge calls"):
        _ = check_score(
            score(ok.model_copy(update={"judge_calls": ok.judge_calls * 2})), run, reg
        )

    unjudged = ok.model_copy(update={"judge_calls": ()})
    assert [(f.code, f.message) for f in check_score(score(unjudged), run, reg)] == [
        ("judge.incomplete", "view 1's value rests on 0 of 1 judge seeds")
    ]
    unscored = unjudged.model_copy(update={"value": None})
    assert check_score(score(unscored), run, reg) == []

    partial = score(ok)
    finished = partial.model_copy(
        update={"stages": (*partial.stages, partial.finish(NOW))}
    )
    assert [(f.code, f.message) for f in check_score(finished, run, reg)] == [
        ("score.incomplete", "3 items missing")
    ]


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
        CanaryCall(run=run_id, phase=p, canary=0, call=c) for p in ("start", "end")
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


def test_execution_is_checked_against_the_calls(reg: Registry) -> None:
    ids = world(reg)
    run_id = uuid4()
    vignette = resolve(reg.get, ids["case"], Case).vignette.fingerprint

    def at(seconds: int) -> datetime:
        return NOW + timedelta(seconds=seconds)

    def item(replicate: int, start: int, end: int, attempts: int = 1) -> RunItem:
        call = Call(
            request=fp("body"),
            started_at=at(start),
            finished_at=at(end),
            attempts=attempts,
            prompt_tokens=fp("tokens"),
        )
        return RunItem(
            run=run_id,
            key=ItemKey(case=ids["case"], replicate=replicate),
            vignette=vignette,
            call=call,
        )

    def check(execution: Execution, *items: RunItem) -> list[tuple[str, str]]:
        started = RunStarted(
            run=run_id, at=NOW, rig=RIG, trial=ids["trial"], execution=execution
        )
        run = Run(stages=(started,), items=items)
        return [(f.code, f.message) for f in check_run(run, reg)]

    sequential = Execution()
    assert check(sequential, item(0, 0, 5), item(1, 5, 9)) == []
    assert check(sequential, item(0, 0, 5, attempts=2), item(1, 5, 9)) == [
        ("execution.retries", "1 calls took more than the 1 attempts allowed")
    ]
    assert check(Execution(retries=1), item(0, 0, 5, attempts=2)) == []
    assert check(sequential, item(0, 5, 9), item(1, 0, 5)) == [
        ("execution.order", "1 items were sent before items scheduled ahead of them")
    ]
    shuffled = Execution(order="shuffled@1", shuffle_seed=7)
    first, second = (r for _, r in shuffled.schedule((ids["case"],), 2))
    assert check(shuffled, item(first, 0, 5), item(second, 5, 9)) == []
    assert [c for c, _ in check(shuffled, item(second, 0, 5), item(first, 5, 9))] == [
        "execution.order"
    ]
    assert check(sequential, item(0, 0, 6), item(1, 5, 9)) == [
        ("execution.concurrency", "2 calls were in flight at once, declared 1")
    ]
    assert check(Execution(concurrency=2), item(0, 0, 6), item(1, 5, 9)) == []


def test_canaries_bracket_the_items(reg: Registry) -> None:
    ids = world(reg)
    run_id = uuid4()
    vignette = resolve(reg.get, ids["case"], Case).vignette.fingerprint

    def call(start: int, end: int) -> Call:
        return Call(
            request=fp("body"),
            started_at=NOW + timedelta(seconds=start),
            finished_at=NOW + timedelta(seconds=end),
        )

    item = RunItem(
        run=run_id,
        key=ItemKey(case=ids["case"], replicate=0),
        vignette=vignette,
        call=call(10, 20),
    )

    def check(start: tuple[int, int], end: tuple[int, int]) -> list[str]:
        started = RunStarted(
            run=run_id, at=NOW, rig=RIG, trial=ids["trial"], canaries=ids["canaries"]
        )
        canaries = (
            CanaryCall(run=run_id, phase="start", canary=0, call=call(*start)),
            CanaryCall(run=run_id, phase="end", canary=0, call=call(*end)),
        )
        run = Run(stages=(started,), items=(item,), canaries=canaries)
        return [f.message for f in check_run(run, reg) if f.code == "canary.phase"]

    assert check((0, 10), (20, 30)) == []
    assert check((0, 15), (25, 30)) == [
        "1 start canary calls hadn't finished when the first item was sent"
    ]
    assert check((0, 5), (15, 30)) == [
        "1 end canary calls started before the last item call finished"
    ]


def test_judge_calls_are_checked_against_the_score_execution(reg: Registry) -> None:
    ids = world(reg)
    run_id, score_id = uuid4(), uuid4()
    vignette = resolve(reg.get, ids["case"], Case).vignette.fingerprint
    keys = [ItemKey(case=ids["case"], replicate=r) for r in range(2)]
    run = Run(
        stages=(RunStarted(run=run_id, at=NOW, rig=RIG, trial=ids["trial"]),),
        items=tuple(
            RunItem(
                run=run_id,
                key=k,
                vignette=vignette,
                call=Call(request=fp("body"), started_at=NOW, finished_at=NOW),
            )
            for k in keys
        ),
    )

    run = Run(stages=(*run.stages, run.finish(NOW)), items=run.items)

    def judged(replicate: int, start: int, end: int, attempts: int = 1) -> ScoreItem:
        call = Call(
            request=fp("judge"),
            started_at=NOW + timedelta(seconds=start),
            finished_at=NOW + timedelta(seconds=end),
            attempts=attempts,
        )
        return ScoreItem(
            score=score_id,
            key=keys[replicate],
            view=1,
            value=1.0,
            judge_calls=(JudgeCall(judge=ids["judge"], seed_index=0, call=call),),
        )

    def check(execution: Execution, *items: ScoreItem) -> list[str]:
        started = ScoreStarted(
            score=score_id,
            run=run_id,
            at=NOW,
            rig=RIG,
            scorer_code=RIG,
            scoring=ids["scoring"],
            execution=execution,
        )
        score = Score(stages=(started,), items=items)
        return [f.code for f in check_score(score, run, reg)]

    sequential = Execution()
    assert check(sequential, judged(0, 0, 5), judged(1, 5, 9)) == []
    assert check(sequential, judged(0, 0, 5, attempts=2)) == ["execution.retries"]
    assert check(sequential, judged(0, 5, 9), judged(1, 0, 5)) == ["execution.order"]
    assert check(sequential, judged(0, 0, 6), judged(1, 5, 9)) == [
        "execution.concurrency"
    ]
    assert check(Execution(concurrency=2), judged(0, 0, 6), judged(1, 5, 9)) == []


def test_records_refuse_what_cannot_have_happened() -> None:
    later = NOW + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="finished before it started"):
        _ = Call(request=fp("body"), started_at=later, finished_at=NOW)
    with pytest.raises(ValidationError, match="result"):
        _ = ToolRun.model_validate(
            {"id": "c0", "name": "t", "started_at": NOW, "finished_at": NOW}
        )
    failed = ToolRun(
        id="c0", name="t", started_at=NOW, finished_at=NOW, result="r", error="e"
    )
    assert json.loads(failed.model_dump_json())["result"] == "r"
    with pytest.raises(ValidationError, match="finite number"):
        _ = ScoreItem(
            score=uuid4(),
            key=ItemKey(case="sha256:" + "1" * 64, replicate=0),
            view=0,
            value=math.nan,
        )

    run_id, trial, canaries = uuid4(), "sha256:" + "0" * 64, "sha256:" + "1" * 64
    started = RunStarted(run=run_id, at=later, rig=RIG, trial=trial)
    with pytest.raises(ValidationError, match="finished before it started"):
        _ = Run(stages=(started, RunFinished(run=run_id, at=NOW, seal=trial)))

    def probed(*phases: Literal["start", "end"]) -> RunStarted:
        return RunStarted(
            run=run_id,
            at=NOW,
            rig=RIG,
            trial=trial,
            canaries=canaries,
            verify_at=phases,
        )

    assert probed("end", "start").canonical == probed("start", "end").canonical
    with pytest.raises(ValidationError, match="duplicates"):
        _ = probed("end", "end")
    with pytest.raises(ValidationError, match="at least 1"):
        _ = probed()
    with pytest.raises(ValidationError, match="goes with a canary set"):
        _ = RunStarted(run=run_id, at=NOW, rig=RIG, trial=trial, verify_at=("end",))


def test_only_items_are_case_derived() -> None:
    records = (
        RunStarted,
        RunItem,
        CanaryCall,
        RunFinished,
        ScoreStarted,
        ScoreItem,
        ScoreFinished,
    )
    assert [r for r in records if r.case_derived] == [RunItem, ScoreItem]


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
        update={
            "output": answer,
            "toolset": reg.add(Toolset(tools=(web,), max_rounds=2)),
        }
    )
    skeleton = reg.add(compile_request(recipe, reg.get))
    trial = reg.add(
        Trial(skeleton=skeleton, engine=ids["engine"], cases=(ids["case"],), seeds=(1,))
    )
    run_id = uuid4()
    started = RunStarted(
        run=run_id,
        at=NOW,
        rig=RIG,
        trial=trial,
        tool_code=(ToolCode(tool=web, code=RIG),),
    )
    vignette = resolve(reg.get, ids["case"], Case).vignette.fingerprint

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

    def code_findings(*entries: ToolCode) -> list[tuple[str, str]]:
        recorded = started.model_copy(update={"tool_code": entries})
        return [
            (f.code, f.message)
            for f in check_run(Run(stages=(recorded,)), reg)
            if f.code == "tools.code"
        ]

    assert code_findings() == [
        ("tools.code", "the code tool 'web_search' ran with isn't recorded")
    ]
    elsewhere = RIG.model_copy(update={"version": "0.0.1"})
    assert code_findings(ToolCode(tool=web, code=elsewhere)) == [
        (
            "tools.code",
            "tool 'web_search' ran with chatddx 0.0.1 abc123, not the code it pins "
            + "(chatddx 0.0.0+dev abc123)",
        )
    ]
    with pytest.raises(StructuralError, match="the skeleton lacks"):
        _ = code_findings(ToolCode(tool=web, code=RIG), ToolCode(tool=answer, code=RIG))
    with pytest.raises(ValidationError, match="duplicates"):
        _ = RunStarted(
            run=run_id,
            at=NOW,
            rig=RIG,
            trial=trial,
            tool_code=(ToolCode(tool=web, code=RIG),) * 2,
        )
    zero = "sha256:" + "0" * 64
    either = (ToolCode(tool=web, code=RIG), ToolCode(tool=zero, code=RIG))
    reordered = started.model_validate(
        {**started.model_dump(), "tool_code": either[::-1]}
    )
    assert [e.tool for e in reordered.tool_code] == sorted([web, zero])
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
    refused = ToolRun(
        id="c0",
        name="fetch",
        started_at=NOW,
        finished_at=NOW,
        result="There is no tool named fetch.",
        error="unknown tool",
    )
    assert check(call("fetch"), Turn(tools=(refused,), call=call("answer"))) == []
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


def test_score_views_are_checked_against_the_output_schema(reg: Registry) -> None:
    ids = world(reg)
    scoring = resolve(reg.get, ids["scoring"], Scoring)
    expectation_schema = resolve(reg.get, scoring.scorer, Scorer).expectation_schema
    scorer = reg.add(
        Scorer(
            code=RIG.model_copy(update={"revision": None}),
            entry_point="chatddx_scoring.match:score",
            expectation_schema=expectation_schema,
            views=(
                View(output="$.ddx[*]", metric="m"),
                View(output="$.ddx.first", metric="m"),
            ),
        )
    )
    unreachable = reg.add(Scoring(scorer=scorer, expectations=scoring.expectations))
    run_id, score_id = uuid4(), uuid4()
    opened = Run(stages=(RunStarted(run=run_id, at=NOW, rig=RIG, trial=ids["trial"]),))
    run = Run(stages=(opened.started, opened.finish(NOW)))
    score = Score(
        stages=(
            ScoreStarted(
                score=score_id,
                run=run_id,
                at=NOW,
                rig=RIG,
                scorer_code=RIG,
                scoring=unreachable,
            ),
        )
    )
    assert [(f.code, f.message) for f in check_score(score, run, reg)] == [
        (
            "view.unreachable",
            "view 1's output selector '$.ddx.first' reaches nothing in the run's "
            + "output schema",
        )
    ]
