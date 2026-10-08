import json
import logging
import random
import re
import time
import uuid
import zlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, cast

from .served import Served

log = logging.getLogger(__name__)

DIAGNOSES = ("Fake diagnosis A", "Fake diagnosis B", "Fake diagnosis C")
ANSWER = "\n".join(DIAGNOSES)

MAX_TEMP = 1e-2
GREEDY_TEMP = 1e-5
HARMONY_EFFORTS = ("high", "medium", "low")
VOCABULARY = 151936

_TRANSPORT = frozenset({"messages", "model", "stream", "stream_options"})

# A word and the space after it. A thinking tag is a token of its own, as it is in
# Qwen3's vocabulary, and vLLM streams it alone.
_WORD = re.compile(r"</?think>|(?:(?!</?think>)\S)+\s*|\s+")


class Failure(Exception):
    def __init__(
        self,
        message: str,
        type: str = "BadRequestError",
        status: int = 400,
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.type: str = type
        self.status: int = status
        self.param: str | None = param

    def body(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.type,
                "param": self.param,
                "code": self.status,
            }
        }


@dataclass(frozen=True)
class Call:
    name: str
    arguments: str
    id: str


@dataclass(frozen=True)
class Reply:
    reasoning: str | None
    content: str
    calls: tuple[Call, ...]
    finish: str
    # the newlines it goes on with after its answer, a token each
    runaway: int = 0


def pick(served: Sequence[Served], body: dict[str, Any]) -> Served:
    model = body.get("model")
    if not model:
        return served[0]
    for s in served:
        if model in s.names:
            return s
    raise Failure(f"The model `{model}` does not exist.", "NotFoundError", 404, "model")


def tool_choice(body: dict[str, Any]) -> Any:
    return body.get("tool_choice", "auto" if body.get("tools") else "none")


def _named(choice: Any) -> str | None:
    if not isinstance(choice, dict):
        return None
    function = cast(dict[str, Any], choice).get("function")
    if not isinstance(function, dict):
        return None
    name = cast(dict[str, Any], function).get("name")
    return name if isinstance(name, str) else None


def _functions(body: dict[str, Any]) -> list[dict[str, Any]]:
    tools = cast(list[dict[str, Any]], body.get("tools") or [])
    return [t.get("function") or {} for t in tools]


def validate(body: dict[str, Any]) -> None:
    if body.get("tools") == []:
        raise Failure(
            "`tools` must not be an empty array. Either provide at least one tool or "
            + "omit the field entirely."
        )
    choice = tool_choice(body)
    if choice not in (None, "none"):
        if body.get("tools") is None:
            raise Failure(
                "When using `tool_choice`, `tools` must be set.", param="tool_choice"
            )
        if isinstance(choice, dict):
            if _named(choice) not in [f.get("name") for f in _functions(body)]:
                raise Failure(
                    "The tool specified in `tool_choice` does not match any of the "
                    + "specified `tools`",
                    param="tool_choice",
                )
        elif choice not in ("auto", "required"):
            raise Failure(
                f"Invalid value for `tool_choice`: {choice}! Only named tools, "
                + '"none", "auto" or "required" are supported.',
                param="tool_choice",
            )


def check(served: Served, body: dict[str, Any]) -> None:
    choice = tool_choice(body)
    unparsed = not served.parses_tools and served.family not in ("harmony", "mistral")
    if unparsed and choice == "auto":
        raise Failure(
            '"auto" tool choice requires --enable-auto-tool-choice and '
            + "--tool-call-parser to be set"
        )
    if unparsed and choice not in (None, "none"):
        raise Failure(f'tool_choice="{choice}" requires --tool-call-parser to be set')
    effort = body.get("reasoning_effort")
    if served.family == "harmony" and effort is not None:
        if effort == "none":
            raise Failure("Harmony does not support reasoning_effort='none'")
        if effort not in HARMONY_EFFORTS:
            raise Failure(
                f"reasoning_effort={effort!r} is not supported by Harmony. "
                + f"Supported values are: {', '.join(HARMONY_EFFORTS)}."
            )
    if body.get("thinking_token_budget") is not None and not (
        served.reasoning_parser or served.reasoning_config
    ):
        raise Failure(
            "thinking_token_budget is set but reasoning_config is not configured. "
            + "Please set --reasoning-parser and/or --reasoning-config to use "
            + "thinking_token_budget."
        )


