import json
import urllib.request
from typing import Any, cast

from chatddx.factors.base import Digest, Frozen, Resolver, resolve
from chatddx.factors.engine import LocalEngine, ModelArtifact, RemoteEngine, flag_names

from .inventory import Inventory

# Where the start-up script decides, rather than the engine's argv.
LISTEN_FLAGS = frozenset({"--host", "--port"})


# What the start-up script runs for one endpoint: vllm serve's arguments, the model's
# location first and then its revision, in the engine's runtime with its env.
class StartUp(Frozen):
    endpoint: str
    engine: Digest
    closure: str
    argv: tuple[str, ...]
    env: dict[str, str]


def start_up(inventory: Inventory, get: Resolver, name: str) -> StartUp:
    endpoint = inventory.endpoint(name)
    match get(endpoint.engine):
        case LocalEngine() as engine:
            pass
        case other:
            raise LookupError(
                f"endpoint {name!r} serves a {other.kind_name}, not ours to start"
            )
    if endpoint.url is None or endpoint.host is None:
        raise LookupError(f"endpoint {name!r} needs a url and a host to start")
    host = inventory.hosts[endpoint.host]
    model = host.models.get(engine.model)
    if model is None:
        raise LookupError(
            f"host {endpoint.host!r} doesn't say where it keeps model {engine.model}"
        )
    template = host.templates.get(engine.chat_template)
    if template is None:
        raise LookupError(
            f"host {endpoint.host!r} doesn't say where it keeps chat template "
            + engine.chat_template
        )
    if taken := sorted(flag_names(engine.argv) & LISTEN_FLAGS):
        raise LookupError(
            f"engine {engine.digest} sets {', '.join(taken)} in its argv, but endpoint "
            + f"{name!r} decides where it listens"
        )
    return StartUp(
        endpoint=name,
        engine=engine.digest,
        closure=engine.runtime.closure,
        argv=(
            model,
            "--revision",
            resolve(get, engine.model, ModelArtifact).revision,
            "--served-model-name",
            engine.served_model_name,
            "--chat-template",
            template,
            "--host",
            host.bind,
            "--port",
            str(endpoint.url.port),
            *engine.argv,
        ),
        env=engine.env,
    )


def url_of(inventory: Inventory, get: Resolver, name: str) -> str:
    endpoint = inventory.endpoint(name)
    match get(endpoint.engine):
        case RemoteEngine(base_url=base_url):
            if endpoint.url is not None and endpoint.url != base_url:
                raise LookupError(
                    f"endpoint {name!r} has url {endpoint.url}, but its remote engine's "
                    + f"base_url is {base_url}"
                )
            return str(base_url)
        case LocalEngine():
            if endpoint.url is None:
                raise LookupError(
                    f"endpoint {name!r} serves a local engine but has no url"
                )
            return str(endpoint.url)
        case other:
            raise LookupError(
                f"endpoint {name!r} names a {other.kind_name}, not an engine"
            )


def served(url: str, timeout: float = 10.0) -> list[str]:
    request = urllib.request.Request(url.rstrip("/") + "/models")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        listed: Any = json.loads(response.read())
    models = cast(list[dict[str, Any]], listed.get("data") or [])
    return [str(m.get("id")) for m in models]


# Before case-derived content goes out: the endpoint must list the name its engine is
# served under, the engine's digest for a local engine, its model for a remote one.
def confirm(
    inventory: Inventory, get: Resolver, name: str, timeout: float = 10.0
) -> None:
    engine = get(inventory.endpoint(name).engine)
    match engine:
        case LocalEngine():
            expected = engine.served_model_name
        case RemoteEngine(model=model):
            expected = model
        case _:
            raise LookupError(
                f"endpoint {name!r} names a {engine.kind_name}, not an engine"
            )
    url = url_of(inventory, get, name)
    listed = served(url, timeout)
    if expected not in listed:
        raise LookupError(
            f"endpoint {name!r} at {url} serves {', '.join(listed) or 'nothing'}, "
            + f"not {expected}"
        )
