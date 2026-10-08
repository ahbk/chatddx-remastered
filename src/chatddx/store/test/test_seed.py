from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from chatddx.catalog import Subject
from chatddx.cli import main
from chatddx.conftest import connect
from chatddx.core.rig import rig
from chatddx.factors.base import Fingerprint
from chatddx.facts.facts import Facts
from chatddx.inventory.inventory import Inventory
from chatddx.inventory.sources import MemorySource
from chatddx.seed import Plan, load_cases, plan_factors, seed
from chatddx.seed.plan import SAMPLE
from chatddx.store import Catalog, People
from chatddx.store.store import Connection

CASES = load_cases(SAMPLE / "cases.toml")
REVISION = "f" * 40
# The fake engines say what they are: no commit, no Nix closure.
FAKES = [
    *(
        f"[lint warning] model {name}: model.revision: revision 'fake' is not a "
        + "commit and can move"
        for name in ("qwen3-8b-awq@fake", "gpt-oss-20b@fake")
    ),
    *(
        f"[lint warning] engine.local {name}: engine.closure: closure 'chatddx "
        + "fake-vllm' is not a Nix store path"
        for name in ("qwen3-8b-awq@fake", "gpt-oss-20b@fake")
    ),
]
SAMPLE_WORLD = Path(__file__).parents[4] / "sample-world" / "inventory.toml"
WORLD = Path(__file__).parents[4] / "world" / "inventory.toml"


def sample_plan(factors: Path = SAMPLE / "factors.toml") -> Plan:
    facts = Facts.load(SAMPLE / "facts.toml")
    return plan_factors(factors, facts, rig(), root=SAMPLE)


def vignettes(**changed: bytes) -> MemorySource:
    texts = {id: f"The vignette of {id}.".encode() for id in CASES}
    return MemorySource(name="sample", vignettes={**texts, **changed})


def tally(lines: list[str]) -> Counter[str]:
    def what(line: str) -> str:
        prefix = line[1 : line.index("]")].split(" ")[0]
        if prefix in ("skipped", "share", "lint"):
            return prefix
        return f"{prefix} {line.split(': ', 1)[1].split(' ')[0]}"

    return Counter(what(line) for line in lines)


# Its own patch, so it's undone last, after the database fixtures that read DB_NAME.
@pytest.fixture(autouse=True)
def revision() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CHATDDX_REVISION", REVISION)
        yield


def test_init_data_seeds_the_archive(conn: Connection) -> None:
    alice = People(conn).add("alice", "Alice")
    plan = sample_plan()
    lines = seed(conn, plan, CASES, vignettes(), alice)
    threads = len(plan.records) + len(CASES)
    assert tally(lines) == Counter(
        {
            "archive created": threads + len(CASES),
            "skipped": 4,
            "share": 1,
            "lint": len(FAKES) + 1,
        }
    )
    shared = threads + len(CASES)
    assert (
        lines[-len(FAKES) - 2]
        == f"[share] {shared} of {shared} archive threads and families with alice"
    )
    components = len({r.digest for r in plan.records}) + 2 * len(CASES)
    assert lines[-len(FAKES) - 1 :] == [
        *FAKES,
        f"[lint] {len(FAKES)} findings in {components} components",
    ]

    catalog = Catalog(conn)
    archive = People(conn).find("archive")
    assert archive is not None
    [plan_thread] = catalog.find(
        "skeleton", "plan (Qwen/Qwen3-8B-AWQ)", owner=archive.id
    )
    [shown] = catalog.find("skeleton", "plan-shown (Qwen/Qwen3-8B-AWQ)")
    variation = catalog.variation(shown)
    assert variation is not None and set(variation.varies) == {"/recipe/output"}
    assert variation.base.thread == plan_thread
    about = catalog.about(Subject(thread=plan_thread))
    assert (about.owner, about.tags, alice.id in about.collaborators) == (
        archive.id,
        frozenset({"ddx"}),
        True,
    )
    [case] = [c for c in vignettes().cases() if c.vignette.id == "DutchFall10w"]
    family = catalog.family(case.digest)
    assert family is not None
    assert catalog.about(Subject(family=family)).language == "en"
    assert catalog.title_of(case.digest) == "DutchFall10w"
    scorer = plan.named("scorer", "plan")
    assert catalog.labels(scorer.digest) == {
        ("view", 0): "differential",
        ("view", 1): "warning",
        ("view", 2): "disposition",
        ("view", 3): "dont-miss",
    }
    [tool] = catalog.find("tool", "web_search", owner=archive.id)
    assert catalog.about(Subject(thread=tool)).description == (
        "Search the web for up-to-date information"
    )

    again = seed(conn, sample_plan(), CASES, vignettes(), alice)
    assert {v for v in tally(again) if v.startswith("archive")} == {"archive validated"}
    assert again[-len(FAKES) - 2].startswith("[share] 0 of ")