def accept(served: Sequence[Served], body: dict[str, Any]) -> Served:
    validate(body)
    chosen = pick(served, body)
    check(chosen, body)
    return chosen


def temperature(body: dict[str, Any]) -> float | None:
    t = body.get("temperature")
    if not isinstance(t, int | float):
        return None
    if 0 < t < MAX_TEMP:
        log.warning(
            "temperature %s is less than %s, which may cause numerical errors nan or "
            + "inf in tensors. We have maxed it out to %s.",
            t,
            MAX_TEMP,
            MAX_TEMP,
        )
        return MAX_TEMP
    return float(t)


def thinks(served: Served, body: dict[str, Any]) -> bool:
    match served.family:
        case "harmony":
            return True
        case "qwen3":
            asked: dict[str, Any] = body.get("chat_template_kwargs") or {}
            effort = body.get("reasoning_effort")
            derived = (
                {}
                if effort is None or "enable_thinking" in asked
                else {"enable_thinking": effort != "none"}
            )
            kwargs = served.default_chat_template_kwargs | derived | asked
            return kwargs.get("enable_thinking", True) is not False
        case _:
            return False


def thinking(body: dict[str, Any]) -> str:
    messages = [
        f"{m.get('role')} ({len(_text(m.get('content')).split())} words)"
        for m in cast(list[dict[str, Any]], body.get("messages") or [])
    ]
    fields = [_field(k, v) for k, v in body.items() if k not in _TRANSPORT]
    return (
        "I am the fake vLLM, and nothing here reads the case. "
        + f"I was sent {', '.join(messages) or 'no messages'}, "
        + f"and {' '.join(fields) or 'no other fields'}. "
        + "So I answer as I always do."
    )


def _schema(body: dict[str, Any]) -> dict[str, Any] | None:
    response_format: dict[str, Any] = body.get("response_format") or {}
    match response_format.get("type"):
        case "json_schema":
            spec: dict[str, Any] = response_format.get("json_schema") or {}
            return spec.get("schema") or {}
        case "json_object":
            return {"type": "object"}
        case _:
            return None


def _called(body: dict[str, Any]) -> list[str]:
    return [
        call.get("function", {}).get("name")
        for m in cast(list[dict[str, Any]], body.get("messages") or [])
        if m.get("role") == "assistant"
        for call in cast(list[dict[str, Any]], m.get("tool_calls") or [])
    ]


def _tool(served: Served, body: dict[str, Any]) -> dict[str, Any] | None:
    choice = tool_choice(body)
    functions = _functions(body)
    if not functions or choice in (None, "none") or not served.parses_tools:
        return None
    called = set(_called(body))
    if (name := _named(choice)) is not None:
        return next(f for f in functions if f.get("name") == name)
    if choice == "required":
        # The compiler offers the answer tool last
        # (src/chatddx/factors/request.py:compile_request).
        *toolset, answer = functions
        return next((f for f in toolset if f.get("name") not in called), answer)
    if _schema(body) is not None:
        return None
    return next((f for f in functions if f.get("name") not in called), None)


def _constrained(served: Served, body: dict[str, Any]) -> bool:
    choice = tool_choice(body)
    by_tool = served.parses_tools and (choice == "required" or isinstance(choice, dict))
    return by_tool or _schema(body) is not None


