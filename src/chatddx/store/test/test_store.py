import json
from uuid import uuid4

import pytest
from psycopg import errors
from psycopg.pq import TransactionStatus

from chatddx.factors.base import StructuralError, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.engine import LocalEngine
from chatddx.factors.request import Recipe, Skeleton
from chatddx.factors.test.sample import NOW, RIG, fp, world
from chatddx.factors.trial import Trial
from chatddx.ledger.ledger import (
    Call,
    CanaryCall,
    Compilation,
    ItemKey,
    JudgeCall,
    Run,
    RunItem,
    RunStarted,
    Score,
    ScoreItem,
    ScoreStarted,
    check_run,
    check_score,
)
from chatddx.store import Store, migrate
from chatddx.store.store import Connection


def call(model: str) -> Call:
    return Call(
        request=fp("body"),
        started_at=NOW,
        finished_at=NOW,
        status=200,
        response={"model": model},
        prompt_tokens=fp("tokens"),
    )


def stored_world(conn: Connection) -> tuple[Store, Registry, dict[str, str]]:
    reg = Registry()
    ids = world(reg)
    store = Store(conn)
    _ = store.add(reg, [ids["trial"], ids["scoring"], ids["canaries"]])
    return store, reg, ids


def test_migrations_apply_up_to_a_tier(empty: Connection) -> None:
    assert migrate(empty, tier=0) == [
        "0001-t0-tables",
        "0004-t0-identity",
        "0006-t0-identity-auth",
        "0009-t0-catalog",
        "0012-t0-catalog-families",
    ]
    assert empty.info.transaction_status == TransactionStatus.IDLE
    assert migrate(empty, tier=0) == []
    with empty.transaction():
        _ = empty.execute(
            "INSERT INTO factor.component VALUES ('d', 'k', 1, '{}', '{}')"
        )
        _ = empty.execute("UPDATE factor.component SET kind = 'other'")
        _ = empty.execute("DELETE FROM factor.component")
    assert migrate(empty) == [
        "0002-t1-grants",
        "0003-t2-integrity",
        "0005-t1-identity-grants",
        "0007-t1-identity-auth-grants",
        "0008-t2-identity-checks",
        "0010-t1-catalog-grants",
        "0011-t2-catalog-checks",
        "0013-t2-catalog-families",
        "0014-t2-catalog-kinds",
    ]
    with pytest.raises(errors.RaiseException, match="insert-only"):
        _ = empty.execute("TRUNCATE factor.component CASCADE")


def test_components_roundtrip(conn: Connection) -> None:
    store, reg, ids = stored_world(conn)
    roots = [ids["trial"], ids["scoring"]]
    loaded = store.load(roots)
    assert loaded.bundle(roots, RIG) == reg.bundle(roots, RIG)
    assert store.get(ids["trial"]) == reg.get(ids["trial"])
    skeleton = resolve(reg.get, ids["trial"], Trial).skeleton
    stored = resolve(store.get, skeleton, Skeleton)
    authored = resolve(reg.get, skeleton, Skeleton)
    assert json.dumps(stored.output_schema) == json.dumps(authored.output_schema)
    assert store.add(reg, [ids["trial"]])
    missing = "sha256:" + "f" * 64
    with pytest.raises(StructuralError, match="not in the store"):
        _ = store.get(missing)
    with pytest.raises(StructuralError, match="not in the store"):
        _ = store.load([missing])


def test_database_guards_components(conn: Connection, admin: Connection) -> None:
    _, reg, ids = stored_world(conn)
    trial = ids["trial"]
    with pytest.raises(errors.RaiseException, match="insert-only"):
        _ = admin.execute("UPDATE factor.component SET v = 2")
    admin.rollback()
    with pytest.raises(errors.RaiseException, match="insert-only"):
        _ = admin.execute("TRUNCATE factor.component CASCADE")
    admin.rollback()
    with pytest.raises(errors.CheckViolation):
        _ = conn.execute(
            "INSERT INTO factor.component VALUES (%s, 'trial', 1, %s, %s::jsonb)",
            ("sha256:" + "f" * 64, reg.raw(trial).decode(), reg.raw(trial).decode()),
        )
    conn.rollback()
    with (
        pytest.raises(errors.RaiseException, match="is not one of"),
        conn.transaction(),
    ):
        _ = conn.execute(
            "INSERT INTO factor.component_ref VALUES (%s, '/x', %s, '{skeleton}')",
            (trial, ids["engine"]),
        )
    with (
        pytest.raises(errors.RaiseException, match="component holds"),
        conn.transaction(),
    ):
        _ = conn.execute(
            "INSERT INTO factor.component_ref VALUES (%s, '/x', %s, '{engine.local}')",
            (trial, ids["engine"]),
        )
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        _ = conn.execute(
            "INSERT INTO factor.component_ref VALUES (%s, '/x', %s, '{trial}')",
            (trial, "sha256:" + "f" * 64),
        )


