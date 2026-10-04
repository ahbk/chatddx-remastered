import re
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from chatddx.factors.base import Fingerprint
from chatddx.factors.cases import SourceCase

# src/chatddx/store/migrations/0014-t2-catalog-kinds.sql repeats these kinds.
THREAD_KINDS = frozenset(
    {
        "chunk.instructions",
        "chunk.few_shot",
        "chunk.prompt",
        "chunk.output",
        "chunk.sampling",
        "chunk.reasoning",
        "chunk.passthrough",
        "skeleton",
        "trial",
        "judge",
        "scorer",
        "scoring",
        "appendix",
        "expectation",
        "model",
        "engine.local",
        "engine.remote",
        "expectation_schema",
        "canary_set",
    }
)


# src/chatddx/store/migrations/0017-t2-catalog-language.sql repeats these fields and the pattern.
LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{1,8})*$")


class EntryField(StrEnum):
    NAME = "name"
    DESCRIPTION = "description"
    TAG = "tag"
    OWNER = "owner"
    COLLABORATOR = "collaborator"
    DELETED = "deleted"
    LANGUAGE = "language"


Part = Literal["view", "resource"]


class _Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Thread(_Frozen):
    id: int
    kind: str
    forked_from: int | None
    by: int
    at: datetime


class Edit(_Frozen):
    id: int
    thread: int
    digest: str
    compilation: str | None
    based_on: int | None
    by: int
    at: datetime


class Variation(_Frozen):
    base: Edit
    head: Edit
    varies: dict[str, tuple[JsonValue, JsonValue]]

    @property
    def moved(self) -> bool:
        return self.head.id != self.base.id


class Binding(_Frozen):
    id: int
    family: int
    case: SourceCase
    vignette: Fingerprint
    by: int
    at: datetime


class Behind(_Frozen):
    path: str
    digest: str
    head: Edit | None = None
    binding: Binding | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.head is None) == (self.binding is None):
            raise ValueError("behind either a newer head or a newer binding")
        return self


class Survey(_Frozen):
    unchanged: tuple[int, ...] = ()
    changed: tuple[tuple[int, str, Fingerprint], ...] = ()
    renamed: tuple[tuple[int, str, str], ...] = ()
    new: tuple[str, ...] = ()
    gone: tuple[int, ...] = ()


class Repair(_Frozen):
    binding: Binding
    cases: dict[str, str]
    appendices: dict[str, str]
    edits: tuple[Edit, ...]


class Subject(_Frozen):
    thread: int | None = None
    family: int | None = None
    run: UUID | None = None
    score: UUID | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if [self.thread, self.family, self.run, self.score].count(None) != 3:
            raise ValueError(
                "a subject is exactly one of a thread, a family, a run or a score"
            )
        return self


class Entry(_Frozen):
    field: EntryField
    value: str | None = None
    person: int | None = None
    present: bool = True

    @model_validator(mode="after")
    def _shape(self) -> Self:
        text = self.field in (
            EntryField.NAME,
            EntryField.DESCRIPTION,
            EntryField.TAG,
            EntryField.LANGUAGE,
        )
        person = self.field in (EntryField.OWNER, EntryField.COLLABORATOR)
        removable = self.field in (
            EntryField.TAG,
            EntryField.COLLABORATOR,
            EntryField.DELETED,
        )
        if (self.value is not None) != text or (self.person is not None) != person:
            raise ValueError(f"wrong value or person for {self.field}")
        if not (self.present or removable):
            raise ValueError(f"{self.field} can't be removed, only replaced")
        if self.field in (EntryField.NAME, EntryField.TAG) and not self.value:
            raise ValueError(f"{self.field} can't be empty")
        if self.field == EntryField.LANGUAGE and not LANGUAGE.match(self.value or ""):
            raise ValueError(
                f"{self.value!r} is not a language tag such as 'sv' or 'pt-BR'"
            )
        return self


class About(_Frozen):
    name: str | None = None
    description: str | None = None
    tags: frozenset[str] = frozenset()
    owner: int | None = None
    collaborators: frozenset[int] = frozenset()
    deleted: bool = False
    language: str | None = None

    @classmethod
    def of(cls, entries: Iterable[Entry]) -> Self:
        name = description = language = None
        owner = None
        tags: set[str] = set()
        collaborators: set[int] = set()
        deleted = False
        for e in entries:
            match e.field:
                case EntryField.NAME:
                    name = e.value
                case EntryField.DESCRIPTION:
                    description = e.value
                case EntryField.TAG:
                    assert e.value is not None
                    _toggle(tags, e.value, e.present)
                case EntryField.OWNER:
                    owner = e.person
                case EntryField.COLLABORATOR:
                    assert e.person is not None
                    _toggle(collaborators, e.person, e.present)
                case EntryField.DELETED:
                    deleted = e.present
                case EntryField.LANGUAGE:
                    language = e.value
        return cls(
            name=name,
            description=description,
            tags=frozenset(tags),
            owner=owner,
            collaborators=frozenset(collaborators),
            deleted=deleted,
            language=language,
        )


def _toggle[T](members: set[T], member: T, present: bool) -> None:
    if present:
        members.add(member)
    else:
        members.discard(member)
