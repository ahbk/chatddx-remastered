import json
import time
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import JsonValue

from chatddx.fake_vllm.served import Served
from chatddx.fake_vllm.server import serving
from chatddx.runner.wire import Rejected, assemble, chunks, events, post, request

SERVED = Served.of(
    "Qwen/Qwen3-8B-AWQ",
    [
        "--served-model-name",
        "sha256:abc",
        "--reasoning-parser",
        "qwen3",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "hermes",
    ],
)
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
STREAM: dict[str, Any] = {"stream": True, "stream_options": {"include_usage": True}}


def body(**fields: Any) -> dict[str, Any]:
    return {
        "model": "sha256:abc",
        "messages": [{"role": "user", "content": "a cough"}],
        "temperature": 0.6,
        "seed": 7,
        "return_token_ids": True,
    } | fields


def function(name: str, parameters: Any = None) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": parameters}}


@pytest.fixture
def url() -> Iterator[str]:
    with serving([SERVED], "127.0.0.1", 0) as (host, port):
        yield f"http://{host}:{port}/v1/"


def whole(url: str, request: dict[str, Any]) -> dict[str, Any]:
    sent = urllib.request.Request(
        url + "chat/completions",
        data=json.dumps(request).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(sent) as response:
        return json.loads(response.read())


def kept(response: dict[str, Any]) -> dict[str, Any]:
    def present(d: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in d.items() if v is not None}

    [choice] = response["choices"]
    return {k: v for k, v in response.items() if k not in ("id", "created")} | {
        "choices": [present(choice) | {"message": present(choice["message"])}]
    }


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"response_format": {"type": "json_schema", "json_schema": {"schema": SCHEMA}}},
        {
            "tools": [function("final_result", SCHEMA)],
            "tool_choice": {"type": "function", "function": {"name": "final_result"}},
        },
        {"tools": [function("lookup"), function("now")], "tool_choice": "required"},
    ],
    ids=["text", "schema", "named tool", "required tools"],
)
def test_a_stream_adds_up_to_the_completion_it_would_have_been(
    url: str, fields: dict[str, Any]
) -> None:
    unstreamed = whole(url, body(**fields))
    with post(url, body(**fields) | STREAM) as stream:
        streamed = assemble(stream)
    assert kept(streamed) == kept(unstreamed)
    assert list(streamed) == [k for k in unstreamed if k in streamed]
    assert streamed["prompt_token_ids"] and streamed["usage"]


def test_an_error_answer_comes_back_with_its_status_and_body(url: str) -> None:
    with pytest.raises(Rejected) as caught:
        _ = post(url, body(model="sha256:other") | STREAM)
    assert caught.value.status == 404
    assert caught.value.body is not None
    assert "sha256:other" in str(caught.value)


def test_closing_a_stream_hangs_up_and_the_engine_hears_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with serving([SERVED], "127.0.0.1", 0, delay=5.0) as (host, port):
        with post(f"http://{host}:{port}/v1", body() | STREAM) as stream:
            assert next(iter(stream))["choices"]
        logged = ""
        hung_up = time.monotonic()
        while "aborted" not in logged and time.monotonic() - hung_up < 2:
            time.sleep(0.01)
            logged += capsys.readouterr().err
    assert "aborted: the client hung up" in logged


def test_server_sent_events_are_read_as_the_standard_has_them() -> None:
    lines = [
        b": a comment\n",
        b'data: {"a":\n',
        b"data: 1}\r\n",
        b"\n",
        b"event: ignored\n",
        b"data:{}\n",
        b"\n",
        b"data: [DONE]\n",
        b"\n",
        b'data: {"after": "done"}\n',
    ]
    assert list(events(lines))[:2] == ['{"a":\n1}', "{}"]
    assert list(chunks(lines)) == [{"a": 1}, {}]
    with pytest.raises(Rejected, match="HTTP 200: overloaded"):
        _ = list(chunks([b'data: {"error": {"message": "overloaded"}}\n\n']))
    with pytest.raises(ValueError, match="isn't a JSON object"):
        _ = list(chunks([b"data: [1]\n\n"]))


def test_a_stream_cut_short_adds_up_to_what_came() -> None:
    opening: dict[str, JsonValue] = {
        "id": "chatcmpl-1",
        "model": "m",
        "prompt_token_ids": [1, 2],
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}],
    }
    later: dict[str, JsonValue] = {
        "id": "chatcmpl-1",
        "prompt_token_ids": [9],
        "choices": [{"index": 0, "delta": {"content": "Fake"}, "token_ids": [5]}],
    }
    assert assemble([opening, later]) == {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Fake"},
                "finish_reason": None,
                "token_ids": [5],
            }
        ],
        "prompt_token_ids": [1, 2],
    }


def test_a_secret_goes_in_a_header_and_nowhere_else() -> None:
    sent = request("http://pelle.km:12009/v1/", {"model": "m"}, "s3cret")
    assert sent.full_url == "http://pelle.km:12009/v1/chat/completions"
    assert sent.get_header("Authorization") == "Bearer s3cret"
    assert sent.data == b'{"model": "m"}'
    assert request("http://h/v1", {}).get_header("Authorization") is None
