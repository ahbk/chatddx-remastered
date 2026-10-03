import hashlib
import re
from typing import Annotated, Literal

from pydantic import Field, HttpUrl, field_validator

from .identity import Component, Digest, Finding, Frozen, RefTo, Sha256Hex

# Flags the start-up script derives from the manifest itself; argv may not set them.
OWNED_FLAGS = frozenset(
    {"--model", "--served-model-name", "--chat-template", "--tokenizer", "--revision"}
)


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
    engine: Literal["vllm"] = "vllm"
    version: str
    closure: str


class LocalEngine(Component):
    kind: Literal["engine.local"] = "engine.local"
    hardware: Hardware
    runtime: Runtime
    model: ModelRef
    chat_template: FileDigest
    argv: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("argv")
    @classmethod
    def _no_owned_flags(cls, argv: tuple[str, ...]) -> tuple[str, ...]:
        owned = {a.split("=", 1)[0] for a in argv} & OWNED_FLAGS
        if owned:
            raise ValueError(f"argv may not set {sorted(owned)}")
        return argv

    @property
    def served_model_name(self) -> str:
        return self.digest


class RemoteEngine(Component):
    kind: Literal["engine.remote"] = "engine.remote"
    api: Literal["openai.chat"] = "openai.chat"
    base_url: HttpUrl
    model: str


Engine = LocalEngine | RemoteEngine
EngineRef = Annotated[Digest, RefTo("engine.local", "engine.remote")]


# Templates that insert the current date make the prompt depend on when a run happens.
_READS_DATE = re.compile(r"strftime_now|date_string|\bnow\s*\(")


def check_chat_template(engine: LocalEngine, template: bytes) -> list[Finding]:
    findings: list[Finding] = []
    if hashlib.sha256(template).hexdigest() != engine.chat_template.sha256:
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
