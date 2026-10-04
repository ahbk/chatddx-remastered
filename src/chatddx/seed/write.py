from collections.abc import Iterable

from chatddx.core.catalog import Entry, EntryField, Subject
from chatddx.core.identity import Person
from chatddx.factors.base import Fingerprint
from chatddx.factors.cases import CaseInput, SourceCase
from chatddx.factors.scoring import Expectation
from chatddx.inventory.sources import Source
from chatddx.ledger.ledger import Compilation
from chatddx.store.catalog import Catalog
from chatddx.store.people import People
from chatddx.store.store import Connection, Store

from .plan import Plan, Planned, SampleCase

ARCHIVE = "archive"


# Kinds a giftbag forks for its owner: what the old giftbag held (slices,
# configurations and cases' targets). Expectation schemas stay the archive's.
def _gifted(kind: str) -> bool:
    return kind.startswith("chunk.") or kind in ("skeleton", "expectation")


def _short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:6]


class _Seeder:
    def __init__(self, conn: Connection, plan: Plan, archive: Person) -> None:
        self.store: Store = Store(conn)
        self.catalog: Catalog = Catalog(conn)
        self.plan: Plan = plan
        self.archive: Person = archive
        self.threads: dict[tuple[str, str], int] = {}
        self.families: list[int] = []
        self.lines: list[str] = []

    def note(self, subject: Subject, entry: Entry) -> None:
        self.catalog.note(subject, entry, self.archive.id)

    def sync(
        self,
        subject: Subject,
        tags: Iterable[str] = (),
        description: str | None = None,
        language: str | None = None,
        name: str | None = None,
    ) -> None:
        about = self.catalog.about(subject)
        if about.owner is None:
            self.note(subject, Entry(field=EntryField.OWNER, person=self.archive.id))
        if name is not None and about.name != name:
            self.note(subject, Entry(field=EntryField.NAME, value=name))
        for tag in sorted(set(tags) - about.tags):
            self.note(subject, Entry(field=EntryField.TAG, value=tag))
        if description is not None and about.description != description:
            self.note(subject, Entry(field=EntryField.DESCRIPTION, value=description))
        if language is not None and about.language != language:
            self.note(subject, Entry(field=EntryField.LANGUAGE, value=language))

    def put(
        self,
        kind: str,
        digest: str,
        found: list[int],
        name: str | None,
        compilation: Compilation | None = None,
        fork_of: int | None = None,
    ) -> tuple[int, str]:
        label = f"{kind} {name}" if name is not None else kind
        if len(found) > 1:
            raise ValueError(f"the archive has {len(found)} threads for {label}")
        if compilation is not None:
            self.store.append(compilation)
        c = None if compilation is None else compilation.digest
        if not found:
            forked_from = None if fork_of is None else self.catalog.head(fork_of).id
            edit = self.catalog.create(
                digest,
                self.archive.id,
                compilation=c,
                forked_from=forked_from,
                name=name,
            )
            return edit.thread, "created"
        thread = found[0]
        if self.catalog.head(thread).digest == digest:
            return thread, "validated"
        _ = self.catalog.edit(thread, digest, self.archive.id, compilation=c)
        return thread, "updated"

    def record(self, r: Planned) -> None:
        found = self.catalog.find(r.kind, r.name, owner=self.archive.id)
        head = None if not found else self.catalog.head(found[0])
        unchanged = head is not None and head.digest == r.digest
        thread, verb = self.put(
            r.kind,
            r.digest,
            found,
            r.name,
            compilation=None if unchanged else r.compilation,
            fork_of=None if r.fork_of is None else self.threads[(r.kind, r.fork_of)],
        )
        self.threads[(r.kind, r.name)] = thread
        self.sync(Subject(thread=thread), r.tags, r.description)
        self.lines.append(f"[archive {r.kind}] {r.name}: {verb} {_short(r.digest)}")

    def case(self, source: Source, id: str, sample: SampleCase, schema: str) -> None:
        try:
            raw = source.fetch(id)
        except LookupError:
            self.lines.append(f"[archive case] {id}: missing at source {source.name!r}")
            return
        case = CaseInput(
            case=SourceCase(source=source.name, id=id), vignette=Fingerprint.of(raw)
        )
        registry = self.plan.registry
        digest = registry.add(case)
        expectation = registry.add(
            Expectation(case=digest, json_schema=schema, data=sample.targets)
        )
        _ = self.store.add(registry, [digest, expectation])
        known = self.catalog.family(digest) is not None
        try:
            family = self.catalog.adopt(digest, self.archive.id)
        except ValueError as e:
            self.lines.append(f"[archive case] {id}: needs repair: {e}")
            return
        self.families.append(family)
        self.sync(Subject(family=family), sample.tags, None, sample.language, name=id)
        verb = "validated" if known else "created"
        self.lines.append(f"[archive case] {id}: {verb} {_short(digest)}")
        found = [
            t
            for t in self.catalog.expectations_of(digest)
            if self.catalog.about(Subject(thread=t)).owner == self.archive.id
        ]
        thread, verb = self.put("expectation", expectation, found, None)
        self.threads[("expectation", id)] = thread
        self.sync(Subject(thread=thread))
        self.lines.append(f"[archive expectation] {id}: {verb} {_short(expectation)}")

    def share(self, user: Person) -> None:
        subjects = [Subject(thread=t) for t in self.threads.values()]
        subjects += [Subject(family=f) for f in self.families]
        shared = 0
        for subject in subjects:
            if user.id not in self.catalog.about(subject).collaborators:
                self.note(subject, Entry(field=EntryField.COLLABORATOR, person=user.id))
                shared += 1
        self.lines.append(
            f"[share] {shared} of {len(subjects)} archive threads and families with "
            + f"{user.login}"
        )

    def giftbag(self, user: Person) -> None:
        for (kind, name), thread in self.threads.items():
            if not _gifted(kind):
                continue
            mine = [
                t
                for t in self.catalog.forks(thread)
                if self.catalog.about(Subject(thread=t)).owner == user.id
            ]
            if mine:
                self.lines.append(f"[giftbag {kind}] {name}: kept")
                continue
            head = self.catalog.head(thread)
            about = self.catalog.about(Subject(thread=thread))
            fork = self.catalog.create(
                head.digest,
                user.id,
                compilation=head.compilation,
                forked_from=head.id,
                name=about.name,
            )
            self.catalog.note(
                Subject(thread=fork.thread),
                Entry(field=EntryField.OWNER, person=user.id),
                user.id,
            )
            self.lines.append(f"[giftbag {kind}] {name}: forked {_short(head.digest)}")


def seed(
    conn: Connection,
    plan: Plan,
    cases: dict[str, SampleCase],
    source: Source,
    user: Person,
    giftbag: bool = False,
) -> list[str]:
    schemas = [r for r in plan.records if r.kind == "expectation_schema"]
    if len(schemas) != 1:
        raise ValueError("the sample needs exactly one expectation schema")
    with conn.transaction():
        people = People(conn)
        archive = people.find(ARCHIVE) or people.add(ARCHIVE, "Archive")
        seeder = _Seeder(conn, plan, archive)
        _ = seeder.store.add(plan.registry, [r.digest for r in plan.records])
        for r in plan.records:
            seeder.record(r)
        seeder.lines += [f"[skipped] {s}" for s in plan.skipped]
        for id, sample in cases.items():
            seeder.case(source, id, sample, schemas[0].digest)
        seeder.share(user)
        if giftbag:
            seeder.giftbag(user)
        return seeder.lines
