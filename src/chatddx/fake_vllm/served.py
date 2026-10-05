import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

VERSION = "0.24.0+fake"

# The context a model takes when --max-model-len doesn't say.
CONTEXT: dict[str, int] = {
    "Qwen/Qwen3-8B-AWQ": 32768,
    "openai/gpt-oss-20b": 131072,
}
DEFAULT_CONTEXT = 32768

FINGERPRINT_MODES = ("full", "hash", "custom", "none")


class Refused(ValueError):
    pass


# vLLM reads "_" as "-" in a flag's name up to its first ".", and the last of a flag
# given twice wins. Values run to the next flag, as for --served-model-name's nargs="+".
def _options(argv: Sequence[str]) -> dict[str, list[str]]:
    options: dict[str, list[str]] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        i += 1
        if not arg.startswith("--") or arg == "--":
            continue
        raw, eq, value = arg.partition("=")
        head, dot, rest = raw.partition(".")
        name = head.replace("_", "-") + dot + rest
        if name == "--config":
            if not eq:
                raise Refused(
                    "the fake vLLM can't read --config FILE; give its arguments instead"
                )
            continue
        values = [value] if eq else []
        while not eq and i < len(argv) and not argv[i].startswith("--"):
            values.append(argv[i])
            i += 1
        options[name] = values
    return options


def _one(options: dict[str, list[str]], name: str) -> str | None:
    values = options.get(name)
    if values is None:
        return None
    if len(values) != 1:
        raise Refused(f"{name} takes one value, not {values}")
    return values[0]


def _int(options: dict[str, list[str]], name: str, default: int) -> int:
    value = _one(options, name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise Refused(
            f"the fake vLLM reads {name} as an integer, not {value!r}"
        ) from None


def _kwargs(options: dict[str, list[str]]) -> dict[str, Any]:
    value = _one(options, "--default-chat-template-kwargs")
    if value is None:
        return {}
    try:
        kwargs: Any = json.loads(value)
    except ValueError:
        kwargs = None
    if not isinstance(kwargs, dict):
        raise Refused(f"--default-chat-template-kwargs {value} is no JSON object")
    return dict(cast(dict[str, Any], kwargs))


def _fingerprint(
    options: dict[str, list[str]], model: str, argv: Sequence[str]
) -> str | None:
    mode = _one(options, "--fingerprint-mode") or "full"
    if mode not in FINGERPRINT_MODES:
        raise Refused(f"--fingerprint-mode {mode!r} is none of {FINGERPRINT_MODES}")
    if mode == "none":
        return None
    if mode == "custom":
        return _one(options, "--fingerprint-value")
    config = json.dumps([model, list(argv)]).encode()
    return f"vllm-{VERSION}-{hashlib.sha256(config).hexdigest()[:8]}"


@dataclass(frozen=True)
class Served:
    model: str
    names: tuple[str, ...]
    max_model_len: int
    reasoning_parser: bool
    reasoning_config: bool
    auto_tools: bool
    tool_parser: str | None
    default_chat_template_kwargs: dict[str, Any]
    system_fingerprint: str | None
    host: str
    port: int

    @classmethod
    def of(cls, model: str, argv: Sequence[str] = ()) -> "Served":
        options = _options(argv)
        auto_tools = "--enable-auto-tool-choice" in options
        tool_parser = _one(options, "--tool-call-parser")
        if auto_tools and tool_parser is None:
            raise Refused("invalid tool call parser: None")
        context = CONTEXT.get(model, DEFAULT_CONTEXT)
        return cls(
            model=model,
            names=tuple(options.get("--served-model-name") or [model]),
            max_model_len=_int(options, "--max-model-len", context),
            reasoning_parser="--reasoning-parser" in options,
            reasoning_config="--reasoning-config" in options,
            auto_tools=auto_tools,
            tool_parser=tool_parser,
            default_chat_template_kwargs=_kwargs(options),
            system_fingerprint=_fingerprint(options, model, argv),
            host=_one(options, "--host") or "127.0.0.1",
            port=_int(options, "--port", 12099),
        )

    @property
    def name(self) -> str:
        return self.names[0]

    # A tool parser exists only with both flags, whatever the error messages name.
    @property
    def parses_tools(self) -> bool:
        return self.auto_tools and self.tool_parser is not None

    @property
    def family(self) -> str | None:
        model = self.model.lower()
        if "gpt-oss" in model:
            return "harmony"
        if "qwen3" in model:
            return "qwen3"
        if "mistral" in model:
            return "mistral"
        return None