def test_run_and_score_roundtrip(conn: Connection) -> None:
    store, reg, ids = stored_world(conn)
    run_id, score_id = uuid4(), uuid4()
    engine = reg.get(ids["engine"])
    assert isinstance(engine, LocalEngine)
    served = engine.served_model_name
    started = RunStarted(
        run=run_id, at=NOW, rig=RIG, trial=ids["trial"], canaries=ids["canaries"]
    )
    items = tuple(
        RunItem(
            run=run_id,
            key=ItemKey(case=ids["case"], replicate=r),
            vignette=fp("v"),
            call=call(served),
        )
        for r in range(2)
    )
    canaries = tuple(
        CanaryCall(run=run_id, phase=p, probe=0, call=call(served))
        for p in ("start", "end")
    )
    finished = Run(stages=(started,), items=items, canaries=canaries).finish(NOW)
    store.append(started, *reversed(items), *canaries)
    store.append(finished)

    run = store.run(run_id)
    assert run.finished == finished
    assert check_run(run, reg) == []

    score_started = ScoreStarted(
        score=score_id,
        run=run_id,
        at=NOW,
        rig=RIG,
        scorer_code=RIG,
        scoring=ids["scoring"],
    )
    score_items = tuple(
        ScoreItem(
            score=score_id,
            key=i.key,
            view=1,
            value=1.0,
            judge_calls=(JudgeCall(judge=ids["judge"], seed_index=0, call=call("j")),),
        )
        for i in items
    )
    score_finished = Score(stages=(score_started,), items=score_items).finish(NOW)
    store.append(score_started, *score_items, score_finished)
    score = store.score(score_id)
    assert score.finished == score_finished
    assert check_score(score, run, reg) == []

    with pytest.raises(LookupError):
        _ = store.run(uuid4())


def test_database_guards_the_ledger(conn: Connection) -> None:
    store, _, ids = stored_world(conn)
    run_id = uuid4()
    item = RunItem(
        run=run_id,
        key=ItemKey(case=ids["case"], replicate=0),
        vignette=fp("v"),
        call=call("m"),
    )
    with pytest.raises(errors.ForeignKeyViolation):
        store.append(item)
    store.append(RunStarted(run=run_id, at=NOW, rig=RIG, trial=ids["trial"]))
    store.append(item)
    with pytest.raises(errors.UniqueViolation):
        store.append(item)
    payload = item.canonical.decode()
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _ = conn.execute(
            """
            INSERT INTO ledger.run_item (run, "case", replicate, payload, doc)
            VALUES (%s, %s, 1, %s, %s::jsonb)
            """,
            (run_id, ids["case"], payload, payload),
        )
    probe = CanaryCall(run=run_id, phase="start", probe=0, call=call("m"))
    store.append(probe)
    with pytest.raises(errors.UniqueViolation):
        store.append(probe.model_copy(update={"call": call("other")}))


def test_compilations_are_idempotent(conn: Connection) -> None:
    store, reg, ids = stored_world(conn)
    trial = reg.get(ids["trial"])
    assert isinstance(trial, Trial)
    skeleton = trial.skeleton
    recipe = Recipe(
        prompt="sha256:" + "1" * 64,
        output="sha256:" + "2" * 64,
        sampling="sha256:" + "3" * 64,
    )
    c = Compilation(recipe=recipe, skeleton=skeleton, compiler=RIG, at=NOW)
    store.append(c)
    store.append(c)
    assert store.compilations(skeleton) == [c]


def test_grants(conn: Connection) -> None:
    store, _, ids = stored_world(conn)
    store.append(RunStarted(run=uuid4(), at=NOW, rig=RIG, trial=ids["trial"]))
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        _ = conn.execute("UPDATE ledger.run_stage SET stage = stage")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        _ = conn.execute("DELETE FROM factor.component")
    privileges = conn.execute(
        """
        SELECT
            has_table_privilege('chatddx_reader', 'factor.component', 'SELECT'),
            has_table_privilege('chatddx_reader', 'ledger.run_stage', 'SELECT')
        """
    ).fetchone()
    assert privileges == (True, False)
