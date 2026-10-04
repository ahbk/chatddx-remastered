from typing import Any
from uuid import uuid4

import pytest
from psycopg import errors, sql
from pydantic import ValidationError

from chatddx.core.catalog import THREAD_KINDS, About, Entry, EntryField, Subject
from chatddx.core.identity import Person
from chatddx.factors.base import Component, StructuralError, iter_refs, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Appendix, CaseInput, SourceCase
from chatddx.factors.request import (
    Insert,
    NativeOutput,
    Output,
    Recipe,
    Sampling,
    compile_request,
)
from chatddx.factors.scoring import Judge, Scoring
from chatddx.factors.test.sample import NOW, RIG, fp, generation_recipe, world
from chatddx.ledger.ledger import Compilation, RunStarted, ScoreStarted
from chatddx.store import Catalog, People, Store
from chatddx.store.store import Connection


def stored_world(
    conn: Connection,
) -> tuple[Catalog, Store, Registry, dict[str, str], Person]:
    reg = Registry()
    ids = world(reg)
    store = Store(conn)
    _ = store.add(reg, [ids["trial"], ids["scoring"], ids["canaries"]])
    return Catalog(conn), store, reg, ids, People(conn).add("alice", "Alice")


def compiled(store: Store, reg: Registry, *instructions: str) -> Compilation:
    return compiled_recipe(store, reg, generation_recipe(reg, *instructions))


def compiled_recipe(store: Store, reg: Registry, recipe: Recipe) -> Compilation:
    skeleton = reg.add(compile_request(recipe, reg.get))
    _ = store.add(reg, [*(s.digest for s in iter_refs(recipe)), skeleton])
    compilation = Compilation(recipe=recipe, skeleton=skeleton, compiler=RIG, at=NOW)
    store.append(compilation)
    return compilation


def instructions(c: Compilation) -> str:
    assert c.recipe.instructions is not None
    return c.recipe.instructions


def test_threads_follow_edits(conn: Connection) -> None:
    catalog, store, reg, ids, alice = stored_world(conn)
    v1 = instructions(compiled(store, reg))
    v2 = instructions(compiled(store, reg, "You are a cardiologist."))
    first = catalog.create(v1, alice.id)
    second = catalog.edit(first.thread, v2, alice.id)
    assert catalog.history(first.thread) == [first, second]
    assert catalog.head(first.thread) == second
    assert catalog.thread(first.thread).kind == "chunk.instructions"
    reverted = catalog.edit(first.thread, v1, alice.id)
    assert catalog.head(first.thread).digest == v1
    assert catalog.containing(v1) == [first, reverted]
    assert catalog.heads("chunk.instructions") == [reverted]

    with pytest.raises(LookupError):
        _ = catalog.create("sha256:" + "9" * 64, alice.id)
    with pytest.raises(ValueError, match="case"):
        _ = catalog.create(ids["case"], alice.id)
    for kind in ("engine", "canaries"):
        assert catalog.history(catalog.create(ids[kind], alice.id).thread)
    with pytest.raises(LookupError):
        _ = catalog.edit(999, v1, alice.id)
    with pytest.raises(LookupError):
        _ = catalog.history(999)
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _ = catalog.edit(first.thread, ids["trial"], alice.id)


def test_skeleton_threads_carry_their_recipe(conn: Connection) -> None:
    catalog, store, reg, ids, alice = stored_world(conn)
    v1 = compiled(store, reg)
    v2 = compiled(store, reg, "You are a cardiologist.")
    edit = catalog.create(v1.skeleton, alice.id, compilation=v1.digest)
    assert edit.compilation == v1.digest
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _ = catalog.edit(edit.thread, v2.skeleton, alice.id, compilation=v1.digest)
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _ = catalog.create(instructions(v1), alice.id, compilation=v1.digest)

    judge = resolve(reg.get, ids["judge"], Judge)
    hand_written = catalog.create(judge.skeleton, alice.id)
    assert hand_written.compilation is None


