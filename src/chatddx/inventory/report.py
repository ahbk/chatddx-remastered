import json
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from chatddx.factors.base import Finding
from chatddx.factors.bundle import Registry
from chatddx.factors.engine import (
    FileDigest,
    Hardware,
    LocalEngine,
    ModelArtifact,
    Runtime,
    check_chat_template,
    flag_names,
)
from chatddx.factors.lint import lint

from .inventory import EndpointSpec, HostSpec
from .serving import LISTEN_FLAGS

# What o11n.vllm's report says about the vLLM servers on one host
# (Kompismoln/o11n, nixos/vllm-report.py).
REPORT = "o11n.vllm.report/1"

# Set by o11n.vllm for every server to find the model, the GPU and CUDA (which the
# runtime closure covers): where an engine runs, not what it is.
HOST_ENV = frozenset(
    {"CUDA_HOME", "CUDA_VISIBLE_DEVICES", "HF_HOME", "HF_HUB_CACHE", "HF_HUB_OFFLINE"}
)


@dataclass(frozen=True)
class Imported:
    name: str
    server: str
    host: str
    model: ModelArtifact
    engine: LocalEngine
    bind: str
    location: str
    template: str
    endpoint: EndpointSpec
    findings: tuple[Finding, ...]


def read(source: str) -> tuple[dict[str, Any], str | None]:
    if re.match(r"^https?://", source):
        with urllib.request.urlopen(source, timeout=600) as response:
            data: Any = json.loads(response.read())
        hostname = urllib.parse.urlsplit(source).hostname
    else:
        text = sys.stdin.read() if source == "-" else Path(source).read_text()
        data, hostname = json.loads(text), None
    report = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    if report.get("report") != REPORT:
        raise ValueError(f"{source} is not an {REPORT} report")
    return report, hostname


# A part of the report that couldn't be gathered holds only the error.
def _error(value: object) -> object | None:
    return (
        cast(dict[str, object], value).get("error") if isinstance(value, dict) else None
    )


def _section(server: dict[str, Any], key: str) -> Any:
    if server.get(key) is None:
        raise ValueError(f"the report has no {key}")
    if (error := _error(server[key])) is not None:
        raise ValueError(f"the report has no {key}: {error}")
    return server[key]


def _max_num_seqs(argv: tuple[str, ...]) -> int | None:
    value: str | None = None
    for i, arg in enumerate(argv):
        name, eq, rest = arg.partition("=")
        if name.replace("_", "-") == "--max-num-seqs":
            value = rest if eq else next(iter(argv[i + 1 : i + 2]), None)
    return None if value is None else int(value)


def _url(host: str, port: int) -> str:
    return f"http://{f'[{host}]' if ':' in host else host}:{port}/v1/"


def _template(engine: LocalEngine, template: dict[str, Any]) -> Iterable[Finding]:
    def finding(
        code: str, message: str, level: Literal["info", "warning"] = "warning"
    ) -> Finding:
        return Finding(level=level, code=code, message=message, subject=engine.digest)

    if not template.get("used", True):
        yield finding(
            "engine.chat_template_unused",
            "vLLM renders this model with Harmony and doesn't read the chat template, so "
            + "its hash pins nothing",
            "info",
        )
        if "VLLM_SYSTEM_START_DATE" not in engine.env:
            yield finding(
                "engine.harmony_date",
                "Harmony writes the current date into every prompt; set "
                + "VLLM_SYSTEM_START_DATE in the server's environment to fix it",
            )
    elif (text := template.get("text")) is None:
        yield finding(
            "engine.chat_template_unread",
            "the report has no template text, so it wasn't checked",
            "info",
        )
    else:
        yield from check_chat_template(engine, cast(str, text).encode())
    own = template.get("model_template")
    if isinstance(own, dict) and cast(dict[str, Any], own).get("matches") is False:
        yield finding(
            "engine.chat_template_not_the_model_s",
            "the chat template isn't the model's own at this revision ("
            + f"{cast(dict[str, Any], own).get('path')})",
            "info",
        )


def _serving(engine: LocalEngine, server: dict[str, Any]) -> Iterable[Finding]:
    def finding(code: str, message: str) -> Finding:
        return Finding(code=code, message=message, subject=engine.digest)

    process = cast(dict[str, Any], server.get("process") or {})
    if (error := _error(process)) is not None:
        yield finding("endpoint.process", f"the server's process wasn't seen: {error}")
    elif not process.get("running"):
        yield finding("endpoint.not_running", "the server isn't running")
    elif not process.get("matches"):
        yield finding(
            "endpoint.argv",
            "the running server's argv isn't the configured one, so it may predate "
            + "the configuration",
        )
    served = cast(dict[str, Any], server.get("served") or {})
    if (error := _error(served)) is not None:
        yield finding("endpoint.unreachable", f"the server didn't answer: {error}")
        return
    version = cast(dict[str, Any], server["package"])["version"]
    if served.get("version") != version:
        yield finding(
            "endpoint.version",
            f"the server says it is vLLM {served.get('version')}, its package {version}",
        )
    models = cast(list[dict[str, Any]], served.get("models") or [])
    names = [m.get("id") for m in models]
    if engine.served_model_name not in names:
        yield finding(
            "endpoint.served_name",
            f"the server answers as {', '.join(map(str, names)) or 'nothing'}; put "
            + f"{engine.served_model_name} first in its servedModelNames",
        )


