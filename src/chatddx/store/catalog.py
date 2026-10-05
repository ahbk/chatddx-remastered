from collections.abc import Collection, Mapping
from typing import Any

from chatddx.catalog import (
    About,
    Behind,
    Binding,
    Edit,
    Entry,
    EntryField,
    Part,
    Repair,
    Subject,
    Survey,
    Thread,
    Variation,
    check_label,
    families,
    language,
    read,
    threads,
    titles,
)
from chatddx.factors.base import Component, Fingerprint, canonical_bytes, resolve
from chatddx.factors.cases import Vignette
from chatddx.factors.request import Compilation, Recipe
from chatddx.factors.scoring import Scorer

from .store import Connection, Store

_THREAD = "t.id, t.kind, t.forked_from, t.by, t.at"
_EDIT = "e.id, e.thread, e.digest, e.compilation, e.based_on, e.by, e.at"
_BINDING = "b.id, b.family, b.source, b.source_id, b.fingerprint, b.by, b.at"


def _thread(row: tuple[Any, ...]) -> Thread:
    id, kind, forked_from, by, at = row
    return Thread(id=id, kind=kind, forked_from=forked_from, by=by, at=at)


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


def _binding(row: tuple[Any, ...]) -> Binding:
    id, family, source, source_id, fingerprint, by, at = row
    return Binding(
        id=id,
        family=family,
        vignette=Vignette(
            source=source,
            id=source_id,
            fingerprint=Fingerprint.model_validate(fingerprint),
        ),
        by=by,
        at=at,
    )


def _jsonb(fingerprint: Fingerprint) -> str:
    return canonical_bytes(
        fingerprint.model_dump(mode="json", context={"canonical": True})
    ).decode()


