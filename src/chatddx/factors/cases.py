import re
import unicodedata
from collections.abc import Callable, Sequence
from typing import Annotated, Literal, override

from pydantic import Field

from .base import (
    Component,
    Digest,
    Finding,
    Fingerprint,
    Frozen,
    RefTo,
    Resolver,
    StructuralError,
    resolve,
)
from .request import AppendixLayout, SlotName


class Vignette(Frozen):
    source: str
    id: str
    fingerprint: Fingerprint


class Appendix(Component):
    kind: Literal["appendix"] = "appendix"
    vignette: Vignette
    text: str = Field(min_length=1)


AppendixRef = Annotated[Digest, RefTo("appendix")]


class Case(Component):
    kind: Literal["case"] = "case"
    vignette: Vignette
    appendices: tuple[AppendixRef, ...] = ()

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        problems: list[str] = []
        for ref in self.appendices:
            a = get(ref)
            assert isinstance(a, Appendix)
            if a.vignette != self.vignette:
                problems.append(f"appendix {ref} is bound to another vignette")
        return problems


CaseRef = Annotated[Digest, RefTo("case")]

# Each op name pins one behavior; a changed behavior gets a new name.
CleanupOp = Literal[
    "newlines.lf@1", "unicode.nfc@1", "strip@1", "blank_lines.collapse@1"
]

_OPS: dict[str, Callable[[str], str]] = {
    "newlines.lf@1": lambda s: s.replace("\r\n", "\n").replace("\r", "\n"),
    "unicode.nfc@1": lambda s: unicodedata.normalize("NFC", s),
    "strip@1": str.strip,
    "blank_lines.collapse@1": lambda s: re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", s),
}


def clean(text: str, ops: Sequence[CleanupOp]) -> str:
    for op in ops:
        text = _OPS[op](text)
    return text


class Prepared(Frozen):
    fills: dict[SlotName, str]
    fingerprint: Fingerprint
    findings: tuple[Finding, ...] = ()


def prepare_case(
    case: Case,
    raw: bytes,
    get: Resolver,
    layout: AppendixLayout,
    cleanup: Sequence[CleanupOp] = (),
    key: tuple[str, bytes] | None = None,
) -> Prepared:
    expected = case.vignette.fingerprint
    given = None if key is None else key[0]
    if expected.key_id != given:
        raise StructuralError(
            f"the case's vignette is fingerprinted with key {expected.key_id!r}, "
            + f"not {given!r}"
        )
    observed = Fingerprint.of(raw, key)
    findings: tuple[Finding, ...] = ()
    if observed != expected:
        findings = (
            Finding(
                code="case.drift",
                message="the vignette at the source differs from the case's",
                subject=case.digest,
            ),
        )
    try:
        # UTF-8 is the contract with sources; a byte-order mark belongs to the encoding.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        vignette = f"{case.vignette.source}/{case.vignette.id}"
        raise StructuralError(
            f"vignette {vignette} isn't UTF-8: {e.reason} at byte {e.start}"
        ) from None
    appendices = [resolve(get, a, Appendix).text for a in case.appendices]
    return Prepared(
        fills={
            "vignette": clean(text, cleanup),
            "appendices": layout.join(appendices),
        },
        fingerprint=observed,
        findings=findings,
    )
