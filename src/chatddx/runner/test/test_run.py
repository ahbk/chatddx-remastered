import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
from psycopg import sql

from chatddx.catalog import Subject
from chatddx.conftest import connect
from chatddx.core import settings
from chatddx.core.rig import rig
from chatddx.factors.base import Fingerprint, resolve
from chatddx.factors.cases import Case, Vignette
from chatddx.factors.request import Compilation, compile_request
from chatddx.factors.trial import Execution, Trial
from chatddx.facts.facts import Facts
from chatddx.fake_vllm.served import Served
from chatddx.fake_vllm.server import serving
from chatddx.identity import Person
from chatddx.inventory.inventory import Inventory
from chatddx.inventory.serving import start_up
from chatddx.inventory.sources import DirectorySource
from chatddx.ledger import Endpoint, ItemKey, RunFinished
from chatddx.runner.gate import Refused
from chatddx.runner.run import Event, ItemEnded, ItemStarted, Noted, Runner
from chatddx.runner.send import STOPPED, Delta
from chatddx.seed import Plan, plan_factors
from chatddx.seed.plan import SAMPLE
from chatddx.store import Catalog, People, Store
from chatddx.store.store import Connection

WORLD = Path(__file__).parents[4] / "sample-world"
QWEN = "qwen3-8b-awq@fake"
GPT_OSS = "gpt-oss-20b@fake"
CASES = ("DutchFall10w", "casesfromedn1")


@pytest.fixture(scope="module")
def plan() -> Plan:
    return plan_factors(
        SAMPLE / "factors.toml", Facts.load(SAMPLE / "facts.toml"), rig()
    )


@pytest.fixture
def conn(db: str) -> Iterator[Connection]:
    with psycopg.connect(settings.database(), dbname=db, autocommit=True) as c:
        yield c


@pytest.fixture
def alice(conn: Connection) -> Person:
    return People(conn).add("alice", "Alice")


def world(tmp_path: Path, port: int) -> Inventory:
    text = (WORLD / "inventory.toml").read_text()
    text = text.replace('path = "vignettes"', f'path = "{WORLD / "vignettes"}"')
    path = tmp_path / "inventory.toml"
    _ = path.write_text(
        text.replace("12099", str(port)).replace("12100", str(port + 1))
    )
    return Inventory.load(path)


@pytest.fixture
def inventory(tmp_path: Path) -> Inventory:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    return world(tmp_path, port)


@pytest.fixture
def fake(inventory: Inventory, plan: Plan) -> Iterator[None]:
    startup = start_up(inventory, plan.registry.get, QWEN)
    with serving([Served.of(startup.argv[0], startup.argv[1:])]):
        yield


def trial(
    plan: Plan,
    skeleton: str = "free-text (Qwen/Qwen3-8B-AWQ)",
    cases: tuple[str, ...] = CASES,
    seeds: tuple[int, ...] = (7, 8),
    engine: str = QWEN,
) -> Trial:
    source = DirectorySource(name="sample", path=WORLD / "vignettes")
    refs = [plan.registry.add(c) for c in source.cases() if c.vignette.id in cases]
    made = Trial(
        skeleton=plan.named("recipe", skeleton).digest,
        engine=plan.named("local_engine", engine).digest,
        cases=tuple(refs),
        seeds=seeds,
    )
    _ = plan.registry.add(made)
    return made


@dataclass
class Heard:
    events: list[Event] = field(default_factory=list[Event])
    then: Callable[[Event], None] | None = None

    def __call__(self, event: Event) -> None:
        self.events.append(event)
        if self.then is not None:
            self.then(event)

    def of[E](self, kind: type[E]) -> list[E]:
        return [e for e in self.events if isinstance(e, kind)]


def reasoning(ended: ItemEnded) -> str:
    response = cast(Any, ended.item.call.response)
    return response["choices"][0]["message"]["reasoning"]


def rows(db: str, table: str) -> int:
    query = sql.SQL("SELECT count(*) FROM ledger.{}").format(sql.Identifier(table))
    with connect(db) as c:
        row = c.execute(query).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.usefixtures("fake")