def test_forks(conn: Connection) -> None:
    catalog, store, reg, ids, alice = stored_world(conn)
    bob = People(conn).add("bob", "Bob")
    v1 = instructions(compiled(store, reg))
    v2 = instructions(compiled(store, reg, "You are a cardiologist."))
    origin = catalog.create(v1, alice.id)
    fork = catalog.create(v2, bob.id, forked_from=origin.id)
    assert catalog.thread(fork.thread).forked_from == origin.id
    assert catalog.heads("chunk.instructions") == [origin, fork]
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _ = catalog.create(ids["trial"], alice.id, forked_from=origin.id)


def test_variations_are_reapplied_on_request(conn: Connection) -> None:
    catalog, store, reg, _, alice = stored_world(conn)
    base = generation_recipe(reg)
    shown = reg.add(
        Output(
            contract=NativeOutput(),
            json_schema={"type": "object"},
            guidance=("Answer with JSON matching:\n\n", Insert(insert="schema")),
        )
    )
    plan = compiled_recipe(store, reg, base)
    plan_shown = compiled_recipe(store, reg, base.model_copy(update={"output": shown}))
    origin = catalog.create(plan.skeleton, alice.id, compilation=plan.digest)
    fork = catalog.create(
        plan_shown.skeleton,
        alice.id,
        compilation=plan_shown.digest,
        forked_from=origin.id,
    )
    assert catalog.recipe(origin.thread) == base
    assert catalog.variation(origin.thread) is None
    variation = catalog.variation(fork.thread)
    assert variation is not None
    assert (variation.base, variation.head, variation.moved) == (origin, origin, False)
    assert variation.varies == {"/recipe/output": (base.output, shown)}
    assert catalog.proposal(fork.thread) is None

    greedy = reg.add(Sampling(temperature=0))
    _ = store.add(reg, [greedy])
    moved_recipe = base.model_copy(update={"sampling": greedy})
    moved = compiled_recipe(store, reg, moved_recipe)
    head = catalog.edit(
        origin.thread, moved.skeleton, alice.id, compilation=moved.digest
    )
    variation = catalog.variation(fork.thread)
    assert variation is not None
    assert (variation.base, variation.head, variation.moved) == (origin, head, True)
    proposal = catalog.proposal(fork.thread)
    assert proposal == moved_recipe.model_copy(update={"output": shown})

    assert isinstance(proposal, Recipe)
    accepted = compiled_recipe(store, reg, proposal)
    with pytest.raises(ValueError, match="origin"):
        _ = catalog.edit(fork.thread, accepted.skeleton, alice.id, based_on=fork.id)
    reapplied = catalog.edit(
        fork.thread,
        accepted.skeleton,
        alice.id,
        compilation=accepted.digest,
        based_on=head.id,
    )
    assert reapplied.based_on == head.id
    variation = catalog.variation(fork.thread)
    assert variation is not None
    assert (variation.base, variation.moved) == (head, False)
    assert variation.varies == {"/recipe/output": (base.output, shown)}
    assert catalog.proposal(fork.thread) is None

    with pytest.raises(errors.RaiseException, match="origin"), conn.transaction():
        _ = conn.execute(
            """
            INSERT INTO catalog.edit (thread, kind, digest, by, based_on)
            VALUES (%s, 'skeleton', %s, %s, %s)
            """,
            (fork.thread, accepted.skeleton, alice.id, fork.id),
        )


def test_chunk_variations(conn: Connection) -> None:
    catalog, store, reg, _, alice = stored_world(conn)
    recommended, longer, cooler = (
        reg.add(Sampling(temperature=0.6, top_p=0.95)),
        reg.add(Sampling(temperature=0.6, top_p=0.95, max_output_tokens=4096)),
        reg.add(Sampling(temperature=0.7, top_p=0.95)),
    )
    _ = store.add(reg, [recommended, longer, cooler])
    origin = catalog.create(recommended, alice.id)
    fork = catalog.create(longer, alice.id, forked_from=origin.id)
    variation = catalog.variation(fork.thread)
    assert variation is not None
    assert variation.varies == {"/max_output_tokens": (None, 4096)}
    _ = catalog.edit(origin.thread, cooler, alice.id)
    assert catalog.proposal(fork.thread) == Sampling(
        temperature=0.7, top_p=0.95, max_output_tokens=4096
    )