def respond(served: Served, body: dict[str, Any], runaway: bool = False) -> Reply:
    t = temperature(body)
    held = _constrained(served, body) and not served.reasoning_parser
    thought_text = thinking(body) if thinks(served, body) and not held else None
    thought = _words(thought_text or "")
    tool = _tool(served, body)
    schema = _schema(body)
    calls: tuple[Call, ...] = ()
    if tool is not None:
        arguments = json.dumps(instance(tool.get("parameters") or {}))
        calls = (Call(tool.get("name", ""), arguments, _call_id(body)),)
        answer: list[str] = []
    elif schema is not None or (schema := _shown_schema(body)) is not None:
        answer = _words(json.dumps(instance(schema), indent=2))
    else:
        answer = _words(_answer(body, t is not None and t < GREEDY_TEMP))

    budget = body.get("thinking_token_budget")
    if isinstance(budget, int):
        thought = thought[:budget]

    named = isinstance(tool_choice(body), dict)
    finish = "tool_calls" if calls and not named else "stop"
    limit = body.get("max_completion_tokens", body.get("max_tokens"))
    if isinstance(limit, int) and len(thought) + len(answer) > limit:
        finish = "length"
        thought = thought[:limit]
        answer = answer[: limit - len(thought)]

    newlines = 0
    if runaway and finish == "stop" and not calls:
        # As gpt-oss does on malborg at times, held to a grammar that lets whitespace in
        # before a closing brace: the answer ends, the tokens don't.
        if schema is not None:
            answer = answer[:-1]
        room = (
            limit
            if isinstance(limit, int)
            else served.max_model_len - len(_prompt_words(body))
        )
        newlines = max(room - len(thought) - len(answer), 0)
        finish = "length"

    reasoning = "".join(thought) if thought_text is not None else None
    content = "".join(answer)
    separated = served.reasoning_parser or (
        served.family == "harmony" and served.parses_tools
    )
    if reasoning is not None and not separated:
        if served.family == "harmony":
            # Harmony's channel markers are special tokens, skipped when detokenized.
            final = "assistantfinal" if finish != "length" or answer else ""
            return Reply(
                None, f"analysis{reasoning}{final}{content}", calls, finish, newlines
            )
        closed = "\n</think>\n\n" if finish != "length" or answer else ""
        return Reply(
            None, f"<think>\n{reasoning}{closed}{content}", calls, finish, newlines
        )
    if body.get("include_reasoning") is False:
        reasoning = None
    return Reply(reasoning, content, calls, finish, newlines)


def _answer(body: dict[str, Any], greedy: bool) -> str:
    if greedy:
        turn = 0
    elif isinstance(seed := body.get("seed"), int):
        turn = seed
    else:
        turn = random.randrange(len(DIAGNOSES))
    turn %= len(DIAGNOSES)
    return "\n".join(DIAGNOSES[turn:] + DIAGNOSES[:turn])


def _call_id(body: dict[str, Any]) -> str:
    return f"chatcmpl-tool-fake-{len(_called(body))}"


def completion(served: Served, body: dict[str, Any], reply: Reply) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": reply.content + "\n" * reply.runaway,
        "reasoning": reply.reasoning,
    }
    if reply.calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in reply.calls
        ]
    ids = body.get("return_token_ids") is True
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": served.name,
        "choices": [
            {
                "index": 0,
                "message": message,
                "logprobs": None,
                "finish_reason": reply.finish,
                "stop_reason": None,
                "token_ids": _ids(_generated(reply)) if ids else None,
            }
        ],
        "usage": _usage(body, reply),
        "system_fingerprint": served.system_fingerprint,
        "prompt_token_ids": _ids(_prompt_words(body)) if ids else None,
    }


def stream(
    served: Served, body: dict[str, Any], reply: Reply
) -> Iterator[tuple[str, int]]:
    model = served.name
    options: dict[str, Any] = body.get("stream_options") or {}
    usage = bool(options.get("include_usage"))
    continuous = usage and bool(options.get("continuous_usage_stats"))
    ids = body.get("return_token_ids") is True
    prompt = len(_prompt_words(body))
    created = int(time.time())
    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    generated = 0

    def chunk(choices: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": choices,
        } | extra

    def event(
        delta: dict[str, Any], words: Sequence[str] = (), finish: str | None = None
    ) -> tuple[str, int]:
        choice: dict[str, Any] = {
            "index": 0,
            "delta": delta,
            "logprobs": None,
            "finish_reason": finish,
        }
        if ids and words:
            choice["token_ids"] = _ids(words)
        extra: dict[str, Any] = {}
        if continuous:
            extra["usage"] = _counted(prompt, generated)
        if finish is not None and not usage and served.system_fingerprint:
            extra["system_fingerprint"] = served.system_fingerprint
        return _data(chunk([choice], **extra)), generated

    first: dict[str, Any] = {}
    if ids:
        first["prompt_token_ids"] = _ids(_prompt_words(body))
    if continuous:
        first["usage"] = _counted(prompt, 0)
    opening = {"index": 0, "delta": {"role": "assistant", "content": ""}}
    yield (
        _data(chunk([opening | {"logprobs": None, "finish_reason": None}], **first)),
        0,
    )

    for word in _words(reply.reasoning or ""):
        generated += 1
        yield event({"reasoning": word}, [word])
    for word in _words(reply.content):
        generated += 1
        yield event({"content": word}, [word])
    for _ in range(reply.runaway):
        generated += 1
        yield event({"content": "\n"}, ["\n"])
    for index, call in enumerate(reply.calls):
        start = {
            "index": index,
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": ""},
        }
        yield event({"tool_calls": [start]})
        for piece in _words(call.arguments):
            generated += 1
            yield event(
                {"tool_calls": [{"index": index, "function": {"arguments": piece}}]},
                [piece],
            )
    yield event({}, finish=reply.finish)

    if usage:
        extra: dict[str, Any] = {"usage": _usage(body, reply)}
        if served.system_fingerprint:
            extra["system_fingerprint"] = served.system_fingerprint
        yield _data(chunk([], **extra)), generated
    yield "data: [DONE]\n\n", generated


