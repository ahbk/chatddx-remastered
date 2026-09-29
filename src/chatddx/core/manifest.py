"""Rig manifest: the declared cage for LLM accuracy trials on emergency-care case vignettes.

Targets Python 3.12+ and pydantic 2.13.4, with no other runtime dependencies, so the same
module can be imported by the orchestrator, the batch runner, the scorer and the NixOS
container's start-up script.

Three kinds of field
====================
* **Declared factors** live on content-addressed *components*. Every field of a component
  can change what the model outputs (or how that output is scored). A component's identity
  is ``sha256`` over its canonical JSON, so two components with the same factors have the
  same digest, whatever they are called in the UI.
* **Attested observations** live on *records* (``RunRecord``, ``ScoreRecord``). They do not
  cause the output; they are the evidence that the declared factors held while it was made.
* **Non-identity context** (routing, governance clearances, lineage back to the business
  layer's intents) lives in ``BundleContext``. It is never hashed. Names, descriptions,
  authors and UI labels do not belong in this module at all.

Composition (B-b)
=================
Request-time chunks are typed sections (instructions, case template, few-shot, output
contract, sampling, reasoning, extra body). ``resolve_request`` composes one of each slot
into a ``RequestSkeleton``: the complete chat-completions body except the case, which stays
behind runtime slots. Cross-section effects are limited to two mechanical kinds that live
here:

* textual injection: a section may ``fills`` a named compile-time slot in another section
  (e.g. the output contract fills ``output_guidance`` in the instructions);
* canonicalization: factors with no bearing on output are removed before hashing
  (e.g. greedy sampling drops seeds, top_p, top_k, min_p).

Policy (reasoning effort -> output budget, defaults tables, ...) belongs to the business
layer, which produces sections; the manifest only sees the result.

Validation
==========
Structural problems raise (pydantic ``ValidationError``, ``ResolutionError``,
``BundleError``). Everything else is a ``Finding``: canonicalization notices are emitted at
construction (into ``context={"findings": [...]}`` if given, else as ``ManifestWarning``),
and cross-component checks come from ``Bundle.lint()`` and ``verify_attestation()``. The one
hard exception is governance: ``Bundle.check_governance`` raises ``GovernanceViolation``.

Reproducibility tiers
=====================
``assess_engine`` derives the tier from the factors rather than declaring it: *bitwise* for a
local engine whose load parameters enable vLLM batch invariance with a pinned attention
backend, the FlashInfer sampler disabled and a pinned closure; *best_effort* otherwise and
always for remote engines. vLLM's batch invariance is beta: as of 0.24 it rejects
Gated-DeltaNet (Qwen3.5/3.6 hybrid) models and does not cover Triton fused-MoE experts, so a
*bitwise* assessment is a necessary condition, not a guarantee. Canary digests on each run are
the empirical check.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import re
import unicodedata
import warnings
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

MANIFEST_VERSION = 1
BUNDLE_FORMAT = "rig-manifest-bundle/1"

# ---------------------------------------------------------------------------------------
# Core: findings, canonical JSON, digests
# ---------------------------------------------------------------------------------------

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
HMAC_PATTERN = r"^hmac-sha256:[0-9a-f]{64}$"
SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"
SLUG_PATTERN = r"^[a-z][a-z0-9_]*$"

Ref = Annotated[str, StringConstraints(pattern=DIGEST_PATTERN)]
"""Digest of another component (A1: components reference each other by content address)."""
Hmac = Annotated[str, StringConstraints(pattern=HMAC_PATTERN)]
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
Sha256Hex = Annotated[str, StringConstraints(pattern=SHA256_HEX_PATTERN)]
JsonObject = dict[str, JsonValue]

Severity = Literal["info", "warning", "block"]


class ManifestError(Exception):
    """Base class for structural errors."""


class ResolutionError(ManifestError):
    """Sections cannot be composed into a request skeleton."""


class BundleError(ManifestError, ValueError):
    """A bundle is corrupt: digest mismatch, dangling or mistyped reference."""


class GovernanceViolation(ManifestError):
    """Case-derived content would reach an engine without the required clearance."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        super().__init__("; ".join(f.message for f in findings))


class Finding(BaseModel):
    """A soft problem: something to know about, never a reason to stop (except ``block``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: Severity
    code: str
    message: str
    subject: str | None = None
    """Digest of the component the finding is about, when there is one."""


class ManifestWarning(UserWarning):
    """Python warning carrying a Finding, used when no findings list is in the context."""

    def __init__(self, finding: Finding):
        self.finding = finding
        super().__init__(f"[{finding.code}] {finding.message}")


def _emit(info: ValidationInfo | None, finding: Finding) -> None:
    ctx = info.context if info is not None else None
    if isinstance(ctx, dict) and isinstance(ctx.get("findings"), list):
        ctx["findings"].append(finding)
    else:
        warnings.warn(ManifestWarning(finding), stacklevel=4)


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8, no NaN/Infinity.

    Floats use Python's shortest round-trip repr. Note that ``1`` and ``1.0`` in free-form
    JSON (``JsonValue`` fields) are different JSON and therefore different digests.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def keyed_hmac(key: bytes, data: str | bytes) -> str:
    """HMAC-SHA256 used for anything derived from sensitive text (vignettes, wire bodies)."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return "hmac-sha256:" + _hmac.new(key, raw, hashlib.sha256).hexdigest()


class Frozen(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        use_attribute_docstrings=True,
        validate_default=True,
    )


RefPath = tuple[str | int, ...]


class Component(Frozen):
    """A content-addressed, immutable manifest component."""

    kind: str
    lint_codes: ClassVar[tuple[str, ...]] = ()

    def identity(self) -> JsonObject:
        return self.model_dump(mode="json")

    @property
    def digest(self) -> str:
        """sha256 over the canonical JSON of the component, salted with the manifest version.

        Computed on demand; free-form JSON fields are plain dicts and must not be mutated.
        """
        return sha256_digest(
            {"manifest": MANIFEST_VERSION, "component": self.identity()}
        )

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        """(path in the JSON dump, digest, allowed kinds) for every outgoing reference."""
        return []

    def lint(self) -> list[Finding]:
        """Findings that can be derived from this component alone."""
        return []

    def _finding(self, severity: Severity, code: str, message: str) -> Finding:
        return Finding(
            severity=severity, code=code, message=message, subject=self.digest
        )


# ---------------------------------------------------------------------------------------
# Engine: what the model runs in
# ---------------------------------------------------------------------------------------

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ModelArtifact(Component):
    """Weights, tokenizer and model-side configuration, pinned by content."""

    kind: Literal["model_artifact"] = "model_artifact"
    repo: str
    """Hugging Face repo id (or a local name when ``files`` carries the identity)."""
    revision: str
    """Commit sha. A branch or tag is accepted but flagged: it can move."""
    files: dict[str, Sha256Hex] = {}
    """sha256 of every file vLLM reads: weights, config.json, tokenizer files,
    generation_config.json, chat template. This is the real identity of the artifact."""
    tokenizer_repo: str | None = None
    """Tokenizer override; ``None`` means the tokenizer that ships with ``repo``."""
    tokenizer_revision: str | None = None

    def lint(self) -> list[Finding]:
        out = []
        if not _COMMIT_RE.match(self.revision):
            out.append(
                self._finding(
                    "warning",
                    "artifact.revision_not_commit",
                    f"revision {self.revision!r} is not a 40-hex commit sha and can move",
                )
            )
        if not self.files:
            out.append(
                self._finding(
                    "warning",
                    "artifact.no_file_hashes",
                    "no file hashes: the artifact is identified by repo+revision only",
                )
            )
        if self.tokenizer_repo and not (
            self.tokenizer_revision and _COMMIT_RE.match(self.tokenizer_revision)
        ):
            out.append(
                self._finding(
                    "warning",
                    "artifact.tokenizer_unpinned",
                    "tokenizer override is not pinned to a commit",
                )
            )
        return out


class RuntimeClosure(Component):
    """The software the engine runs: vLLM, torch, CUDA libraries, kernels, transformers.

    ``nix_store_path``/``nar_hash`` pin the whole closure; ``oci_digest`` is the alternative
    for non-Nix development setups. ``packages`` lists the versions the closure is declared
    to contain; they are verified against the attestation and used by lint.
    """

    kind: Literal["runtime_closure"] = "runtime_closure"
    nix_store_path: (
        Annotated[str, StringConstraints(pattern=r"^/nix/store/[0-9a-z]{32}-.+$")]
        | None
    ) = None
    nar_hash: str | None = None
    """SRI hash of the closure's NAR (``sha256-...``); content-addressed, survives rebuilds."""
    oci_digest: (
        Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")] | None
    ) = None
    packages: dict[str, str] = {}
    """e.g. {"vllm": "0.24.0", "torch": "2.11.0", "transformers": "5.1.0", "xgrammar": "..."}"""

    def lint(self) -> list[Finding]:
        out = []
        if not (self.nix_store_path or self.oci_digest):
            out.append(
                self._finding(
                    "warning",
                    "closure.unpinned",
                    "neither a Nix store path nor an OCI digest pins the runtime",
                )
            )
        elif self.nix_store_path and not self.nar_hash:
            out.append(
                self._finding(
                    "info",
                    "closure.no_nar_hash",
                    "store path given without NAR hash; impure rebuilds could change content",
                )
            )
        if "vllm" not in self.packages:
            out.append(
                self._finding(
                    "info",
                    "closure.no_vllm_version",
                    "packages does not declare the vLLM version",
                )
            )
        return out


