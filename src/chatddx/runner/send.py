import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import JsonValue

from chatddx.factors.base import Fingerprint
from chatddx.factors.trial import Execution
from chatddx.ledger import Call, fingerprint_prompt_tokens, fingerprint_request

from . import wire

STREAM: dict[str, JsonValue] = {
    "stream": True,
    "stream_options": {"include_usage": True},
}
STOPPED = "stopped"


@dataclass(frozen=True)
class Delta:
    kind: Literal["reasoning", "content", "tool_call"]
    text: str
    # Tokens generated so far in this call.
    tokens: int


# A KeyboardInterrupt hung up mid-call; `call` is the call as far as it got.
class Stopped(Exception):
    def __init__(self, call: Call) -> None:
        self.call: Call = call
        super().__init__(STOPPED)


def _now() -> datetime:
    return datetime.now(UTC)


def _deltas(chunk: wire.Chunk) -> Iterator[tuple[Delta, int]]:
    choices = chunk.get("choices")
    for choice in choices if isinstance(choices, list) else []:
        if not isinstance(choice, dict) or choice.get("index", 0) != 0:
            continue
        delta = choice.get("delta")
        found: list[Delta] = []
        for key, value in (delta if isinstance(delta, dict) else {}).items():
            if key in ("reasoning", "reasoning_content") and isinstance(value, str):
                found.append(Delta("reasoning", value, 0))
            elif key == "content" and isinstance(value, str) and value:
                found.append(Delta("content", value, 0))
            elif key == "tool_calls" and isinstance(value, list):
                for call in value:
                    function = call.get("function") if isinstance(call, dict) else None
                    if isinstance(function, dict):
                        text = function.get("name") or function.get("arguments")
                        if isinstance(text, str) and text:
                            found.append(Delta("tool_call", text, 0))
        ids = choice.get("token_ids")
        count = len(ids) if isinstance(ids, list) else len(found)
        for n, d in enumerate(found):
            yield d, count if n == 0 else 0


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


# Streaming is the wire's business: `Call.request` fingerprints the body as `render` or
# `next_request` made it, without the stream's keys. Only a call that got no answer is
# tried again, since an answer cut short is an answer.
def send(
    url: str,
    body: Mapping[str, JsonValue],
    *,
    execution: Execution | None = None,
    secret: str | None = None,
    on_delta: Callable[[Delta], None] | None = None,
    key: tuple[str, bytes] | None = None,
    backoff: float = 1.0,
) -> Call:
    execution = execution or Execution()
    request = fingerprint_request(dict(body), key)
    started = _now()
    attempts = 0
    received: list[wire.Chunk] = []

    def call(
        status: int | None,
        error: str | None,
        response: dict[str, JsonValue] | None = None,
    ) -> Call:
        prompt_tokens: Fingerprint | None = None
        if response is None and received:
            response = wire.assemble(received)
        if response is not None:
            response, prompt_tokens = fingerprint_prompt_tokens(response, key)
        return Call(
            request=request,
            started_at=started,
            finished_at=_now(),
            status=status,
            response=response,
            prompt_tokens=prompt_tokens,
            attempts=attempts,
            error=error,
        )

    try:
        while True:
            attempts += 1
            try:
                stream = wire.post(
                    url,
                    {**body, **STREAM},
                    secret=secret,
                    timeout=execution.timeout_s,
                )
                break
            except wire.Rejected as e:
                if attempts > execution.retries or not _retryable(e.status):
                    return call(e.status, str(e), e.body)
            except OSError as e:
                if attempts > execution.retries:
                    return call(None, f"{type(e).__name__}: {e}")
            time.sleep(backoff * 2 ** (attempts - 1))
    except KeyboardInterrupt:
        raise Stopped(call(None, STOPPED)) from None

    limit = execution.whitespace_limit
    deadline = (
        None if execution.timeout_s is None else time.monotonic() + execution.timeout_s
    )
    tokens = blank = 0
    error: str | None = None
    with stream:
        try:
            for chunk in stream:
                received.append(chunk)
                for delta, count in _deltas(chunk):
                    tokens += count
                    whitespace = delta.kind == "content" and not delta.text.strip()
                    blank = blank + count if whitespace else 0
                    if on_delta is not None:
                        on_delta(Delta(delta.kind, delta.text, tokens))
                if limit is not None and blank >= limit:
                    error = f"stopped: nothing but whitespace for {blank} tokens"
                    break
                if deadline is not None and time.monotonic() > deadline:
                    error = f"stopped: no end after {execution.timeout_s} s"
                    break
        except KeyboardInterrupt:
            raise Stopped(call(stream.status, STOPPED)) from None
        except (wire.Rejected, OSError, ValueError) as e:
            error = f"{type(e).__name__}: {e}"
    return call(stream.status, error)