def test_a_run_sends_each_item_and_writes_its_log(
    conn: Connection, inventory: Inventory, alice: Person, plan: Plan
) -> None:
    made = trial(plan)
    running = Runner(conn, inventory, alice).start(made, plan.registry, QWEN)
    heard = Heard()
    running.send(heard)
    assert running.finish() == []

    stored = Store(conn).run(running.run)
    started = stored.started
    assert (started.trial, started.rig, started.execution) == (
        made.digest,
        rig(),
        Execution(),
    )
    assert started.endpoint == Endpoint(
        name=QWEN, url=str(inventory.endpoint(QWEN).url)
    )
    assert isinstance(stored.finished, RunFinished) and stored.finished.findings == ()
    assert [i.key for i in heard.of(ItemStarted)] == [
        ItemKey(case=c, replicate=r) for c in made.cases for r in (0, 1)
    ]
    assert {i.key for i in stored.items} == {e.key for e in heard.of(ItemStarted)}
    assert all(i.call.error is None and i.call.status == 200 for i in stored.items)
    assert [
        f"seed={e.seed} " in reasoning(d)
        for e, d in zip(heard.of(ItemStarted), heard.of(ItemEnded), strict=True)
    ] == [True] * 4
    assert heard.of(Delta) and not heard.of(Noted)
    about = Catalog(conn).about(Subject(run=running.run))
    assert about.owner == alice.id


@pytest.mark.usefixtures("fake")
def test_a_greedy_skeleton_is_sent_without_its_seed(
    conn: Connection, inventory: Inventory, alice: Person, plan: Plan
) -> None:
    named = plan.named("recipe", "free-text (Qwen/Qwen3-8B-AWQ)")
    assert named.compilation is not None
    compiled = resolve(plan.registry.get, named.compilation, Compilation)
    greedy = compiled.recipe.model_copy(
        update={"sampling": plan.named("sampling", "greedy").digest}
    )
    skeleton = compile_request(greedy, plan.registry.get)
    assert skeleton.greedy
    made = trial(plan, cases=CASES[:1], seeds=(7,)).model_copy(
        update={"skeleton": plan.registry.add(skeleton)}
    )
    _ = plan.registry.add(made)
    running = Runner(conn, inventory, alice).start(made, plan.registry, QWEN)
    heard = Heard()
    running.send(heard)
    _ = running.finish()
    [ended] = heard.of(ItemEnded)
    assert "seed=" not in reasoning(ended) and "temperature=0" in reasoning(ended)


@pytest.mark.usefixtures("fake")
def test_each_row_lands_as_it_is_written(
    conn: Connection, db: str, inventory: Inventory, alice: Person, plan: Plan
) -> None:
    running = Runner(conn, inventory, alice).start(trial(plan), plan.registry, QWEN)
    assert rows(db, "run_stage") == 1
    landed: list[int] = []
    running.send(
        Heard(
            then=lambda e: (
                landed.append(rows(db, "run_item"))
                if isinstance(e, ItemEnded)
                else None
            )
        )
    )
    assert landed == [1, 2, 3, 4]
    _ = running.finish()
    assert rows(db, "run_stage") == 2


def test_the_runner_wants_rows_committed_as_they_are_written(
    db: str, inventory: Inventory, alice: Person
) -> None:
    with connect(db) as c, pytest.raises(ValueError, match="autocommit"):
        _ = Runner(c, inventory, alice)


@pytest.mark.usefixtures("fake")
def test_a_case_that_can_t_be_read_or_has_changed_is_noted(
    conn: Connection, inventory: Inventory, alice: Person, plan: Plan
) -> None:
    sample = trial(plan, cases=CASES[:1], seeds=(7,))
    real = resolve(plan.registry.get, sample.cases[0], Case)
    missing = Case(
        vignette=Vignette(
            source="sample", id="nowhere", fingerprint=real.vignette.fingerprint
        )
    )
    changed = Case(
        vignette=real.vignette.model_copy(
            update={"fingerprint": Fingerprint.of(b"before")}
        )
    )
    made = sample.model_copy(
        update={"cases": tuple(plan.registry.add(c) for c in (real, missing, changed))}
    )
    _ = plan.registry.add(made)
    running = Runner(conn, inventory, alice).start(made, plan.registry, QWEN)
    heard = Heard()
    running.send(heard)
    findings = running.finish()
    noted = sorted(n.finding.code for n in heard.of(Noted))
    assert noted == ["case.drift", "case.unreadable"]
    stored = Store(conn).run(running.run)
    assert stored.finished is not None
    assert sorted(f.code for f in stored.finished.findings) == noted
    assert len(stored.items) == 2
    codes = [f.code for f in findings]
    assert "run.incomplete" in codes and "case.drift" in codes