class Hardware(Component):
    """GPU class and host driver. Different architectures select different kernels, so
    outputs are only comparable within one hardware class."""

    kind: Literal["hardware"] = "hardware"
    gpu_name: str
    """As reported by nvidia-smi, e.g. "NVIDIA GeForce RTX 5090"."""
    compute_capability: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")]
    gpu_count: Annotated[int, Field(ge=1)] = 1
    driver_version: str
    """Host kernel-module driver; lives outside the Nix closure."""


class LoadParams(Component):
    """vLLM load-time parameters (0.24 flag names). ``None`` means "the closure's default",
    which is still reproducible because the closure is pinned; lint lists the unset ones that
    most affect output so the cage stays legible."""

    kind: Literal["load_params"] = "load_params"
    dtype: Literal["auto", "bfloat16", "float16", "float32"] | None = None
    quantization: str | None = None
    quantization_config: JsonObject | None = None
    kv_cache_dtype: str | None = None
    max_model_len: Annotated[int, Field(ge=1)] | None = None
    attention_backend: str | None = None
    """e.g. FLASH_ATTN, FLASHINFER, TRITON_ATTN. Auto-selection depends on hardware and version."""
    enforce_eager: bool | None = None
    compilation_config: JsonObject | None = None
    enable_prefix_caching: bool | None = None
    enable_chunked_prefill: bool | None = None
    async_scheduling: bool | None = None
    max_num_seqs: Annotated[int, Field(ge=1)] | None = None
    max_num_batched_tokens: Annotated[int, Field(ge=1)] | None = None
    gpu_memory_utilization: Annotated[float, Field(gt=0, le=1)] | None = None
    """Sets KV-cache size, hence preemption and batch composition."""
    tensor_parallel_size: Annotated[int, Field(ge=1)] = 1
    seed: int | None = None
    """Engine seed (per-request seeds come from the sampling section)."""
    trust_remote_code: bool = False
    hf_overrides: JsonObject | None = None
    generation_config: str | None = None
    """vLLM 0.24 defaults to "auto": any sampling field a request leaves unset is taken from
    the artifact's generation_config.json. "vllm" uses vLLM's own defaults."""
    override_generation_config: JsonObject | None = None
    tokenizer_mode: str | None = None
    chat_template: str | None = None
    """Literal Jinja text. ``None`` uses the template shipped with the artifact."""
    chat_template_content_format: Literal["auto", "string", "openai"] | None = None
    default_chat_template_kwargs: JsonObject | None = None
    reasoning_parser: str | None = None
    """Decides what is returned as content and what as reasoning."""
    structured_outputs_config: JsonObject | None = None
    """e.g. {"backend": "xgrammar"}; governs constrained decoding for json_schema/tool output."""
    speculative_config: JsonObject | None = None
    model_runner_v2: bool | None = None
    """VLLM_USE_V2_MODEL_RUNNER; ``None`` lets vLLM choose per model family."""
    batch_invariant: bool = False
    """VLLM_BATCH_INVARIANT. Beta; see the module docstring for known gaps."""
    env: dict[str, str] = {}
    """Other environment variables that can affect numerics or scheduling,
    e.g. {"VLLM_USE_FLASHINFER_SAMPLER": "0"}."""
    extra_args: tuple[str, ...] = ()
    """Further CLI arguments, verbatim and in order."""

    @field_validator("env")
    @classmethod
    def _env_not_shadowing(cls, v: dict[str, str]) -> dict[str, str]:
        for k in ("VLLM_BATCH_INVARIANT", "VLLM_USE_V2_MODEL_RUNNER"):
            if k in v:
                raise ValueError(f"{k} is a typed field; do not set it through env")
        return v

    def lint(self) -> list[Finding]:
        out = []
        if self.generation_config in (None, "auto"):
            out.append(
                self._finding(
                    "info",
                    "load.generation_config_auto",
                    "sampling fields a request leaves unset come from the artifact's generation_config.json",
                )
            )
        if self.chat_template is None:
            out.append(
                self._finding(
                    "info",
                    "load.chat_template_default",
                    "chat template comes from the artifact",
                )
            )
        if self.dtype in (None, "auto"):
            out.append(
                self._finding(
                    "info",
                    "load.dtype_auto",
                    "dtype is chosen from the checkpoint config",
                )
            )
        if self.speculative_config:
            out.append(
                self._finding(
                    "info",
                    "load.speculative",
                    "speculative decoding preserves the distribution, not individual samples",
                )
            )
        return out


EngineApi = Literal["openai_chat_completions"]


class LocalEngine(Component):
    """An engine we run: a vLLM server started from these four components.

    The server must be started with ``--served-model-name <digest>`` so that every response
    names the cage it came from (see ``vllm_launch``)."""

    kind: Literal["local_engine"] = "local_engine"
    api: EngineApi = "openai_chat_completions"
    artifact: Ref
    closure: Ref
    hardware: Ref
    load: Ref

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        return [
            (("artifact",), self.artifact, ("model_artifact",)),
            (("closure",), self.closure, ("runtime_closure",)),
            (("hardware",), self.hardware, ("hardware",)),
            (("load",), self.load, ("load_params",)),
        ]


class RemoteEngine(Component):
    """An engine behind an API we do not control. Only the endpoint and requested model can
    be declared; everything else is attested per run (returned model id, fingerprint,
    canary digests)."""

    kind: Literal["remote_engine"] = "remote_engine"
    api: EngineApi = "openai_chat_completions"
    base_url: Annotated[str, StringConstraints(pattern=r"^https?://\S+$")]
    model: str

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


ENGINE_KINDS = ("local_engine", "remote_engine")
Tier = Literal["bitwise", "best_effort"]


def assess_engine(
    engine: LocalEngine | RemoteEngine,
    *,
    closure: RuntimeClosure | None = None,
    hardware: Hardware | None = None,
    load: LoadParams | None = None,
) -> tuple[Tier, list[Finding]]:
    """Derive the reproducibility tier from the declared factors."""
    subj = engine.digest
    if isinstance(engine, RemoteEngine):
        return "best_effort", [
            Finding(
                severity="info",
                code="engine.remote_best_effort",
                subject=subj,
                message="remote engine: outputs are reproducible only as far as the provider keeps its engine fixed",
            )
        ]
    assert closure and hardware and load, (
        "local engines need closure, hardware and load"
    )
    reasons: list[str] = []
    out: list[Finding] = []
    if not load.batch_invariant:
        reasons.append(
            "batch invariance is off, so outputs depend on what else is in the batch"
        )
    else:
        major = int(hardware.compute_capability.split(".")[0])
        if major < 8:
            out.append(
                Finding(
                    severity="warning",
                    code="engine.batch_invariant_cc",
                    subject=subj,
                    message=f"batch invariance needs compute capability >= 8.0, hardware is {hardware.compute_capability}",
                )
            )
            reasons.append("hardware below compute capability 8.0")
        if load.attention_backend is None:
            out.append(
                Finding(
                    severity="warning",
                    code="engine.attention_backend_auto",
                    subject=subj,
                    message="batch invariance with an auto-selected attention backend",
                )
            )
            reasons.append("attention backend not pinned")
        if load.env.get("VLLM_USE_FLASHINFER_SAMPLER") != "0":
            out.append(
                Finding(
                    severity="warning",
                    code="engine.flashinfer_sampler",
                    subject=subj,
                    message="batch invariance needs VLLM_USE_FLASHINFER_SAMPLER=0",
                )
            )
            reasons.append("FlashInfer sampler not disabled")
        if load.speculative_config:
            reasons.append("speculative decoding")
    if not (closure.nix_store_path or closure.oci_digest):
        reasons.append("runtime closure not pinned")
    if reasons:
        out.append(
            Finding(
                severity="info",
                code="engine.best_effort",
                subject=subj,
                message="best-effort tier: " + "; ".join(reasons),
            )
        )
        return "best_effort", out
    return "bitwise", out


class VllmLaunch(Frozen):
    argv: tuple[str, ...]
    env: dict[str, str]


