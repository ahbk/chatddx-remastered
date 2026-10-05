import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Annotated, ClassVar, Protocol, Self, cast, override
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, Field, JsonValue

from chatddx.factors.base import (
    Code,
    Finding,
    Frozen,
    StructuralError,
    canonical_bytes,
    sha256_digest,
    sorted_keys,
)
from chatddx.factors.cases import CaseRef


def _utc(at: datetime) -> datetime:
    return at.astimezone(UTC)


# The offset is part of the canonical bytes, so without this one instant would seal
# differently depending on the writer's time zone.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_utc)]


def in_order(started: datetime, finished: datetime) -> None:
    if finished < started:
        raise ValueError("finished before it started")


class Record(Frozen):
    case_derived: ClassVar[bool] = True
    # Bump when an existing field's meaning or default changes; additive fields don't.
    schema_version: ClassVar[int] = 1

    def canonical_doc(self) -> dict[str, JsonValue]:
        doc = cast(
            dict[str, JsonValue],
            self.model_dump(mode="json", context={"canonical": True}),
        )
        return sorted_keys({**doc, "v": type(self).schema_version})

    @property
    def canonical(self) -> bytes:
        return canonical_bytes(self.canonical_doc())

    @classmethod
    def parse(cls, data: bytes | str) -> Self:
        loaded: object = json.loads(data)
        if not isinstance(loaded, dict):
            raise StructuralError(f"{cls.__name__} is not a JSON object")
        doc = cast(dict[str, object], loaded)
        v = doc.pop("v", None)
        if v != cls.schema_version:
            raise StructuralError(
                f"{cls.__name__} v{v} is not readable by v{cls.schema_version}"
            )
        return cls.model_validate(doc)


class ItemKey(Frozen):
    case: CaseRef
    replicate: int = Field(ge=0)

    @override
    def __hash__(self) -> int:
        return hash((self.case, self.replicate))


class Stage(Protocol):
    @property
    def stage(self) -> str: ...

    @property
    def at(self) -> datetime: ...


def check_log(
    stages: Sequence[Stage], order: tuple[str, ...], ids: Iterable[UUID]
) -> None:
    names = [s.stage for s in stages]
    if names != list(order[: len(names)]):
        raise StructuralError(f"stages {names} do not follow {order}")
    for later in stages[1:]:
        in_order(stages[0].at, later.at)
    if len(set(ids)) > 1:
        raise StructuralError("rows belong to different logs")


# Canonical rows keep old seals valid when a defaulted field is added, and sorting
# makes the seal independent of the order storage returns rows in.
def seal_rows(started: Record, **rows: Iterable[Record]) -> str:
    doc: dict[str, JsonValue] = {
        name: sorted((r.canonical_doc() for r in group), key=canonical_bytes)
        for name, group in rows.items()
    }
    doc["started"] = started.canonical_doc()
    return sha256_digest(canonical_bytes(sorted_keys(doc)))


class Finished(Protocol):
    @property
    def seal(self) -> str: ...


class Log(Protocol):
    @property
    def finished(self) -> Finished | None: ...

    def seal(self) -> str: ...


def check_seal(log: Log, subject: str) -> list[Finding]:
    finished = log.finished
    if finished is not None and finished.seal != log.seal():
        return [
            Finding(
                code="ledger.seal",
                message="rows changed after the log was sealed",
                subject=subject,
            )
        ]
    return []


def ran_pinned(pinned: Code, ran: Code) -> bool:
    return (ran.distribution, ran.version) == (
        pinned.distribution,
        pinned.version,
    ) and (pinned.revision is None or ran.revision == pinned.revision)


def code_name(code: Code) -> str:
    revision = "" if code.revision is None else f" {code.revision}"
    return f"{code.distribution} {code.version}{revision}"
