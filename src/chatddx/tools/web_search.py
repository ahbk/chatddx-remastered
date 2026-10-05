import re
import urllib.parse
import urllib.request
from html import unescape

SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "Mozilla/5.0 (compatible; chatddx-web-search/1.0)"

_RESULT = re.compile(
    r'class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    + r".*?"
    + r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")


def web_search(query: str, max_results: int = 5) -> str:
    request = urllib.request.Request(
        f"{SEARCH_URL}?{urllib.parse.urlencode({'q': query})}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=10.0) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        page = response.read().decode(charset, errors="replace")
    return results(page, query, max_results)


def results(page: str, query: str, max_results: int = 5) -> str:
    found: list[str] = []
    for match in _RESULT.finditer(page):
        title = _text(match.group("title"))
        url = _target(match.group("href"))
        snippet = _text(match.group("snippet"))
        found.append(f"{len(found) + 1}. {title}\n   {url}\n   {snippet}")
        if len(found) >= max_results:
            break
    if not found:
        return f"No results found for '{query}'."
    return "\n".join(found)


def _text(fragment: str) -> str:
    return unescape(_TAG.sub("", fragment)).strip()


# DuckDuckGo links a result through its own redirect, with the target in `uddg`.
def _target(href: str) -> str:
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urllib.parse.urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href
