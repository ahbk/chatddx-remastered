from collections import Counter
from pathlib import Path

import pytest

from chatddx.catalog import Subject
from chatddx.cli import main
from chatddx.core.rig import rig
from chatddx.facts.facts import Facts
from chatddx.inventory.sources import MemorySource
from chatddx.seed import Plan, load_cases, plan_factors, seed
from chatddx.seed.plan import SAMPLE
from chatddx.store import Catalog, People
from chatddx.store.store import Connection
from chatddx.store.test.conftest import connect

CASES = load_cases(SAMPLE / "cases.toml")


def sample_plan(factors: Path = SAMPLE / "factors.toml") -> Plan:
    facts = Facts.load(SAMPLE / "facts.toml")
    return plan_factors(factors, facts, rig(), root=SAMPLE)


def vignettes(**changed: bytes) -> MemorySource:
    texts = {id: f"The vignette of {id}.".encode() for id in CASES}
    return MemorySource(name="sample", vignettes={**texts, **changed})


def tally(lines: list[str]) -> Counter[str]:
    def what(line: str) -> str:
        prefix = line[1 : line.index("]")].split(" ")[0]
        if prefix in ("skipped", "share"):
            return prefix
        return f"{prefix} {line.split(': ', 1)[1].split(' ')[0]}"

    return Counter(what(line) for line in lines)


def test_init_data_seeds_the_archive(conn: Connection) -> None:
    alice = People(conn).add("alice", "Alice")
    plan = sample_plan()
    lines = seed(conn, plan, CASES, vignettes(), alice)
    threads = len(plan.records) + len(CASES)
    assert tally(lines) == Counter(
        {"archive created": threads + len(CASES), "skipped": 4, "share": 1}
    )
    shared = threads + len(CASES)
    assert (
        lines[-1]
        == f"[share] {shared} of {shared} archive threads and families with alice"
    )

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

    again = seed(conn, sample_plan(), CASES, vignettes(), alice)
    assert {v for v in tally(again) if v.startswith("archive")} == {"archive validated"}
    assert again[-1].startswith("[share] 0 of ")


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
            )
        )
    ]
    assert len([line for line in lines if "updated" in line]) == 3
    assert any(
        line.startswith("[archive case] DutchFall10w: needs repair") for line in lines
    )
    forked = [line for line in lines if line.startswith("[giftbag")]
    assert forked and all(line.split(": ")[1].startswith("forked") for line in forked)
    assert not any(line.startswith("[giftbag expectation_schema") for line in forked)

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
    with pytest.raises(SystemExit, match="no person with login 'alice'"):
        main(["init-data", "alice", "--world", str(world)])
    main(["person", "add", "alice", "Alice"])
    _ = capsys.readouterr()
    main(["init-data", "alice", "--world", str(world), "--giftbag"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("[archive chunk.prompt] case: created")
    assert any(line.startswith("[giftbag skeleton] plan (") for line in out)
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM catalog.family").fetchone() == (
            len(CASES),
        )

    main(["init-data", "alice", "--vignettes", str(cases)])
    out = capsys.readouterr().out.splitlines()
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
