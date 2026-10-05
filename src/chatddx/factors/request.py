import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Annotated, Literal, NamedTuple, cast, override
from urllib.parse import unquote

from pydantic import (
    AfterValidator,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from .base import (
    Api,
    Code,
    Component,
    Digest,
    Frozen,
    RefTo,
    Resolver,
    Settings,
    StructuralError,
    distinct,
    resolve,
    sorted_keys,
)

SlotName = Literal["vignette", "appendices", "completion", "expectation"]
InsertName = Literal["schema", "output_guidance", "tool_guidance"]
# As with CleanupOp (cases.py), each op name pins one behavior; a changed behavior
# gets a new name.
SchemaOp = Literal["inline_refs@1"]
Purpose = Literal["generation", "judge"]

SLOTS_BY_PURPOSE: dict[Purpose, frozenset[SlotName]] = {
    "generation": frozenset({"vignette", "appendices"}),
    "judge": frozenset({"vignette", "appendices", "completion", "expectation"}),
}
REQUIRED_SLOTS: dict[Purpose, frozenset[SlotName]] = {
    "generation": frozenset({"vignette"}),
    "judge": frozenset({"completion"}),
}
INSERTS_BY_KIND: dict[str, frozenset[InsertName]] = {
    "chunk.instructions": frozenset({"output_guidance", "tool_guidance"}),
    "chunk.prompt": frozenset({"output_guidance", "tool_guidance"}),
    "chunk.output": frozenset({"schema"}),
}

# Body keys filled at send time or fixed by the rig; no chunk or skeleton may set them.
RUNTIME_KEYS = frozenset(
    {"model", "messages", "seed", "stream", "n", "return_token_ids"}
)
OUTPUT_KEYS = frozenset({"response_format", "tools", "tool_choice"})
# Body keys the sampling and reasoning chunks manage, whether a recipe sets them or not.
MANAGED_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "presence_penalty",
        "frequency_penalty",
        "repetition_penalty",
        "stop",
        "max_completion_tokens",
        "max_tokens",
        "reasoning_effort",
        "thinking_token_budget",
        "chat_template_kwargs",
    }
)
# vLLM request keys that would replace the engine's chat template, constrain the answer
# outside the output contract, or put the prompt's text in the stored response.
BYPASS_KEYS = frozenset(
    {"chat_template", "structured_outputs", "return_prompt_text", "prompt_logprobs"}
)
GREEDY_DROPS = ("top_p", "top_k", "min_p")


class Slot(Frozen):
    slot: SlotName


Segment = str | Slot


class Insert(Frozen):
    insert: InsertName
    before: str = ""
    after: str = ""


def _merged[T](segments: Iterable[str | T]) -> tuple[str | T, ...]:
    merged: list[str | T] = []
    for s in segments:
        if isinstance(s, str) and merged and isinstance(merged[-1], str):
            merged[-1] += s
        else:
            merged.append(s)
    return tuple(s for s in merged if s != "")


def _text(text: str | tuple[str | Insert, ...]) -> str | tuple[str | Insert, ...]:
    if isinstance(text, str):
        return text
    merged = _merged(text)
    if all(isinstance(s, str) for s in merged):
        return "".join(cast(tuple[str, ...], merged))
    return merged


Text = Annotated[str | tuple[str | Insert, ...], AfterValidator(_text)]


def _segments(text: str | tuple[str | Insert, ...]) -> tuple[str | Insert, ...]:
    return (text,) if isinstance(text, str) else text


def _inserts(segments: Iterable[object]) -> list[InsertName]:
    return [s.insert for s in segments if isinstance(s, Insert)]


def _check_inserts(kind: str, segments: Iterable[object]) -> list[InsertName]:
    used = _inserts(segments)
    if stray := set(used) - INSERTS_BY_KIND[kind]:
        raise ValueError(f"{kind} cannot use inserts {sorted(stray)}")
    if dupes := {i for i in used if used.count(i) > 1}:
        raise ValueError(f"inserts used more than once: {sorted(dupes)}")
    return used