def vllm_launch(
    engine: LocalEngine,
    artifact: ModelArtifact,
    load: LoadParams,
    *,
    model_path: str | None = None,
    chat_template_path: str | None = None,
) -> VllmLaunch:
    """Render the ``vllm serve`` command line and environment for a local engine.

    ``model_path``: a local directory holding the verified artifact files (otherwise the repo
    id and revision are passed). ``chat_template_path``: where the caller wrote
    ``load.chat_template``; required when a template is declared.
    """
    if load.chat_template is not None and chat_template_path is None:
        raise ValueError(
            "load.chat_template is set: write it to a file and pass chat_template_path"
        )
    argv: list[str] = ["vllm", "serve", model_path or artifact.repo]
    if model_path is None:
        argv += ["--revision", artifact.revision]
    if artifact.tokenizer_repo:
        argv += ["--tokenizer", artifact.tokenizer_repo]
    if artifact.tokenizer_revision:
        argv += ["--tokenizer-revision", artifact.tokenizer_revision]
    argv += ["--served-model-name", engine.digest]

    def opt(flag: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, bool):
            argv.append(f"--{flag}" if value else f"--no-{flag}")
        elif isinstance(value, dict):
            argv.extend([f"--{flag}", canonical_json(value)])
        else:
            argv.extend([f"--{flag}", str(value)])

    opt("dtype", load.dtype)
    opt("quantization", load.quantization)
    opt("quantization-config", load.quantization_config)
    opt("kv-cache-dtype", load.kv_cache_dtype)
    opt("max-model-len", load.max_model_len)
    opt("attention-backend", load.attention_backend)
    opt("enforce-eager", load.enforce_eager)
    opt("compilation-config", load.compilation_config)
    opt("enable-prefix-caching", load.enable_prefix_caching)
    opt("enable-chunked-prefill", load.enable_chunked_prefill)
    opt("async-scheduling", load.async_scheduling)
    opt("max-num-seqs", load.max_num_seqs)
    opt("max-num-batched-tokens", load.max_num_batched_tokens)
    opt("gpu-memory-utilization", load.gpu_memory_utilization)
    opt("tensor-parallel-size", load.tensor_parallel_size)
    opt("seed", load.seed)
    if load.trust_remote_code:
        argv.append("--trust-remote-code")
    opt("hf-overrides", load.hf_overrides)
    opt("generation-config", load.generation_config)
    opt("override-generation-config", load.override_generation_config)
    opt("tokenizer-mode", load.tokenizer_mode)
    opt("chat-template", chat_template_path)
    opt("chat-template-content-format", load.chat_template_content_format)
    opt("default-chat-template-kwargs", load.default_chat_template_kwargs)
    opt("reasoning-parser", load.reasoning_parser)
    opt("structured-outputs-config", load.structured_outputs_config)
    opt("speculative-config", load.speculative_config)
    argv.extend(load.extra_args)

    env = dict(load.env)
    env["VLLM_BATCH_INVARIANT"] = "1" if load.batch_invariant else "0"
    if load.model_runner_v2 is not None:
        env["VLLM_USE_V2_MODEL_RUNNER"] = "1" if load.model_runner_v2 else "0"
    return VllmLaunch(argv=tuple(argv), env=env)


# ---------------------------------------------------------------------------------------
# Request sections (typed slots) and the resolved skeleton
# ---------------------------------------------------------------------------------------

RUNTIME_SLOTS: dict[str, frozenset[str]] = {
    "generation": frozenset({"case", "appendices"}),
    "judge": frozenset({"case", "appendices", "output", "expectation"}),
    "canary": frozenset(),
}
"""Slots filled by the runner/scorer at request time, per skeleton purpose. Every other slot
is compile-time and must be resolved by ``fills`` or a default."""

Purpose = Literal["generation", "judge", "canary"]


class Text(Frozen):
    type: Literal["text"] = "text"
    text: str


class Slot(Frozen):
    """A hole in a prompt. Filled by plain substitution, never templating: clinical text is
    full of braces and other characters a template engine would misread."""

    type: Literal["slot"] = "slot"
    name: Slug
    default: str | None = None
    """Used when no section fills a compile-time slot."""


Segment = Annotated[Union[Text, Slot], Field(discriminator="type")]


def _canonical_segments(v: Any) -> Any:
    """Accept a plain string or strings inside the list; merge adjacent text; drop empty text."""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return v
    out: list[Any] = []
    for seg in v:
        if isinstance(seg, str):
            seg = {"type": "text", "text": seg}
        elif isinstance(seg, BaseModel):
            seg = seg.model_dump()
        if (
            isinstance(seg, dict)
            and seg.get("type", "text") == "text"
            and "name" not in seg
        ):
            if seg.get("text", "") == "":
                continue
            if (
                out
                and isinstance(out[-1], dict)
                and out[-1].get("type", "text") == "text"
                and "name" not in out[-1]
            ):
                out[-1] = {"type": "text", "text": out[-1]["text"] + seg["text"]}
                continue
            seg = {"type": "text", "text": seg["text"]}
        out.append(seg)
    return out


Segments = Annotated[tuple[Segment, ...], BeforeValidator(_canonical_segments)]


def _slots(segments: Iterable[Text | Slot]) -> list[Slot]:
    return [s for s in segments if isinstance(s, Slot)]


FILLS_DOC = "Text this section injects into named compile-time slots of other sections."
Fills = Annotated[dict[Slug, str], Field(description=FILLS_DOC)]


class InstructionsSection(Component):
    """System/developer message. Optional: some chat templates (e.g. older Gemma) have no
    system role, in which case everything goes in the case template."""

    kind: Literal["instructions"] = "instructions"
    role: Literal["system", "developer"] = "system"
    content: Segments
    fills: Fills = {}


class AppendixLayout(Frozen):
    """How case appendices are rendered into the ``appendices`` slot."""

    heading_prefix: str = ""
    heading_suffix: str = "\n"
    separator: str = "\n\n"
    when_empty: str = ""

    def render(self, appendices: Iterable[Appendix]) -> str:
        items = [
            f"{self.heading_prefix}{a.title}{self.heading_suffix}{a.body}"
            for a in appendices
        ]
        return self.separator.join(items) if items else self.when_empty


class CaseTemplateSection(Component):
    """The user message that carries the case. Must contain the runtime slot ``case``."""

    kind: Literal["case_template"] = "case_template"
    content: Segments
    appendix_layout: AppendixLayout = AppendixLayout()
    fills: Fills = {}


class ChatMessage(Frozen):
    role: Literal["user", "assistant"]
    content: str


class FewShotSection(Component):
    """Worked examples inserted between the instructions and the case message."""

    kind: Literal["few_shot"] = "few_shot"
    messages: tuple[ChatMessage, ...]


OutputMode = Literal["native", "tool", "prompted", "text"]


class OutputContract(Component):
    """Where the answer goes and what shape it has.

    * ``native``: ``response_format`` json_schema (constrained decoding on vLLM)
    * ``tool``: a forced function call whose arguments are the answer
    * ``prompted``: the schema is only described in the prompt (via ``fills``)
    * ``text``: free text

    With P2 this section is produced by capturing the body pydantic-ai would send for an
    output type; ``fills`` then holds the guidance text pydantic-ai would have injected.
    """

    kind: Literal["output_contract"] = "output_contract"
    mode: OutputMode
    json_schema: JsonObject | None = None
    """The literal JSON Schema, as sent; not a reference to a Python class."""
    name: Slug = "final_result"
    description: str | None = None
    strict: bool | None = None
    fills: Fills = {}

    @model_validator(mode="after")
    def _check(self) -> OutputContract:
        if self.mode in ("native", "tool") and self.json_schema is None:
            raise ValueError(f"mode {self.mode!r} requires json_schema")
        if self.mode == "text" and self.json_schema is not None:
            raise ValueError("mode 'text' takes no json_schema")
        return self

    def body_fragment(self) -> JsonObject:
        if self.mode == "native":
            js: JsonObject = {"name": self.name, "schema": self.json_schema}
            if self.description is not None:
                js["description"] = self.description
            if self.strict is not None:
                js["strict"] = self.strict
            return {"response_format": {"type": "json_schema", "json_schema": js}}
        if self.mode == "tool":
            fn: JsonObject = {"name": self.name, "parameters": self.json_schema}
            if self.description is not None:
                fn["description"] = self.description
            if self.strict is not None:
                fn["strict"] = self.strict
            return {
                "tools": [{"type": "function", "function": fn}],
                "tool_choice": {"type": "function", "function": {"name": self.name}},
            }
        return {}


_SAMPLING_KEYS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "presence_penalty",
    "frequency_penalty",
    "repetition_penalty",
    "stop",
)


