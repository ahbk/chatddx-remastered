import socket
from collections.abc import Iterator
from typing import Any, cast

import pytest

from chatddx.factors.base import Fingerprint, canonical_bytes
from chatddx.factors.trial import Execution
from chatddx.fake_vllm.chat import ANSWER
from chatddx.fake_vllm.server import serving
from chatddx.ledger import fingerprint_request
from chatddx.runner.send import STOPPED, Delta, Stopped, send
from chatddx.runner.test.test_wire import SERVED, body, whole


def at(host: str, port: int) -> str:
    return f"http://{host}:{port}/v1/"


@pytest.fixture
def url() -> Iterator[str]:
    with serving([SERVED], "127.0.0.1", 0) as address:
        yield at(*address)


def choice(response: dict[str, Any] | None) -> Any:
    assert response is not None
    return response["choices"][0]


def content(response: dict[str, Any] | None) -> str:
    return choice(response)["message"]["content"]


def test_a_call_records_the_body_s_fingerprint_and_the_answer_without_the_prompt(
    url: str,
) -> None:
    deltas: list[Delta] = []
    call = send(url, body(), on_delta=deltas.append)
    assert call.request == fingerprint_request(body())
    assert (call.status, call.error, call.attempts) == (200, None, 1)
    assert call.response is not None and "prompt_token_ids" not in call.response
    ids = whole(url, body())["prompt_token_ids"]
    assert call.prompt_tokens == Fingerprint.of(canonical_bytes(ids))
    assert call.returned_model == "sha256:abc"
    assert (
        content(call.response) == "Fake diagnosis B\nFake diagnosis C\nFake diagnosis A"
    )
    kinds = [d.kind for d in deltas]
    assert kinds == sorted(kinds, key=["reasoning", "content"].index)
    assert [d.tokens for d in deltas] == list(range(1, len(deltas) + 1))
    assert deltas[-1].tokens == cast(Any, call.response)["usage"]["completion_tokens"]


def test_a_runaway_is_cut_short_and_what_came_is_kept(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with serving([SERVED], "127.0.0.1", 0, runaway=True) as address:
        call = send(at(*address), body(temperature=0))
        cut = send(
            at(*address), body(temperature=0), execution=Execution(whitespace_limit=5)
        )
        let_run = send(
            at(*address),
            body(temperature=0, max_tokens=150),
            execution=Execution(whitespace_limit=None),
        )
    assert call.error == "stopped: nothing but whitespace for 100 tokens"
    assert content(call.response) == ANSWER + "\n" * 100
    assert choice(call.response)["finish_reason"] is None
    assert cut.error == "stopped: nothing but whitespace for 5 tokens"
    assert let_run.error is None
    assert choice(let_run.response)["finish_reason"] == "length"
    assert "aborted: the client hung up" in capsys.readouterr().err


def test_an_interrupt_mid_stream_records_what_came_as_stopped(url: str) -> None:
    seen: list[Delta] = []

    def interrupt(delta: Delta) -> None:
        seen.append(delta)
        if len(seen) == 3:
            raise KeyboardInterrupt

    with pytest.raises(Stopped) as caught:
        _ = send(url, body(), on_delta=interrupt)
    call = caught.value.call
    assert (call.error, call.status) == (STOPPED, 200)
    assert call.prompt_tokens is not None
    message = choice(call.response)["message"]
    assert message["reasoning"] == "".join(d.text for d in seen)


def test_only_a_call_that_got_no_answer_is_tried_again(url: str) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = at("127.0.0.1", s.getsockname()[1])
    refused = send(closed, body(), execution=Execution(retries=2), backoff=0)
    assert refused.attempts == 3
    assert refused.status is None and refused.response is None
    assert refused.error is not None and "Connection refused" in refused.error

    rejected = send(url, body(model="sha256:other"), execution=Execution(retries=2))
    assert (rejected.attempts, rejected.status) == (1, 404)
    assert rejected.response is not None and "error" in rejected.response
    assert rejected.error is not None and rejected.error.startswith("HTTP 404: ")


def test_a_call_past_its_timeout_is_cut_short() -> None:
    with serving([SERVED], "127.0.0.1", 0, delay=0.05) as address:
        call = send(at(*address), body(), execution=Execution(timeout_s=0.3))
    assert call.error == "stopped: no end after 0.3 s"
    assert choice(call.response)["finish_reason"] is None