def test_init_data_lints_what_it_lands(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHATDDX_REVISION")
    alice = People(conn).add("alice", "Alice")
    plan = sample_plan()
    unlike = replace(CASES["Dutchfall11w"], targets={"warning": {"pattern": "shock"}})
    lines = seed(conn, plan, {**CASES, "Dutchfall11w": unlike}, vignettes(), alice)
    components = len({r.digest for r in plan.records}) + 2 * len(CASES)
    assert [line for line in lines if line.startswith("[lint")] == [
        *FAKES,
        *(
            f"[lint warning] scorer {name}: scorer.revision: scorer code has no revision"
            for name in ("plan", "diagnoses", "free-text", "raw")
        ),
        "[lint warning] expectation Dutchfall11w: expectation.invalid: at the root: "
        + "'diagnosis' is a required property",
        f"[lint] {5 + len(FAKES)} findings in {components} components",
    ]


def test_from_facts_records_follow_the_models_the_engines_serve(
    tmp_path: Path,
) -> None:
    factors = tmp_path / "factors.toml"
    _ = factors.write_text(
        """
[remote_engine."qwen@api"]
base_url = "https://api.example/v1/"
model = "Qwen/Qwen3-8B-AWQ"

[remote_engine."gemma@api"]
base_url = "https://api.example/v1/"
model = "google/gemma-3-27b-it"

[prompt.case]
segments = [{ slot = "vignette" }]

[output.raw]
contract = { kind = "text" }

[sampling.recommended]
from_facts = { effort = "default" }

[recipe.raw]
prompt = "case"
output = "raw"
sampling = "recommended"
"""
    )
    plan = plan_factors(factors, Facts.load(SAMPLE / "facts.toml"), rig())
    assert [(r.table, r.name) for r in plan.records] == [
        ("remote_engine", "qwen@api"),
        ("remote_engine", "gemma@api"),
        ("prompt", "case"),
        ("output", "raw"),
        ("sampling", "recommended (Qwen/Qwen3-8B-AWQ)"),
        ("recipe", "raw (Qwen/Qwen3-8B-AWQ)"),
    ]
    assert plan.skipped == [
        "from_facts records for google/gemma-3-27b-it: the facts don't know it, served "
        + "by engine.remote gemma@api",
        "from_facts records for openai/gpt-oss-20b: no planned engine serves it",
    ]
    assert not [s for s in sample_plan().skipped if s.startswith("from_facts records")]


def test_init_data_updates_and_gives_forks(conn: Connection, tmp_path: Path) -> None:
    alice = People(conn).add("alice", "Alice")
    _ = seed(conn, sample_plan(), CASES, vignettes(), alice)
    edited = tmp_path / "factors.toml"
    _ = edited.write_text(
        (SAMPLE / "factors.toml")
        .read_text()
        .replace(
            'guidance = "Fill in the management plan for the case."',
            'guidance = "Fill in the whole management plan."',
        )
    )
    lines = seed(
        conn,
        sample_plan(edited),
        CASES,
        vignettes(DutchFall10w=b"Edited at the source."),
        alice,
        giftbag=True,
    )
    assert sorted(line for line in lines if "updated" in line) == [
        line
        for line in sorted(lines)
        if line.startswith(
            (
                "[archive chunk.output] management-plan:",
                "[archive skeleton] plan (",
                "[archive skeleton] plan-web (",
            )
        )
    ]
    assert len([line for line in lines if "updated" in line]) == 5
    assert any(
        line.startswith("[archive case] DutchFall10w: needs repair") for line in lines
    )
    forked = [line for line in lines if line.startswith("[giftbag")]
    assert forked and all(line.split(": ")[1].startswith("forked") for line in forked)
    assert not any(
        line.startswith(("[giftbag expectation_schema", "[giftbag scorer"))
        for line in forked
    )
    assert any(line.startswith("[giftbag tool] web_search: forked") for line in forked)

    catalog = Catalog(conn)
    archive = People(conn).find("archive")
    assert archive is not None
    [archived] = catalog.find("chunk.output", "management-plan", owner=archive.id)
    assert len(catalog.find("chunk.output", "management-plan", owner=alice.id)) == 1
    owners = [catalog.about(Subject(thread=t)).owner for t in catalog.forks(archived)]
    assert owners.count(alice.id) == 1
    again = seed(
        conn,
        sample_plan(edited),
        CASES,
        vignettes(DutchFall10w=b"Edited at the source."),
        alice,
        giftbag=True,
    )
    assert {line.split(": ")[1] for line in again if line.startswith("[giftbag")} == {
        "kept"
    }

    gone = MemorySource(name="sample", vignettes={})
    lines = seed(
        conn, sample_plan(edited), {"Dutchfall11w": CASES["Dutchfall11w"]}, gone, alice
    )
    assert "[archive case] Dutchfall11w: missing at source 'sample'" in lines


def test_init_data_command(
    db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DB_NAME", db)
    cases = tmp_path / "cases"
    cases.mkdir()
    for id in CASES:
        _ = (cases / f"{id}.txt").write_text(f"The vignette of {id}.")
    world = tmp_path / "world.toml"
    _ = world.write_text('[source.sample]\npath = "cases"\n')
    main(["init-data", "alice", "--world", str(world), "--giftbag"])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "[person] alice: added"
    assert out[1].startswith("[archive model] qwen3-8b-awq@fake: created")
    assert any(line.startswith("[archive chunk.prompt] case: created") for line in out)
    assert any(line.startswith("[giftbag skeleton] plan (") for line in out)
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM catalog.family").fetchone() == (
            len(CASES),
        )

    main(["init-data", "alice", "--vignettes", str(cases)])
    out = capsys.readouterr().out.splitlines()
    assert not [line for line in out if line.startswith("[person]")]
    with connect(db) as conn:
        alice = People(conn).find("alice")
    assert alice is not None and (alice.name, alice.roles) == ("alice", frozenset())
    assert {
        line.split(": ")[1].split(" ")[0] for line in out if line.startswith("[archive")
    } == {"validated"}
    with pytest.raises(SystemExit, match="a directory of <id>.txt files"):
        main(["init-data", "alice", "--vignettes", str(SAMPLE / "cases.toml")])
    with pytest.raises(SystemExit, match="none of the sample's 99 cases"):
        main(["init-data", "alice", "--vignettes", str(SAMPLE)])
    for neither_or_both in ([], ["--world", str(world), "--vignettes", str(cases)]):
        with pytest.raises(SystemExit):
            main(["init-data", "alice", *neither_or_both])

    main(["init-data", "alice", "--world", str(SAMPLE_WORLD)])
    sample = Inventory.load(SAMPLE_WORLD)
    assert [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("[world endpoint]")
    ] == [
        f"[world endpoint] {name}: {e.url} serves engine.local {name} "
        + e.engine.removeprefix("sha256:")[:6]
        for name, e in sample.endpoints.items()
    ]

    world = ["--world", str(WORLD), "--factors", str(WORLD.parent / "factors.toml")]
    main(["init-data", "alice", *world])
    real = Inventory.load(WORLD)
    out = capsys.readouterr().out.splitlines()
    for name in real.endpoints:
        assert any(
            line.startswith(f"[archive engine.local] {name}: created") for line in out
        )
    assert [line for line in out if line.startswith("[world endpoint]")] == [
        f"[world endpoint] {name}: {e.url} serves engine.local {name} "
        + e.engine.removeprefix("sha256:")[:6]
        for name, e in real.endpoints.items()
    ]


def test_wipe_data_deletes_and_unshares_and_init_data_gives_it_back(
    db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DB_NAME", db)
    cases = tmp_path / "cases"
    cases.mkdir()
    for id in CASES:
        _ = (cases / f"{id}.txt").write_text(f"The vignette of {id}.")

    def run(*argv: str) -> list[str]:
        main(list(argv))
        return capsys.readouterr().out.splitlines()

    def rows() -> tuple[int, int]:
        with connect(db) as conn:
            threads = conn.execute("SELECT count(*) FROM catalog.thread").fetchone()
            entries = conn.execute("SELECT count(*) FROM catalog.entry").fetchone()
        assert threads is not None and entries is not None
        return threads[0], entries[0]

    init = ["init-data", "alice", "--vignettes", str(cases), "--giftbag"]
    seeded = run(*init)
    gifts = sum(line.startswith("[giftbag ") for line in seeded)
    shares = int(next(line for line in seeded if line.startswith("[share]")).split()[1])
    threads, entries = rows()
    _ = run(*init)
    assert rows() == (threads, entries)

    wiped = run("wipe-data", "alice")
    assert sum(int(line.split("deleted ")[1].split(",")[0]) for line in wiped) == gifts
    assert sum(int(line.split("unshared ")[1]) for line in wiped) == shares
    assert rows() == (threads, entries + gifts + shares)
    with connect(db) as conn:
        catalog = Catalog(conn)
        alice = People(conn).find("alice")
        assert alice is not None
        mine = catalog.involving(alice.id)
        assert all(
            catalog.about(s).deleted for s in mine if catalog.about(s).owner == alice.id
        )
        assert not [s for s in mine if alice.id in catalog.about(s).collaborators]
    assert run("wipe-data", "alice") == ["[wipe] alice: nothing to delete or unshare"]
    assert rows() == (threads, entries + gifts + shares)

    # Each later run writes an entry per gift and per share, and no thread.
    cost = gifts + shares
    for cycle in (1, 2):
        again = run(*init)
        assert sum(line.endswith(": restored") for line in again) == gifts
        assert not [line for line in again if ": created" in line or "forked" in line]
        assert rows() == (threads, entries + 2 * cycle * cost)
        _ = run("wipe-data", "alice")
        assert rows() == (threads, entries + (2 * cycle + 1) * cost)

    with pytest.raises(SystemExit, match="'archive' holds the sample data"):
        main(["wipe-data", "archive"])
    with pytest.raises(SystemExit, match="no person with login 'nobody'"):
        main(["wipe-data", "nobody"])


def test_the_sample_world_holds_the_vignettes_at_7893656() -> None:
    source = Inventory.load(SAMPLE_WORLD).source("sample")
    assert source.sensitive
    assert source.ids() == sorted(CASES)
    for id in source.ids():
        _ = source.fetch(id).decode()
    listing = "".join(
        f"{c.vignette.id} {c.vignette.fingerprint.hex}\n" for c in source.cases()
    )
    assert (
        Fingerprint.of(listing.encode()).hex
        == "ce41d6b218347d2b792c86160969f937c6926c8bff255b8644c7159402d7028c"
    )