def _fill(
    segments: Iterable[Segment | Insert], fills: Mapping[InsertName, str]
) -> tuple[Segment, ...]:
    filled: list[Segment] = []
    for s in segments:
        if isinstance(s, Insert):
            text = fills[s.insert]
            filled.append(f"{s.before}{text}{s.after}" if text else "")
        else:
            filled.append(s)
    return _merged(filled)


def _fill_text(
    text: str | tuple[str | Insert, ...], fills: Mapping[InsertName, str]
) -> str:
    return "".join(s for s in _fill(_segments(text), fills) if isinstance(s, str))


class Message(Frozen):
    role: Literal["system", "developer", "user", "assistant"]
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
    role: Literal["system", "developer"] = "system"
    text: Text

    @model_validator(mode="after")
    def _inserts(self) -> "Instructions":
        _ = _check_inserts(self.kind, _segments(self.text))
        return self


class Example(Frozen):
    role: Literal["user", "assistant"]
    content: str


class FewShot(Component):
    kind: Literal["chunk.few_shot"] = "chunk.few_shot"
    messages: tuple[Example, ...] = Field(min_length=1)


class Prompt(Component):
    kind: Literal["chunk.prompt"] = "chunk.prompt"
    purpose: Purpose = "generation"
    segments: tuple[Segment | Insert, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _slots(self) -> "Prompt":
        _check_slots(self.purpose, [self.segments])
        _ = _check_inserts(self.kind, self.segments)
        return self


class NativeOutput(Frozen):
    kind: Literal["native"] = "native"


class ToolOutput(Frozen):
    kind: Literal["tool"] = "tool"
    name: str
    description: str | None = Field(default=None, min_length=1)


class TextOutput(Frozen):
    kind: Literal["text"] = "text"
    json_schema: dict[str, JsonValue] | None = None


OutputContract = Annotated[
    NativeOutput | ToolOutput | TextOutput, Field(discriminator="kind")
]


def _pointer(schema: dict[str, JsonValue], ref: str) -> JsonValue:
    if ref != "#" and not ref.startswith("#/"):
        raise ValueError(f"{ref} is outside the schema")
    node: JsonValue = schema
    for token in unquote(ref[2:]).split("/") if ref != "#" else ():
        key = token.replace("~1", "/").replace("~0", "~")
        match node:
            case dict() if key in node:
                node = node[key]
            case list() if key.isdigit() and int(key) < len(node):
                node = node[int(key)]
            case _:
                raise ValueError(f"{ref} refers to nothing")
    return node


def _inline_refs(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    def resolve(node: JsonValue, seen: tuple[str, ...]) -> JsonValue:
        match node:
            case {"$ref": str(ref), **siblings}:
                if ref in seen:
                    raise ValueError(f"{ref} refers to itself")
                target = resolve(_pointer(schema, ref), (*seen, ref))
                rest = {k: resolve(v, seen) for k, v in siblings.items()}
                return {**target, **rest} if isinstance(target, dict) else target
            case dict():
                return {k: resolve(v, seen) for k, v in node.items()}
            case list():
                return [resolve(v, seen) for v in node]
            case _:
                return node

    top = {k: v for k, v in schema.items() if k not in ("$defs", "definitions")}
    return cast(dict[str, JsonValue], resolve(top, ()))


_SCHEMA_OPS: dict[SchemaOp, Callable[[dict[str, JsonValue]], dict[str, JsonValue]]] = {
    "inline_refs@1": _inline_refs,
}


class Output(Component):
    kind: Literal["chunk.output"] = "chunk.output"
    contract: OutputContract
    json_schema: dict[str, JsonValue] | None = None
    guidance: Text | None = None
    schema_ops: Annotated[tuple[SchemaOp, ...], AfterValidator(distinct)] = ()

    @model_validator(mode="after")
    def _schema_placement(self) -> "Output":
        if isinstance(self.contract, TextOutput):
            if self.contract.json_schema is not None:
                raise ValueError(
                    "an output's schema goes in json_schema, not its contract"
                )
        elif self.json_schema is None:
            raise ValueError(f"{self.contract.kind} contracts need json_schema")
        return self

    @model_validator(mode="after")
    def _inserts(self) -> "Output":
        if self.guidance is None:
            return self
        used = _check_inserts(self.kind, _segments(self.guidance))
        if "schema" in used and self.output_schema is None:
            raise ValueError("only an output with a schema can show it")
        return self

    @model_validator(mode="after")
    def _schema_ops(self) -> "Output":
        if self.schema_ops and self.json_schema is None:
            raise ValueError("schema ops need a schema")
        _ = self.output_schema
        return self

    @property
    def output_schema(self) -> dict[str, JsonValue] | None:
        schema = self.json_schema
        if schema is None:
            return None
        for op in self.schema_ops:
            schema = _SCHEMA_OPS[op](schema)
        return schema

    def guidance_text(self) -> str:
        if self.guidance is None:
            return ""
        schema = json.dumps(self.output_schema, indent=2, ensure_ascii=False)
        return _fill_text(self.guidance, {"schema": schema})


class Sampling(Component):
    kind: Literal["chunk.sampling"] = "chunk.sampling"
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    repetition_penalty: float | None = None
    stop: tuple[str, ...] = ()
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_tokens_key: Literal["max_completion_tokens", "max_tokens"] = (
        "max_completion_tokens"
    )

    @model_validator(mode="before")
    @classmethod
    def _greedy(cls, data: object) -> object:
        return _canonical_sampling(data)

    def body(self) -> dict[str, JsonValue]:
        body: dict[str, JsonValue] = self.model_dump(
            exclude_none=True,
            exclude={"kind", "stop", "max_output_tokens", "max_tokens_key"},
        )
        if self.stop:
            body["stop"] = list(self.stop)
        if self.max_output_tokens is not None:
            body[self.max_tokens_key] = self.max_output_tokens
        return body


class Reasoning(Component):
    kind: Literal["chunk.reasoning"] = "chunk.reasoning"
    effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    thinking_token_budget: int | None = Field(default=None, ge=0)
    chat_template_kwargs: Settings = Field(default_factory=dict)


class Passthrough(Component):
    kind: Literal["chunk.passthrough"] = "chunk.passthrough"
    body: Settings

    @model_validator(mode="after")
    def _no_owned_keys(self) -> "Passthrough":
        reserved = RUNTIME_KEYS | OUTPUT_KEYS | MANAGED_KEYS | BYPASS_KEYS
        if owned := self.body.keys() & reserved:
            raise ValueError(f"passthrough may not set {sorted(owned)}")
        return self


ToolName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
EntryPoint = Annotated[str, StringConstraints(pattern=r"^[\w.]+:[\w.]+$")]


# A function the model may call between turns. Its code is pinned like a scorer's,
# since what it returns is what the model reads next.
class Tool(Component):
    kind: Literal["tool"] = "tool"
    name: ToolName
    description: str = Field(min_length=1)
    parameters: dict[str, JsonValue]
    code: Code
    entry_point: EntryPoint


ToolRef = Annotated[Digest, RefTo("tool")]


class Toolset(Component):
    kind: Literal["chunk.toolset"] = "chunk.toolset"
    tools: tuple[ToolRef, ...] = Field(min_length=1)
    guidance: str | None = Field(default=None, min_length=1)
    max_rounds: int = Field(default=5, ge=1)

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        names = [resolve(get, t, Tool).name for t in self.tools]
        if dupes := sorted({n for n in names if names.count(n) > 1}):
            return [f"tools named more than once: {dupes}"]
        return []


# Source text -> translation, applied to every text a recipe brings, as gettext does.
class Translations(Component):
    kind: Literal["chunk.translations"] = "chunk.translations"
    entries: Annotated[dict[str, str], AfterValidator(sorted_keys)] = Field(
        min_length=1
    )

    @field_validator("entries")
    @classmethod
    def _entries(cls, entries: dict[str, str]) -> dict[str, str]:
        if any(not text.strip() for text in entries):
            raise ValueError("whitespace isn't translated")
        if empty := sorted(
            text for text, translation in entries.items() if not translation
        ):
            raise ValueError(f"no translation given for {empty}")
        return entries


class AppendixLayout(Frozen):
    before: str = "\n\n"
    between: str = "\n\n"
    after: str = ""

    def join(self, appendices: list[str]) -> str:
        if not appendices:
            return ""
        return self.before + self.between.join(appendices) + self.after


class Recipe(Frozen):
    purpose: Purpose = "generation"
    instructions: Annotated[Digest, RefTo("chunk.instructions")] | None = None
    few_shot: Annotated[Digest, RefTo("chunk.few_shot")] | None = None
    prompt: Annotated[Digest, RefTo("chunk.prompt")]
    output: Annotated[Digest, RefTo("chunk.output")]
    sampling: Annotated[Digest, RefTo("chunk.sampling")]
    reasoning: Annotated[Digest, RefTo("chunk.reasoning")] | None = None
    passthrough: Annotated[Digest, RefTo("chunk.passthrough")] | None = None
    appendix_layout: AppendixLayout = AppendixLayout()
    translations: Annotated[Digest, RefTo("chunk.translations")] | None = None
    toolset: Annotated[Digest, RefTo("chunk.toolset")] | None = None


# The frozen request: what a trial or judge actually references.


class Skeleton(Component):
    kind: Literal["skeleton"] = "skeleton"
    purpose: Purpose = "generation"
    api: Api = "chat.completions"
    messages: tuple[Message, ...] = Field(min_length=1)
    body: Settings = Field(default_factory=dict)
    contract: OutputContract
    appendix_layout: AppendixLayout = AppendixLayout()
    tools: tuple[ToolRef, ...] = ()
    max_rounds: int | None = Field(default=None, ge=1)

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
        if bool(self.tools) != (self.max_rounds is not None):
            raise ValueError("a skeleton has max_rounds exactly when it has tools")
        keys = set(self.body) & OUTPUT_KEYS
        names = _tool_names(self.body)
        if len(set(names)) != len(names):
            raise ValueError("tools named more than once")
        choice = self.body.get("tool_choice")
        match self.contract:
            case NativeOutput() if self.tools:
                if keys != {"response_format", "tools", "tool_choice"} or (
                    choice != "auto"
                ):
                    raise ValueError(
                        "native contract with tools needs response_format, tools "
                        + "and tool_choice auto"
                    )
            case NativeOutput():
                if keys != {"response_format"}:
                    raise ValueError(
                        "native contract needs response_format and no tools"
                    )
            case ToolOutput(name=name, description=description):
                if keys != {"tools", "tool_choice"}:
                    raise ValueError("tool contract needs tools and tool_choice only")
                if self.tools and (name not in names or choice != "required"):
                    raise ValueError(
                        f"tool contract with tools needs a tool named {name!r} "
                        + "and tool_choice required"
                    )
                if not self.tools and names != [name]:
                    raise ValueError(
                        f"tool contract needs exactly one tool named {name!r}"
                    )
                if _tool_description(self.body, name) != description:
                    raise ValueError(
                        f"tool contract needs its tool described as {description!r}"
                    )
            case TextOutput() if self.tools:
                if keys != {"tools", "tool_choice"} or choice != "auto":
                    raise ValueError(
                        "text contract with tools needs tools and tool_choice auto"
                    )
            case TextOutput():
                if keys:
                    raise ValueError("text contract may not constrain the output")
        match self.contract, self.output_schema:
            case (TextOutput(), _) | (_, dict()):
                pass
            case _:
                raise ValueError(
                    f"{self.contract.kind} contract needs its schema as an object"
                )
        return self

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        answer = self.contract.name if isinstance(self.contract, ToolOutput) else None
        offered = [n for n in _tool_names(self.body) if n != answer]
        named = [resolve(get, t, Tool).name for t in self.tools]
        if offered != named:
            return [f"the body offers tools {offered}, the skeleton names {named}"]
        return []

    @property
    def greedy(self) -> bool:
        return self.body.get("temperature") == 0

    @property
    def output_schema(self) -> JsonValue:
        match self.contract:
            case NativeOutput():
                match self.body.get("response_format"):
                    case {"type": "json_schema", "json_schema": {"schema": schema}}:
                        return schema
                    case _:
                        return None
            case ToolOutput(name=name):
                function = _tool_function(self.body, name)
                return None if function is None else function.get("parameters")
            case TextOutput(json_schema=schema):
                return schema


SkeletonRef = Annotated[Digest, RefTo("skeleton")]


def _tool_name(tool: JsonValue) -> str:
    match tool:
        case {"function": {"name": str() as name}}:
            return name
        case _:
            raise ValueError(f"a tool needs a function with a name, not {tool!r}")


def _tool_names(body: Mapping[str, JsonValue]) -> list[str]:
    match body.get("tools"):
        case None:
            return []
        case list() as tools:
            return [_tool_name(t) for t in tools]
        case _:
            raise ValueError("tools must be a list")


def _tool_function(
    body: Mapping[str, JsonValue], name: str
) -> dict[str, JsonValue] | None:
    tools = body.get("tools")
    for t in tools if isinstance(tools, list) else []:
        match t:
            case {"function": {"name": str() as n} as function} if n == name:
                return function
            case _:
                pass
    return None


def _tool_description(body: Mapping[str, JsonValue], name: str) -> JsonValue:
    function = _tool_function(body, name)
    return None if function is None else function.get("description")


def _check_slots(
    purpose: Purpose, contents: Sequence[tuple[Segment | Insert, ...]]
) -> None:
    used = [s.slot for c in contents for s in c if isinstance(s, Slot)]
    if stray := set(used) - SLOTS_BY_PURPOSE[purpose]:
        raise ValueError(f"{purpose} requests cannot use slots {sorted(stray)}")
    if missing := REQUIRED_SLOTS[purpose] - set(used):
        raise ValueError(f"{purpose} requests need slots {sorted(missing)}")
    if dupes := {s for s in used if used.count(s) > 1}:
        raise ValueError(f"slots used more than once: {sorted(dupes)}")


Translate = Callable[[str], str]

# Schema keywords whose string value is prose the model may read, keywords whose
# members are named schemas, and keywords holding instance data, never translated.
_PROSE = frozenset({"title", "description"})
_NAMED = frozenset(
    {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
)
_DATA = frozenset({"enum", "const", "default", "examples"})


def _tr(text: str, tr: Translate) -> str:
    return tr(text) if text.strip() else text


def _tr_segments[T](
    segments: Iterable[str | Insert | T], tr: Translate
) -> tuple[str | Insert | T, ...]:
    translated: list[str | Insert | T] = []
    for s in segments:
        if isinstance(s, str):
            translated.append(_tr(s, tr))
        elif isinstance(s, Insert):
            translated.append(
                Insert(
                    insert=s.insert, before=_tr(s.before, tr), after=_tr(s.after, tr)
                )
            )
        else:
            translated.append(s)
    return tuple(translated)


def _tr_text(
    text: str | tuple[str | Insert, ...], tr: Translate
) -> str | tuple[str | Insert, ...]:
    return _tr(text, tr) if isinstance(text, str) else _tr_segments(text, tr)


def _tr_schema(node: JsonValue, tr: Translate) -> JsonValue:
    match node:
        case list():
            return [_tr_schema(v, tr) for v in node]
        case dict():
            translated: dict[str, JsonValue] = {}
            for k, v in node.items():
                if k in _PROSE and isinstance(v, str):
                    translated[k] = _tr(v, tr)
                elif k in _NAMED and isinstance(v, dict):
                    translated[k] = {name: _tr_schema(s, tr) for name, s in v.items()}
                elif k in _DATA:
                    translated[k] = v
                else:
                    translated[k] = _tr_schema(v, tr)
            return translated
        case _:
            return node


def _tr_output(output: Output, tr: Translate) -> Output:
    doc = output.model_dump()
    if output.guidance is not None:
        doc["guidance"] = _tr_text(output.guidance, tr)
    if output.json_schema is not None:
        doc["json_schema"] = _tr_schema(output.json_schema, tr)
    match output.contract:
        case ToolOutput(description=str() as description):
            doc["contract"] = {**doc["contract"], "description": _tr(description, tr)}
        case _:
            pass
    return Output.model_validate(doc)


class _Parts(NamedTuple):
    instructions: Instructions | None
    few_shot: FewShot | None
    prompt: Prompt
    output: Output
    appendix_layout: AppendixLayout
    toolset: Toolset | None
    tools: tuple[Tool, ...]


# The recipe's text-bearing parts, with every text passed through tr(part).
def _parts(recipe: Recipe, get: Resolver, tr: Callable[[str], Translate]) -> _Parts:
    instructions = few_shot = None
    if recipe.instructions is not None:
        c = resolve(get, recipe.instructions, Instructions)
        instructions = Instructions(
            role=c.role, text=_tr_text(c.text, tr("instructions"))
        )
    if recipe.few_shot is not None:
        f = resolve(get, recipe.few_shot, FewShot)
        few_shot = FewShot(
            messages=tuple(
                Example(role=m.role, content=_tr(m.content, tr("few_shot")))
                for m in f.messages
            )
        )
    p = resolve(get, recipe.prompt, Prompt)
    prompt = Prompt(purpose=p.purpose, segments=_tr_segments(p.segments, tr("prompt")))
    output = _tr_output(resolve(get, recipe.output, Output), tr("output"))
    layout, t = recipe.appendix_layout, tr("appendix_layout")
    appendix_layout = AppendixLayout(
        before=_tr(layout.before, t),
        between=_tr(layout.between, t),
        after=_tr(layout.after, t),
    )
    toolset, tools = None, ()
    if recipe.toolset is not None:
        ts, t = resolve(get, recipe.toolset, Toolset), tr("toolset")
        toolset = Toolset(
            tools=ts.tools,
            guidance=None if ts.guidance is None else _tr(ts.guidance, t),
            max_rounds=ts.max_rounds,
        )
        tools = tuple(
            Tool.model_validate(
                {
                    **tool.model_dump(),
                    "description": _tr(tool.description, t),
                    "parameters": _tr_schema(tool.parameters, t),
                }
            )
            for tool in (resolve(get, d, Tool) for d in ts.tools)
        )
    return _Parts(
        instructions, few_shot, prompt, output, appendix_layout, toolset, tools
    )


# What a translation of the recipe needs, per part, in order; whitespace is left out.
def texts(recipe: Recipe, get: Resolver) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}

    def collect(part: str) -> Translate:
        seen = found.setdefault(part, [])

        def tr(text: str) -> str:
            if text not in seen:
                seen.append(text)
            return text

        return tr

    _ = _parts(recipe, get, collect)
    return {part: tuple(seen) for part, seen in found.items() if seen}


def compile_request(recipe: Recipe, get: Resolver) -> Skeleton:
    entries = (
        None
        if recipe.translations is None
        else resolve(get, recipe.translations, Translations).entries
    )
    missing: set[str] = set()

    def translate(text: str) -> str:
        if entries is None:
            return text
        if text not in entries:
            missing.add(text)
            return text
        return entries[text]

    parts = _parts(recipe, get, lambda _: translate)
    if missing:
        raise StructuralError(
            "no translation for " + ", ".join(repr(t) for t in sorted(missing))
        )
    prompt, output = parts.prompt, parts.output
    sampling = resolve(get, recipe.sampling, Sampling)
    if prompt.purpose != recipe.purpose:
        raise StructuralError(f"{prompt.purpose} prompt in a {recipe.purpose} recipe")

    toolset = parts.toolset
    fills: dict[InsertName, str] = {
        "output_guidance": output.guidance_text(),
        "tool_guidance": "" if toolset is None else toolset.guidance or "",
    }
    instructions = parts.instructions
    in_prompt = set(_inserts(prompt.segments))
    in_instructions = set(
        [] if instructions is None else _inserts(_segments(instructions.text))
    )
    system = ""
    role: Literal["system", "developer"] = "system"
    if instructions is not None:
        system, role = _fill_text(instructions.text, fills), instructions.role
    # Guidance goes where it's inserted, or else after the instructions, in this order.
    guidances: tuple[InsertName, ...] = ("output_guidance", "tool_guidance")
    for name in guidances:
        if name in in_prompt and name in in_instructions:
            raise StructuralError(
                f"{name.replace('_', ' ')} is inserted in both the instructions "
                + "and the prompt"
            )
        if (text := fills[name]) and name not in in_prompt | in_instructions:
            system = f"{system}\n\n{text}" if system else text

    messages: list[Message] = []
    if system:
        messages.append(Message(role=role, content=(system,)))
    if (few_shot := parts.few_shot) is not None:
        messages.extend(
            Message(role=m.role, content=(m.content,)) for m in few_shot.messages
        )
    messages.append(Message(role="user", content=_fill(prompt.segments, fills)))

    body: dict[str, JsonValue] = {}
    if recipe.passthrough is not None:
        passthrough = resolve(get, recipe.passthrough, Passthrough)
        body.update(passthrough.body)
    managed = sampling.body()
    if recipe.reasoning is not None:
        reasoning = resolve(get, recipe.reasoning, Reasoning)
        if reasoning.effort is not None:
            managed["reasoning_effort"] = reasoning.effort
        if reasoning.thinking_token_budget is not None:
            managed["thinking_token_budget"] = reasoning.thinking_token_budget
        if reasoning.chat_template_kwargs:
            managed["chat_template_kwargs"] = reasoning.chat_template_kwargs
    offered: list[JsonValue] = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in parts.tools
    ]
    if offered:
        managed["tools"], managed["tool_choice"] = offered, "auto"
    contract = output.contract
    match contract:
        case NativeOutput():
            managed["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "output",
                    "strict": True,
                    "schema": output.output_schema,
                },
            }
        case ToolOutput(name=name, description=description):
            if name in {t.name for t in parts.tools}:
                raise StructuralError(
                    f"the answer tool {name!r} has the name of a tool in the toolset"
                )
            function: dict[str, JsonValue] = {"name": name}
            if description is not None:
                function["description"] = description
            function["parameters"] = output.output_schema
            managed["tools"] = [*offered, {"type": "function", "function": function}]
            managed["tool_choice"] = (
                "required"
                if offered
                else {"type": "function", "function": {"name": name}}
            )
        case TextOutput():
            contract = TextOutput(json_schema=output.output_schema)
    body.update(managed)

    return Skeleton(
        purpose=recipe.purpose,
        messages=tuple(messages),
        body=body,
        contract=contract,
        appendix_layout=parts.appendix_layout,
        tools=() if toolset is None else toolset.tools,
        max_rounds=None if toolset is None else toolset.max_rounds,
    )


