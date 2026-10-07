import hashlib
import socket
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

from chatddx.core.rig import rig
from chatddx.factors.bundle import Registry
from chatddx.factors.engine import (
    FileDigest,
    Hardware,
    LocalEngine,
    ModelArtifact,
    RemoteEngine,
    Runtime,
)
from chatddx.facts.facts import Facts
from chatddx.fake_vllm.served import Served
from chatddx.fake_vllm.server import server
from chatddx.inventory.inventory import EndpointSpec, HostSpec, Inventory
from chatddx.inventory.serving import confirm, start_up, url_of
from chatddx.seed import Plan, plan_factors
from chatddx.seed.plan import SAMPLE
from chatddx.seed.world import endpoints

WORLD = Path(__file__).parents[4] / "sample-world" / "inventory.toml"
TEMPLATE = hashlib.sha256(b"{{ messages }}").hexdigest()


def sample_plan() -> Plan:
    return plan_factors(
        SAMPLE / "factors.toml", Facts.load(SAMPLE / "facts.toml"), rig()
    )


def local(reg: Registry, *argv: str) -> tuple[str, str]:
    model = reg.add(
        ModelArtifact(
            repo="Qwen/Qwen3-8B-AWQ",
            revision="d" * 40,
            files=(FileDigest(path="model.safetensors", sha256="a" * 64),),
        )
    )
    engine = reg.add(
        LocalEngine(
            hardware=Hardware(
                gpu="NVIDIA GeForce RTX 3070",
                compute_capability=(8, 6),
                vram_mib=8192,
                driver="580.95.05",
            ),
            runtime=Runtime(
                version="0.24.0", closure="/nix/store/" + "v" * 32 + "-vllm"
            ),
            model=model,
            chat_template=TEMPLATE,
            argv=argv,
        )
    )
    return model, engine


def world(
    engine: str,
    models: dict[str, str] | None = None,
    templates: dict[str, str] | None = None,
) -> Inventory:
    return Inventory(
        root=Path("."),
        endpoints={
            "qwen@pelle": EndpointSpec.model_validate(
                {"engine": engine, "url": "http://pelle.km:12009/v1/", "host": "pelle"}
            )
        },
        hosts={
            "pelle": HostSpec(bind="::", models=models or {}, templates=templates or {})
        },
    )


def test_endpoints_and_hosts_are_declared_in_the_inventory(tmp_path: Path) -> None:
    path = tmp_path / "inventory.toml"
    _ = path.write_text(
        '[endpoint.qwen]\nengine = "sha256:'
        + "c" * 64
        + '"\n'
        + 'url = "http://pelle.km:12009/v1/"\nhost = "pelle"\n\n[host.pelle]\n'
    )
    inventory = Inventory.load(path)
    endpoint = inventory.endpoint("qwen")
    assert (endpoint.max_jobs, endpoint.credential, str(endpoint.url)) == (
        1,
        None,
        "http://pelle.km:12009/v1/",
    )
    assert inventory.hosts["pelle"] == HostSpec(bind="0.0.0.0")
    with pytest.raises(LookupError, match="no endpoint 'other'"):
        _ = inventory.endpoint("other")
    _ = path.write_text(
        '[endpoint.qwen]\nengine = "sha256:' + "c" * 64 + '"\nhost = "x"\n'
    )
    with pytest.raises(ValidationError, match="names no host 'x'"):
        _ = Inventory.load(path)


def test_a_start_up_joins_the_engine_with_where_its_host_keeps_things() -> None:
    reg = Registry()
    model, engine = local(reg, "--max-model-len", "8192")
    inventory = world(engine, {model: "/models/qwen3"}, {TEMPLATE: "/etc/qwen3.jinja"})
    startup = start_up(inventory, reg.get, "qwen@pelle")
    assert startup.argv == (
        "/models/qwen3",
        "--revision",
        "d" * 40,
        "--served-model-name",
        engine,
        "--chat-template",
        "/etc/qwen3.jinja",
        "--host",
        "::",
        "--port",
        "12009",
        "--max-model-len",
        "8192",
    )
    assert startup.closure == "/nix/store/" + "v" * 32 + "-vllm"
    assert url_of(inventory, reg.get, "qwen@pelle") == "http://pelle.km:12009/v1/"


