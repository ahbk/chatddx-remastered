from pathlib import Path

import pytest
from chatddx.inventory.inventory import Inventory
from chatddx.inventory.sources import DirectorySource, MemorySource
from pydantic import ValidationError

from chatddx.factors.base import Fingerprint
from chatddx.factors.cases import CaseInput, SourceCase


def vignettes(root: Path) -> Path:
    directory = root / "vignettes"
    directory.mkdir()
    _ = (directory / "Dutchfall11w.txt").write_bytes(b"A 72-year-old man.\n")
    _ = (directory / "casesfromedn1.txt").write_bytes("60-årig kvinna.".encode())
    _ = (directory / "notes.md").write_text("not a vignette")
    return directory


def test_sources_are_declared_in_the_inventory(tmp_path: Path) -> None:
    directory = vignettes(tmp_path)
    path = tmp_path / "inventory.toml"
    _ = path.write_text('[source.sample]\npath = "vignettes"\n')
    source = Inventory.load(path).source("sample")
    assert isinstance(source, DirectorySource)
    assert (source.name, source.path, source.sensitive) == (
        "sample",
        directory.resolve(),
        True,
    )
    with pytest.raises(LookupError, match="no source 'other'"):
        _ = Inventory.load(path).source("other")

    _ = path.write_text('[source.sample]\npath = "vignettes"\nsensitive = false\n')
    assert not Inventory.load(path).source("sample").sensitive
    _ = path.write_text('[host.pelle]\naddress = "pelle.km"\n')
    with pytest.raises(ValueError, match="only 'source' tables"):
        _ = Inventory.load(path)
    _ = path.write_text('[source.sample]\nkind = "s3"\npath = "x"\n')
    with pytest.raises(ValidationError):
        _ = Inventory.load(path)


def test_directory_sources_list_fetch_and_make_cases(tmp_path: Path) -> None:
    source = DirectorySource(name="sample", path=vignettes(tmp_path))
    assert source.ids() == ["Dutchfall11w", "casesfromedn1"]
    assert source.fetch("Dutchfall11w") == b"A 72-year-old man.\n"
    with pytest.raises(LookupError, match="no case 'Missing'"):
        _ = source.fetch("Missing")
    for id in ("../inventory", "sub/case", ".."):
        with pytest.raises(LookupError, match="outside"):
            _ = source.fetch(id)
    assert source.cases() == [
        CaseInput(
            case=SourceCase(source="sample", id="Dutchfall11w"),
            vignette=Fingerprint.of(b"A 72-year-old man.\n"),
        ),
        CaseInput(
            case=SourceCase(source="sample", id="casesfromedn1"),
            vignette=Fingerprint.of("60-årig kvinna.".encode()),
        ),
    ]


def test_memory_sources_stand_in_for_tests() -> None:
    source = MemorySource(name="inline", vignettes={"c2": b"two", "c1": b"one"})
    assert source.ids() == ["c1", "c2"]
    assert source.fetch("c1") == b"one"
    assert source.sensitive
    with pytest.raises(LookupError, match="no case 'c3'"):
        _ = source.fetch("c3")
