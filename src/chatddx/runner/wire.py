import json
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Mapping
from http.client import HTTPResponse
from types import TracebackType
from typing import Any, Self, cast

from pydantic import JsonValue

type Chunk = dict[str, JsonValue]


# An error answer: an HTTP status other than 2xx, or an error sent in place of a chunk.
class Rejected(Exception):
    def __init__(self, status: int, body: dict[str, JsonValue] | None) -> None:
        self.status: int = status
        self.body: dict[str, JsonValue] | None = body
        super().__init__(f"HTTP {status}: {_message(body)}")


def _message(body: Mapping[str, JsonValue] | None) -> str:
    if body is None:
        return "no body"
    error = body.get("error")
    for found in (error, body):
        if isinstance(found, dict) and isinstance(m := found.get("message"), str):
            return m
    return error if isinstance(error, str) else json.dumps(body)[:200]


def _parsed(raw: bytes) -> dict[str, JsonValue] | None:
    try:
        body: JsonValue = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def request(
    url: str, body: Mapping[str, JsonValue], secret: str | None = None
) -> urllib.request.Request:
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if secret is not None:
        headers["Authorization"] = f"Bearer {secret}"
    return urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )


class Stream:
    def __init__(self, response: HTTPResponse) -> None:
        self._response: HTTPResponse = response

    @property
    def status(self) -> int:
        return self._response.status

    def __iter__(self) -> Iterator[Chunk]:
        return chunks(self._response)

    # Closing hangs up, which vLLM hears and aborts the request on.
    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()


def post(
    url: str,
    body: Mapping[str, JsonValue],
    *,
    secret: str | None = None,
    timeout: float | None = None,
) -> Stream:
    try:
        response = cast(
            HTTPResponse,
            urllib.request.urlopen(request(url, body, secret), timeout=timeout),
        )
    except urllib.error.HTTPError as e:
        with e:
            raise Rejected(e.code, _parsed(e.read())) from None
    return Stream(response)


def events(lines: Iterable[bytes]) -> Iterator[str]:
    data: list[str] = []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
                data = []
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data.append(value.removeprefix(" "))
    if data:
        yield "\n".join(data)


def chunks(lines: Iterable[bytes]) -> Iterator[Chunk]:
    for data in events(lines):
        if data == "[DONE]":
            return
        chunk = _parsed(data.encode())
        if chunk is None:
            raise ValueError(f"a chunk that isn't a JSON object: {data[:200]!r}")
        if "error" in chunk:
            raise Rejected(200, chunk)
        yield chunk


def _merge_call(calls: dict[int, dict[str, Any]], delta: JsonValue) -> None:
    if not isinstance(delta, dict):
        return
    index = delta.get("index", 0)
    call = calls.setdefault(index if isinstance(index, int) else 0, {})
    for key in ("id", "type"):
        if (value := delta.get(key)) is not None:
            call[key] = value
    function = delta.get("function")
    if isinstance(function, dict):
        into: dict[str, Any] = call.setdefault(
            "function", {"name": None, "arguments": ""}
        )
        if (name := function.get("name")) is not None:
            into["name"] = name
        if isinstance(arguments := function.get("arguments"), str):
            into["arguments"] += arguments


# Shaped as vLLM answers unstreamed. vLLM sends the prompt's token ids in the first
# chunk only.
def assemble(chunks: Iterable[Chunk]) -> dict[str, JsonValue]:
    head: dict[str, JsonValue] = {}
    tail: dict[str, JsonValue] = {}
    message: dict[str, JsonValue] = {"role": "assistant"}
    calls: dict[int, dict[str, Any]] = {}
    ending: dict[str, JsonValue] = {"finish_reason": None}
    token_ids: list[JsonValue] | None = None
    for chunk in chunks:
        for key in ("id", "created", "model", "prompt_token_ids"):
            if key not in head and chunk.get(key) is not None:
                head[key] = chunk[key]
        for key in ("usage", "system_fingerprint"):
            if chunk.get(key) is not None:
                tail[key] = chunk[key]
        choices = chunk.get("choices")
        for part in choices if isinstance(choices, list) else []:
            if not isinstance(part, dict) or part.get("index", 0) != 0:
                continue
            delta = part.get("delta")
            for key, value in (delta if isinstance(delta, dict) else {}).items():
                if key == "tool_calls" and isinstance(value, list):
                    for call in value:
                        _merge_call(calls, call)
                elif key == "role" and isinstance(value, str):
                    message["role"] = value
                elif isinstance(value, str):
                    joined = message.get(key)
                    message[key] = (joined if isinstance(joined, str) else "") + value
            if isinstance(ids := part.get("token_ids"), list):
                token_ids = [*(token_ids or []), *ids]
            for key in ("finish_reason", "stop_reason"):
                if part.get(key) is not None:
                    ending[key] = part[key]
    if calls:
        tool_calls: list[JsonValue] = [calls[i] for i in sorted(calls)]
        message["tool_calls"] = tool_calls
    choice: dict[str, JsonValue] = {"index": 0, "message": message, **ending}
    if token_ids is not None:
        choice["token_ids"] = token_ids
    response: dict[str, JsonValue] = {k: head[k] for k in ("id",) if k in head}
    response["object"] = "chat.completion"
    response |= {k: head[k] for k in ("created", "model") if k in head}
    response["choices"] = [choice]
    response |= {k: tail[k] for k in ("usage", "system_fingerprint") if k in tail}
    response |= {k: head[k] for k in ("prompt_token_ids",) if k in head}
    return response