class SamplingSection(Component):
    """Sampling parameters and the replicate plan.

    Unset fields are not sent, so the engine's defaults apply (for vLLM with
    ``generation_config="auto"``: the artifact's generation_config.json).

    ``seeds`` are explicit, one per replicate; reusing a sampling section across trials gives
    common random numbers across engines. Greedy sampling (temperature exactly 0) makes
    seeds, top_p, top_k and min_p meaningless, so they are dropped and not sent; the replicate
    count is kept to measure engine nondeterminism.
    """

    kind: Literal["sampling"] = "sampling"
    temperature: Annotated[float, Field(ge=0)] | None = None
    top_p: Annotated[float, Field(gt=0, le=1)] | None = None
    top_k: int | None = None
    min_p: Annotated[float, Field(ge=0, le=1)] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    repetition_penalty: Annotated[float, Field(gt=0)] | None = None
    max_output_tokens: Annotated[int, Field(ge=1)] | None = None
    max_tokens_key: Literal["max_completion_tokens", "max_tokens"] = (
        "max_completion_tokens"
    )
    stop: tuple[str, ...] | None = None
    replicates: Annotated[int, Field(ge=1)] = 1
    seeds: tuple[Annotated[int, Field(ge=0, lt=2**63)], ...] | None = None

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_greedy(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return data
        t = data.get("temperature")
        try:
            greedy = t is not None and float(t) == 0.0
        except (TypeError, ValueError):
            return data
        if greedy:
            data = dict(data)
            dropped = [
                k
                for k in ("seeds", "top_p", "top_k", "min_p")
                if data.get(k) is not None
            ]
            for k in dropped:
                data[k] = None
            if dropped:
                _emit(
                    info,
                    Finding(
                        severity="info",
                        code="sampling.greedy_canonicalized",
                        message=f"greedy sampling: dropped {', '.join(dropped)} (no bearing on output)",
                    ),
                )
        return data

    @model_validator(mode="after")
    def _check(self) -> SamplingSection:
        if self.seeds is not None and len(self.seeds) != self.replicates:
            raise ValueError(
                f"{len(self.seeds)} seeds for {self.replicates} replicates"
            )
        return self

    @property
    def greedy(self) -> bool:
        return self.temperature == 0.0

    def body_fragment(self) -> JsonObject:
        body: JsonObject = {
            k: v for k in _SAMPLING_KEYS if (v := getattr(self, k)) is not None
        }
        if "stop" in body:
            body["stop"] = list(self.stop or ())
        if self.max_output_tokens is not None:
            body[self.max_tokens_key] = self.max_output_tokens
        return body

    def lint(self) -> list[Finding]:
        out = []
        if self.temperature is not None and 0 < self.temperature < 0.01:
            out.append(
                self._finding(
                    "warning",
                    "sampling.temperature_clamped",
                    "vLLM clamps temperatures in (0, 0.01) to 0.01",
                )
            )
        if not self.greedy and self.seeds is None:
            out.append(
                self._finding(
                    "warning", "sampling.unseeded", "non-greedy sampling without seeds"
                )
            )
        if self.seeds is not None and len(set(self.seeds)) != len(self.seeds):
            out.append(
                self._finding(
                    "warning", "sampling.duplicate_seeds", "replicates share seeds"
                )
            )
        unset = [
            k
            for k in ("temperature", "top_p", "top_k", "min_p", "max_output_tokens")
            if getattr(self, k) is None
        ]
        if self.greedy:
            unset = [k for k in unset if k not in ("top_p", "top_k", "min_p")]
        if unset:
            out.append(
                self._finding(
                    "info",
                    "sampling.engine_defaults",
                    f"engine defaults apply for: {', '.join(unset)}",
                )
            )
        return out


def suggest_seeds(n: int) -> tuple[int, ...]:
    """Random 31-bit seeds (portable across OpenAI-compatible APIs) for the UI to propose."""
    import secrets

    return tuple(secrets.randbits(31) for _ in range(n))


class ReasoningSection(Component):
    """Reasoning/thinking controls. Note vLLM derives ``enable_thinking`` from
    ``reasoning_effort`` unless ``chat_template_kwargs`` sets it explicitly."""

    kind: Literal["reasoning"] = "reasoning"
    reasoning_effort: str | None = None
    thinking_token_budget: Annotated[int, Field(ge=0)] | None = None
    chat_template_kwargs: JsonObject = {}
    fills: Fills = {}

    def body_fragment(self) -> JsonObject:
        body: JsonObject = {}
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
        if self.thinking_token_budget is not None:
            body["thinking_token_budget"] = self.thinking_token_budget
        if self.chat_template_kwargs:
            body["chat_template_kwargs"] = self.chat_template_kwargs
        return body


_RESERVED_BODY_KEYS = frozenset(
    {"model", "messages", "seed", "n", "stream", "stream_options"}
)


class ExtraBodySection(Component):
    """Engine-specific request fields, merged verbatim into the top level of the body
    (e.g. vLLM's ``structured_outputs``, ``skip_special_tokens``, ``logit_bias``).
    A key that another section also produces is a composition error."""

    kind: Literal["extra_body"] = "extra_body"
    body: JsonObject

    @field_validator("body")
    @classmethod
    def _no_reserved(cls, v: JsonObject) -> JsonObject:
        bad = sorted(_RESERVED_BODY_KEYS & v.keys())
        if bad:
            raise ValueError(f"reserved keys cannot be set through extra_body: {bad}")
        return v


class SkeletonMessage(Frozen):
    role: Literal["system", "developer", "user", "assistant"]
    content: Segments


class RequestSkeleton(Component):
    """The resolved request: a complete chat-completions body except for runtime slots
    (the case), the per-replicate seed and the model name (the engine's served name).

    This, not the sections that produced it, is the identity of the request-time factors.
    Keys that only change what is *captured* (``logprobs``, ``top_logprobs``) are not
    factors; a runner may add them after ``render`` and they end up in ``request_hmac`` only.
    """

    kind: Literal["request_skeleton"] = "request_skeleton"
    purpose: Purpose
    messages: tuple[SkeletonMessage, ...]
    body: JsonObject
    appendix_layout: AppendixLayout | None = None
    output_mode: OutputMode
    output_schema: JsonObject | None = None
    """For the scorer; for native/tool modes it is also inside ``body``."""
    replicates: Annotated[int, Field(ge=1)] = 1
    seeds: tuple[Annotated[int, Field(ge=0, lt=2**63)], ...] | None = None

    @model_validator(mode="after")
    def _check(self) -> RequestSkeleton:
        allowed = RUNTIME_SLOTS[self.purpose]
        for m in self.messages:
            for s in _slots(m.content):
                if s.name not in allowed:
                    raise ValueError(
                        f"unresolved slot {s.name!r} in a {self.purpose} skeleton"
                    )
        if "appendices" in self.runtime_slots() and self.appendix_layout is None:
            raise ValueError("an appendices slot needs an appendix_layout")
        if (
            "appendices" not in self.runtime_slots()
            and self.appendix_layout is not None
        ):
            raise ValueError("appendix_layout without an appendices slot")
        if self.seeds is not None and len(self.seeds) != self.replicates:
            raise ValueError("seeds must match replicates")
        if _RESERVED_BODY_KEYS & self.body.keys():
            raise ValueError(
                f"reserved keys in body: {sorted(_RESERVED_BODY_KEYS & self.body.keys())}"
            )
        return self

    def runtime_slots(self) -> set[str]:
        return {s.name for m in self.messages for s in _slots(m.content)}

    @property
    def greedy(self) -> bool:
        return self.body.get("temperature") == 0.0

    def render(
        self, model: str, replicate: int, fills: Mapping[str, str] | None = None
    ) -> JsonObject:
        """The exact wire body for one request."""
        fills = dict(fills or {})
        needed = self.runtime_slots()
        missing = needed - fills.keys()
        if missing:
            raise ValueError(f"missing runtime fills: {sorted(missing)}")
        extra = fills.keys() - needed
        if extra:
            raise ValueError(f"fills for slots not in the skeleton: {sorted(extra)}")
        if not 0 <= replicate < self.replicates:
            raise IndexError(f"replicate {replicate} out of range({self.replicates})")
        messages = [
            {
                "role": m.role,
                "content": "".join(
                    s.text if isinstance(s, Text) else fills[s.name] for s in m.content
                ),
            }
            for m in self.messages
        ]
        body: JsonObject = {
            "model": model,
            "messages": messages,
            **json.loads(canonical_json(self.body)),
        }
        if self.seeds is not None:
            body["seed"] = self.seeds[replicate]
        return body

    def lint(self) -> list[Finding]:
        out = []
        if self.purpose == "generation" and "case" not in self.runtime_slots():
            out.append(
                self._finding(
                    "warning",
                    "skeleton.no_case_slot",
                    "generation request never includes the case",
                )
            )
        return out


def resolve_request(
    *,
    purpose: Purpose = "generation",
    case_template: CaseTemplateSection,
    output: OutputContract,
    sampling: SamplingSection,
    instructions: InstructionsSection | None = None,
    few_shot: FewShotSection | None = None,
    reasoning: ReasoningSection | None = None,
    extra: ExtraBodySection | None = None,
) -> RequestSkeleton:
    """Compose one section per slot into a skeleton (B-b mechanics only).

    Compile-time slots are filled from the sections' ``fills`` (each slot by at most one
    section) or their defaults; runtime slots are left for the runner. Body keys may come from
    only one section.
    """
    providers: dict[str, tuple[str, str]] = {}
    for sec in (instructions, case_template, output, reasoning):
        if sec is None:
            continue
        for name, text in sec.fills.items():
            if name in RUNTIME_SLOTS[purpose]:
                raise ResolutionError(f"{sec.kind} tries to fill runtime slot {name!r}")
            if name in providers:
                raise ResolutionError(
                    f"slot {name!r} filled by both {providers[name][0]} and {sec.kind}"
                )
            providers[name] = (sec.kind, text)

    used: set[str] = set()

    def fill(segments: tuple[Text | Slot, ...]) -> list[Any]:
        out: list[Any] = []
        for s in segments:
            if isinstance(s, Text):
                out.append(s.text)
            elif s.name in RUNTIME_SLOTS[purpose]:
                out.append({"type": "slot", "name": s.name})
            elif s.name in providers:
                used.add(s.name)
                out.append(providers[s.name][1])
            elif s.default is not None:
                out.append(s.default)
            else:
                raise ResolutionError(
                    f"compile-time slot {s.name!r} has no fill and no default"
                )
        return out

    messages: list[dict[str, Any]] = []
    if instructions is not None:
        messages.append(
            {"role": instructions.role, "content": fill(instructions.content)}
        )
    if few_shot is not None:
        messages.extend(
            {"role": m.role, "content": m.content} for m in few_shot.messages
        )
    messages.append({"role": "user", "content": fill(case_template.content)})

    unused = providers.keys() - used
    if unused:
        raise ResolutionError(f"fills with no matching slot: {sorted(unused)}")

    body: JsonObject = {}
    for name, frag in (
        ("sampling", sampling.body_fragment()),
        ("output_contract", output.body_fragment()),
        ("reasoning", reasoning.body_fragment() if reasoning else {}),
        ("extra_body", extra.body if extra else {}),
    ):
        clash = body.keys() & frag.keys()
        if clash:
            raise ResolutionError(
                f"{name} sets body keys already set by another section: {sorted(clash)}"
            )
        body.update(frag)

    has_appendices = any(
        isinstance(s, dict)
        and s.get("type") == "slot"
        and s.get("name") == "appendices"
        for m in messages
        if isinstance(m["content"], list)
        for s in m["content"]
    )
    return RequestSkeleton(
        purpose=purpose,
        messages=tuple(messages),
        body=body,
        appendix_layout=case_template.appendix_layout if has_appendices else None,
        output_mode=output.mode,
        output_schema=output.json_schema,
        replicates=sampling.replicates,
        seeds=sampling.seeds,
    )


# ---------------------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------------------


class CaseSource(Component):
    """Where vignettes are fetched from. Vignettes themselves never enter the manifest; their
    drift is detected by keyed HMAC, so the key id is part of the identity."""

    kind: Literal["case_source"] = "case_source"
    source_id: str
    hmac_key_id: str


NormOp = Literal[
    "newlines_lf",  # \r\n and \r -> \n
    "unicode_line_breaks_lf",  # U+2028, U+2029, U+0085 -> \n
    "nbsp_to_space",  # U+00A0, U+202F -> space
    "remove_zero_width",  # U+200B, U+200C, U+200D, U+2060, U+FEFF removed
    "strip_trailing_whitespace",  # per line: trailing spaces and tabs
    "collapse_blank_lines",  # 2+ blank lines -> 1 blank line
    "strip",  # leading/trailing whitespace of the whole text
    "unicode_nfc",
    "unicode_nfkc",  # flagged: folds superscripts and micro sign in lab values
]

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))