def instance(
    schema: Any,
    root: Any = None,
    key: str = "value",
    n: int | None = None,
    depth: int = 0,
) -> Any:
    root = schema if root is None else root
    if not isinstance(schema, dict) or depth > 12:
        return None
    schema = cast(dict[str, Any], schema)
    if "$ref" in schema:
        return instance(_resolve(root, schema["$ref"]), root, key, n, depth + 1)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for union in ("anyOf", "oneOf", "allOf"):
        options = [
            o
            for o in cast(list[dict[str, Any]], schema.get(union, []))
            if o.get("type") != "null"
        ]
        if options:
            # A branch holds together with the keywords beside it, as `required` beside
            # `type` and `properties` does.
            beside = {k: v for k, v in schema.items() if k != union}
            return instance(beside | options[0], root, key, n, depth + 1)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in cast(list[str], kind) if k != "null"), "null")
    match kind:
        case "object":
            properties: dict[str, Any] = schema.get("properties", {})
            return {
                name: instance(sub, root, name, n, depth + 1)
                for name, sub in properties.items()
            }
        case "array":
            count = min(max(schema.get("minItems", 3), 1), schema.get("maxItems", 3))
            return [
                instance(schema.get("items", {}), root, key, i, depth + 1)
                for i in range(1, count + 1)
            ]
        case "string":
            return f"fake {key.replace('_', ' ')}" + ("" if n is None else f" {n}")
        case "integer":
            return int(schema.get("minimum", n or 1))
        case "number":
            return float(schema.get("minimum", n or 1))
        case "boolean":
            return False
        case _:
            if "properties" in schema:
                return instance(schema | {"type": "object"}, root, key, n, depth + 1)
            return None


def _shown_schema(body: dict[str, Any]) -> dict[str, Any] | None:
    for m in cast(list[dict[str, Any]], body.get("messages") or []):
        if m.get("role") == "system":
            shown = _shown(_text(m.get("content")))
            if shown is not None:
                return shown
    return None


def _shown(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            schema = cast(dict[str, Any], value)
            if {"type", "properties"} & schema.keys():
                return schema
    return None


def _resolve(root: Any, ref: str) -> Any:
    node: Any = root
    for part in ref.removeprefix("#/").split("/"):
        node = cast(dict[str, Any], node).get(part) if isinstance(node, dict) else None
    return node


def _text(content: Any) -> str:
    if isinstance(content, list):
        parts = cast(list[Any], content)
        return " ".join(
            str(cast(dict[str, Any], p).get("text") or "")
            for p in parts
            if isinstance(p, dict)
        )
    return "" if content is None else str(content)


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


# What the model reads: the messages' text, and the tools the chat template lays out.
def _prompt_words(body: dict[str, Any]) -> list[str]:
    texts = [
        _text(m.get("content"))
        for m in cast(list[dict[str, Any]], body.get("messages") or [])
    ]
    if body.get("tools"):
        texts.append(json.dumps(body["tools"]))
    return [w for t in texts for w in t.split()]


def _generated(reply: Reply) -> list[str]:
    arguments = "".join(call.arguments for call in reply.calls)
    texts = (reply.reasoning or "", reply.content, arguments)
    return [w for t in texts for w in _words(t)] + ["\n"] * reply.runaway


def _ids(words: Sequence[str]) -> list[int]:
    return [zlib.crc32(w.encode()) % VOCABULARY for w in words]


def _usage(body: dict[str, Any], reply: Reply) -> dict[str, int]:
    return _counted(len(_prompt_words(body)), len(_generated(reply)))


def _counted(prompt: int, completion: int) -> dict[str, int]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _data(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk)}\n\n"


def _field(key: str, value: Any) -> str:
    match key:
        case "response_format":
            return f"response_format={cast(dict[str, Any], value).get('type')}"
        case "tools":
            return "tools=" + ",".join(
                str(f.get("name")) for f in _functions({"tools": value})
            )
        case _:
            return f"{key}={json.dumps(value)}"