def imported(
    report: dict[str, Any], server: str, endpoint_host: str | None = None
) -> Imported:
    hostname = cast(str, report["hostname"])
    name = f"{server}@{hostname}"
    servers = cast(dict[str, dict[str, Any]], report["servers"])
    if server not in servers:
        raise LookupError(
            f"the report has no server {server!r}, only {sorted(servers)}"
        )
    s = servers[server]
    try:
        gpus = cast(list[dict[str, Any]], _section(s, "gpus"))
        if len(gpus) != 1:
            raise ValueError(
                f"runs on {len(gpus)} GPUs, and an engine's hardware is one"
            )
        gpu = gpus[0]
        found = cast(dict[str, Any], _section(s, "model"))
        if found.get("revision") is None:
            raise ValueError(
                f"its model {found.get('id')} is no Hugging Face repo at a revision"
            )
        template = cast(dict[str, Any], _section(s, "chat_template"))
        model = ModelArtifact(
            repo=found["id"],
            revision=found["revision"],
            files=tuple(
                FileDigest(path=f["path"], sha256=f["sha256"]) for f in found["files"]
            ),
        )
        argv = tuple(cast(list[str], s["extra_args"]))
        if taken := sorted(flag_names(argv) & LISTEN_FLAGS):
            raise ValueError(f"its extra arguments set {', '.join(taken)}")
        environment = cast(dict[str, str], s["environment"])
        engine = LocalEngine(
            hardware=Hardware.model_validate(
                {
                    "gpu": gpu["name"],
                    "compute_capability": gpu["compute_capability"],
                    "vram_mib": gpu["memory_mib"],
                    "driver": gpu["driver"],
                }
            ),
            runtime=Runtime.model_validate(
                {"version": s["package"]["version"], "closure": s["runtime"]}
            ),
            model=model.digest,
            chat_template=template["sha256"],
            argv=argv,
            env={k: v for k, v in environment.items() if k not in HOST_ENV},
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"{name} can't be imported: {e}") from e
    registry = Registry()
    _ = registry.add(model)
    _ = registry.add(engine)
    endpoint = EndpointSpec.model_validate(
        {
            "engine": engine.digest,
            "url": _url(endpoint_host or hostname, s["port"]),
            "host": hostname,
            "max_jobs": _max_num_seqs(argv) or 1,
        }
    )
    return Imported(
        name=name,
        server=server,
        host=hostname,
        model=model,
        engine=engine,
        bind=s["host"],
        location=found["path"],
        template=template["path"],
        endpoint=endpoint,
        findings=(
            *lint(registry),
            *_template(engine, template),
            *_serving(engine, s),
        ),
    )


_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(key: str) -> str:
    return key if _BARE.match(key) else _value(key)


def _value(value: object) -> str:
    match value:
        case bool():
            return "true" if value else "false"
        case int():
            return str(value)
        case str():
            return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
        case list() | tuple():
            return f"[{', '.join(_value(v) for v in cast(Iterable[object], value))}]"
        case dict():
            items = cast(dict[str, object], value).items()
            return "{ " + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in items) + " }"
        case _:
            raise TypeError(f"no TOML for {value!r}")


def factors_toml(imports: Iterable[Imported]) -> str:
    tables: list[str] = []
    for i in imports:
        files = "".join(
            f"\n  {_value({'path': f.path, 'sha256': f.sha256})},"
            for f in i.model.files
        )
        tables.append(
            f"[model.{_key(i.name)}]\n"
            + f"repo = {_value(i.model.repo)}\n"
            + f"revision = {_value(i.model.revision)}\n"
            + f"files = [{files}\n]\n"
        )
        e = i.engine
        fields = {
            "description": f"{i.model.repo} on {i.host}, as its o11n.vllm report has it.",
            "tags": [i.host],
            "model": i.name,
            "hardware": e.hardware.model_dump(),
            "runtime": {"version": e.runtime.version, "closure": e.runtime.closure},
            "chat_template": e.chat_template,
            "argv": e.argv,
            "env": e.env,
        }
        tables.append(
            f"[local_engine.{_key(i.name)}]\n"
            + "".join(f"{k} = {_value(v)}\n" for k, v in fields.items() if v)
        )
    return "\n".join(tables)


def endpoints_toml(imports: Iterable[Imported]) -> str:
    tables: list[str] = []
    hosts: dict[str, HostSpec] = {}
    urls: dict[str, str] = {}
    for i in imports:
        e = i.endpoint
        if (other := urls.setdefault(str(e.url), i.name)) != i.name:
            raise ValueError(f"{i.name} and {other} are both at {e.url}")
        tables.append(
            f"[endpoint.{_key(i.name)}]\n"
            + f"engine = {_value(e.engine)}\n"
            + f"url = {_value(str(e.url))}\n"
            + f"host = {_value(e.host)}\n"
            + f"max_jobs = {_value(e.max_jobs)}\n"
        )
        known = hosts.get(i.host, HostSpec(bind=i.bind))
        if known.bind != i.bind:
            raise ValueError(
                f"{i.name} binds {i.bind}, another server on {i.host} {known.bind}"
            )
        hosts[i.host] = HostSpec(
            bind=i.bind,
            models=known.models | {i.model.digest: i.location},
            templates=known.templates | {i.engine.chat_template: i.template},
        )
    for name, host in hosts.items():
        tables.append(
            f"[host.{_key(name)}]\nbind = {_value(host.bind)}\n\n"
            + f"[host.{_key(name)}.models]\n"
            + "".join(f"{_key(k)} = {_value(v)}\n" for k, v in host.models.items())
            + f"\n[host.{_key(name)}.templates]\n"
            + "".join(f"{_key(k)} = {_value(v)}\n" for k, v in host.templates.items())
        )
    return "\n".join(tables)
