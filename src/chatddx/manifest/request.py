from collections.abc import Mapping
from typing import Annotated, Literal, cast, override

from pydantic import Field, JsonValue, model_validator

from .identity import Component, Digest, Frozen, RefTo, Resolver, StructuralError

SlotName = Literal["case", "appendices", "completion", "expectation"]
Purpose = Literal["generation", "judge"]

SLOTS_BY_PURPOSE: dict[Purpose, frozenset[SlotName]] = {
    "generation": frozenset({"case", "appendices"}),
    "judge": frozenset({"case", "appendices", "completion", "expectation"}),
}
REQUIRED_SLOTS: dict[Purpose, frozenset[SlotName]] = {
    "generation": frozenset({"case"}),
    "judge": frozenset({"completion"}),
}

# Body keys filled at send time or fixed by the rig; no chunk or skeleton may set them.
RUNTIME_KEYS = frozenset({"model", "messages", "seed", "stream", "n"})
OUTPUT_KEYS = frozenset({"response_format", "tools", "tool_choice"})
GREEDY_DROPS = ("top_p", "top_k", "min_p")


class Slot(Frozen):
    slot: SlotName


Segment = str | Slot


class Message(Frozen):
    role: Literal["system", "user", "assistant"]
    content: tuple[Segment, ...] = Field(min_length=1)


def _canonical_sampling(data: object) -> object:
    if not isinstance(data, dict):
        return data
    fields = cast(dict[str, object], data)
    if fields.get("temperature") != 0:
        return fields
    return {k: v for k, v in fields.items() if k not in GREEDY_DROPS}


# Chunks: the typed slots the UI authors.


class Instructions(Component):
    kind: Literal["chunk.instructions"] = "chunk.instructions"
    text: str


