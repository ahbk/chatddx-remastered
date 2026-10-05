import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema.validators import validator_for

from chatddx.core.rig import rig
from chatddx.factors.request import (
    NativeOutput,
    Skeleton,
    TextOutput,
    ToolOutput,
    next_request,
    render,
    tool_calls,
)
from chatddx.facts.facts import Facts
from chatddx.fake_vllm.chat import (
    ANSWER,
    Call,
    Failure,
    Reply,
    accept,
    completion,
    instance,
    respond,
    stream,
    thinking,
)
from chatddx.fake_vllm.served import VERSION, Refused, Served
from chatddx.seed import plan_factors
from chatddx.seed.plan import SAMPLE

QWEN = "Qwen/Qwen3-8B-AWQ"
GPT_OSS = "openai/gpt-oss-20b"
TOOLS = ("--enable-auto-tool-choice", "--tool-call-parser")
# As the old chatddx served them (chatddx 7893656, src/chatddx/data/inventory/malborg.toml).
QWEN_ARGV = ("--reasoning-parser", "qwen3", *TOOLS[:1], TOOLS[1], "hermes")
GPT_OSS_ARGV = ("--reasoning-parser", "openai_gptoss", TOOLS[0], TOOLS[1], "openai")
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
HELD = {"type": "json_schema", "json_schema": {"schema": SCHEMA}}


def body(model: str = QWEN, **fields: Any) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "a cough"}],
    } | fields


def qwen(*argv: str) -> Served:
    return Served.of(QWEN, argv or QWEN_ARGV)


def gpt_oss(*argv: str) -> Served:
    return Served.of(GPT_OSS, argv or GPT_OSS_ARGV)


def reply(served: Served, request: dict[str, Any], runaway: bool = False) -> Reply:
    return respond(accept([served], request), request, runaway)


def refusal(served: Served, request: dict[str, Any]) -> Failure:
    with pytest.raises(Failure) as caught:
        _ = accept([served], request)
    return caught.value


def function(name: str, parameters: Any = None) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": parameters}}


def named(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name}}