def test_threads_behind_are_proposed_not_moved(conn: Connection) -> None:
    catalog, store, reg, ids, alice = stored_world(conn)
    v1 = compiled(store, reg)
    v2 = compiled(store, reg, "You are a cardiologist.")
    chunk = catalog.create(instructions(v1), alice.id)
    skeleton = catalog.create(v1.skeleton, alice.id, compilation=v1.digest)
    trial = catalog.create(ids["trial"], alice.id)
    _ = catalog.create(instructions(v2), alice.id, forked_from=chunk.id)
    assert catalog.behind(skeleton.thread) == []
    assert catalog.behind(trial.thread) == []

    _ = catalog.edit(chunk.thread, instructions(v2), alice.id)
    [behind] = catalog.behind(skeleton.thread)
    assert (behind.path, behind.digest, behind.head.digest) == (
        "/recipe/instructions",
        instructions(v1),
        instructions(v2),
    )
    assert catalog.behind(trial.thread) == []

    moved = catalog.edit(skeleton.thread, v2.skeleton, alice.id, compilation=v2.digest)
    assert catalog.behind(skeleton.thread) == []
    [behind] = catalog.behind(trial.thread)
    assert (behind.path, behind.head) == ("/skeleton", moved)
    assert catalog.head(trial.thread) == trial

    catalog.note(
        Subject(thread=skeleton.thread), Entry(field=EntryField.DELETED), alice.id
    )
    assert catalog.behind(trial.thread) == []


def test_entries(conn: Connection) -> None:
    catalog, store, _, ids, alice = stored_world(conn)
    bob = People(conn).add("bob", "Bob")
    trial = catalog.create(ids["trial"], alice.id)
    subject = Subject(thread=trial.thread)
    assert catalog.about(subject) == About()
    for entry in (
        Entry(field=EntryField.NAME, value="baseline"),
        Entry(field=EntryField.NAME, value="gemma baseline"),
        Entry(field=EntryField.DESCRIPTION, value="first try"),
        Entry(field=EntryField.TAG, value="gemma"),
        Entry(field=EntryField.TAG, value="draft"),
        Entry(field=EntryField.TAG, value="draft", present=False),
        Entry(field=EntryField.OWNER, person=alice.id),
        Entry(field=EntryField.COLLABORATOR, person=bob.id),
        Entry(field=EntryField.DELETED),
    ):
        catalog.note(subject, entry, alice.id)
    assert catalog.about(subject) == About(
        name="gemma baseline",
        description="first try",
        tags=frozenset({"gemma"}),
        owner=alice.id,
        collaborators=frozenset({bob.id}),
        deleted=True,
    )
    assert catalog.heads("trial") == []
    assert catalog.heads("trial", deleted=True) == [trial]
    catalog.note(subject, Entry(field=EntryField.DELETED, present=False), bob.id)
    assert catalog.heads("trial") == [trial]

    run, score = uuid4(), uuid4()
    store.append(RunStarted(run=run, at=NOW, rig=RIG, trial=ids["trial"]))
    store.append(
        ScoreStarted(
            score=score,
            run=run,
            at=NOW,
            rig=RIG,
            scorer_code=RIG,
            scoring=ids["scoring"],
        )
    )
    catalog.note(Subject(run=run), Entry(field=EntryField.OWNER, person=bob.id), bob.id)
    catalog.note(
        Subject(score=score), Entry(field=EntryField.TAG, value="pilot"), bob.id
    )
    assert catalog.about(Subject(run=run)) == About(owner=bob.id)
    assert catalog.about(Subject(score=score)) == About(tags=frozenset({"pilot"}))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        catalog.note(
            Subject(run=uuid4()), Entry(field=EntryField.TAG, value="x"), bob.id
        )


