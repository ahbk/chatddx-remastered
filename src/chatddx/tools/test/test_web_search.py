import urllib.request
from email.message import Message
from types import TracebackType
from typing import Self

import pytest

from chatddx.tools import web_search as module
from chatddx.tools.web_search import results, web_search

PAGE = """
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpe&amp;rut=x">
    Pulmonary <b>embolism</b> &amp; you</a>
  <a class="result__snippet" href="#">Signs of <b>PE</b> include dyspnoea.</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.org/pneumonia">Pneumonia</a>
  <a class="result__snippet" href="#">An infection of the lungs.</a>
</div>
"""


def test_it_lists_the_results_with_their_targets_and_snippets() -> None:
    assert results(PAGE, "pe") == (
        "1. Pulmonary embolism & you\n   https://example.org/pe\n"
        + "   Signs of PE include dyspnoea.\n"
        + "2. Pneumonia\n   https://example.org/pneumonia\n"
        + "   An infection of the lungs."
    )
    assert results(PAGE, "pe", max_results=1).count("\n") == 2
    assert results("<html></html>", "pe") == "No results found for 'pe'."


class _Response:
    def __init__(self, body: bytes) -> None:
        self.headers: Message = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"
        self._body: bytes = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        pass


def test_it_asks_duckduckgo_for_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[urllib.request.Request, float]] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        asked.append((request, timeout))
        return _Response(PAGE.encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert web_search("lung embolism", max_results=1).startswith("1. Pulmonary")
    [(request, timeout)] = asked
    assert request.full_url == "https://html.duckduckgo.com/html/?q=lung+embolism"
    assert request.get_header("User-agent") == module.USER_AGENT
    assert timeout == 10.0
