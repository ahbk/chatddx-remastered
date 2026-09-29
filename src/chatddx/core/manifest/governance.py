from collections.abc import Iterable
from urllib.parse import urlsplit

from pydantic import HttpUrl

from .identity import Frozen


class ClearanceError(PermissionError):
    pass


class Clearance(Frozen):
    origin: HttpUrl
    case_derived: bool


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname or "", parts.port


def require_clearance(url: str, clearances: Iterable[Clearance]) -> None:
    target = _origin(url)
    if not any(c.case_derived and _origin(str(c.origin)) == target for c in clearances):
        raise ClearanceError(f"{url} is not cleared for case-derived content")