def _apply_op(op: str, text: str) -> str:
    match op:
        case "newlines_lf":
            return text.replace("\r\n", "\n").replace("\r", "\n")
        case "unicode_line_breaks_lf":
            return text.replace(" ", "\n").replace(" ", "\n").replace("\u0085", "\n")
        case "nbsp_to_space":
            return text.replace(" ", " ").replace(" ", " ")
        case "remove_zero_width":
            return text.translate(_ZERO_WIDTH)
        case "strip_trailing_whitespace":
            return re.sub(r"[ \t]+(?=\n|\Z)", "", text)
        case "collapse_blank_lines":
            return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
        case "strip":
            return text.strip()
        case "unicode_nfc":
            return unicodedata.normalize("NFC", text)
        case "unicode_nfkc":
            return unicodedata.normalize("NFKC", text)
    raise ValueError(op)


class Normalization(Component):
    """Ordered, closed set of text operations applied to the raw vignette. The semantics of
    each op are defined here and versioned with ``MANIFEST_VERSION``."""

    kind: Literal["normalization"] = "normalization"
    ops: tuple[NormOp, ...]

    def apply(self, text: str) -> str:
        for op in self.ops:
            text = _apply_op(op, text)
        return text

    def lint(self) -> list[Finding]:
        out = []
        if "unicode_nfkc" in self.ops:
            out.append(
                self._finding(
                    "warning",
                    "normalization.nfkc",
                    "NFKC folds characters such as ² and µ that carry meaning in lab values",
                )
            )
        if len(set(self.ops)) != len(self.ops):
            out.append(
                self._finding(
                    "info",
                    "normalization.repeated_op",
                    "an operation is listed more than once",
                )
            )
        return out


class Appendix(Component):
    """A block appended to a case (the vignette itself is never edited). Reusable across
    cases, e.g. a standard vitals block."""

    kind: Literal["appendix"] = "appendix"
    title: str
    body: str


class CaseInput(Component):
    """One case as the model will see it: source record + normalization + appendices."""

    kind: Literal["case_input"] = "case_input"
    source: Ref
    case_id: str
    raw_hmac: Hmac | None = None
    """Keyed HMAC of the raw vignette as fetched; a mismatch at run time is drift."""
    normalization: Ref
    appendices: tuple[Ref, ...] = ()
    """Order is significant: appendices are rendered in this order."""

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        refs: list[tuple[RefPath, str, tuple[str, ...]]] = [
            (("source",), self.source, ("case_source",)),
            (("normalization",), self.normalization, ("normalization",)),
        ]
        refs += [
            (("appendices", i), d, ("appendix",)) for i, d in enumerate(self.appendices)
        ]
        return refs

    def lint(self) -> list[Finding]:
        if self.raw_hmac is None:
            return [
                self._finding(
                    "warning",
                    "case.no_hmac",
                    f"case {self.case_id!r} has no raw HMAC; drift cannot be detected",
                )
            ]
        return []


class CaseSet(Component):
    """A set of case inputs. Order has no bearing on output, so members are sorted."""

    kind: Literal["case_set"] = "case_set"
    cases: tuple[Ref, ...]

    @field_validator("cases", mode="before")
    @classmethod
    def _sort(cls, v: Any, info: ValidationInfo) -> Any:
        if isinstance(v, (list, tuple)):
            uniq = sorted(set(v))
            if len(uniq) != len(v):
                _emit(
                    info,
                    Finding(
                        severity="info",
                        code="case_set.duplicates_removed",
                        message="duplicate case inputs removed",
                    ),
                )
            return tuple(uniq)
        return v

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        return [(("cases", i), d, ("case_input",)) for i, d in enumerate(self.cases)]


def prepare_case(
    raw_text: str,
    *,
    case_input: CaseInput,
    normalization: Normalization,
    appendices: Iterable[Appendix],
    layout: AppendixLayout | None,
    hmac_key: bytes,
) -> tuple[dict[str, str], str, list[Finding]]:
    """Runner helper: raw vignette -> runtime fills. Returns (fills, observed raw HMAC, findings).

    Drift (raw HMAC mismatch) is a warning; the case is still run.
    """
    apps = list(appendices)
    if normalization.digest != case_input.normalization:
        raise ValueError("normalization does not match the case input")
    if tuple(a.digest for a in apps) != case_input.appendices:
        raise ValueError("appendices do not match the case input (content or order)")
    observed = keyed_hmac(hmac_key, raw_text)
    findings: list[Finding] = []
    if case_input.raw_hmac is not None and observed != case_input.raw_hmac:
        findings.append(
            Finding(
                severity="warning",
                code="case.drift",
                subject=case_input.digest,
                message=f"case {case_input.case_id!r} changed at the source since it was declared",
            )
        )
    fills = {"case": normalization.apply(raw_text)}
    if layout is not None:
        fills["appendices"] = layout.render(apps)
    return fills, observed, findings


# ---------------------------------------------------------------------------------------
# Trial
# ---------------------------------------------------------------------------------------


