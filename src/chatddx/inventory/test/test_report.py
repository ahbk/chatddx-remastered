import hashlib
import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from chatddx.cli import main
from chatddx.core.rig import rig
from chatddx.facts.facts import Facts
from chatddx.inventory.inventory import Inventory
from chatddx.inventory.report import (
    REPORT,
    Imported,
    endpoints_toml,
    factors_toml,
    imported,
    read,
)
from chatddx.inventory.serving import start_up
from chatddx.seed import plan_factors
from chatddx.seed.world import endpoints as endpoints_of

STORE = "/nix/store/" + "s" * 32
COMMIT = "4da05a8edb55c6046cce958586c33b61da07bb79"
TEMPLATE = "{%- for message in messages %}{{ message.content }}{%- endfor %}\n"


def template(text: str = TEMPLATE, used: bool = True) -> dict[str, Any]:
    return {
        "path": f"{STORE}-chat_template.jinja",
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
        "used": used,
        "model_template": {
            "path": "tokenizer_config.json",
            "sha256": "e" * 64,
            "matches": True,
        },
    }


# A report as o11n.vllm's vllm-report gives it for one server, pelle's qwen3-8b.
def server(**changes: Any) -> dict[str, Any]:
    extra = [
        "--enforce-eager",
        "--max-model-len=8192",
        "--max-num-seqs=1",
        "--tool-call-parser=hermes",
        "--enable-auto-tool-choice",
    ]
    vllm = f"{STORE}-python3.13-vllm-0.24.0"
    reported: dict[str, Any] = {
        "unit": "container@vllm-qwen3-8b.service",
        "service": "vllm.service",
        "package": {"name": "vllm", "version": "0.24.0", "path": vllm},
        "runtime": f"{STORE}-vllm-qwen3-8b-runtime",
        "argv": [f"{vllm}/bin/vllm", "serve", "Qwen/Qwen3-8B-AWQ", "--host=::"],
        "environment": {
            "CUDA_HOME": f"{STORE}-cudatoolkit",
            "CUDA_VISIBLE_DEVICES": "0",
            "HF_HOME": "/var/lib/huggingface",
            "HF_HUB_CACHE": "/srv/models/huggingface",
            "HF_HUB_OFFLINE": "1",
            "VLLM_USE_FLASHINFER_SAMPLER": "0",
        },
        "host": "::",
        "port": 12009,
        "model": {
            "id": "Qwen/Qwen3-8B-AWQ",
            "revision": COMMIT,
            "path": f"/srv/models/huggingface/models--Qwen--Qwen3-8B-AWQ/snapshots/{COMMIT}",
            "model_type": "qwen3",
            "files": [
                {"path": "config.json", "size": 1, "sha256": "c" * 64},
                {"path": "model.safetensors", "size": 1, "sha256": "a" * 64},
            ],
        },
        "revision": COMMIT,
        "chat_template": template(),
        "served_model_names": [],
        "extra_args": extra,
        "gpus": [
            {
                "index": 0,
                "uuid": "GPU-0",
                "name": "NVIDIA GeForce RTX 3070",
                "compute_capability": [8, 6],
                "memory_mib": 8192,
                "driver": "595.99.02",
            }
        ],
        "process": {"running": True, "pid": 7, "argv": [], "matches": True},
        "served": {
            "version": "0.24.0",
            "models": [{"id": "Qwen/Qwen3-8B-AWQ", "root": "Qwen/Qwen3-8B-AWQ"}],
        },
    }
    return reported | changes


def report(
    servers: dict[str, dict[str, Any]] | None = None, hostname: str = "pelle"
) -> dict[str, Any]:
    return {
        "report": REPORT,
        "hostname": hostname,
        "system": f"{STORE}-nixos-system-{hostname}",
        "servers": servers or {"qwen3-8b": server()},
    }


# Served under its digest, as it is once ops have put the digest in servedModelNames.
def serving_its_digest(changes: dict[str, Any] | None = None) -> Imported:
    first = imported(report({"qwen3-8b": server(**(changes or {}))}), "qwen3-8b")
    served = {"version": "0.24.0", "models": [{"id": first.engine.digest}]}
    return imported(
        report({"qwen3-8b": server(served=served, **(changes or {}))}), "qwen3-8b"
    )