def render(
    skeleton: Skeleton,
    *,
    model: str,
    seed: int | None,
    fills: Mapping[SlotName, str],
    return_token_ids: bool = True,
) -> dict[str, JsonValue]:
    messages: list[JsonValue] = []
    for m in skeleton.messages:
        parts = [s if isinstance(s, str) else fills[s.slot] for s in m.content]
        messages.append({"role": m.role, "content": "".join(parts)})
    body: dict[str, JsonValue] = {"model": model, "messages": messages, **skeleton.body}
    if seed is not None and not skeleton.greedy:
        body["seed"] = seed
    if return_token_ids:
        body["return_token_ids"] = True
    return body


# Tool rounds. A response that calls tools gets one more request: the same body, the
# assistant's message (its reasoning left out) and one tool message per call, in order.


class ToolCall(Frozen):
    id: str
    name: str
    arguments: str


def _message(response: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    match response.get("choices"):
        case [{"message": dict() as message}, *_]:
            return message
        case _:
            return {}


def tool_calls(response: Mapping[str, JsonValue]) -> tuple[ToolCall, ...]:
    calls = _message(response).get("tool_calls")
    found: list[ToolCall] = []
    for call in calls if isinstance(calls, list) else []:
        match call:
            case {
                "id": str() as id,
                "function": {"name": str() as name, "arguments": str() as arguments},
            }:
                found.append(ToolCall(id=id, name=name, arguments=arguments))
            case _:
                pass
    return tuple(found)


def next_request(
    body: Mapping[str, JsonValue],
    response: Mapping[str, JsonValue],
    results: Mapping[str, str],
) -> dict[str, JsonValue]:
    message, calls = _message(response), tool_calls(response)
    if missing := [c.id for c in calls if c.id not in results]:
        raise StructuralError(f"no result for tool calls {missing}")
    previous = body.get("messages")
    messages: list[JsonValue] = [
        *(previous if isinstance(previous, list) else []),
        {
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls"),
        },
        *(
            {"role": "tool", "tool_call_id": c.id, "content": results[c.id]}
            for c in calls
        ),
    ]
    return {**body, "messages": messages}