class Prompt(Component):
    kind: Literal["chunk.prompt"] = "chunk.prompt"
    purpose: Purpose = "generation"
    segments: tuple[Segment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _slots(self) -> "Prompt":
        _check_slots(self.purpose, [self.segments])
        return self


class NativeOutput(Frozen):
    kind: Literal["native"] = "native"


class ToolOutput(Frozen):
    kind: Literal["tool"] = "tool"
    name: str


class TextOutput(Frozen):
    kind: Literal["text"] = "text"
    json_schema: dict[str, JsonValue] | None = None


OutputContract = Annotated[
    NativeOutput | ToolOutput | TextOutput, Field(discriminator="kind")
]


class Output(Component):
    kind: Literal["chunk.output"] = "chunk.output"
    contract: OutputContract
    json_schema: dict[str, JsonValue] | None = None
    guidance: str | None = None

    @model_validator(mode="after")
    def _schema_placement(self) -> "Output":
        if isinstance(self.contract, TextOutput):
            if self.json_schema is not None:
                raise ValueError("text contracts carry their schema in the contract")
        elif self.json_schema is None:
            raise ValueError(f"{self.contract.kind} contracts need json_schema")
        return self


class Sampling(Component):
    kind: Literal["chunk.sampling"] = "chunk.sampling"
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    max_tokens: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    repetition_penalty: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _greedy(cls, data: object) -> object:
        return _canonical_sampling(data)


class Reasoning(Component):
    kind: Literal["chunk.reasoning"] = "chunk.reasoning"
    effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    chat_template_kwargs: dict[str, JsonValue] = Field(default_factory=dict)


class Passthrough(Component):
    kind: Literal["chunk.passthrough"] = "chunk.passthrough"
    body: dict[str, JsonValue]

    @model_validator(mode="after")
    def _no_owned_keys(self) -> "Passthrough":
        owned = self.body.keys() & (RUNTIME_KEYS | OUTPUT_KEYS)
        if owned:
            raise ValueError(f"passthrough may not set {sorted(owned)}")
        return self


class AppendixLayout(Frozen):
    before: str = "\n\n"
    between: str = "\n\n"
    after: str = ""

    def join(self, appendices: list[str]) -> str:
        if not appendices:
            return ""
        return self.before + self.between.join(appendices) + self.after


class RequestSpec(Component):
    kind: Literal["request"] = "request"
    purpose: Purpose = "generation"
    instructions: Annotated[Digest, RefTo("chunk.instructions")] | None = None
    prompt: Annotated[Digest, RefTo("chunk.prompt")]
    output: Annotated[Digest, RefTo("chunk.output")]
    sampling: Annotated[Digest, RefTo("chunk.sampling")]
    reasoning: Annotated[Digest, RefTo("chunk.reasoning")] | None = None
    passthrough: Annotated[Digest, RefTo("chunk.passthrough")] | None = None
    appendix_layout: AppendixLayout = AppendixLayout()

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        prompt = get(self.prompt)
        assert isinstance(prompt, Prompt)
        if prompt.purpose != self.purpose:
            return [f"{prompt.purpose} prompt in a {self.purpose} request"]
        return []


# The frozen request: what a trial or judge actually references.


class Skeleton(Component):
    kind: Literal["skeleton"] = "skeleton"
    purpose: Purpose = "generation"
    api: Literal["chat.completions"] = "chat.completions"
    messages: tuple[Message, ...] = Field(min_length=1)
    body: dict[str, JsonValue] = Field(default_factory=dict)
    contract: OutputContract
    appendix_layout: AppendixLayout = AppendixLayout()

    @model_validator(mode="before")
    @classmethod
    def _greedy_body(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        fields = cast(dict[str, object], data)
        if "body" not in fields:
            return fields
        return {**fields, "body": _canonical_sampling(fields["body"])}

    @model_validator(mode="after")
    def _structure(self) -> "Skeleton":
        if owned := self.body.keys() & RUNTIME_KEYS:
            raise ValueError(f"body may not set {sorted(owned)}")
        _check_slots(self.purpose, [m.content for m in self.messages])
        match self.contract:
            case NativeOutput():
                if set(self.body) & OUTPUT_KEYS != {"response_format"}:
                    raise ValueError(
                        "native contract needs response_format and no tools"
                    )
            case ToolOutput(name=name):
                if set(self.body) & OUTPUT_KEYS != {"tools", "tool_choice"}:
                    raise ValueError("tool contract needs tools and tool_choice only")
                if _tool_names(self.body) != [name]:
                    raise ValueError(
                        f"tool contract needs exactly one tool named {name!r}"
                    )
            case TextOutput():
                if set(self.body) & OUTPUT_KEYS:
                    raise ValueError("text contract may not constrain the output")
        return self

    @property
    def greedy(self) -> bool:
        return self.body.get("temperature") == 0

    @property
    def output_schema(self) -> JsonValue:
        match self.contract:
            case NativeOutput():
                return _get(self.body, "response_format", "json_schema", "schema")
            case ToolOutput():
                return _get(self.body, "tools", 0, "function", "parameters")
            case TextOutput(json_schema=schema):
                return schema


SkeletonRef = Annotated[Digest, RefTo("skeleton")]


def _get(doc: JsonValue, *path: str | int) -> JsonValue:
    for p in path:
        match doc, p:
            case list(), int():
                doc = doc[p]
            case dict(), str():
                doc = doc[p]
            case _:
                raise KeyError(p)
    return doc


def _tool_names(body: Mapping[str, JsonValue]) -> list[str]:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return []
    return [str(_get(t, "function", "name")) for t in tools]


def _check_slots(purpose: Purpose, contents: list[tuple[Segment, ...]]) -> None:
    used = [s.slot for c in contents for s in c if isinstance(s, Slot)]
    if stray := set(used) - SLOTS_BY_PURPOSE[purpose]:
        raise ValueError(f"{purpose} requests cannot use slots {sorted(stray)}")
    if missing := REQUIRED_SLOTS[purpose] - set(used):
        raise ValueError(f"{purpose} requests need slots {sorted(missing)}")
    if dupes := {s for s in used if used.count(s) > 1}:
        raise ValueError(f"slots used more than once: {sorted(dupes)}")


def compile_request(spec: RequestSpec, get: Resolver) -> Skeleton:
    prompt = get(spec.prompt)
    output = get(spec.output)
    sampling = get(spec.sampling)
    assert isinstance(prompt, Prompt)
    assert isinstance(output, Output)
    assert isinstance(sampling, Sampling)

    system = ""
    if spec.instructions is not None:
        instructions = get(spec.instructions)
        assert isinstance(instructions, Instructions)
        system = instructions.text
    if output.guidance:
        system = f"{system}\n\n{output.guidance}" if system else output.guidance

    messages: list[Message] = []
    if system:
        messages.append(Message(role="system", content=(system,)))
    messages.append(Message(role="user", content=prompt.segments))

    body: dict[str, JsonValue] = {}
    if spec.passthrough is not None:
        passthrough = get(spec.passthrough)
        assert isinstance(passthrough, Passthrough)
        body.update(passthrough.body)
    managed: dict[str, JsonValue] = dict(
        sampling.model_dump(exclude_none=True, exclude={"kind"})
    )
    if spec.reasoning is not None:
        reasoning = get(spec.reasoning)
        assert isinstance(reasoning, Reasoning)
        if reasoning.effort is not None:
            managed["reasoning_effort"] = reasoning.effort
        if reasoning.chat_template_kwargs:
            managed["chat_template_kwargs"] = reasoning.chat_template_kwargs
    match output.contract:
        case NativeOutput():
            managed["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "output",
                    "strict": True,
                    "schema": output.json_schema,
                },
            }
        case ToolOutput(name=name):
            managed["tools"] = [
                {
                    "type": "function",
                    "function": {"name": name, "parameters": output.json_schema},
                }
            ]
            managed["tool_choice"] = {"type": "function", "function": {"name": name}}
        case TextOutput():
            pass
    if clash := body.keys() & managed.keys():
        raise StructuralError(f"passthrough overrides managed keys {sorted(clash)}")
    body.update(managed)

    return Skeleton(
        purpose=spec.purpose,
        messages=tuple(messages),
        body=body,
        contract=output.contract,
        appendix_layout=spec.appendix_layout,
    )


def render(
    skeleton: Skeleton,
    *,
    model: str,
    seed: int | None,
    fills: Mapping[SlotName, str],
) -> dict[str, JsonValue]:
    messages: list[JsonValue] = []
    for m in skeleton.messages:
        parts = [s if isinstance(s, str) else fills[s.slot] for s in m.content]
        messages.append({"role": m.role, "content": "".join(parts)})
    body: dict[str, JsonValue] = {"model": model, "messages": messages, **skeleton.body}
    if seed is not None and not skeleton.greedy:
        body["seed"] = seed
    return body