def test_families(conn: Connection) -> None:
    catalog, store, reg, ids, alice = stored_world(conn)
    family = catalog.adopt(ids["case"], alice.id)
    assert catalog.adopt(ids["case"], alice.id) == family
    assert catalog.family(ids["case"]) == family
    case = resolve(reg.get, ids["case"], CaseInput)
    [binding] = catalog.bindings(family)
    assert (binding.case, binding.vignette) == (case.case, case.vignette)

    bare = reg.add(CaseInput(case=case.case, vignette=case.vignette))
    _ = store.add(reg, [bare])
    assert catalog.adopt(bare, alice.id) == family
    catalog.note(
        Subject(family=family),
        Entry(field=EntryField.NAME, value="chest pain"),
        alice.id,
    )
    assert catalog.about(Subject(family=family)).name == "chest pain"

    source = case.case.source
    changed = reg.add(CaseInput(case=case.case, vignette=fp("edited at the source")))
    renamed = reg.add(
        CaseInput(
            case=SourceCase(source=source, id="c1-renamed"), vignette=case.vignette
        )
    )
    other = reg.add(
        CaseInput(case=SourceCase(source=source, id="c2"), vignette=fp("x"))
    )
    _ = store.add(reg, [changed, renamed, other])
    with pytest.raises(ValueError, match="new content"):
        _ = catalog.adopt(changed, alice.id)
    with pytest.raises(ValueError, match="a new name"):
        _ = catalog.adopt(renamed, alice.id)
    assert catalog.family(changed) is None
    assert catalog.adopt(other, alice.id) != family

    with pytest.raises(LookupError):
        _ = catalog.adopt(ids["trial"], alice.id)
    with pytest.raises(LookupError):
        _ = catalog.bindings(999)


def test_behind_looks_through_cases(conn: Connection) -> None:
    catalog, store, reg, ids, alice = stored_world(conn)
    case = resolve(reg.get, ids["case"], CaseInput)
    [appendix] = case.appendices
    edited = reg.add(
        Appendix(case=case.case, vignette=case.vignette, text="Troponin 120 ng/L.")
    )
    _ = store.add(reg, [edited])
    trial = catalog.create(ids["trial"], alice.id)
    expectation = resolve(reg.get, ids["scoring"], Scoring).expectations[0]
    expected = catalog.create(expectation, alice.id)
    thread = catalog.create(appendix, alice.id)
    assert catalog.behind(trial.thread) == []

    head = catalog.edit(thread.thread, edited, alice.id)
    assert [(b.path, b.digest, b.head) for b in catalog.behind(trial.thread)] == [
        ("/cases/0/appendices/0", appendix, head)
    ]
    assert [(b.path, b.head) for b in catalog.behind(expected.thread)] == [
        ("/case/appendices/0", head)
    ]


