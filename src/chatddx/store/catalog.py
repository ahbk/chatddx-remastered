from typing import Any, LiteralString

from pydantic import JsonValue

from chatddx.core.catalog import (
    THREAD_KINDS,
    About,
    Behind,
    Binding,
    Edit,
    Entry,
    EntryField,
    Part,
    Subject,
    Thread,
    Variation,
)
from chatddx.factors.base import (
    Component,
    Fingerprint,
    canonical_bytes,
    iter_refs,
    parse_component,
    resolve,
)
from chatddx.factors.cases import SourceCase
from chatddx.factors.request import Recipe
from chatddx.factors.scoring import Scorer
from chatddx.ledger.ledger import Compilation

from .store import Connection, Store

_EDIT = "e.id, e.thread, e.digest, e.compilation, e.based_on, e.by, e.at"

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
    id, thread, digest, compilation, based_on, by, at = row
    return Edit(
        id=id,
        thread=thread,
        digest=digest,
        compilation=compilation,
        based_on=based_on,
        by=by,
        at=at,
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
        self,
        thread: int,
        digest: str,
        by: int,
        *,
        compilation: str | None = None,
        based_on: int | None = None,
    ) -> Edit:
        # src/chatddx/store/migrations/0016-t2-catalog-based-on.sql repeats this check.
        if based_on is not None:
            in_origin = self._conn.execute(
                """
                SELECT FROM catalog.thread t
                JOIN catalog.edit o ON o.id = t.forked_from
                JOIN catalog.edit b ON b.thread = o.thread AND b.id >= o.id
                WHERE t.id = %s AND b.id = %s
                """,
                (thread, based_on),
            ).fetchone()
            if in_origin is None:
                raise ValueError(
                    f"edit {based_on} is not in the origin of thread {thread}"
                )
        row = self._conn.execute(
            f"""
            INSERT INTO catalog.edit AS e (thread, kind, digest, compilation, based_on, by)
            SELECT t.id, t.kind, %s, %s, %s, %s FROM catalog.thread t WHERE t.id = %s
            RETURNING {_EDIT}
            """,
            (digest, compilation, based_on, by, thread),
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
        if (recipe := self._recipe(head)) is not None:
            refs += [(s.path, s.digest) for s in iter_refs(recipe, "/recipe")]
        refs += self._through_cases(refs)
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

    def recipe(self, thread: int) -> Recipe | None:
        return self._recipe(self.head(thread))

    def variation(self, thread: int) -> Variation | None:
        forked_from = self.thread(thread).forked_from
        if forked_from is None:
            return None
        history = self.history(thread)
        based_on = [e.based_on for e in history if e.based_on is not None]
        base = self._edit_by_id(based_on[-1] if based_on else forked_from)
        fork = history[-1]
        by_recipe = base.compilation is not None and fork.compilation is not None
        before, after = self._shape(base, by_recipe), self._shape(fork, by_recipe)
        return Variation(
            base=base,
            head=self.head(base.thread),
            varies={
                path: (before.get(path), after.get(path))
                for path in sorted(before.keys() | after.keys())
                if before.get(path) != after.get(path)
            },
        )

    def proposal(self, thread: int) -> Recipe | Component | None:
        variation = self.variation(thread)
        if variation is None or not variation.moved:
            return None
        fork = self.head(thread)
        if variation.base.compilation is not None and fork.compilation is not None:
            recipe = self._recipe(variation.head)
            if recipe is None:
                return None
            changes = {
                path.removeprefix("/recipe/"): after
                for path, (_, after) in variation.varies.items()
            }
            return Recipe.model_validate({**recipe.model_dump(mode="json"), **changes})
        doc = Store(self._conn).get(variation.head.digest).canonical_doc()
        for path, (_, after) in variation.varies.items():
            if after is None:
                _ = doc.pop(path[1:], None)
            else:
                doc[path[1:]] = after
        return parse_component(canonical_bytes(doc))

    def adopt(self, case: str, by: int) -> int:
        with self._conn.transaction():
            if (family := self.family(case)) is not None:
                return family
            stored = self._conn.execute(
                "SELECT FROM factor.component WHERE digest = %s AND kind = 'case'",
                (case,),
            ).fetchone()
            if stored is None:
                raise LookupError(f"{case} is not a stored case")
            conflict = self._conn.execute(
                """
                SELECT b.family, b.source_id = c.doc #>> '{case,id}'
                FROM factor.component c, catalog.family f CROSS JOIN LATERAL (
                    SELECT * FROM catalog.binding h
                    WHERE h.family = f.id ORDER BY h.id DESC LIMIT 1
                ) b
                WHERE c.digest = %s AND b.source = c.doc #>> '{case,source}'
                    AND (b.source_id = c.doc #>> '{case,id}' OR b.vignette = c.doc -> 'vignette')
                LIMIT 1
                """,
                (case,),
            ).fetchone()
            if conflict is not None:
                family, renamed = conflict[0], not conflict[1]
                change = "a new name" if renamed else "new content"
                raise ValueError(
                    f"{case} gives family {family}'s vignette {change}; rebind the family"
                )
            row = self._conn.execute(
                "INSERT INTO catalog.family (by) VALUES (%s) RETURNING id", (by,)
            ).fetchone()
            assert row is not None
            _ = self._conn.execute(
                """
                INSERT INTO catalog.binding (family, source, source_id, vignette, by)
                SELECT %s, doc #>> '{case,source}', doc #>> '{case,id}', doc -> 'vignette', %s
                FROM factor.component WHERE digest = %s
                """,
                (row[0], by, case),
            )
            return int(row[0])

    def family(self, case: str) -> int | None:
        row = self._conn.execute(
            """
            SELECT b.family
            FROM factor.component c JOIN catalog.binding b
                ON b.source = c.doc #>> '{case,source}'
                AND b.source_id = c.doc #>> '{case,id}'
                AND b.vignette = c.doc -> 'vignette'
            WHERE c.digest = %s AND c.kind = 'case'
            ORDER BY b.id DESC LIMIT 1
            """,
            (case,),
        ).fetchone()
        return None if row is None else int(row[0])

    def bindings(self, family: int) -> list[Binding]:
        rows = self._conn.execute(
            """
            SELECT id, source, source_id, vignette, by, at
            FROM catalog.binding WHERE family = %s ORDER BY id
            """,
            (family,),
        ).fetchall()
        if not rows:
            raise LookupError(f"no family with id {family}")
        return [
            Binding(
                id=id,
                family=family,
                case=SourceCase(source=source, id=source_id),
                vignette=Fingerprint.model_validate(vignette),
                by=by,
                at=at,
            )
            for id, source, source_id, vignette, by, at in rows
        ]

    def note(self, subject: Subject, entry: Entry, by: int) -> None:
        _ = self._conn.execute(
            """
            INSERT INTO catalog.entry (
                thread, family, run, run_stage, score, score_stage,
                field, value, person, present, by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                subject.thread,
                subject.family,
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

    def _edit_by_id(self, id: int) -> Edit:
        row = self._conn.execute(
            f"SELECT {_EDIT} FROM catalog.edit e WHERE e.id = %s", (id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no edit with id {id}")
        return _edit(row)

    def _recipe(self, edit: Edit) -> Recipe | None:
        if edit.compilation is None:
            return None
        row = self._conn.execute(
            "SELECT payload FROM factor.compilation WHERE digest = %s",
            (edit.compilation,),
        ).fetchone()
        assert row is not None
        return Compilation.parse(str(row[0])).recipe

    def _shape(self, edit: Edit, by_recipe: bool) -> dict[str, JsonValue]:
        if by_recipe:
            recipe = self._recipe(edit)
            assert recipe is not None
            return {
                f"/recipe/{k}": v for k, v in recipe.model_dump(mode="json").items()
            }
        doc = Store(self._conn).get(edit.digest).canonical_doc()
        return {f"/{k}": v for k, v in doc.items() if k not in ("kind", "v")}

    @staticmethod
    def _subject(subject: Subject) -> tuple[LiteralString, object]:
        if subject.thread is not None:
            return "thread", subject.thread
        if subject.family is not None:
            return "family", subject.family
        if subject.run is not None:
            return "run", subject.run
        return "score", subject.score

    def _through_cases(self, refs: list[tuple[str, str]]) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            """
            SELECT r.src, r.path, r.dst
            FROM factor.component_ref r JOIN factor.component c ON c.digest = r.src
            WHERE c.kind = 'case' AND r.src = ANY(%s)
            """,
            ([d for _, d in refs],),
        ).fetchall()
        return [
            (path + inner, dst)
            for src, inner, dst in rows
            for path, digest in refs
            if digest == src
        ]
