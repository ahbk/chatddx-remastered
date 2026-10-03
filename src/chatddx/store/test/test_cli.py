import pytest

from chatddx.cli import main


def test_migrate_command(
    empty_db: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DB_NAME", empty_db)
    main(["migrate", "--tier", "1", "--dry-run"])
    main(["migrate", "--tier", "1"])
    main(["migrate", "--tier", "1"])
    main(["migrate"])
    assert capsys.readouterr().out.splitlines() == [
        "would apply 0001-t0-tables",
        "would apply 0002-t1-grants",
        "applied 0001-t0-tables",
        "applied 0002-t1-grants",
        "nothing to apply up to tier 1",
        "applied 0003-t2-integrity",
    ]
    with pytest.raises(SystemExit):
        main(["migrate", "--tier", "3"])
