import re
import unicodedata
from collections.abc import Callable, Sequence
from typing import Annotated, ClassVar, Literal, override

from pydantic import Field, field_validator

from .identity import Component, Digest, Frozen, Hmac, RefTo, Resolver


class SourceCase(Frozen):
    source: str
    id: str


class Appendix(Component):
    kind: Literal["appendix"] = "appendix"
    case_derived: ClassVar[bool] = True
    case: SourceCase
    vignette: Hmac
    text: str = Field(min_length=1)


AppendixRef = Annotated[Digest, RefTo("appendix")]


class CaseInput(Component):
    kind: Literal["case"] = "case"
    case: SourceCase
    vignette: Hmac
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


class CaseSet(Component):
    kind: Literal["case_set"] = "case_set"
    normalization: tuple[NormalizeOp, ...] = ()
    cases: tuple[CaseInputRef, ...] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def _unique(cls, cases: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(cases)) != len(cases):
            raise ValueError("duplicate case inputs")
        return cases


CaseSetRef = Annotated[Digest, RefTo("case_set")]