@pytest.mark.parametrize(
    "entry",
    [
        {"field": "name"},
        {"field": "name", "value": ""},
        {"field": "name", "value": "x", "present": False},
        {"field": "owner", "value": "alice"},
        {"field": "owner", "person": 1, "present": False},
        {"field": "collaborator"},
        {"field": "tag", "value": "x", "person": 1},
        {"field": "deleted", "value": "yes"},
        {"field": "colour", "value": "red"},
    ],
)
def test_entry_shapes(entry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _ = Entry.model_validate(entry)


def test_subject_is_one_thing() -> None:
    with pytest.raises(ValidationError):
        _ = Subject()
    with pytest.raises(ValidationError):
        _ = Subject(thread=1, run=uuid4())
    with pytest.raises(ValidationError):
        _ = Subject(thread=1, family=1)


def test_labels(conn: Connection) -> None:
    catalog, _, reg, ids, alice = stored_world(conn)
    scorer = resolve(reg.get, ids["scoring"], Scoring).scorer
    catalog.label(scorer, "view", 0, "top-1 match", alice.id)
    catalog.label(scorer, "view", 1, "judge", alice.id)
    catalog.label(scorer, "view", 1, "judge grade", alice.id)
    catalog.label(scorer, "resource", 0, "synonyms", alice.id)
    assert catalog.labels(scorer) == {
        ("view", 0): "top-1 match",
        ("view", 1): "judge grade",
        ("resource", 0): "synonyms",
    }
    with pytest.raises(ValueError, match="position 2"):
        catalog.label(scorer, "view", 2, "x", alice.id)
    with pytest.raises(StructuralError):
        catalog.label(ids["trial"], "view", 0, "x", alice.id)
    with (
        pytest.raises(errors.RaiseException, match="no resource at position 1"),
        conn.transaction(),
    ):
        _ = conn.execute(
            """
            INSERT INTO catalog.label (scorer, part, position, value, by)
            VALUES (%s, 'resource', 1, 'x', %s)
            """,
            (scorer, alice.id),
        )


def test_database_guards_the_catalog(conn: Connection, admin: Connection) -> None:
    catalog, _, _, ids, alice = stored_world(conn)
    trial = catalog.create(ids["trial"], alice.id)
    family = catalog.adopt(ids["case"], alice.id)
    catalog.note(
        Subject(thread=trial.thread),
        Entry(field=EntryField.NAME, value="x"),
        alice.id,
    )
    conn.commit()
    for table in ("thread", "edit", "entry", "label", "family", "binding"):
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            _ = conn.execute(
                sql.SQL("DELETE FROM catalog.{}").format(sql.Identifier(table))
            )
    for table in ("edit", "binding"):
        with pytest.raises(errors.RaiseException, match="insert-only"):
            _ = admin.execute(
                sql.SQL("UPDATE catalog.{} SET by = by").format(sql.Identifier(table))
            )
        admin.rollback()
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _ = conn.execute(
            """
            INSERT INTO catalog.entry (thread, family, field, value, by)
            VALUES (%s, %s, 'name', 'x', %s)
            """,
            (trial.thread, family, alice.id),
        )
    for statement in (
        "INSERT INTO catalog.thread (kind, by) VALUES ('case', %s)",
        "INSERT INTO catalog.entry (field, value, by) VALUES ('name', 'x', %s)",
    ):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _ = conn.execute(statement, (alice.id,))
    for field, value in (("owner", "alice"), ("colour", "red"), ("name", "")):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _ = conn.execute(
                """
                INSERT INTO catalog.entry (thread, field, value, by)
                VALUES (%s, %s, %s, %s)
                """,
                (trial.thread, field, value, alice.id),
            )
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _ = conn.execute(
            """
            INSERT INTO catalog.edit (thread, kind, digest, by)
            VALUES (%s, 'trial', %s, %s)
            """,
            (trial.thread, ids["case"], alice.id),
        )


def test_kinds_and_fields_match_the_database(admin: Connection) -> None:
    checks = [
        str(d)
        for (d,) in admin.execute(
            """
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid IN ('catalog.thread'::regclass, 'catalog.entry'::regclass)
                AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%%ANY (ARRAY%%'
            """
        )
    ]
    for names in (THREAD_KINDS, {f.value for f in EntryField}):
        [check] = [c for c in checks if all(f"'{n}'" in c for n in names)]
        assert check.count("'") == 2 * len(names)
    assert THREAD_KINDS == Component.registry.keys() - {"case"}


def test_reader_reads_the_catalog(conn: Connection) -> None:
    privileges = conn.execute(
        """
        SELECT
            bool_and(has_table_privilege('chatddx_reader', c.oid, 'SELECT')),
            bool_or(has_table_privilege('chatddx_reader', c.oid, 'INSERT')),
            bool_and(has_table_privilege('chatddx_writer', c.oid, 'INSERT')),
            bool_or(has_table_privilege('chatddx_writer', c.oid, 'UPDATE'))
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'catalog' AND c.relkind = 'r'
        """
    ).fetchone()
    assert privileges == (True, False, True, False)