class Rows:
    def __init__(self, conn: Connection) -> None:
        self._conn: Connection = conn
        self._store: Store = Store(conn)

    def get(self, digest: str) -> Component:
        return self._store.get(digest)

    def kind(self, digest: str) -> str | None:
        row = self._conn.execute(
            "SELECT kind FROM factor.component WHERE digest = %s", (digest,)
        ).fetchone()
        return None if row is None else str(row[0])

    def compilations(self, skeleton: str) -> list[Compilation]:
        return self._store.compilations(skeleton)

    def thread(self, id: int) -> Thread | None:
        row = self._conn.execute(
            f"SELECT {_THREAD} FROM catalog.thread t WHERE t.id = %s", (id,)
        ).fetchone()
        return None if row is None else _thread(row)

    def threads(self, kind: str) -> list[Thread]:
        rows = self._conn.execute(
            f"SELECT {_THREAD} FROM catalog.thread t WHERE t.kind = %s ORDER BY t.id",
            (kind,),
        ).fetchall()
        return [_thread(r) for r in rows]

    def threads_forked_from(self, edits: Collection[int]) -> list[Thread]:
        rows = self._conn.execute(
            f"""
            SELECT {_THREAD} FROM catalog.thread t
            WHERE t.forked_from = ANY(%s::bigint[]) ORDER BY t.id
            """,
            (list(edits),),
        ).fetchall()
        return [_thread(r) for r in rows]

    def edit(self, id: int) -> Edit | None:
        row = self._conn.execute(
            f"SELECT {_EDIT} FROM catalog.edit e WHERE e.id = %s", (id,)
        ).fetchone()
        return None if row is None else _edit(row)

    def edits(self, threads: Collection[int]) -> list[Edit]:
        rows = self._conn.execute(
            f"""
            SELECT {_EDIT} FROM catalog.edit e
            WHERE e.thread = ANY(%s::bigint[]) ORDER BY e.id
            """,
            (list(threads),),
        ).fetchall()
        return [_edit(r) for r in rows]

    def edits_holding(self, digests: Collection[str]) -> list[Edit]:
        rows = self._conn.execute(
            f"""
            SELECT {_EDIT} FROM catalog.edit e
            WHERE e.digest = ANY(%s::text[]) ORDER BY e.id
            """,
            (list(digests),),
        ).fetchall()
        return [_edit(r) for r in rows]

    def entries(self, subjects: Collection[Subject]) -> list[tuple[Subject, Entry]]:
        rows = self._conn.execute(
            """
            SELECT thread, family, run, score, field, value, person, present
            FROM catalog.entry
            WHERE thread = ANY(%s::bigint[]) OR family = ANY(%s::bigint[])
                OR run = ANY(%s::uuid[]) OR score = ANY(%s::uuid[])
            ORDER BY id
            """,
            (
                [s.thread for s in subjects if s.thread is not None],
                [s.family for s in subjects if s.family is not None],
                [s.run for s in subjects if s.run is not None],
                [s.score for s in subjects if s.score is not None],
            ),
        ).fetchall()
        return [
            (
                Subject(thread=thread, family=family, run=run, score=score),
                Entry(
                    field=EntryField(field), value=value, person=person, present=present
                ),
            )
            for thread, family, run, score, field, value, person, present in rows
        ]

    def bindings(self, family: int) -> list[Binding]:
        rows = self._conn.execute(
            f"""
            SELECT {_BINDING} FROM catalog.binding b
            WHERE b.family = %s ORDER BY b.id
            """,
            (family,),
        ).fetchall()
        return [_binding(r) for r in rows]

    def bindings_in(self, source: str) -> list[Binding]:
        rows = self._conn.execute(
            f"""
            SELECT {_BINDING} FROM catalog.binding b
            WHERE b.source = %s ORDER BY b.id
            """,
            (source,),
        ).fetchall()
        return [_binding(r) for r in rows]

    def bound_to(self, vignette: Vignette, kind: str) -> list[str]:
        return [
            str(d)
            for (d,) in self._conn.execute(
                """
                SELECT digest FROM factor.component
                WHERE kind = %s AND doc #>> '{vignette,source}' = %s
                    AND doc #>> '{vignette,id}' = %s
                    AND doc #> '{vignette,fingerprint}' = %s::jsonb
                ORDER BY digest
                """,
                (kind, vignette.source, vignette.id, _jsonb(vignette.fingerprint)),
            )
        ]

    def expectations(self, cases: Collection[str]) -> list[str]:
        return [
            str(d)
            for (d,) in self._conn.execute(
                """
                SELECT digest FROM factor.component
                WHERE kind = 'expectation' AND doc ->> 'case' = ANY(%s::text[])
                ORDER BY digest
                """,
                (list(cases),),
            )
        ]

    def languages(self, digests: Collection[str]) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            """
            SELECT digest, value FROM catalog.language
            WHERE digest = ANY(%s::text[]) ORDER BY id
            """,
            (list(digests),),
        ).fetchall()
        return [(str(d), str(v)) for d, v in rows]

    def labels(self, scorer: str) -> list[tuple[Part, int, str]]:
        rows = self._conn.execute(
            """
            SELECT part, position, value FROM catalog.label
            WHERE scorer = %s ORDER BY id
            """,
            (scorer,),
        ).fetchall()
        return [(part, int(position), str(value)) for part, position, value in rows]


