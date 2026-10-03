from typing import Any, LiteralString

from chatddx.core.catalog import (
    THREAD_KINDS,
    About,
    Behind,
    Edit,
    Entry,
    EntryField,
    Part,
    Subject,
    Thread,
)
from chatddx.factors.base import iter_refs, resolve
from chatddx.factors.scoring import Scorer
from chatddx.ledger.ledger import Compilation

from .store import Connection, Store

_EDIT = "e.id, e.thread, e.digest, e.compilation, e.by, e.at"

_HEAD: LiteralString = """
    CROSS JOIN LATERAL (
        SELECT * FROM catalog.edit h WHERE h.thread = t.id ORDER BY h.id DESC LIMIT 1
    ) e
"""

_DELETED: LiteralString = """
    coalesce((
        SELECT d.present FROM catalog.entry d
        WHERE d.thread = t.id AND d.field = 'deleted'
        ORDER BY d.id DESC LIMIT 1
    ), false)
"""


def _edit(row: tuple[Any, ...]) -> Edit:
    id, thread, digest, compilation, by, at = row
    return Edit(
        id=id, thread=thread, digest=digest, compilation=compilation, by=by, at=at
    )


class Catalog:
    def __init__(self, conn: Connection) -> None:
        self._conn: Connection = conn

    def create(
        self,
        digest: str,
        by: int,
        *,
        compilation: str | None = None,
        forked_from: int | None = None,
    ) -> Edit:
        with self._conn.transaction():
            row = self._conn.execute(
                "SELECT kind FROM factor.component WHERE digest = %s", (digest,)
            ).fetchone()
            if row is None:
                raise LookupError(f"{digest} is not in the store")
            kind = str(row[0])
            if kind not in THREAD_KINDS:
                raise ValueError(f"{kind} components have no threads")
            row = self._conn.execute(
                """
                INSERT INTO catalog.thread (kind, forked_from, by)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (kind, forked_from, by),
            ).fetchone()
            assert row is not None
            return self.edit(row[0], digest, by, compilation=compilation)

    def edit(
        self, thread: int, digest: str, by: int, *, compilation: str | None = None
    ) -> Edit:
        row = self._conn.execute(
            f"""
            INSERT INTO catalog.edit AS e (thread, kind, digest, compilation, by)
            SELECT t.id, t.kind, %s, %s, %s FROM catalog.thread t WHERE t.id = %s
            RETURNING {_EDIT}
            """,
            (digest, compilation, by, thread),
        ).fetchone()
        if row is None:
            raise LookupError(f"no thread with id {thread}")
        return _edit(row)

    def thread(self, id: int) -> Thread:
        row = self._conn.execute(
            "SELECT id, kind, forked_from, by, at FROM catalog.thread WHERE id = %s",
            (id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"no thread with id {id}")
        id, kind, forked_from, by, at = row
        return Thread(id=id, kind=kind, forked_from=forked_from, by=by, at=at)

    def history(self, thread: int) -> list[Edit]:
        rows = self._conn.execute(
            f"SELECT {_EDIT} FROM catalog.edit e WHERE e.thread = %s ORDER BY e.id",
            (thread,),
        ).fetchall()
        if not rows:
            raise LookupError(f"no thread with id {thread}")
        return [_edit(r) for r in rows]

    def head(self, thread: int) -> Edit:
        return self.history(thread)[-1]

    def heads(self, kind: str, *, deleted: bool = False) -> list[Edit]:
        rows = self._conn.execute(
            f"""
            SELECT {_EDIT} FROM catalog.thread t {_HEAD}
            WHERE t.kind = %s AND {_DELETED} = %s
            ORDER BY t.id
            """,
            (kind, deleted),
        ).fetchall()
        return [_edit(r) for r in rows]

    def containing(self, digest: str) -> list[Edit]:
        rows = self._conn.execute(
            f"SELECT {_EDIT} FROM catalog.edit e WHERE e.digest = %s ORDER BY e.id",
            (digest,),
        ).fetchall()
        return [_edit(r) for r in rows]

    def behind(self, thread: int) -> list[Behind]:
        head = self.head(thread)
        refs = [
            (str(path), str(dst))
            for path, dst in self._conn.execute(
                "SELECT path, dst FROM factor.component_ref WHERE src = %s",
                (head.digest,),
            )
        ]
        if head.compilation is not None:
            row = self._conn.execute(
                "SELECT payload FROM factor.compilation WHERE digest = %s",
                (head.compilation,),
            ).fetchone()
            assert row is not None
            recipe = Compilation.parse(str(row[0])).recipe
            refs += [(s.path, s.digest) for s in iter_refs(recipe, "/recipe")]
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT r.path, r.digest, {_EDIT}
            FROM unnest(%s::text[], %s::text[]) AS r (path, digest)
            JOIN catalog.edit x ON x.digest = r.digest
            JOIN catalog.thread t ON t.id = x.thread {_HEAD}
            WHERE e.digest <> r.digest AND NOT {_DELETED}
            ORDER BY r.path, e.thread
            """,
            ([p for p, _ in refs], [d for _, d in refs]),
        ).fetchall()
        return [Behind(path=r[0], digest=r[1], head=_edit(r[2:])) for r in rows]

    def note(self, subject: Subject, entry: Entry, by: int) -> None:
        _ = self._conn.execute(
            """
            INSERT INTO catalog.entry
                (thread, run, run_stage, score, score_stage, field, value, person, present, by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                subject.thread,
                subject.run,
                None if subject.run is None else "started",
                subject.score,
                None if subject.score is None else "started",
                entry.field,
                entry.value,
                entry.person,
                entry.present,
                by,
            ),
        )

    def about(self, subject: Subject) -> About:
        column, key = self._subject(subject)
        rows = self._conn.execute(
            f"""
            SELECT field, value, person, present FROM catalog.entry
            WHERE {column} = %s ORDER BY id
            """,
            (key,),
        )
        return About.of(
            Entry(field=EntryField(f), value=v, person=p, present=present)
            for f, v, p, present in rows
        )

    def label(
        self, scorer: str, part: Part, position: int, value: str, by: int
    ) -> None:
        component = resolve(Store(self._conn).get, scorer, Scorer)
        parts = component.views if part == "view" else component.resources
        if not 0 <= position < len(parts):
            raise ValueError(f"{scorer} has no {part} at position {position}")
        _ = self._conn.execute(
            """
            INSERT INTO catalog.label (scorer, part, position, value, by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (scorer, part, position, value, by),
        )

    def labels(self, scorer: str) -> dict[tuple[Part, int], str]:
        rows = self._conn.execute(
            """
            SELECT DISTINCT ON (part, position) part, position, value
            FROM catalog.label WHERE scorer = %s
            ORDER BY part, position, id DESC
            """,
            (scorer,),
        )
        return {(part, position): value for part, position, value in rows}

    @staticmethod
    def _subject(subject: Subject) -> tuple[LiteralString, object]:
        if subject.thread is not None:
            return "thread", subject.thread
        if subject.run is not None:
            return "run", subject.run
        return "score", subject.score
