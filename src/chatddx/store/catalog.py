from collections.abc import Mapping
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
    Repair,
    Subject,
    Survey,
    Thread,
    Variation,
)
from chatddx.core.titles import describe, describe_change, describe_recipe
from chatddx.factors.base import (
    Component,
    Fingerprint,
    canonical_bytes,
    iter_refs,
    parse_component,
    resolve,
)
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Appendix, Case, Vignette
from chatddx.factors.request import Compilation, Recipe, Skeleton, texts
from chatddx.factors.scoring import Expectation, Scorer
from chatddx.factors.trial import Trial

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


def _binding(family: int, row: tuple[Any, ...]) -> Binding:
    id, source, source_id, fingerprint, by, at = row
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
        name: str | None = None,
    ) -> Edit:
        named = None if name is None else Entry(field=EntryField.NAME, value=name)
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
            edit = self.edit(row[0], digest, by, compilation=compilation)
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

    # Live threads of a kind whose current name is `name`, owned by `owner` if given.
    def find(self, kind: str, name: str, *, owner: int | None = None) -> list[int]:
        rows = self._conn.execute(
            f"""
            SELECT t.id FROM catalog.thread t
            WHERE t.kind = %s AND NOT {_DELETED}
                AND (
                    SELECT n.value FROM catalog.entry n
                    WHERE n.thread = t.id AND n.field = 'name'
                    ORDER BY n.id DESC LIMIT 1
                ) = %s
                AND (%s::int IS NULL OR (
                    SELECT o.person FROM catalog.entry o
                    WHERE o.thread = t.id AND o.field = 'owner'
                    ORDER BY o.id DESC LIMIT 1
                ) = %s)
            ORDER BY t.id
            """,
            (kind, name, owner, owner),
        ).fetchall()
        return [int(r[0]) for r in rows]

    # Threads forked from any edit of `thread`.
    def forks(self, thread: int) -> list[int]:
        rows = self._conn.execute(
            """
            SELECT t.id FROM catalog.thread t JOIN catalog.edit o ON o.id = t.forked_from
            WHERE o.thread = %s ORDER BY t.id
            """,
            (thread,),
        ).fetchall()
        return [int(r[0]) for r in rows]

    # Expectation threads whose head expects `case`.
    def expectations_of(self, case: str) -> list[int]:
        return [t for t, _ in self._expectation_heads([case])]

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
        heads = [Behind(path=r[0], digest=r[1], head=_edit(r[2:])) for r in rows]
        return sorted([*heads, *self._rebound(refs)], key=lambda b: b.path)

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
            origin_head=self.head(base.thread),
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
            recipe = self._recipe(variation.origin_head)
            if recipe is None:
                return None
            changes = {
                path.removeprefix("/recipe/"): after
                for path, (_, after) in variation.varies.items()
            }
            return Recipe.model_validate({**recipe.model_dump(mode="json"), **changes})
        doc = Store(self._conn).get(variation.origin_head.digest).canonical_doc()
        for path, (_, after) in variation.varies.items():
            if after is None:
                _ = doc.pop(path[1:], None)
            else:
                doc[path[1:]] = after
        return parse_component(canonical_bytes(doc))

    def title(self, thread: int) -> str:
        name = self.about(Subject(thread=thread)).name
        if name is not None:
            return name
        variation = self.variation(thread)
        if variation is not None:
            base = self.title(variation.base.thread)
            changes = [
                describe_change(path, after, self.title_of)
                for path, (_, after) in variation.varies.items()
            ]
            return ", ".join([base, *changes]) if changes else f"a fork of {base}"
        head = self.head(thread)
        return self._derive(head.digest, head.compilation)

    def title_of(self, digest: str) -> str:
        if self._kind(digest) == "case":
            family = self.family(digest)
            if family is not None and (name := self.about(Subject(family=family)).name):
                return name
            return self._derive(digest)
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT t.id, x.id = e.id, {_DELETED}
            FROM catalog.edit x JOIN catalog.thread t ON t.id = x.thread {_HEAD}
            WHERE x.digest = %s
            ORDER BY 3, 2 DESC, 1
            """,
            (digest,),
        ).fetchall()
        for deleted in (False, True):
            threads = [(t, head) for t, head, d in rows if d == deleted]
            names = {t: self.about(Subject(thread=t)).name for t, _ in threads}
            for t, head in threads:
                if head and names[t] is not None:
                    return str(names[t])
            for t, head in threads:
                if head:
                    return self.title(t)
            for t, _ in threads:
                if names[t] is not None:
                    return f"{names[t]} (earlier)"
        return self._derive(digest)

    # The language a component is in, from labels; None when unlabelled or unclear.
    def language_of(self, digest: str) -> str | None:
        store = Store(self._conn)
        match self._kind(digest):
            case "case":
                family = self.family(digest)
                if family is None:
                    return None
                return self.about(Subject(family=family)).language
            case "expectation":
                return self.language_of(resolve(store.get, digest, Expectation).case)
            case "trial":
                return self.language_of(resolve(store.get, digest, Trial).skeleton)
            case "skeleton":
                compilations = store.compilations(digest)
                if not compilations:
                    return self._label(digest)
                recipe = compilations[0].recipe
                if recipe.translations is not None:
                    return self._label(recipe.translations)
                parts: list[str | None] = [
                    getattr(recipe, part)
                    for part in texts(recipe, store.get)
                    if part != "appendix_layout"
                ]
                labels = {None if p is None else self._label(p) for p in parts}
                return labels.pop() if len(labels) == 1 else None
            case _:
                return self._label(digest)

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
                SELECT b.family, b.source_id = c.doc #>> '{vignette,id}'
                FROM factor.component c, catalog.family f CROSS JOIN LATERAL (
                    SELECT * FROM catalog.binding h
                    WHERE h.family = f.id ORDER BY h.id DESC LIMIT 1
                ) b
                WHERE c.digest = %s AND b.source = c.doc #>> '{vignette,source}'
                    AND (
                        b.source_id = c.doc #>> '{vignette,id}'
                        OR b.vignette = c.doc #> '{vignette,fingerprint}'
                    )
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
                SELECT %s, doc #>> '{vignette,source}', doc #>> '{vignette,id}',
                    doc #> '{vignette,fingerprint}', %s
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
                ON b.source = c.doc #>> '{vignette,source}'
                AND b.source_id = c.doc #>> '{vignette,id}'
                AND b.vignette = c.doc #> '{vignette,fingerprint}'
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
        return [_binding(family, row) for row in rows]

    def survey(self, source: str, listing: Mapping[str, Fingerprint]) -> Survey:
        current = [
            _binding(row[0], row[1:])
            for row in self._conn.execute(
                """
                SELECT family, id, source, source_id, vignette, by, at FROM (
                    SELECT DISTINCT ON (family) * FROM catalog.binding
                    ORDER BY family, id DESC
                ) b
                WHERE source = %s ORDER BY family
                """,
                (source,),
            )
        ]
        by_id = {b.vignette.id: b for b in current}
        unchanged: list[int] = []
        changed: list[tuple[int, str, Fingerprint]] = []
        renamed: list[tuple[int, str, str]] = []
        new: list[str] = []
        for id, fingerprint in sorted(listing.items()):
            moved = [
                b
                for b in current
                if b.vignette.fingerprint == fingerprint
                and b.vignette.id != id
                and b.vignette.id not in listing
            ]
            if (b := by_id.get(id)) is not None:
                if b.vignette.fingerprint == fingerprint:
                    unchanged.append(b.family)
                else:
                    changed.append((b.family, id, fingerprint))
            elif len(moved) == 1:
                renamed.append((moved[0].family, moved[0].vignette.id, id))
            else:
                new.append(id)
        seen = {*unchanged, *(f for f, *_ in changed), *(f for f, *_ in renamed)}
        return Survey(
            unchanged=tuple(unchanged),
            changed=tuple(changed),
            renamed=tuple(renamed),
            new=tuple(new),
            gone=tuple(b.family for b in current if b.family not in seen),
        )

    def repair(
        self,
        family: int,
        by: int,
        *,
        id: str | None = None,
        fingerprint: Fingerprint | None = None,
    ) -> Repair:
        old = self.bindings(family)[-1].vignette
        vignette = Vignette(
            source=old.source,
            id=old.id if id is None else id,
            fingerprint=old.fingerprint if fingerprint is None else fingerprint,
        )
        if (vignette.id == old.id) == (vignette.fingerprint == old.fingerprint):
            raise ValueError(
                "a repair changes exactly one of the id or the fingerprint"
            )
        store = Store(self._conn)
        reg = Registry()
        appendices: dict[str, str] = {}

        def rebind(appendix: str) -> str:
            if appendix not in appendices:
                text = resolve(store.get, appendix, Appendix).text
                appendices[appendix] = reg.add(Appendix(vignette=vignette, text=text))
            return appendices[appendix]

        with self._conn.transaction():
            binding = self._bind(family, vignette, by)
            cases: dict[str, str] = {}
            for digest in self._at(old, "case"):
                c = resolve(store.get, digest, Case)
                cases[digest] = reg.add(
                    Case(
                        vignette=vignette,
                        appendices=tuple(rebind(a) for a in c.appendices),
                    )
                )
            moves = [(t, rebind(d)) for t, d in self._heads_at(old, "appendix")]
            if id is not None:
                for t, d in self._expectation_heads(list(cases)):
                    e = resolve(store.get, d, Expectation)
                    _ = reg.add(store.get(e.expectation_schema))
                    rekeyed = Expectation(
                        case=cases[e.case],
                        expectation_schema=e.expectation_schema,
                        data=e.data,
                    )
                    moves.append((t, reg.add(rekeyed)))
            _ = store.add(reg, list(reg))
            edits = tuple(self.edit(t, d, by) for t, d in moves)
        return Repair(binding=binding, cases=cases, appendices=appendices, edits=edits)

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
        return resolve(Store(self._conn).get, edit.compilation, Compilation).recipe

    def _label(self, digest: str) -> str | None:
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT (
                SELECT l.value FROM catalog.entry l
                WHERE l.thread = t.id AND l.field = 'language'
                ORDER BY l.id DESC LIMIT 1
            )
            FROM catalog.thread t
            WHERE NOT {_DELETED} AND EXISTS (
                SELECT FROM catalog.edit x WHERE x.thread = t.id AND x.digest = %s
            )
            """,
            (digest,),
        ).fetchall()
        return str(rows[0][0]) if len(rows) == 1 and rows[0][0] is not None else None

    def _derive(self, digest: str, compilation: str | None = None) -> str:
        store = Store(self._conn)
        component = store.get(digest)
        if isinstance(component, Skeleton):
            compilations = (
                store.compilations(digest)
                if compilation is None
                else [resolve(store.get, compilation, Compilation)]
            )
            if compilations:
                return describe_recipe(compilations[0].recipe, self.title_of)
        return describe(component, self.title_of)

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

    # src/chatddx/store/migrations/0018-t2-catalog-bindings.sql keeps each binding's id or vignette.
    def _bind(self, family: int, vignette: Vignette, by: int) -> Binding:
        row = self._conn.execute(
            """
            INSERT INTO catalog.binding (family, source, source_id, vignette, by)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            RETURNING id, source, source_id, vignette, by, at
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
        return _binding(family, row)

    def _at(self, vignette: Vignette, kind: LiteralString) -> list[str]:
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

    def _heads_at(
        self, vignette: Vignette, kind: LiteralString
    ) -> list[tuple[int, str]]:
        return [
            (int(t), str(d))
            for t, d in self._conn.execute(
                f"""
                SELECT t.id, e.digest FROM catalog.thread t {_HEAD}
                WHERE t.kind = %s AND e.digest = ANY(%s)
                ORDER BY t.id
                """,
                (kind, self._at(vignette, kind)),
            )
        ]

    def _expectation_heads(self, cases: list[str]) -> list[tuple[int, str]]:
        return [
            (int(t), str(d))
            for t, d in self._conn.execute(
                f"""
                SELECT t.id, e.digest FROM catalog.thread t {_HEAD}
                JOIN factor.component c ON c.digest = e.digest
                WHERE t.kind = 'expectation' AND c.doc ->> 'case' = ANY(%s)
                ORDER BY t.id
                """,
                (cases,),
            )
        ]

    def _rebound(self, refs: list[tuple[str, str]]) -> list[Behind]:
        store = Store(self._conn)
        behind: list[Behind] = []
        for path, digest in refs:
            if self._kind(digest) != "case" or (family := self.family(digest)) is None:
                continue
            current = self.bindings(family)[-1]
            case = resolve(store.get, digest, Case)
            stale = case.vignette != current.vignette
            if stale and not self.about(Subject(family=family)).deleted:
                behind.append(Behind(path=path, digest=digest, binding=current))
        return behind

    def _kind(self, digest: str) -> str | None:
        row = self._conn.execute(
            "SELECT kind FROM factor.component WHERE digest = %s", (digest,)
        ).fetchone()
        return None if row is None else str(row[0])

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
