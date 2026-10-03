from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

# src/chatddx/store/migrations/0011-t2-catalog-checks.sql repeats these kinds and fields.
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
    }
)


class EntryField(StrEnum):
    NAME = "name"
    DESCRIPTION = "description"
    TAG = "tag"
    OWNER = "owner"
    COLLABORATOR = "collaborator"
    DELETED = "deleted"


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
    by: int
    at: datetime


class Behind(_Frozen):
    path: str
    digest: str
    head: Edit


class Subject(_Frozen):
    thread: int | None = None
    run: UUID | None = None
    score: UUID | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if [self.thread, self.run, self.score].count(None) != 2:
            raise ValueError("a subject is exactly one of a thread, a run or a score")
        return self


class Entry(_Frozen):
    field: EntryField
    value: str | None = None
    person: int | None = None
    present: bool = True

    @model_validator(mode="after")
    def _shape(self) -> Self:
        text = self.field in (EntryField.NAME, EntryField.DESCRIPTION, EntryField.TAG)
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
        return self


class About(_Frozen):
    name: str | None = None
    description: str | None = None
    tags: frozenset[str] = frozenset()
    owner: int | None = None
    collaborators: frozenset[int] = frozenset()
    deleted: bool = False

    @classmethod
    def of(cls, entries: Iterable[Entry]) -> Self:
        name = description = None
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
        return cls(
            name=name,
            description=description,
            tags=frozenset(tags),
            owner=owner,
            collaborators=frozenset(collaborators),
            deleted=deleted,
        )


def _toggle[T](members: set[T], member: T, present: bool) -> None:
    if present:
        members.add(member)
    else:
        members.discard(member)
