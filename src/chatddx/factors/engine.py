import hashlib
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, HttpUrl, field_validator

from .base import (
    Api,
    Component,
    Digest,
    Finding,
    Frozen,
    RefTo,
    Sha256Hex,
    sorted_keys,
)

# Flags the start-up script derives from the manifest itself; argv may not set them.
OWNED_FLAGS = frozenset(
    {"--model", "--served-model-name", "--chat-template", "--tokenizer", "--revision"}
)
# Flags that pull arguments from elsewhere: `--config FILE` reads a YAML file the digest
# doesn't cover, and `--config=FILE` is accepted but silently ignored (vLLM 0.24).
UNPINNED_FLAGS = frozenset({"--config"})


# vLLM reads "_" as "-" in a flag's name, up to its first "." (FlexibleArgumentParser).
def flag_names(argv: tuple[str, ...]) -> frozenset[str]:
    names: set[str] = set()
    for arg in argv:
        if arg.startswith("--"):
            name, dot, rest = arg.split("=", 1)[0].partition(".")
            names.add(name.replace("_", "-") + dot + rest)
    return frozenset(names)


# vLLM's parser expands an unambiguous prefix of a flag to the whole flag.
def _abbreviations(
    argv: tuple[str, ...], flags: frozenset[str]
) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name in flag_names(argv) - {"--"}:
        base = name.partition(".")[0]
        if expanded := sorted(f for f in flags if f != base and f.startswith(base)):
            found[name] = expanded
    return found


_NEGATIVE = re.compile(r"-\d")


# `vllm serve` takes the model as its one bare argument, which the start-up script gives,
# so another bare argument stops vLLM from starting. One that follows a flag written
# without "=" may be that flag's value; telling them apart needs vLLM's list of flags.
def _bare_arguments(argv: tuple[str, ...]) -> list[str]:
    bare: list[str] = []
    value = False
    for i, arg in enumerate(argv):
        if arg == "--":
            return bare + list(argv[i + 1 :])
        if arg.startswith("-") and not _NEGATIVE.match(arg):
            value = "=" not in arg
        elif value:
            value = False
        else:
            bare.append(arg)
    return bare


class FileDigest(Frozen):
    path: str
    sha256: Sha256Hex


class ModelArtifact(Component):
    kind: Literal["model"] = "model"
    repo: str
    revision: str
    files: tuple[FileDigest, ...] = Field(min_length=1)

    @field_validator("files")
    @classmethod
    def _sorted_unique(cls, files: tuple[FileDigest, ...]) -> tuple[FileDigest, ...]:
        paths = [f.path for f in files]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate file paths")
        return tuple(sorted(files, key=lambda f: f.path))


ModelRef = Annotated[Digest, RefTo("model")]


class Hardware(Frozen):
    gpu: str
    compute_capability: tuple[int, int]
    vram_mib: int
    driver: str


class Runtime(Frozen):
    server: Literal["vllm"] = "vllm"
    version: str
    closure: str


class LocalEngine(Component):
    kind: Literal["engine.local"] = "engine.local"
    api: Api = "chat.completions"
    hardware: Hardware
    runtime: Runtime
    model: ModelRef
    chat_template: Sha256Hex
    argv: tuple[str, ...] = ()
    env: Annotated[dict[str, str], AfterValidator(sorted_keys)] = Field(
        default_factory=dict
    )

    @field_validator("argv")
    @classmethod
    def _no_owned_flags(cls, argv: tuple[str, ...]) -> tuple[str, ...]:
        owned = flag_names(argv) & OWNED_FLAGS
        if owned:
            raise ValueError(f"argv may not set {sorted(owned)}")
        if unpinned := flag_names(argv) & UNPINNED_FLAGS:
            raise ValueError(
                f"argv may not use {sorted(unpinned)}: its arguments belong in argv, "
                + "where the digest pins them"
            )
        if short := _abbreviations(argv, OWNED_FLAGS | UNPINNED_FLAGS):
            listed = ", ".join(
                f"{a} ({' or '.join(f)})" for a, f in sorted(short.items())
            )
            raise ValueError(f"argv may not abbreviate flags it may not use: {listed}")
        if bare := _bare_arguments(argv):
            raise ValueError(
                f"argv may not have bare arguments {bare}: vLLM takes the model as its "
                + "only bare argument, and the start-up script gives it"
            )
        return argv

    @property
    def served_model_name(self) -> str:
        return self.digest


class RemoteEngine(Component):
    kind: Literal["engine.remote"] = "engine.remote"
    api: Api = "chat.completions"
    base_url: HttpUrl
    model: str

    @property
    def served_model_name(self) -> str:
        return self.model


Engine = LocalEngine | RemoteEngine
EngineRef = Annotated[Digest, RefTo("engine.local", "engine.remote")]


# Templates that insert the current date make the prompt depend on when a run happens.
_READS_DATE = re.compile(r"strftime_now|date_string|\bnow\s*\(")


def check_chat_template(engine: LocalEngine, template: bytes) -> list[Finding]:
    findings: list[Finding] = []
    if hashlib.sha256(template).hexdigest() != engine.chat_template:
        findings.append(
            Finding(
                code="engine.chat_template",
                message="chat template does not match the declared digest",
                subject=engine.digest,
            )
        )
    if _READS_DATE.search(template.decode(errors="replace")):
        findings.append(
            Finding(
                code="engine.chat_template_date",
                message="chat template reads the current date",
                subject=engine.digest,
            )
        )
    return findings
