from collections.abc import Iterable

from chatddx.catalog import Entry, EntryField, Subject
from chatddx.factors.base import Fingerprint
from chatddx.factors.cases import Case, Vignette
from chatddx.factors.lint import lint
from chatddx.factors.scoring import Expectation
from chatddx.facts.lint import lint as lint_facts, reasons
from chatddx.identity import Person
from chatddx.inventory.sources import Source
from chatddx.store.catalog import Catalog
from chatddx.store.people import People
from chatddx.store.store import Connection, Store

from .plan import Plan, Planned, SampleCase

ARCHIVE = "archive"


# Kinds a giftbag forks for its owner: what the old giftbag held (slices with their
# tools, configurations and cases' targets). Expectation schemas and scorers stay the
# archive's.
def _gifted(kind: str) -> bool:
    return kind.startswith("chunk.") or kind in ("tool", "skeleton", "expectation")


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
        self.landed: dict[str, list[str]] = {}
        self.lines: list[str] = []

    def land(self, digest: str, what: str) -> None:
        self.landed.setdefault(digest, []).append(what)

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
        compilation: str | None = None,
        fork_of: int | None = None,
    ) -> tuple[int, str]:
        what = f"{kind} {name}" if name is not None else kind
        if len(found) > 1:
            raise ValueError(f"the archive has {len(found)} threads for {what}")
        if not found:
            forked_from = None if fork_of is None else self.catalog.head(fork_of).id
            edit = self.catalog.create(
                digest,
                self.archive.id,
                compilation=compilation,
                forked_from=forked_from,
                name=name,
            )
            return edit.thread, "created"
        thread = found[0]
        if self.catalog.head(thread).digest == digest:
            return thread, "validated"
        _ = self.catalog.edit(thread, digest, self.archive.id, compilation=compilation)
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
        self.land(r.digest, f"{r.kind} {r.name}")
        self.sync(Subject(thread=thread), r.tags, r.description)
        labelled = self.catalog.labels(r.digest) if r.labels else {}
        for position, label in enumerate(r.labels):
            if label is not None and labelled.get(("view", position)) != label:
                self.catalog.label(r.digest, "view", position, label, self.archive.id)
        self.lines.append(f"[archive {r.kind}] {r.name}: {verb} {_short(r.digest)}")

    def case(self, source: Source, id: str, sample: SampleCase, schema: str) -> None:
        try:
            raw = source.fetch(id)
        except LookupError:
            self.lines.append(f"[archive case] {id}: missing at source {source.name!r}")
            return
        case = Case(
            vignette=Vignette(
                source=source.name, id=id, fingerprint=Fingerprint.of(raw)
            )
        )
        registry = self.plan.registry
        digest = registry.add(case)
        expectation = registry.add(
            Expectation(case=digest, expectation_schema=schema, data=sample.targets)
        )
        _ = self.store.add(registry, [digest, expectation])
        known = self.catalog.family(digest) is not None
        try:
            family = self.catalog.adopt(digest, self.archive.id)
        except ValueError as e:
            self.lines.append(f"[archive case] {id}: needs repair: {e}")
            return
        self.families.append(family)
        self.land(digest, f"case {id}")
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
        self.land(expectation, f"expectation {id}")
        self.sync(Subject(thread=thread))
        self.lines.append(f"[archive expectation] {id}: {verb} {_short(expectation)}")

    def lint(self) -> None:
        registry, facts, digests = self.plan.registry, self.plan.facts, [*self.landed]
        findings = lint(
            registry,
            digests,
            languages=self.catalog.language_of,
            reasons=reasons(facts, registry),
        )
        findings += lint_facts(registry, facts, digests)
        for f in findings:
            what = ", ".join(self.landed.get(f.subject or "") or [str(f.subject)])
            self.lines.append(f"[lint {f.level}] {what}: {f.code}: {f.message}")
        n = len(findings)
        self.lines.append(
            f"[lint] {n} finding{'' if n == 1 else 's'} in {len(digests)} components"
        )

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
            _ = self.catalog.create(
                head.digest,
                user.id,
                compilation=head.compilation,
                forked_from=head.id,
                name=about.name,
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
        compilations = [r.compilation for r in plan.records if r.compilation]
        _ = seeder.store.add(
            plan.registry, [*(r.digest for r in plan.records), *compilations]
        )
        for r in plan.records:
            seeder.record(r)
        seeder.lines += [f"[skipped] {s}" for s in plan.skipped]
        for id, sample in cases.items():
            seeder.case(source, id, sample, schemas[0].digest)
        seeder.share(user)
        if giftbag:
            seeder.giftbag(user)
        seeder.lint()
        return seeder.lines