def test_a_start_up_is_refused_where_the_inventory_or_the_engine_falls_short() -> None:
    reg = Registry()
    model, engine = local(reg)
    with pytest.raises(LookupError, match="where it keeps model"):
        _ = start_up(world(engine, templates={TEMPLATE: "/t"}), reg.get, "qwen@pelle")
    with pytest.raises(LookupError, match="where it keeps chat template"):
        _ = start_up(world(engine, models={model: "/m"}), reg.get, "qwen@pelle")
    _, listening = local(reg, "--port=9000")
    complete = world(listening, {model: "/m"}, {TEMPLATE: "/t"})
    with pytest.raises(LookupError, match="sets --port in its argv"):
        _ = start_up(complete, reg.get, "qwen@pelle")
    remote = reg.add(
        RemoteEngine.model_validate(
            {"base_url": "https://api.example.org/v1/", "model": "gemma"}
        )
    )
    with pytest.raises(LookupError, match="engine.remote, not ours to start"):
        _ = start_up(world(remote), reg.get, "qwen@pelle")


def test_a_remote_engine_is_reached_at_its_base_url() -> None:
    reg = Registry()
    remote = reg.add(
        RemoteEngine.model_validate(
            {"base_url": "https://api.example.org/v1/", "model": "gemma"}
        )
    )
    alone = Inventory(
        root=Path("."),
        endpoints={"gemma": EndpointSpec(engine=remote, credential="GEMMA_KEY")},
    )
    assert url_of(alone, reg.get, "gemma") == "https://api.example.org/v1/"
    with pytest.raises(LookupError, match="but its remote engine's base_url is"):
        _ = url_of(world(remote), reg.get, "qwen@pelle")


def test_the_sample_world_binds_each_fake_engine_the_sample_seeds() -> None:
    inventory = Inventory.load(WORLD)
    plan = sample_plan()
    assert sorted(inventory.endpoints) == ["gpt-oss-20b@fake", "qwen3-8b-awq@fake"]
    for name, endpoint in inventory.endpoints.items():
        engine = plan.registry.get(plan.named("local_engine", name).digest)
        assert endpoint.engine == engine.digest
        startup = start_up(inventory, plan.registry.get, name)
        assert startup.argv[1:5] == (
            "--revision",
            "fake",
            "--served-model-name",
            engine.digest,
        )
        assert startup.closure == "chatddx fake-vllm"
    assert [
        line.split(": ")[1].split(" serves ")[1] for line in endpoints(inventory, plan)
    ] == [
        f"engine.local {name} {inventory.endpoint(name).engine.removeprefix('sha256:')[:6]}"
        for name in inventory.endpoints
    ]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def serving(served: Served) -> Generator[None]:
    fake = server([served])
    thread = threading.Thread(
        target=fake.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield
    finally:
        fake.shutdown()
        fake.server_close()


def test_a_fake_started_from_an_endpoint_serves_its_engine(tmp_path: Path) -> None:
    port = str(free_port())
    moved = tmp_path / "inventory.toml"
    _ = moved.write_text(
        WORLD.read_text().replace("12099", port).replace("12100", port)
    )
    inventory = Inventory.load(moved)
    plan = sample_plan()
    startup = start_up(inventory, plan.registry.get, "qwen3-8b-awq@fake")
    with serving(Served.of(startup.argv[0], startup.argv[1:])):
        confirm(inventory, plan.registry.get, "qwen3-8b-awq@fake")
        qwen = inventory.endpoint("qwen3-8b-awq@fake").engine
        gpt_oss = inventory.endpoint("gpt-oss-20b@fake").engine
        with pytest.raises(LookupError, match=f"serves {qwen}, not {gpt_oss}"):
            confirm(inventory, plan.registry.get, "gpt-oss-20b@fake")
    with pytest.raises(OSError):
        confirm(inventory, plan.registry.get, "qwen3-8b-awq@fake", timeout=1.0)