def called(*names: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": "a cough"}]
    for n, name in enumerate(names):
        messages += [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call-{n}",
                        "type": "function",
                        "function": {"name": name, "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": f"call-{n}", "content": "done"},
        ]
    return messages


def valid(schema: Any, document: Any) -> bool:
    return not list(validator_for(schema)(schema).iter_errors(document))


# ------------------------------------------------------------------ serving


def test_it_reads_vllm_serve_s_flags_as_vllm_does() -> None:
    served = Served.of(
        QWEN,
        [
            "--served_model_name",
            "sha256:abc",
            "alias",
            "--max-model-len=4096",
            "--port",
            "1",
            "--port",
            "12100",
            "--config=ignored.yaml",
        ],
    )
    assert (served.names, served.name, served.max_model_len, served.port) == (
        ("sha256:abc", "alias"),
        "sha256:abc",
        4096,
        12100,
    )
    assert (Served.of(QWEN).names, Served.of(QWEN).max_model_len) == ((QWEN,), 32768)
    assert Served.of(GPT_OSS).max_model_len == 131072
    assert not Served.of(QWEN, ["--tool-call-parser", "hermes"]).parses_tools
    assert qwen().parses_tools


def test_it_won_t_start_where_vllm_won_t_or_where_it_can_t_follow() -> None:
    with pytest.raises(Refused, match="invalid tool call parser"):
        _ = Served.of(QWEN, ["--enable-auto-tool-choice"])
    with pytest.raises(Refused, match="--config FILE"):
        _ = Served.of(QWEN, ["--config", "serve.yaml"])
    with pytest.raises(Refused, match="as an integer"):
        _ = Served.of(QWEN, ["--max-model-len", "32k"])
    with pytest.raises(Refused, match="no JSON object"):
        _ = Served.of(QWEN, ["--default-chat-template-kwargs", "[]"])


def test_its_fingerprint_follows_its_setup_and_mode() -> None:
    fingerprint = Served.of(QWEN).system_fingerprint
    assert fingerprint is not None and fingerprint.startswith(f"vllm-{VERSION}-")
    assert Served.of(QWEN, ["--port", "1"]).system_fingerprint != fingerprint
    assert Served.of(QWEN, ["--fingerprint-mode", "none"]).system_fingerprint is None
    custom = ["--fingerprint-mode", "custom", "--fingerprint-value", "mine"]
    assert Served.of(QWEN, custom).system_fingerprint == "mine"


def test_it_answers_as_the_model_it_serves_or_not_at_all() -> None:
    served = Served.of(QWEN, ["--served-model-name", "sha256:abc"])
    response = completion(served, body("sha256:abc"), reply(served, body("sha256:abc")))
    assert response["model"] == "sha256:abc"
    assert accept([served], body("")) == served
    missing = refusal(served, body(QWEN))
    assert (missing.status, missing.type, missing.param) == (
        404,
        "NotFoundError",
        "model",
    )
    assert missing.body()["error"]["message"] == f"The model `{QWEN}` does not exist."


# ------------------------------------------------------------------ reasoning


def test_qwen3_thinks_unless_told_not_to() -> None:
    assert reply(qwen(), body()).reasoning == thinking(body())
    off = body(chat_template_kwargs={"enable_thinking": False})
    assert reply(qwen(), off).reasoning is None
    assert reply(qwen(), body(reasoning_effort="none")).reasoning is None
    assert reply(qwen(), body(reasoning_effort="low")).reasoning is not None
    on = body(reasoning_effort="none", chat_template_kwargs={"enable_thinking": True})
    assert reply(qwen(), on).reasoning is not None


def test_the_server_s_template_kwargs_are_the_default_a_request_overrides() -> None:
    quiet = qwen(
        *QWEN_ARGV, "--default-chat-template-kwargs", '{"enable_thinking": false}'
    )
    assert reply(quiet, body()).reasoning is None
    assert reply(quiet, body(reasoning_effort="low")).reasoning is not None
    on = body(chat_template_kwargs={"enable_thinking": True})
    assert reply(quiet, on).reasoning is not None


def test_gpt_oss_always_thinks_and_harmony_takes_three_efforts() -> None:
    off = body(GPT_OSS, chat_template_kwargs={"enable_thinking": False})
    assert reply(gpt_oss(), off).reasoning is not None
    for effort in ("low", "medium", "high"):
        assert reply(gpt_oss(), body(GPT_OSS, reasoning_effort=effort)).reasoning
    none = refusal(gpt_oss(), body(GPT_OSS, reasoning_effort="none"))
    assert none.message == "Harmony does not support reasoning_effort='none'"
    minimal = refusal(gpt_oss(), body(GPT_OSS, reasoning_effort="minimal"))
    assert minimal.message == (
        "reasoning_effort='minimal' is not supported by Harmony. Supported values "
        + "are: high, medium, low."
    )


def test_a_model_of_no_family_it_knows_doesn_t_think() -> None:
    llama = "meta-llama/Llama-3.1-8B"
    assert reply(Served.of(llama), body(llama)).reasoning is None


def test_it_thinks_about_the_fields_it_was_sent() -> None:
    thought = thinking(
        body(GPT_OSS, reasoning_effort="low", temperature=1.0, response_format=HELD)
    )
    assert (
        'reasoning_effort="low" temperature=1.0 response_format=json_schema' in thought
    )
    assert "user (2 words)" in thought


def test_a_thinking_budget_cuts_the_thinking_short_where_reasoning_is_set_up() -> None:
    cut = reply(qwen(), body(thinking_token_budget=3, temperature=0))
    assert (cut.reasoning, cut.content, cut.finish) == ("I am the ", ANSWER, "stop")
    configured = Served.of(
        QWEN, ["--reasoning-config", '{"think_end_str": "</think>"}']
    )
    assert reply(configured, body(thinking_token_budget=3, temperature=0)).content
    refused = refusal(Served.of(QWEN), body(thinking_token_budget=3))
    assert refused.message.startswith(
        "thinking_token_budget is set but reasoning_config is not configured."
    )
    assert refused.status == 400


def test_max_tokens_are_spent_on_thinking_first() -> None:
    cut = reply(qwen(), body(max_completion_tokens=5))
    assert (cut.reasoning, cut.content, cut.finish) == (
        "I am the fake vLLM, ",
        "",
        "length",
    )
    assert reply(qwen(), body(max_tokens=5)) == cut


def test_without_a_reasoning_parser_the_thinking_stays_in_the_content() -> None:
    qwen_reply = reply(Served.of(QWEN), body(temperature=0))
    assert qwen_reply.reasoning is None
    assert (
        qwen_reply.content
        == f"<think>\n{thinking(body(temperature=0))}\n</think>\n\n{ANSWER}"
    )
    harmony = body(GPT_OSS, temperature=0)
    harmony_reply = reply(Served.of(GPT_OSS), harmony)
    assert harmony_reply.reasoning is None
    assert harmony_reply.content == f"analysis{thinking(harmony)}assistantfinal{ANSWER}"


def test_without_a_reasoning_parser_a_grammar_leaves_no_room_to_think() -> None:
    no_reasoner = qwen(TOOLS[0], TOOLS[1], "hermes")
    held = reply(no_reasoner, body(response_format=HELD))
    assert (held.reasoning, json.loads(held.content)) == (None, {"ok": False})
    tool = body(
        tools=[function("final_result", SCHEMA)], tool_choice=named("final_result")
    )
    by_tool = reply(no_reasoner, tool)
    assert (by_tool.reasoning, by_tool.content) == (None, "")
    assert reply(qwen(), tool).reasoning is not None
    free = reply(no_reasoner, body(temperature=0))
    assert free.content.startswith("<think>\n")


def test_it_leaves_the_reasoning_out_when_asked_to() -> None:
    assert reply(qwen(), body(include_reasoning=False)).reasoning is None


# ------------------------------------------------------------------ sampling


def test_only_an_exact_zero_temperature_is_greedy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def answer(**fields: Any) -> str:
        return reply(qwen(), body(**fields)).content

    assert answer(temperature=0, seed=1) == answer(temperature=0, seed=2) == ANSWER
    with caplog.at_level(logging.WARNING):
        tiny = {answer(temperature=0.001, seed=seed) for seed in range(3)}
    assert len(tiny) == 3
    assert "temperature 0.001 is less than 0.01" in caplog.text
    assert "maxed it out to 0.01" in caplog.text
    assert answer(temperature=0.7, seed=5) == answer(temperature=0.7, seed=5)


# ------------------------------------------------------------------ outputs


@pytest.mark.parametrize(
    "path", sorted((SAMPLE / "schemas").glob("*.json")), ids=lambda p: p.stem
)
def test_what_it_writes_holds_to_the_sample_s_schemas(path: Path) -> None:
    schema = json.loads(path.read_text())
    assert valid(schema, instance(schema))


def test_what_it_writes_follows_references_unions_and_counts() -> None:
    schema = {
        "$defs": {
            "Diagnosis": {"type": "object", "properties": {"name": {"type": "string"}}}
        },
        "type": "object",
        "properties": {
            "diagnoses": {
                "type": "array",
                "items": {"$ref": "#/$defs/Diagnosis"},
                "minItems": 2,
                "maxItems": 2,
            },
            "severity": {"enum": ["high", "low"]},
            "kind": {"const": "plan"},
            "maybe": {"anyOf": [{"type": "null"}, {"type": "integer"}]},
        },
    }
    assert instance(schema) == {
        "diagnoses": [{"name": "fake name 1"}, {"name": "fake name 2"}],
        "severity": "high",
        "kind": "plan",
        "maybe": 1,
    }


def test_held_or_shown_a_schema_it_answers_with_a_document_that_holds() -> None:
    assert json.loads(reply(qwen(), body(response_format=HELD)).content) == {
        "ok": False
    }
    shown = {"role": "system", "content": f"Answer with this:\n{json.dumps(SCHEMA)}"}
    messages = [shown, {"role": "user", "content": "a cough"}]
    assert json.loads(reply(qwen(), body(messages=messages)).content) == {"ok": False}
    json_object = body(response_format={"type": "json_object"})
    assert json.loads(reply(qwen(), json_object).content) == {}


# ------------------------------------------------------------------ tools


def test_a_named_tool_is_called_and_stops() -> None:
    tool = body(
        tools=[function("final_result", SCHEMA)], tool_choice=named("final_result")
    )
    called_once = reply(qwen(), tool)
    assert called_once.calls == (
        Call("final_result", '{"ok": false}', "chatcmpl-tool-fake-0"),
    )
    assert (called_once.content, called_once.finish) == ("", "stop")


def test_required_calls_the_toolset_then_the_answer_tool_offered_last() -> None:
    tools = [function("lookup"), function("now"), function("final_result", SCHEMA)]
    choose: dict[str, Any] = {"tools": tools, "tool_choice": "required"}

    def call(*before: str) -> Call:
        [made] = reply(qwen(), body(messages=called(*before), **choose)).calls
        return made

    assert call().name == "lookup"
    assert call("lookup").name == "now"
    assert call("lookup", "now") == Call(
        "final_result", '{"ok": false}', "chatcmpl-tool-fake-2"
    )
    assert reply(qwen(), body(**choose)).finish == "tool_calls"


def test_auto_calls_each_tool_once_then_answers() -> None:
    tools = [function("lookup"), function("now")]
    assert [c.name for c in reply(qwen(), body(tools=tools)).calls] == ["lookup"]
    assert reply(qwen(), body(tools=tools)).finish == "tool_calls"
    done = reply(
        qwen(), body(tools=tools, messages=called("lookup", "now"), temperature=0)
    )
    assert (done.calls, done.content, done.finish) == ((), ANSWER, "stop")


def test_auto_with_a_response_format_answers_by_the_schema_and_calls_nothing() -> None:
    held = reply(qwen(), body(tools=[function("lookup")], response_format=HELD))
    assert (held.calls, json.loads(held.content)) == ((), {"ok": False})


def test_without_a_tool_parser_tools_are_refused() -> None:
    tools = [function("final_result", SCHEMA)]
    for served in (Served.of(QWEN), Served.of(QWEN, ["--tool-call-parser", "hermes"])):
        auto = refusal(served, body(tools=tools))
        assert auto.message == (
            '"auto" tool choice requires --enable-auto-tool-choice and '
            + "--tool-call-parser to be set"
        )
        required = refusal(served, body(tools=tools, tool_choice="required"))
        assert required.message == (
            'tool_choice="required" requires --tool-call-parser to be set'
        )
        by_name = refusal(served, body(tools=tools, tool_choice=named("final_result")))
        assert by_name.message.endswith("requires --tool-call-parser to be set")
    assert reply(Served.of(QWEN), body(tools=tools, tool_choice="none")).calls == ()


def test_without_a_tool_parser_harmony_goes_out_unconstrained() -> None:
    harmony = Served.of(GPT_OSS, ["--reasoning-parser", "openai_gptoss"])
    tool = body(
        GPT_OSS,
        tools=[function("final_result", SCHEMA)],
        tool_choice=named("final_result"),
        temperature=0,
    )
    loose = reply(harmony, tool)
    assert (loose.calls, loose.content, loose.reasoning is not None) == (
        (),
        ANSWER,
        True,
    )


def test_a_tool_choice_must_name_a_tool_it_is_offered() -> None:
    assert refusal(qwen(), body(tool_choice="required")).param == "tool_choice"
    stray = refusal(
        qwen(), body(tools=[function("lookup")], tool_choice=named("other"))
    )
    assert stray.message.startswith(
        "The tool specified in `tool_choice` does not match"
    )
    assert refusal(qwen(), body(tools=[])).message.startswith("`tools` must not be")
    assert refusal(qwen(), body(tools=[function("x")], tool_choice="any")).param == (
        "tool_choice"
    )


# ------------------------------------------------------------------ responses


def test_it_returns_token_ids_only_when_asked() -> None:
    plain = completion(qwen(), body(), reply(qwen(), body()))
    assert plain["prompt_token_ids"] is None
    assert plain["choices"][0]["token_ids"] is None
    asked = body(return_token_ids=True)
    response = completion(qwen(), asked, reply(qwen(), asked))
    usage = response["usage"]
    assert len(response["prompt_token_ids"]) == usage["prompt_tokens"] == 2
    assert len(response["choices"][0]["token_ids"]) == usage["completion_tokens"]
    other = body(
        messages=[{"role": "user", "content": "a fever"}], return_token_ids=True
    )
    assert (
        completion(qwen(), other, reply(qwen(), other))["prompt_token_ids"]
        != (response["prompt_token_ids"])
    )
    assert response["system_fingerprint"] == qwen().system_fingerprint


def test_it_answers_whole_when_asked_not_to_stream() -> None:
    harmony = body(GPT_OSS, temperature=0)
    message = completion(gpt_oss(), harmony, reply(gpt_oss(), harmony))["choices"][0][
        "message"
    ]
    assert (message["content"], message["reasoning"]) == (ANSWER, thinking(harmony))
    assert "tool_calls" not in message


def events(chunks: Iterator[tuple[str, int]]) -> list[Any]:
    lines = [chunk.removeprefix("data: ").strip() for chunk, _ in chunks]
    assert lines[-1] == "[DONE]"
    return [json.loads(line) for line in lines[:-1]]


def streamed(
    served: Served, request: dict[str, Any], runaway: bool = False
) -> list[Any]:
    return events(stream(served, request, reply(served, request, runaway)))


def deltas(chunks: list[Any]) -> list[dict[str, Any]]:
    return [c["choices"][0]["delta"] for c in chunks if c["choices"]]


def test_it_streams_the_thinking_then_the_answer_then_the_usage() -> None:
    request = body(stream_options={"include_usage": True}, temperature=0)
    chunks = streamed(qwen(), request)
    assert deltas(chunks)[0] == {"role": "assistant", "content": ""}
    assert "".join(d.get("reasoning", "") for d in deltas(chunks)) == thinking(request)
    assert "".join(d.get("content", "") for d in deltas(chunks)) == ANSWER
    assert chunks[-2]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["usage"]["prompt_tokens"] == 2
    assert chunks[-1]["system_fingerprint"] == qwen().system_fingerprint
    assert "system_fingerprint" not in chunks[-2]
    assert "system_fingerprint" in streamed(qwen(), body())[-1]


def test_it_streams_the_prompt_s_token_ids_in_its_first_chunk_only() -> None:
    chunks = streamed(qwen(), body(return_token_ids=True))
    assert [("prompt_token_ids" in c) for c in chunks].count(True) == 1
    assert len(chunks[0]["prompt_token_ids"]) == 2
    assert all("token_ids" in c["choices"][0] for c in chunks[1:-1])
    assert "prompt_token_ids" not in streamed(qwen(), body())[0]


def test_it_streams_a_call_as_its_name_then_its_arguments() -> None:
    tool = body(
        tools=[function("final_result", SCHEMA)], tool_choice=named("final_result")
    )
    chunks = streamed(qwen(), tool)
    calls = [call for d in deltas(chunks) for call in d.get("tool_calls", [])]
    assert calls[0]["function"] == {"name": "final_result", "arguments": ""}
    assert "".join(c["function"]["arguments"] for c in calls) == '{"ok": false}'
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    required = streamed(qwen(), tool | {"tool_choice": "required"})
    assert required[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_it_streams_calls_made_at_once_one_after_another_by_their_index() -> None:
    made = Reply(
        None,
        "",
        (Call("lookup", '{"q": "a"}', "call-a"), Call("now", "{}", "call-b")),
        "tool_calls",
    )
    calls = [
        call
        for d in deltas(events(stream(qwen(), body(), made)))
        for call in d.get("tool_calls", [])
    ]
    started = [c for c in calls if "id" in c]
    assert [(c["index"], c["id"], c["function"]["name"]) for c in started] == [
        (0, "call-a", "lookup"),
        (1, "call-b", "now"),
    ]


def test_asked_for_it_it_counts_the_usage_on_every_chunk_as_it_goes() -> None:
    options = {"include_usage": True, "continuous_usage_stats": True}
    chunks = streamed(qwen(), body(stream_options=options))
    counts = [chunk["usage"]["completion_tokens"] for chunk in chunks]
    assert counts[0] == 0
    assert counts == sorted(counts)
    assert counts[-2] == counts[-1] > 0
    assert {chunk["usage"]["prompt_tokens"] for chunk in chunks} == {2}
    alone = streamed(qwen(), body(stream_options={"continuous_usage_stats": True}))
    assert not any("usage" in chunk for chunk in alone)


def test_running_away_it_answers_then_writes_newlines_till_max_tokens_run_out() -> None:
    limited = body(
        max_tokens=100, stream_options={"include_usage": True}, temperature=0
    )
    chunks = streamed(qwen(), limited, runaway=True)
    newlines = sum(d.get("content") == "\n" for d in deltas(chunks))
    assert newlines > 0
    assert (
        "".join(d.get("content", "") for d in deltas(chunks))
        == ANSWER + "\n" * newlines
    )
    assert chunks[-2]["choices"][0]["finish_reason"] == "length"
    assert chunks[-1]["usage"]["completion_tokens"] == 100


def test_running_away_with_no_max_tokens_it_writes_till_the_context_runs_out() -> None:
    small = Served.of(QWEN, [*QWEN_ARGV, "--max-model-len", "500"])
    usage = completion(small, body(), reply(small, body(), runaway=True))["usage"]
    assert usage["total_tokens"] == 500


def test_a_call_or_an_answer_cut_short_doesn_t_run_away() -> None:
    call = reply(qwen(), body(tools=[function("lookup")]), runaway=True)
    cut = reply(qwen(), body(max_tokens=5), runaway=True)
    assert (call.runaway, call.finish) == (0, "tool_calls")
    assert (cut.runaway, cut.finish) == (0, "length")


def test_held_to_a_schema_it_runs_away_before_the_closing_brace() -> None:
    held = reply(qwen(), body(response_format=HELD), runaway=True)
    assert held.content == json.dumps({"ok": False}, indent=2).removesuffix("}")
    assert held.runaway > 0


# ------------------------------------------------------------------ the sample


def sample() -> list[Skeleton]:
    plan = plan_factors(
        SAMPLE / "factors.toml", Facts.load(SAMPLE / "facts.toml"), rig()
    )
    found: list[Skeleton] = []
    for r in plan.records:
        if r.kind == "skeleton":
            skeleton = plan.registry.get(r.digest)
            assert isinstance(skeleton, Skeleton)
            found.append(skeleton)
    return found


def test_it_answers_every_sample_skeleton_as_its_contract_asks() -> None:
    engines = [
        Served.of(QWEN, ["--served-model-name", "qwen", *QWEN_ARGV]),
        Served.of(GPT_OSS, ["--served-model-name", "gpt-oss", *GPT_OSS_ARGV]),
    ]
    skeletons = sample()
    assert len(skeletons) == 12
    for skeleton in skeletons:
        for served in engines:
            request = render(
                skeleton,
                model=served.name,
                seed=7,
                fills={"vignette": "A 72-year-old man.", "appendices": ""},
            )
            response = completion(
                served, request, respond(accept(engines, request), request)
            )
            message = response["choices"][0]["message"]
            schema = skeleton.output_schema
            match skeleton.contract:
                case NativeOutput():
                    assert valid(schema, json.loads(message["content"]))
                case ToolOutput(name=name):
                    [call] = tool_calls(response)
                    assert call.name == name
                    assert valid(schema, json.loads(call.arguments))
                case TextOutput(json_schema=dict() as shown):
                    assert valid(shown, json.loads(message["content"]))
                case TextOutput():
                    assert message["content"]
            assert response["prompt_token_ids"]


def test_a_tool_round_goes_from_the_toolset_to_the_answer_tool() -> None:
    first = body(
        tools=[function("lookup", SCHEMA), function("final_result", SCHEMA)],
        tool_choice="required",
    )
    response = completion(qwen(), first, reply(qwen(), first))
    [lookup] = tool_calls(response)
    second = next_request(first, response, {lookup.id: "nothing found"})
    [answer] = tool_calls(completion(qwen(), second, reply(qwen(), second)))
    assert (lookup.name, answer.name) == ("lookup", "final_result")
    assert lookup.id != answer.id