class Catalog:
    def __init__(self, conn: Connection) -> None:
        self._conn: Connection = conn
        self._rows: Rows = Rows(conn)

    def create(
        self,
        digest: str,
        by: int,
        *,
        compilation: str | None = None,
        forked_from: int | None = None,
        name: str | None = None,
        owner: int | None = None,
    ) -> Edit:
        named = None if name is None else Entry(field=EntryField.NAME, value=name)
        owned = Entry(field=EntryField.OWNER, person=by if owner is None else owner)
        with self._conn.transaction():
            kind = self._rows.kind(digest)
            if kind is None:
                raise LookupError(f"{digest} is not in the store")
            threads.check_threadable(kind)
            row = self._conn.execute(
                """
                INSERT INTO catalog.thread (kind, forked_from, by)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (kind, forked_from, by),
            ).fetchone()
            assert row is not None
            edit = self.edit(row[0], digest, by, compilation=compilation)
            self.note(Subject(thread=edit.thread), owned, by)
            if named is not None:
                self.note(Subject(thread=edit.thread), named, by)
            return edit

    def edit(
        self,
        thread: int,
        digest: str,
        by: int,
        *,
        compilation: str | None = None,
        based_on: int | None = None,
    ) -> Edit:
        if based_on is not None:
            threads.check_based_on(self._rows, thread, based_on)
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
        return threads.thread(self._rows, id)

    def history(self, thread: int) -> list[Edit]:
        return threads.history(self._rows, thread)

    def head(self, thread: int) -> Edit:
        return threads.head(self._rows, thread)

    def heads(self, kind: str, *, deleted: bool = False) -> list[Edit]:
        return threads.heads(self._rows, kind, deleted=deleted)

    def find(self, kind: str, name: str, *, owner: int | None = None) -> list[int]:
        return threads.find(self._rows, kind, name, owner=owner)

    def forks(self, thread: int) -> list[int]:
        return threads.forks(self._rows, thread)

    def expectations_of(self, case: str) -> list[int]:
        return threads.expectations_of(self._rows, case)

    def containing(self, digest: str) -> list[Edit]:
        return self._rows.edits_holding([digest])

    def behind(self, thread: int) -> list[Behind]:
        return threads.behind(self._rows, thread)

    def recipe(self, thread: int) -> Recipe | None:
        return threads.recipe(self._rows, threads.head(self._rows, thread))

    def variation(self, thread: int) -> Variation | None:
        return threads.variation(self._rows, thread)

    def proposal(self, thread: int) -> Recipe | Component | None:
        return threads.proposal(self._rows, thread)

    def title(self, thread: int) -> str:
        return titles.title(self._rows, thread)

    def title_of(self, digest: str) -> str:
        return titles.title_of(self._rows, digest)

    def language_of(self, digest: str) -> str | None:
        return language.language_of(self._rows, digest)

    def adopt(self, case: str, by: int, *, owner: int | None = None) -> int:
        with self._conn.transaction():
            if (family := families.family_of(self._rows, case)) is not None:
                return family
            vignette = families.check_adoptable(self._rows, case)
            row = self._conn.execute(
                "INSERT INTO catalog.family (by) VALUES (%s) RETURNING id", (by,)
            ).fetchone()
            assert row is not None
            family = int(row[0])
            _ = self._bind(family, vignette, by)
            owned = Entry(field=EntryField.OWNER, person=by if owner is None else owner)
            self.note(Subject(family=family), owned, by)
            return family

    def family(self, case: str) -> int | None:
        return families.family_of(self._rows, case)

    def bindings(self, family: int) -> list[Binding]:
        return families.bindings(self._rows, family)

    def survey(self, source: str, listing: Mapping[str, Fingerprint]) -> Survey:
        return families.survey(self._rows, source, listing)

    def repair(
        self,
        family: int,
        by: int,
        *,
        id: str | None = None,
        fingerprint: Fingerprint | None = None,
    ) -> Repair:
        with self._conn.transaction():
            plan = families.plan_repair(
                self._rows, family, id=id, fingerprint=fingerprint
            )
            binding = self._bind(family, plan.vignette, by)
            _ = Store(self._conn).add(plan.registry, list(plan.registry))
            edits = tuple(self.edit(t, d, by) for t, d in plan.moves)
        return Repair(
            binding=binding, cases=plan.cases, appendices=plan.appendices, edits=edits
        )

    def note(self, subject: Subject, entry: Entry, by: int) -> None:
        language.check_entry(subject, entry)
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

    def language(self, digest: str, value: str, by: int) -> None:
        kind = language.check_language(self._rows, digest, value)
        _ = self._conn.execute(
            """
            INSERT INTO catalog.language (digest, kind, value, by)
            VALUES (%s, %s, %s, %s)
            """,
            (digest, kind, value, by),
        )

    def about(self, subject: Subject) -> About:
        return read.about(self._rows, subject)

    def label(
        self, scorer: str, part: Part, position: int, value: str, by: int
    ) -> None:
        check_label(resolve(self._rows.get, scorer, Scorer), part, position)
        _ = self._conn.execute(
            """
            INSERT INTO catalog.label (scorer, part, position, value, by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (scorer, part, position, value, by),
        )

    def labels(self, scorer: str) -> dict[tuple[Part, int], str]:
        return read.labels(self._rows, scorer)

    # src/chatddx/store/migrations/0026-t2-catalog-binding-fingerprint.sql keeps each
    # binding's source, and its id or fingerprint.
    def _bind(self, family: int, vignette: Vignette, by: int) -> Binding:
        row = self._conn.execute(
            f"""
            INSERT INTO catalog.binding AS b (family, source, source_id, fingerprint, by)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            RETURNING {_BINDING}
            """,
            (
                family,
                vignette.source,
                vignette.id,
                _jsonb(vignette.fingerprint),
                by,
            ),
        ).fetchone()
        assert row is not None
        return _binding(row)
