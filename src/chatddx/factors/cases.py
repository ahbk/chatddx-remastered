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


class SourceCase(Frozen):
    source: str
    id: str


class Appendix(Component):
    kind: Literal["appendix"] = "appendix"
    case: SourceCase
    vignette: Fingerprint
    text: str = Field(min_length=1)


AppendixRef = Annotated[Digest, RefTo("appendix")]


class CaseInput(Component):
    kind: Literal["case"] = "case"
    case: SourceCase
    vignette: Fingerprint
    appendices: tuple[AppendixRef, ...] = ()

    @override
    def cross_check(self, get: Resolver) -> list[str]:
        problems: list[str] = []
        for ref in self.appendices:
            a = get(ref)
            assert isinstance(a, Appendix)
            if (a.case, a.vignette) != (self.case, self.vignette):
                problems.append(f"appendix {ref} is bound to another case or vignette")
        return problems


CaseInputRef = Annotated[Digest, RefTo("case")]

# Each op name pins one behavior; a changed behavior gets a new name.
NormalizeOp = Literal[
    "newlines.lf@1", "unicode.nfc@1", "strip@1", "blank_lines.collapse@1"
]

_OPS: dict[str, Callable[[str], str]] = {
    "newlines.lf@1": lambda s: s.replace("\r\n", "\n").replace("\r", "\n"),
    "unicode.nfc@1": lambda s: unicodedata.normalize("NFC", s),
    "strip@1": str.strip,
    "blank_lines.collapse@1": lambda s: re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", s),
}


def normalize(text: str, ops: Sequence[NormalizeOp]) -> str:
    for op in ops:
        text = _OPS[op](text)
    return text


class Prepared(Frozen):
    fills: dict[SlotName, str]
    vignette: Fingerprint
    findings: tuple[Finding, ...] = ()


def prepare_case(
    case: CaseInput,
    raw: bytes,
    get: Resolver,
    layout: AppendixLayout,
    normalization: Sequence[NormalizeOp] = (),
    key: tuple[str, bytes] | None = None,
) -> Prepared:
    given = None if key is None else key[0]
    if case.vignette.key_id != given:
        raise StructuralError(
            f"the case's vignette is fingerprinted with key {case.vignette.key_id!r}, "
            + f"not {given!r}"
        )
    observed = Fingerprint.of(raw, key)
    findings: tuple[Finding, ...] = ()
    if observed != case.vignette:
        findings = (
            Finding(
                code="case.drift",
                message="the vignette at the source differs from the case's",
                subject=case.digest,
            ),
        )
    appendices = [resolve(get, a, Appendix).text for a in case.appendices]
    return Prepared(
        fills={
            "case": normalize(raw.decode(), normalization),
            "appendices": layout.join(appendices),
        },
        vignette=observed,
        findings=findings,
    )