class ExecutionPolicy(Frozen):
    """How the runner issues requests. Without batch invariance, concurrency and order change
    batch composition and therefore outputs, so they are factors."""

    max_concurrency: Annotated[int, Field(ge=1)] = 1
    order: Literal["case_major", "replicate_major", "shuffled"] = "case_major"
    shuffle_seed: int | None = None
    transport_retries: Annotated[int, Field(ge=0)] = 0
    """Retries on transport errors only; a retried request is a new generation."""
    request_timeout_s: Annotated[float, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def _check(self) -> ExecutionPolicy:
        if self.order == "shuffled" and self.shuffle_seed is None:
            raise ValueError("shuffled order needs a shuffle_seed")
        if self.order != "shuffled" and self.shuffle_seed is not None:
            raise ValueError("shuffle_seed only applies to shuffled order")
        return self

    def schedule(self, cases: Iterable[str], replicates: int) -> list[tuple[str, int]]:
        """Deterministic request order, independent of the Python implementation."""
        cases = list(cases)
        if self.order == "replicate_major":
            return [(c, r) for r in range(replicates) for c in cases]
        items = [(c, r) for c in cases for r in range(replicates)]
        if self.order == "shuffled":
            items.sort(
                key=lambda cr: hashlib.sha256(
                    f"{self.shuffle_seed}:{cr[0]}:{cr[1]}".encode()
                ).digest()
            )
        return items


class Trial(Component):
    """Engine x request skeleton x case set, plus how to execute it. Immutable; a batch
    runner turns it into a RunRecord."""

    kind: Literal["trial"] = "trial"
    engine: Ref
    request: Ref
    cases: Ref
    execution: ExecutionPolicy = ExecutionPolicy()

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        return [
            (("engine",), self.engine, ENGINE_KINDS),
            (("request",), self.request, ("request_skeleton",)),
            (("cases",), self.cases, ("case_set",)),
        ]


class CanarySet(Component):
    """Non-sensitive probe requests (canary-purpose skeletons, ideally greedy) run at the
    start and end of every run. Their output digests detect engine drift."""

    kind: Literal["canary_set"] = "canary_set"
    requests: tuple[Ref, ...]

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        return [
            (("requests", i), d, ("request_skeleton",))
            for i, d in enumerate(self.requests)
        ]


# ---------------------------------------------------------------------------------------
# Scoring (X2: scorer-typed expectations, views into both schemas)
# ---------------------------------------------------------------------------------------


class ExpectationSchema(Component):
    """JSON Schema of the expectations a scorer consumes. Validation of payloads against it is
    the scorer's job (this module has no JSON Schema validator)."""

    kind: Literal["expectation_schema"] = "expectation_schema"
    json_schema: JsonObject


class Expectation(Component):
    """What is expected for one source case. Keyed by (source, case_id) rather than by case
    input: appendices change what the model sees, not what the right answer is. If an
    appendix does change the right answer, author a separate expectation set."""

    kind: Literal["expectation"] = "expectation"
    expectation_schema: Ref
    source: Ref
    case_id: str
    payload: JsonValue

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        return [
            (("expectation_schema",), self.expectation_schema, ("expectation_schema",)),
            (("source",), self.source, ("case_source",)),
        ]


class ExpectationSet(Component):
    kind: Literal["expectation_set"] = "expectation_set"
    items: tuple[Ref, ...]

    @field_validator("items", mode="before")
    @classmethod
    def _sort(cls, v: Any) -> Any:
        return tuple(sorted(set(v))) if isinstance(v, (list, tuple)) else v

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        return [(("items", i), d, ("expectation",)) for i, d in enumerate(self.items)]


ParseStatus = Literal["parsed", "extracted", "unparseable", "no_output"]
_MISSING = object()


class ParsePolicy(Frozen):
    """How raw model text becomes JSON before views are applied.

    * ``strict``: the whole text must be JSON
    * ``extract``: strict first, then a fenced ```json block, then the first JSON value found
      anywhere in the text

    ``on_schema_violation``: output that parses but violates the schema is still scored as far
    as the views reach (``score_best_effort``) or given no score (``score_none``).
    """

    strategy: Literal["strict", "extract"] = "extract"
    on_schema_violation: Literal["score_best_effort", "score_none"] = (
        "score_best_effort"
    )

    def parse(self, text: str | None) -> tuple[Any, ParseStatus]:
        if text is None or not text.strip():
            return _MISSING, "no_output"
        try:
            return json.loads(text), "parsed"
        except json.JSONDecodeError:
            if self.strategy == "strict":
                return _MISSING, "unparseable"
        for m in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.S):
            try:
                return json.loads(m.group(1)), "extracted"
            except json.JSONDecodeError:
                pass
        dec = json.JSONDecoder()
        for m in re.finditer(r"[\[{]", text):
            try:
                value, _ = dec.raw_decode(text, m.start())
                return value, "extracted"
            except json.JSONDecodeError:
                continue
        return _MISSING, "unparseable"


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901 JSON Pointer; returns the module's MISSING sentinel instead of raising."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer {pointer!r}")
    cur = doc
    for raw in pointer[1:].split("/"):
        tok = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            if tok not in cur:
                return _MISSING
            cur = cur[tok]
        elif isinstance(cur, list):
            if not tok.isdigit() or int(tok) >= len(cur):
                return _MISSING
            cur = cur[int(tok)]
        else:
            return _MISSING
    return cur


def is_missing(value: Any) -> bool:
    return value is _MISSING


JsonPointer = Annotated[str, StringConstraints(pattern=r"^(/([^~/]|~[01])*)*$")]


class TextMatchMetric(Frozen):
    """A function in the scorer's code base, identified by name, with its parameters.
    Synonym tables and ontology releases it uses are listed in ``ScorerSpec.resources``."""

    type: Literal["text_match"] = "text_match"
    function: str
    params: JsonObject = {}


class JudgeVerdict(Frozen):
    """How a judge's output becomes a number."""

    parse: ParsePolicy = ParsePolicy()
    pointer: JsonPointer = ""
    labels: dict[str, float] | None = None
    """Map categorical verdicts to scores; ``None`` means the pointer yields a number."""


class JudgeMetric(Frozen):
    """An LLM judge (J2): a judge-purpose skeleton sent through the same runner as generation.
    Runtime slots available: output, expectation, case, appendices."""

    type: Literal["judge"] = "judge"
    engine: Ref
    request: Ref
    epochs: Annotated[int, Field(ge=1)] = 1
    """Judge calls per item; seeds come from the judge skeleton (replicates must be >= epochs)."""
    verdict: JudgeVerdict = JudgeVerdict()


Metric = Annotated[Union[TextMatchMetric, JudgeMetric], Field(discriminator="type")]


class ScoringView(Frozen):
    """Score one part of the output against one part of the expectation."""

    name: Slug
    output_pointer: JsonPointer = ""
    expectation_pointer: JsonPointer = ""
    metric: Metric


class CodeIdentity(Frozen):
    package: str
    version: str
    source_hash: str | None = None
    """Commit sha or hash of the scorer source; flagged when absent."""


class ScorerSpec(Component):
    kind: Literal["scorer"] = "scorer"
    code: CodeIdentity
    expectation_schema: Ref
    parse: ParsePolicy = ParsePolicy()
    views: tuple[ScoringView, ...]
    resources: dict[str, str] = {}
    """Named data the metrics read (synonym tables, ontology releases) -> content hash or release id."""

    @field_validator("views")
    @classmethod
    def _unique(cls, v: tuple[ScoringView, ...]) -> tuple[ScoringView, ...]:
        names = [x.name for x in v]
        if len(set(names)) != len(names):
            raise ValueError("view names must be unique")
        return v

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        refs: list[tuple[RefPath, str, tuple[str, ...]]] = [
            (("expectation_schema",), self.expectation_schema, ("expectation_schema",))
        ]
        for i, v in enumerate(self.views):
            if isinstance(v.metric, JudgeMetric):
                refs.append(
                    (("views", i, "metric", "engine"), v.metric.engine, ENGINE_KINDS)
                )
                refs.append(
                    (
                        ("views", i, "metric", "request"),
                        v.metric.request,
                        ("request_skeleton",),
                    )
                )
        return refs

    def judge_engines(self) -> list[str]:
        return [
            v.metric.engine for v in self.views if isinstance(v.metric, JudgeMetric)
        ]

    def lint(self) -> list[Finding]:
        out = []
        if self.code.source_hash is None:
            out.append(
                self._finding(
                    "warning", "scorer.code_unpinned", "scorer code has no source hash"
                )
            )
        if any(isinstance(v.metric, JudgeMetric) for v in self.views):
            out.append(
                self._finding(
                    "info",
                    "scorer.judge_best_effort",
                    "LLM judge scores are best-effort reproducible",
                )
            )
        return out


# ---------------------------------------------------------------------------------------
# Records: what happened (attested, immutable)
# ---------------------------------------------------------------------------------------


class EngineAttestation(Frozen):
    """What an engine reported about itself. Local engines: from the container's cage endpoint.
    Remote engines: from response fields and headers."""

    engine: Ref
    phase: Literal["start", "end"]
    observed_at: datetime
    served_model_name: str | None = None
    nix_store_path: str | None = None
    nar_hash: str | None = None
    oci_digest: str | None = None
    packages: dict[str, str] = {}
    gpu_name: str | None = None
    compute_capability: str | None = None
    gpu_count: int | None = None
    driver_version: str | None = None
    argv: tuple[str, ...] | None = None
    env: dict[str, str] = {}
    response_model: str | None = None
    system_fingerprint: str | None = None
    headers: dict[str, str] = {}
    """Selected response headers that identify the engine."""