def test_a_server_s_report_becomes_its_model_engine_endpoint_and_host() -> None:
    i = imported(report(), "qwen3-8b", "pelle.kompismoln.se")
    assert i.name == "qwen3-8b@pelle"
    assert (i.model.repo, i.model.revision) == ("Qwen/Qwen3-8B-AWQ", COMMIT)
    assert [f.path for f in i.model.files] == ["config.json", "model.safetensors"]
    e = i.engine
    assert e.model == i.model.digest
    assert (e.hardware.gpu, e.hardware.compute_capability, e.hardware.vram_mib) == (
        "NVIDIA GeForce RTX 3070",
        (8, 6),
        8192,
    )
    assert (e.hardware.driver, e.runtime.version) == ("595.99.02", "0.24.0")
    assert e.runtime.closure == f"{STORE}-vllm-qwen3-8b-runtime"
    assert e.chat_template == template()["sha256"]
    assert e.argv == tuple(server()["extra_args"])
    assert e.env == {"VLLM_USE_FLASHINFER_SAMPLER": "0"}
    assert str(i.endpoint.url) == "http://pelle.kompismoln.se:12009/v1/"
    assert (i.endpoint.engine, i.endpoint.host, i.endpoint.max_jobs) == (
        e.digest,
        "pelle",
        1,
    )
    assert (i.bind, i.location, i.template) == (
        "::",
        "Qwen/Qwen3-8B-AWQ",
        f"{STORE}-chat_template.jinja",
    )
    assert str(imported(report(), "qwen3-8b").endpoint.url) == "http://pelle:12009/v1/"


def test_its_capacity_is_the_server_s_max_num_seqs_however_it_is_spelled() -> None:
    for extra, jobs in [
        ([], 1),
        (["--max-num-seqs=4"], 4),
        (["--max_num_seqs", "3"], 3),
        (["--max-num-seqs=2", "--max-num-seqs", "6"], 6),
    ]:
        i = imported(report({"q": server(extra_args=extra)}), "q")
        assert i.endpoint.max_jobs == jobs


def test_the_tables_it_prints_seed_the_engine_it_imported_and_start_it_as_served(
    tmp_path: Path,
) -> None:
    gpt = server(
        model=server()["model"] | {"id": "openai/gpt-oss-20b", "model_type": "gpt_oss"},
        chat_template=template("null\n", used=False),
        extra_args=["--max-num-seqs", "4", "--reasoning-parser=openai_gptoss"],
        environment=server()["environment"] | {"VLLM_SYSTEM_START_DATE": "2026-01-01"},
    )
    imports = [
        imported(report(), "qwen3-8b", "pelle.kompismoln.se"),
        imported(report({"gpt-oss-20b": gpt}, "malborg"), "gpt-oss-20b"),
    ]
    factors = tmp_path / "factors.toml"
    _ = factors.write_text(factors_toml(imports))
    plan = plan_factors(factors, Facts(), rig())
    for i in imports:
        assert plan.named("model", i.name).digest == i.model.digest
        assert plan.named("local_engine", i.name).digest == i.engine.digest
        assert plan.named("local_engine", i.name).tags == (i.host,)
    world = tmp_path / "inventory.toml"
    _ = world.write_text(endpoints_toml(imports))
    inventory = Inventory.load(world)
    assert set(inventory.hosts) == {"pelle", "malborg"}
    startup = start_up(inventory, plan.registry.get, "qwen3-8b@pelle")
    assert startup.argv == (
        "Qwen/Qwen3-8B-AWQ",
        "--revision",
        COMMIT,
        "--served-model-name",
        imports[0].engine.digest,
        "--chat-template",
        f"{STORE}-chat_template.jinja",
        "--host",
        "::",
        "--port",
        "12009",
        *server()["extra_args"],
    )
    assert startup.env == {"VLLM_USE_FLASHINFER_SAMPLER": "0"}
    assert inventory.endpoint("gpt-oss-20b@malborg").max_jobs == 4
    sample = tmp_path / "sample.toml"
    _ = sample.write_text('[prompt.case]\nsegments = [{ slot = "vignette" }]\n')
    both = plan_factors(sample, Facts(), rig(), more=[factors])
    assert (
        both.named("local_engine", "qwen3-8b@pelle").digest == imports[0].engine.digest
    )
    with pytest.raises(
        ValueError,
        match=r"model \['gpt-oss-20b@malborg', 'qwen3-8b@pelle'\] are named before",
    ):
        _ = plan_factors(factors, Facts(), rig(), more=[factors])


