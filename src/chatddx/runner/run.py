from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from chatddx.catalog import Entry, EntryField, Subject
from chatddx.core.rig import rig
from chatddx.factors.base import Finding, Fingerprint, StructuralError, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Case, prepare_case
from chatddx.factors.engine import LocalEngine, RemoteEngine
from chatddx.factors.request import Skeleton, render
from chatddx.factors.trial import Execution, Trial
from chatddx.identity import Person
from chatddx.inventory.inventory import Inventory
from chatddx.inventory.serving import url_of
from chatddx.ledger import (
    Call,
    Endpoint,
    ItemKey,
    Run,
    RunItem,
    RunStarted,
    check_run,
)
from chatddx.store import Catalog, Store
from chatddx.store.store import Connection

from . import gate, send as sending
from .send import Delta


@dataclass(frozen=True)
class ItemStarted:
    key: ItemKey
    seed: int


@dataclass(frozen=True)
class ItemEnded:
    item: RunItem


@dataclass(frozen=True)
class Noted:
    finding: Finding


type Event = ItemStarted | Delta | ItemEnded | Noted


def _now() -> datetime:
    return datetime.now(UTC)


def _ignore(_: Event) -> None:
    pass


class Runner:
    def __init__(self, conn: Connection, inventory: Inventory, by: Person) -> None:
        # Each row must land as it's written, so a run that dies leaves a log to read.
        if not conn.autocommit:
            raise ValueError("the runner needs a connection in autocommit mode")
        self._conn: Connection = conn
        self._inventory: Inventory = inventory
        self._by: Person = by

    def start(
        self,
        trial: Trial,
        registry: Registry,
        endpoint: str,
        execution: Execution | None = None,
    ) -> "Running":
        execution = execution or Execution()
        _ = registry.add(trial)
        if execution.concurrency > 1:
            raise ValueError("the runner sends one call at a time so far")
        if resolve(registry.get, trial.skeleton, Skeleton).tools:
            raise ValueError("the runner doesn't run tools yet")
        gate.check(self._inventory, registry.get, endpoint, trial)
        secret = gate.secret(self._inventory, endpoint)
        url = url_of(self._inventory, registry.get, endpoint)
        store = Store(self._conn)
        _ = store.add(registry, [trial.digest])
        started = RunStarted(
            run=uuid4(),
            at=_now(),
            rig=rig(),
            trial=trial.digest,
            execution=execution,
            endpoint=Endpoint(name=endpoint, url=url),
        )
        store.append(started)
        Catalog(self._conn).note(
            Subject(run=started.run),
            Entry(field=EntryField.OWNER, person=self._by.id),
            self._by.id,
        )
        return Running(self._conn, self._inventory, registry, started, url, secret)


class Running:
    def __init__(
        self,
        conn: Connection,
        inventory: Inventory,
        registry: Registry,
        started: RunStarted,
        url: str,
        secret: str | None,
    ) -> None:
        self._store: Store = Store(conn)
        self._inventory: Inventory = inventory
        self._registry: Registry = registry
        self._started: RunStarted = started
        self._url: str = url
        self._secret: str | None = secret
        self._items: list[RunItem] = []
        self._findings: list[Finding] = []
        self._stopping: bool = False
        self._finished: bool = False

    @property
    def run(self) -> UUID:
        return self._started.run

    @property
    def started(self) -> RunStarted:
        return self._started

    # Ends `send` after the item under way.
    def stop(self) -> None:
        self._stopping = True

    # A KeyboardInterrupt during a call records the call as stopped and is raised
    # again; one raised in `on_event` while a call streams counts as during the call.
    def send(self, on_event: Callable[[Event], None] | None = None) -> None:
        emit: Callable[[Event], None] = on_event or _ignore
        get = self._registry.get
        trial = resolve(get, self._started.trial, Trial)
        skeleton = resolve(get, trial.skeleton, Skeleton)
        engine = get(trial.engine)
        assert isinstance(engine, LocalEngine | RemoteEngine)
        execution = self._started.execution
        sent = {i.key for i in self._items}
        for case_ref, replicate in execution.schedule(trial.cases, len(trial.seeds)):
            key = ItemKey(case=case_ref, replicate=replicate)
            if self._stopping or self._finished:
                return
            if key in sent:
                continue
            seed = trial.seeds[replicate]
            emit(ItemStarted(key, seed))
            case = resolve(get, case_ref, Case)
            try:
                source = self._inventory.source(case.vignette.source)
                raw = source.fetch(case.vignette.id)
                prepared = prepare_case(
                    case, raw, get, skeleton.appendix_layout, trial.cleanup
                )
            except (LookupError, StructuralError) as e:
                self._note(
                    Finding(code="case.unreadable", message=str(e), subject=str(key)),
                    emit,
                )
                continue
            for finding in prepared.findings:
                self._note(finding, emit)
            body = render(
                skeleton,
                model=engine.served_model_name,
                seed=seed,
                fills=prepared.fills,
            )
            try:
                call = sending.send(
                    self._url,
                    body,
                    execution=execution,
                    secret=self._secret,
                    on_delta=emit,
                )
            except sending.Stopped as stopped:
                self._stopping = True
                self._append(key, prepared.fingerprint, stopped.call, emit)
                raise KeyboardInterrupt from None
            self._append(key, prepared.fingerprint, call, emit)

    def finish(self) -> list[Finding]:
        if self._finished:
            raise ValueError(f"run {self.run} is finished")
        run = Run(stages=(self._started,), items=tuple(self._items))
        finished = run.finish(_now(), self._findings)
        self._store.append(finished)
        self._finished = True
        return check_run(
            Run(stages=(self._started, finished), items=run.items), self._registry
        )

    def _note(self, finding: Finding, emit: Callable[[Event], None]) -> None:
        self._findings.append(finding)
        emit(Noted(finding))

    def _append(
        self,
        key: ItemKey,
        vignette: Fingerprint,
        call: Call,
        emit: Callable[[Event], None],
    ) -> None:
        item = RunItem(run=self.run, key=key, vignette=vignette, call=call)
        self._store.append(item)
        self._items.append(item)
        emit(ItemEnded(item))