@pytest.mark.usefixtures("fake")
def test_stopping_ends_the_run_after_the_item_under_way(
    conn: Connection, inventory: Inventory, alice: Person, plan: Plan
) -> None:
    running = Runner(conn, inventory, alice).start(trial(plan), plan.registry, QWEN)

    def stop(event: Event) -> None:
        if isinstance(event, Delta):
            running.stop()

    running.send(Heard(then=stop))
    findings = running.finish()
    assert len(Store(conn).run(running.run).items) == 1
    assert ("run.incomplete", "3 items missing") in [
        (f.code, f.message) for f in findings
    ]


@pytest.mark.usefixtures("fake")
def test_an_interrupt_mid_call_records_the_call_as_stopped(
    conn: Connection, inventory: Inventory, alice: Person, plan: Plan
) -> None:
    running = Runner(conn, inventory, alice).start(trial(plan), plan.registry, QWEN)
    deltas: list[Delta] = []

    def interrupt(event: Event) -> None:
        if isinstance(event, Delta):
            deltas.append(event)
            if len(deltas) == 2:
                raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        running.send(Heard(then=interrupt))
    running.send()
    _ = running.finish()
    [item] = Store(conn).run(running.run).items
    assert item.call.error == STOPPED and item.call.prompt_tokens is not None


def test_nothing_is_sent_or_written_where_the_gate_refuses(
    conn: Connection,
    db: str,
    inventory: Inventory,
    alice: Person,
    plan: Plan,
) -> None:
    def refused(made: Trial, endpoint: str, at: Inventory = inventory) -> str:
        with pytest.raises(Refused) as caught:
            _ = Runner(conn, at, alice).start(made, plan.registry, endpoint)
        return str(caught.value)

    uncleared = inventory.model_copy(update={"cleared": {}})
    assert "isn't cleared for source sample, which is sensitive" in refused(
        trial(plan), QWEN, uncleared
    )
    assert "serves engine" in refused(trial(plan), GPT_OSS)
    assert f"endpoint {GPT_OSS!r} can't be confirmed" in refused(
        trial(plan, "free-text (openai/gpt-oss-20b)", engine=GPT_OSS), GPT_OSS
    )
    web = trial(plan, "plan-web (Qwen/Qwen3-8B-AWQ)")
    with pytest.raises(ValueError, match="doesn't run tools yet"):
        _ = Runner(conn, inventory, alice).start(web, plan.registry, QWEN)
    assert rows(db, "run_stage") == 0


@pytest.mark.usefixtures("fake")
def test_a_secret_is_sent_and_never_written(
    conn: Connection,
    db: str,
    inventory: Inventory,
    alice: Person,
    plan: Plan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyed = inventory.model_copy(
        update={
            "endpoints": inventory.endpoints
            | {
                QWEN: inventory.endpoint(QWEN).model_copy(
                    update={"credential": "FAKE_KEY"}
                )
            }
        }
    )
    monkeypatch.delenv("FAKE_KEY", raising=False)
    with pytest.raises(Refused, match=r"needs the secret in \$FAKE_KEY"):
        _ = Runner(conn, keyed, alice).start(trial(plan), plan.registry, QWEN)
    monkeypatch.setenv("FAKE_KEY", "s3cret-value")
    running = Runner(conn, keyed, alice).start(trial(plan), plan.registry, QWEN)
    running.send()
    _ = running.finish()
    with connect(db) as c:
        stored = c.execute(
            "SELECT payload FROM ledger.run_stage UNION ALL SELECT payload FROM ledger.run_item"
        ).fetchall()
    assert stored and not any("s3cret" in str(p) for (p,) in stored)


@pytest.mark.usefixtures("fake")
def test_a_run_is_finished_once_and_sends_nothing_after(
    conn: Connection, inventory: Inventory, alice: Person, plan: Plan
) -> None:
    running = Runner(conn, inventory, alice).start(trial(plan), plan.registry, QWEN)
    _ = running.finish()
    heard = Heard()
    running.send(heard)
    assert heard.events == []
    with pytest.raises(ValueError, match="is finished"):
        _ = running.finish()