def test_servers_on_one_host_share_its_table_unless_they_bind_apart() -> None:
    other = server(
        model=server()["model"] | {"id": "Qwen/Qwen3-0.6B"},
        chat_template=template("{{ messages }}"),
        port=12010,
    )
    both = report({"qwen3-8b": server(), "small": other})
    world = endpoints_toml([imported(both, "qwen3-8b"), imported(both, "small")])
    assert world.count("[host.pelle]") == 1
    assert '= "Qwen/Qwen3-0.6B"' in world and '= "Qwen/Qwen3-8B-AWQ"' in world
    apart = report({"qwen3-8b": server(), "small": other | {"host": "127.0.0.1"}})
    with pytest.raises(ValueError, match="small@pelle binds 127.0.0.1"):
        _ = endpoints_toml([imported(apart, "qwen3-8b"), imported(apart, "small")])
    same = report({"qwen3-8b": server(), "small": other | {"port": 12009}})
    with pytest.raises(ValueError, match="small@pelle and qwen3-8b@pelle are both at"):
        _ = endpoints_toml([imported(same, "qwen3-8b"), imported(same, "small")])


def test_it_refuses_what_an_engine_can_t_record() -> None:
    gpu = server()["gpus"][0]
    for changes, why in [
        ({"gpus": [gpu, gpu | {"index": 1}]}, "runs on 2 GPUs"),
        ({"gpus": {"error": "FileNotFoundError: nvidia-smi"}}, "no gpus: FileNotF"),
        ({"model": {"error": "FileNotFoundError: no model"}}, "no model: FileNotF"),
        (
            {"model": server()["model"] | {"id": f"{STORE}-qwen", "revision": None}},
            "no Hugging Face repo at a revision",
        ),
        ({"chat_template": {"error": "OSError: gone"}}, "no chat_template: OSError"),
        ({"extra_args": ["--port", "1"]}, "set --port"),
        ({"extra_args": ["--served-model-name", "x"]}, "may not set"),
        ({"extra_args": ["--config", "x.yaml"]}, "may not use"),
    ]:
        with pytest.raises(
            ValueError, match=f"(?s)qwen3-8b@pelle can't be imported.*{why}"
        ):
            _ = imported(report({"qwen3-8b": server(**changes)}), "qwen3-8b")
    with pytest.raises(LookupError, match="no server 'other', only"):
        _ = imported(report(), "other")


def test_it_reports_what_doesn_t_hold() -> None:
    def codes(changes: dict[str, Any]) -> list[str]:
        return [f.code for f in serving_its_digest(changes).findings]

    assert codes({}) == []
    assert codes({"runtime": "/opt/vllm"}) == ["engine.closure"]
    assert codes(
        {"model": server()["model"] | {"revision": "main"}, "revision": "main"}
    ) == ["model.revision"]
    assert codes({"chat_template": template("{{ strftime_now('%Y') }}")}) == [
        "engine.chat_template_date"
    ]
    unread = template()
    del unread["text"]
    assert codes({"chat_template": unread}) == ["engine.chat_template_unread"]
    own = template()
    own["model_template"] |= {"matches": False}
    assert codes({"chat_template": own}) == ["engine.chat_template_not_the_model_s"]
    harmony = template("null\n", used=False)
    assert codes({"chat_template": harmony}) == [
        "engine.chat_template_unused",
        "engine.harmony_date",
    ]
    dated = server()["environment"] | {"VLLM_SYSTEM_START_DATE": "2026-01-01"}
    assert codes({"chat_template": harmony, "environment": dated}) == [
        "engine.chat_template_unused"
    ]
    assert codes({"process": {"running": False}}) == ["endpoint.not_running"]
    assert codes({"process": {"error": "OSError: x"}}) == ["endpoint.process"]
    assert codes({"process": {"running": True, "matches": False}}) == ["endpoint.argv"]
    assert codes({"package": server()["package"] | {"version": "0.24.1"}}) == [
        "endpoint.version"
    ]
    served = imported(report(), "qwen3-8b").findings
    assert [(f.code, f.subject) for f in served] == [
        ("endpoint.served_name", imported(report(), "qwen3-8b").engine.digest)
    ]
    assert "answers as Qwen/Qwen3-8B-AWQ; put sha256:" in served[0].message
    down = server(served={"error": "URLError: refused"})
    assert [f.code for f in imported(report({"q": down}), "q").findings] == [
        "endpoint.unreachable"
    ]