class CanaryResult(Frozen):
    canary_set: Ref
    engine: Ref
    phase: Literal["start", "end"]
    digests: tuple[str, ...]
    """sha256 of canonical {content, reasoning_content, tool_arguments, finish_reason} per request."""


def canary_digest(
    content: str | None,
    reasoning_content: str | None,
    tool_arguments: str | None,
    finish_reason: str | None,
) -> str:
    return sha256_digest(
        {
            "content": content,
            "reasoning_content": reasoning_content,
            "tool_arguments": tool_arguments,
            "finish_reason": finish_reason,
        }
    )


RequestStatus = Literal["ok", "http_error", "timeout", "transport_error"]


class Completion(Frozen):
    """One model response as received."""

    status: RequestStatus
    http_status: int | None = None
    content: str | None = None
    reasoning_content: str | None = None
    tool_arguments: str | None = None
    """Raw arguments string of the forced tool call (tool output mode)."""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    response_model: str | None = None
    system_fingerprint: str | None = None
    attempts: Annotated[int, Field(ge=1)] = 1
    error: str | None = None


class RunItem(Frozen):
    case_input: Ref
    replicate: Annotated[int, Field(ge=0)]
    seed: int | None
    observed_raw_hmac: Hmac
    request_hmac: Hmac
    """Keyed HMAC of the canonical wire body (which contains case text)."""
    completion: Completion


class RunRecord(Component):
    kind: Literal["run_record"] = "run_record"
    trial: Ref
    started_at: datetime
    finished_at: datetime
    attestations: tuple[EngineAttestation, ...] = ()
    canaries: tuple[CanaryResult, ...] = ()
    findings: tuple[Finding, ...] = ()
    items: tuple[RunItem, ...]

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        refs: list[tuple[RefPath, str, tuple[str, ...]]] = [
            (("trial",), self.trial, ("trial",))
        ]
        refs += [
            (("items", i, "case_input"), it.case_input, ("case_input",))
            for i, it in enumerate(self.items)
        ]
        return refs


ScoreValidity = Literal[
    "valid",
    "extracted",
    "schema_violation",
    "unparseable",
    "no_output",
    "missing_output_path",
    "missing_expectation_path",
    "no_expectation",
    "judge_failed",
]


class JudgeCall(Frozen):
    view: Slug
    case_input: Ref
    replicate: int
    epoch: int
    request_hmac: Hmac
    completion: Completion


class ScoreItem(Frozen):
    case_input: Ref
    replicate: int
    view: Slug
    validity: ScoreValidity
    value: float | None
    detail: JsonValue = None


class ScoreRecord(Component):
    kind: Literal["score_record"] = "score_record"
    run: Ref
    scorer: Ref
    expectations: Ref
    started_at: datetime
    finished_at: datetime
    attestations: tuple[EngineAttestation, ...] = ()
    """Judge engines."""
    findings: tuple[Finding, ...] = ()
    judge_calls: tuple[JudgeCall, ...] = ()
    items: tuple[ScoreItem, ...]

    def references(self) -> list[tuple[RefPath, str, tuple[str, ...]]]:
        return [
            (("run",), self.run, ("run_record",)),
            (("scorer",), self.scorer, ("scorer",)),
            (("expectations",), self.expectations, ("expectation_set",)),
        ]


def verify_attestation(
    att: EngineAttestation,
    engine: LocalEngine | RemoteEngine,
    *,
    closure: RuntimeClosure | None = None,
    hardware: Hardware | None = None,
    expected_argv: tuple[str, ...] | None = None,
    expected_env: Mapping[str, str] | None = None,
) -> list[Finding]:
    """Compare declared factors with what the engine reported. Mismatches are warnings."""
    out: list[Finding] = []

    def check(code: str, declared: Any, observed: Any, what: str) -> None:
        if declared is not None and observed is not None and declared != observed:
            out.append(
                Finding(
                    severity="warning",
                    code=code,
                    subject=engine.digest,
                    message=f"{what}: declared {declared!r}, observed {observed!r}",
                )
            )

    if att.engine != engine.digest:
        raise ValueError("attestation is for a different engine")
    if isinstance(engine, RemoteEngine):
        check("attest.model", engine.model, att.response_model, "model")
        return out
    check(
        "attest.served_name", engine.digest, att.served_model_name, "served model name"
    )
    if closure is not None:
        check(
            "attest.closure", closure.nix_store_path, att.nix_store_path, "Nix closure"
        )
        check("attest.nar_hash", closure.nar_hash, att.nar_hash, "NAR hash")
        check("attest.oci", closure.oci_digest, att.oci_digest, "OCI digest")
        for pkg, ver in closure.packages.items():
            check("attest.package", ver, att.packages.get(pkg), f"package {pkg}")
    if hardware is not None:
        check("attest.gpu", hardware.gpu_name, att.gpu_name, "GPU")
        check(
            "attest.cc",
            hardware.compute_capability,
            att.compute_capability,
            "compute capability",
        )
        check("attest.gpu_count", hardware.gpu_count, att.gpu_count, "GPU count")
        check("attest.driver", hardware.driver_version, att.driver_version, "driver")
    if expected_argv is not None:
        check("attest.argv", expected_argv, att.argv, "vllm argv")
    if expected_env is not None:
        for k, v in expected_env.items():
            check("attest.env", v, att.env.get(k), f"env {k}")
    return out


def compare_canaries(a: CanaryResult, b: CanaryResult) -> list[Finding]:
    """Drift between two canary results (start vs end of a run, or against a reference run)."""
    if a.canary_set != b.canary_set or a.engine != b.engine:
        raise ValueError("canary results are for different sets or engines")
    changed = [
        i for i, (x, y) in enumerate(zip(a.digests, b.digests, strict=True)) if x != y
    ]
    if not changed:
        return []
    return [
        Finding(
            severity="warning",
            code="canary.drift",
            subject=a.engine,
            message=f"canary outputs changed for request(s) {changed} between {a.phase} and {b.phase}",
        )
    ]


# ---------------------------------------------------------------------------------------
# Non-identity context: routing, governance, lineage
# ---------------------------------------------------------------------------------------


class EndpointBinding(Frozen):
    """Where an engine is reachable and what it is cleared for. Not a factor: moving a server
    or granting clearance does not change the cage."""

    engine: Ref
    base_url: str
    clearances: frozenset[str] = frozenset()


class CaseSourcePolicy(Frozen):
    source: Ref
    required_clearances: frozenset[str]


class Lineage(Frozen):
    """Which business-layer intents or sections produced a component (for the A2 export)."""

    component: Ref
    derived_from: tuple[Ref, ...] = ()
    compiler: str | None = None
    """e.g. "orchestrator 0.3.1 / pydantic-ai 1.x capture"."""


class BundleContext(Frozen):
    endpoints: tuple[EndpointBinding, ...] = ()
    case_policies: tuple[CaseSourcePolicy, ...] = ()
    lineage: tuple[Lineage, ...] = ()
    sections: tuple[
        Annotated[
            Union[
                InstructionsSection,
                CaseTemplateSection,
                FewShotSection,
                OutputContract,
                SamplingSection,
                ReasoningSection,
                ExtraBodySection,
            ],
            Field(discriminator="kind"),
        ],
        ...,
    ] = ()
    """Sections referenced by lineage, kept for readability of the export."""


# ---------------------------------------------------------------------------------------
# Bundle: the A2 export and cross-component checks
# ---------------------------------------------------------------------------------------

AnyComponent = Annotated[
    Union[
        ModelArtifact,
        RuntimeClosure,
        Hardware,
        LoadParams,
        LocalEngine,
        RemoteEngine,
        InstructionsSection,
        CaseTemplateSection,
        FewShotSection,
        OutputContract,
        SamplingSection,
        ReasoningSection,
        ExtraBodySection,
        RequestSkeleton,
        CaseSource,
        Normalization,
        Appendix,
        CaseInput,
        CaseSet,
        Trial,
        CanarySet,
        ExpectationSchema,
        Expectation,
        ExpectationSet,
        ScorerSpec,
        RunRecord,
        ScoreRecord,
    ],
    Field(discriminator="kind"),
]
component_adapter: TypeAdapter[Any] = TypeAdapter(AnyComponent)


def load_component(
    data: Mapping[str, Any] | str, *, context: dict[str, Any] | None = None
) -> Component:
    if isinstance(data, str):
        return component_adapter.validate_json(data, context=context)
    return component_adapter.validate_python(data, context=context)


