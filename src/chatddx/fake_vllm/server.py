import json
import select
import socket
import time
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

from .chat import Failure, accept, completion, respond, stream
from .served import VERSION, Refused, Served


def models(served: Sequence[Served]) -> dict[str, Any]:
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": name,
                "object": "model",
                "created": created,
                "owned_by": "vllm",
                "root": s.model,
                "parent": None,
                "max_model_len": s.max_model_len,
                "permission": [],
            }
            for s in served
            for name in s.names
        ],
    }


def got(served: Sequence[Served], path: str) -> tuple[int, Any]:
    match path.partition("?")[0].rstrip("/"):
        case "/v1/models":
            return 200, models(served)
        case "/version":
            return 200, {"version": VERSION}
        case "/health":
            return 200, None
        case _:
            return 404, {"detail": "Not Found"}


def _request(served: Sequence[Served], raw: bytes) -> tuple[Served, dict[str, Any]]:
    try:
        body: Any = json.loads(raw)
    except ValueError as e:
        raise Failure(f"the request body isn't JSON: {e}") from None
    if not isinstance(body, dict):
        raise Failure("the request body isn't a JSON object")
    request = cast(dict[str, Any], body)
    return accept(served, request), request


class _Handler(BaseHTTPRequestHandler):
    served: tuple[Served, ...] = ()
    delay: float = 0.0
    runaway: bool = False

    def do_GET(self) -> None:
        self._json(*got(self.served, self.path))

    def do_POST(self) -> None:
        if self.path.partition("?")[0].rstrip("/") != "/v1/chat/completions":
            self._json(404, {"detail": "Not Found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            served, body = _request(self.served, self.rfile.read(length))
        except Failure as f:
            self._json(f.status, f.body())
            return
        reply = respond(served, body, self.runaway)
        if not body.get("stream"):
            self._json(200, completion(served, body, reply))
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        sent = 0
        for i, (event, generated) in enumerate(stream(served, body, reply)):
            if (i and not self._next_token()) or not self._sent(event.encode()):
                self.log_message("aborted: the client hung up after %d tokens", sent)
                return
            sent = generated

    # Waits for the next token, and hears a hang-up from the socket meanwhile, as vLLM
    # does, rather than at the next write.
    def _next_token(self) -> bool:
        ready, _, _ = select.select([self.connection], [], [], self.delay)
        if not ready:
            return True
        try:
            return self.connection.recv(1, socket.MSG_PEEK) != b""
        except OSError:
            return False

    def _sent(self, data: bytes) -> bool:
        try:
            _ = self.wfile.write(data)
            self.wfile.flush()
        except OSError:
            return False
        return True

    def _json(self, status: int, body: Any) -> None:
        content = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        _ = self.wfile.write(content)


def server(
    served: Sequence[Served],
    host: str | None = None,
    port: int | None = None,
    delay: float = 0.0,
    runaway: bool = False,
) -> ThreadingHTTPServer:
    if not served:
        raise ValueError("the fake vLLM needs a model to serve")
    handler = type(
        "Handler",
        (_Handler,),
        {"served": tuple(served), "delay": delay, "runaway": runaway},
    )
    address = (
        served[0].host if host is None else host,
        served[0].port if port is None else port,
    )
    return ThreadingHTTPServer(address, handler)


def run(model: str, argv: Sequence[str], delay: float, runaway: bool) -> None:
    try:
        served = Served.of(model, argv)
    except Refused as e:
        raise SystemExit(f"the fake vLLM won't start: {e}") from None
    fake = server([served], delay=delay, runaway=runaway)
    host, port = fake.server_address[:2]
    names = ", ".join(served.names)
    print(
        f"the fake vLLM {VERSION}, serving {model} as {names} at http://{host!s}:{port}/v1"
    )
    try:
        fake.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        fake.server_close()