@contextmanager
def serving(body: bytes) -> Generator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            _ = self.wfile.write(body)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{http.server_address[1]}/"
    finally:
        http.shutdown()
        http.server_close()


def test_it_reads_a_report_from_its_endpoint_or_a_file(tmp_path: Path) -> None:
    with serving(json.dumps(report()).encode()) as url:
        assert read(url) == (report(), "127.0.0.1")
    path = tmp_path / "report.json"
    _ = path.write_text(json.dumps(report()))
    assert read(str(path)) == (report(), None)
    _ = path.write_text(json.dumps({"servers": {}}))
    with pytest.raises(ValueError, match="is not an o11n.vllm.report/1 report"):
        _ = read(str(path))


def import_engine(tmp_path: Path, *argv: str) -> tuple[Path, Path]:
    factors, endpoints = tmp_path / "factors.toml", tmp_path / "endpoints.toml"
    main(
        [
            "import-engine",
            *argv,
            "--factors",
            str(factors),
            "--endpoints",
            str(endpoints),
        ]
    )
    return factors, endpoints


def test_the_command_writes_the_engines_and_their_endpoints_apart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "report.json"
    _ = path.write_text(json.dumps(report()))
    factors, endpoints = import_engine(
        tmp_path, str(path), "--endpoint-host", "pelle.km"
    )
    out, err = capsys.readouterr()
    digest = imported(report(), "qwen3-8b").engine.digest
    assert (
        out
        == f"[import] qwen3-8b@pelle: engine {digest} at http://pelle.km:12009/v1/\n"
    )
    assert err == (
        "[import warning] qwen3-8b@pelle: endpoint.served_name: the server answers as "
        + f"Qwen/Qwen3-8B-AWQ; put {digest} first in its servedModelNames\n"
    )
    assert factors.read_text().startswith(
        f"# Written by `chatddx import-engine {path}`.\n# The models and engines"
    )
    assert "[endpoint." not in factors.read_text()
    assert "[model." not in endpoints.read_text()
    world = tmp_path / "inventory.toml"
    _ = world.write_text('include = ["endpoints.toml"]\n')
    sample = tmp_path / "sample.toml"
    _ = sample.write_text('[prompt.case]\nsegments = [{ slot = "vignette" }]\n')
    plan = plan_factors(sample, Facts(), rig(), more=[factors])
    assert endpoints_of(Inventory.load(world), plan) == [
        "[world endpoint] qwen3-8b@pelle: http://pelle.km:12009/v1/ serves "
        + f"engine.local qwen3-8b@pelle {digest.removeprefix('sha256:')[:6]}"
    ]
    with pytest.raises(SystemExit, match="import-engine: no report has a server other"):
        _ = import_engine(tmp_path, str(path), "--server", "other")
    with pytest.raises(SystemExit):
        main(["import-engine", str(path)])


def test_the_command_imports_several_hosts_at_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gpt = server(model=server()["model"] | {"id": "openai/gpt-oss-20b"})
    paths = [str(tmp_path / "pelle.json"), str(tmp_path / "malborg.json")]
    _ = Path(paths[0]).write_text(json.dumps(report()))
    _ = Path(paths[1]).write_text(json.dumps(report({"gpt-oss-20b": gpt}, "malborg")))
    factors, endpoints = import_engine(tmp_path, *paths)
    assert [line.split(":")[0] for line in capsys.readouterr().out.splitlines()] == [
        "[import] qwen3-8b@pelle",
        "[import] gpt-oss-20b@malborg",
    ]
    assert set(Inventory.load(endpoints).hosts) == {"pelle", "malborg"}
    assert '[local_engine."gpt-oss-20b@malborg"]' in factors.read_text()
    _ = import_engine(tmp_path, *paths, "--server", "gpt-oss-20b")
    assert "qwen3-8b@pelle" not in factors.read_text()
    with pytest.raises(SystemExit, match="--endpoint-host takes one report"):
        _ = import_engine(tmp_path, *paths, "--endpoint-host", "pelle.km")
