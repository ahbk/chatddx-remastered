import json
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest

from chatddx.cli import main
from chatddx.fake_vllm.chat import ANSWER
from chatddx.fake_vllm.served import VERSION, Served
from chatddx.fake_vllm.server import serving as fake_serving

QWEN = "Qwen/Qwen3-8B-AWQ"
SERVED = Served.of(
    QWEN,
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


def body(**fields: Any) -> dict[str, Any]:
    return {
        "model": "sha256:abc",
        "messages": [{"role": "user", "content": "a cough"}],
        "temperature": 0,
    } | fields


def serving(
    delay: float = 0.0, runaway: bool = False
) -> AbstractContextManager[tuple[str, int]]:
    return fake_serving([SERVED], "127.0.0.1", 0, delay, runaway)


@pytest.fixture
def url() -> Iterator[str]:
    with serving() as (host, port):
        yield f"http://{host}:{port}"


def call(url: str, path: str, data: bytes | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url + path, data=data)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(url: str, payload: Any) -> tuple[int, Any]:
    status, raw = call(url, "/v1/chat/completions", json.dumps(payload).encode())
    return status, json.loads(raw)


def test_it_serves_chat_completions_over_http(url: str) -> None:
    status, response = post(url, body())
    assert status == 200
    assert response["model"] == "sha256:abc"
    assert response["choices"][0]["message"]["content"] == ANSWER


def test_it_refuses_as_vllm_does_with_its_status_and_error(url: str) -> None:
    status, response = post(url, body(model="other"))
    assert (status, response["error"]["type"], response["error"]["code"]) == (
        404,
        "NotFoundError",
        404,
    )
    status, response = post(url, body(tool_choice="required"))
    assert (status, response["error"]["param"]) == (400, "tool_choice")
    status, raw = call(url, "/v1/chat/completions", b"{not json")
    assert status == 400 and json.loads(raw)["error"]["type"] == "BadRequestError"


def test_it_streams_over_http(url: str) -> None:
    status, raw = call(
        url, "/v1/chat/completions", json.dumps(body(stream=True)).encode()
    )
    lines = [line for line in raw.decode().split("\n\n") if line]
    assert status == 200
    assert lines[-1] == "data: [DONE]"
    contents = [
        json.loads(line.removeprefix("data: "))["choices"][0]["delta"].get(
            "content", ""
        )
        for line in lines[:-1]
    ]
    assert "".join(contents) == ANSWER


def test_it_lists_what_it_serves_says_its_version_and_is_healthy(url: str) -> None:
    status, raw = call(url, "/v1/models")
    [card] = json.loads(raw)["data"]
    assert status == 200
    assert (card["id"], card["root"], card["max_model_len"]) == (
        "sha256:abc",
        QWEN,
        32768,
    )
    assert json.loads(call(url, "/version")[1]) == {"version": VERSION}
    assert call(url, "/health") == (200, b"")


def test_it_serves_nothing_else(url: str) -> None:
    assert call(url, "/v1/completions", b"{}")[0] == 404
    assert call(url, "/v1/completions") == (404, b'{"detail": "Not Found"}')


def test_served_it_runs_away_when_told_to() -> None:
    with serving(runaway=True) as (host, port):
        status, response = post(f"http://{host}:{port}", body(max_tokens=100))
    [choice] = response["choices"]
    content = choice["message"]["content"]
    assert (status, choice["finish_reason"]) == (200, "length")
    assert content.startswith(ANSWER) and content != ANSWER
    assert content.removeprefix(ANSWER).strip("\n") == ""


def test_a_client_that_hangs_up_is_heard_at_once_and_its_request_aborted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = json.dumps(body(stream=True)).encode()
    logged = ""
    with serving(delay=5.0) as address:
        with socket.create_connection(address) as client:
            client.sendall(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: fake\r\n"
                + b"Content-Type: application/json\r\n"
                + b"Content-Length: %d\r\n\r\n%s" % (len(request), request)
            )
            assert b"200 OK" in client.recv(4096)
        hung_up = time.monotonic()
        while "aborted" not in logged and time.monotonic() - hung_up < 2:
            time.sleep(0.01)
            logged += capsys.readouterr().err
    logged += capsys.readouterr().err
    assert "aborted: the client hung up after 0 tokens" in logged
    assert "Traceback" not in logged


def test_the_command_won_t_start_what_vllm_wouldn_t() -> None:
    with pytest.raises(SystemExit, match="won't start: invalid tool call parser"):
        main(["fake-vllm", QWEN, "--enable-auto-tool-choice"])