def component_json_schemas() -> dict[str, JsonObject]:
    """JSON Schema per component kind, e.g. for generating the web UI's forms."""
    return {
        m.model_fields["kind"].default: m.model_json_schema()
        for m in (
            ModelArtifact,
            RuntimeClosure,
            Hardware,
            LoadParams,
            LocalEngine,
            RemoteEngine,
            InstructionsSection,
            CaseTemplateSection,
            FewShotSection,
            OutputContract,
            SamplingSection,
            ReasoningSection,
            ExtraBodySection,
            RequestSkeleton,
            CaseSource,
            Normalization,
            Appendix,
            CaseInput,
            CaseSet,
            Trial,
            CanarySet,
            ExpectationSchema,
            Expectation,
            ExpectationSet,
            ScorerSpec,
            RunRecord,
            ScoreRecord,
        )
    }


class Bundle(Frozen):
    """A root component with the transitive closure of everything it references, keyed by
    digest (A2 export). Verifiable offline: every key must equal its component's digest and
    every reference must resolve to an allowed kind."""

    format: Literal["rig-manifest-bundle/1"] = BUNDLE_FORMAT
    root: Ref
    components: dict[Ref, AnyComponent]
    context: BundleContext = BundleContext()

    @model_validator(mode="after")
    def _verify(self) -> Bundle:
        for key, comp in self.components.items():
            if comp.digest != key:
                raise BundleError(
                    f"digest mismatch for {comp.kind}: key {key}, content {comp.digest}"
                )
        if self.root not in self.components:
            raise BundleError("root is not in the bundle")
        for key, comp in self.components.items():
            for path, ref, kinds in comp.references():
                target = self.components.get(ref)
                if target is None:
                    raise BundleError(
                        f"{comp.kind} {key} references missing {ref} at {path}"
                    )
                if target.kind not in kinds:
                    raise BundleError(
                        f"{comp.kind} {key} at {path} expects {kinds}, got {target.kind}"
                    )
            if isinstance(comp, ExpectationSet):
                seen: set[tuple[str, str]] = set()
                for d in comp.items:
                    e = self.components[d]
                    k = (e.source, e.case_id)
                    if k in seen:
                        raise BundleError(
                            f"expectation set {key} has two expectations for case {e.case_id!r}"
                        )
                    seen.add(k)
        return self

    @classmethod
    def collect(
        cls,
        root: Component,
        lookup: Callable[[str], Component] | Mapping[str, Component],
        context: BundleContext | None = None,
    ) -> Bundle:
        get = lookup.__getitem__ if isinstance(lookup, Mapping) else lookup
        comps: dict[str, Component] = {}
        stack = [root]
        while stack:
            c = stack.pop()
            if c.digest in comps:
                continue
            comps[c.digest] = c
            for _, ref, _ in c.references():
                if ref not in comps:
                    stack.append(get(ref))
        return cls(
            root=root.digest, components=comps, context=context or BundleContext()
        )

    def get(self, digest: str, *kinds: str) -> Any:
        comp = self.components[digest]
        if kinds and comp.kind not in kinds:
            raise BundleError(f"{digest} is a {comp.kind}, expected {kinds}")
        return comp

    def materialize(self, digest: str | None = None) -> JsonObject:
        """Nested, human-readable view: every reference replaced by ``{"$ref": d, ...}``."""
        comp = self.components[digest or self.root]
        doc = comp.identity()
        doc["$digest"] = comp.digest
        for path, ref, _ in comp.references():
            node: Any = doc
            for p in path[:-1]:
                node = node[p]
            node[path[-1]] = {"$ref": ref, **self.materialize(ref)}
        return doc

    def to_json(self) -> str:
        return self.model_dump_json()

    # -- checks -------------------------------------------------------------------------

    def engine_parts(self, engine_digest: str) -> dict[str, Any]:
        eng = self.get(engine_digest, *ENGINE_KINDS)
        if isinstance(eng, RemoteEngine):
            return {"engine": eng}
        return {
            "engine": eng,
            "artifact": self.get(eng.artifact),
            "closure": self.get(eng.closure),
            "hardware": self.get(eng.hardware),
            "load": self.get(eng.load),
        }

    def assess(self, engine_digest: str) -> tuple[Tier, list[Finding]]:
        p = self.engine_parts(engine_digest)
        return assess_engine(
            p["engine"],
            closure=p.get("closure"),
            hardware=p.get("hardware"),
            load=p.get("load"),
        )

    def lint(self) -> list[Finding]:
        out: list[Finding] = []
        for comp in self.components.values():
            out.extend(comp.lint())
            if comp.kind in ENGINE_KINDS:
                out.extend(self.assess(comp.digest)[1])
            if isinstance(comp, Trial):
                out.extend(self._lint_trial(comp))
            if isinstance(comp, ScorerSpec):
                out.extend(self._lint_scorer(comp))
            if isinstance(comp, ScoreRecord):
                scorer: ScorerSpec = self.get(comp.scorer)
                es: ExpectationSet = self.get(comp.expectations)
                if any(
                    self.get(d).expectation_schema != scorer.expectation_schema
                    for d in es.items
                ):
                    out.append(
                        Finding(
                            severity="warning",
                            code="score.schema_mismatch",
                            subject=comp.digest,
                            message="expectations use a different schema than the scorer consumes",
                        )
                    )
        seen: set[tuple[str, str, str | None]] = set()
        uniq = []
        for f in out:
            key = (f.code, f.message, f.subject)
            if key not in seen:
                seen.add(key)
                uniq.append(f)
        return uniq

    def _lint_trial(self, t: Trial) -> list[Finding]:
        out = []
        sk: RequestSkeleton = self.get(t.request)
        if sk.purpose != "generation":
            out.append(
                Finding(
                    severity="warning",
                    code="trial.skeleton_purpose",
                    subject=t.digest,
                    message=f"trial uses a {sk.purpose}-purpose skeleton",
                )
            )
        tier, _ = self.assess(t.engine)
        if tier == "best_effort" and t.execution.max_concurrency > 1:
            out.append(
                Finding(
                    severity="info",
                    code="trial.concurrency",
                    subject=t.digest,
                    message="best-effort engine with concurrency > 1: batch composition varies between runs",
                )
            )
        eng = self.get(t.engine)
        if isinstance(eng, RemoteEngine):
            if "temperature" not in sk.body:
                out.append(
                    Finding(
                        severity="warning",
                        code="trial.remote_defaults",
                        subject=t.digest,
                        message="remote engine with unset temperature: provider defaults are unknown and may change",
                    )
                )
        cs: CaseSet = self.get(t.cases)
        needs_appendix = any(self.get(c).appendices for c in cs.cases)
        if needs_appendix and "appendices" not in sk.runtime_slots():
            out.append(
                Finding(
                    severity="warning",
                    code="trial.appendices_dropped",
                    subject=t.digest,
                    message="some cases have appendices but the skeleton has no appendices slot",
                )
            )
        return out

    def _lint_scorer(self, s: ScorerSpec) -> list[Finding]:
        out = []
        for v in s.views:
            if isinstance(v.metric, JudgeMetric):
                sk: RequestSkeleton = self.get(v.metric.request)
                if sk.purpose != "judge":
                    out.append(
                        Finding(
                            severity="warning",
                            code="scorer.judge_purpose",
                            subject=s.digest,
                            message=f"view {v.name!r} uses a {sk.purpose}-purpose skeleton",
                        )
                    )
                if sk.replicates < v.metric.epochs:
                    out.append(
                        Finding(
                            severity="warning",
                            code="scorer.epochs",
                            subject=s.digest,
                            message=f"view {v.name!r}: {v.metric.epochs} epochs but judge skeleton has {sk.replicates} replicates",
                        )
                    )
        return out

    def check_governance(
        self, trial_digest: str, *, scorer_digest: str | None = None
    ) -> None:
        """Hard block: every engine that will receive case-derived content (the trial's engine
        and any judge engines) must hold every clearance its case sources require.
        Uses ``context.endpoints`` and ``context.case_policies``."""
        t: Trial = self.get(trial_digest, "trial")
        cs: CaseSet = self.get(t.cases, "case_set")
        sources = {self.get(c, "case_input").source for c in cs.cases}
        required: set[str] = set()
        for pol in self.context.case_policies:
            if pol.source in sources:
                required |= pol.required_clearances
        engines = [t.engine]
        if scorer_digest is not None:
            engines += self.get(scorer_digest, "scorer").judge_engines()
        findings = []
        for e in engines:
            bindings = [b for b in self.context.endpoints if b.engine == e]
            if not bindings:
                if required:
                    findings.append(
                        Finding(
                            severity="block",
                            code="governance.no_binding",
                            subject=e,
                            message=f"engine {e} has no endpoint binding and cases require {sorted(required)}",
                        )
                    )
                continue
            for b in bindings:
                missing = required - b.clearances
                if missing:
                    findings.append(
                        Finding(
                            severity="block",
                            code="governance.clearance",
                            subject=e,
                            message=f"endpoint {b.base_url} lacks clearance {sorted(missing)}",
                        )
                    )
        if findings:
            raise GovernanceViolation(findings)
