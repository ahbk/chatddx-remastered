import psycopg
import pytest

from chatddx.cli import main
from chatddx.core import settings
from chatddx.identity import Role
from chatddx.store import People


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
        "would apply 0004-t0-identity",
        "would apply 0005-t1-identity-grants",
        "would apply 0006-t0-identity-auth",
        "would apply 0007-t1-identity-auth-grants",
        "would apply 0009-t0-catalog",
        "would apply 0010-t1-catalog-grants",
        "would apply 0012-t0-catalog-families",
        "would apply 0015-t0-catalog-based-on",
        "would apply 0023-t0-catalog-languages",
        "would apply 0025-t0-catalog-binding-fingerprint",
        "applied 0001-t0-tables",
        "applied 0002-t1-grants",
        "applied 0004-t0-identity",
        "applied 0005-t1-identity-grants",
        "applied 0006-t0-identity-auth",
        "applied 0007-t1-identity-auth-grants",
        "applied 0009-t0-catalog",
        "applied 0010-t1-catalog-grants",
        "applied 0012-t0-catalog-families",
        "applied 0015-t0-catalog-based-on",
        "applied 0023-t0-catalog-languages",
        "applied 0025-t0-catalog-binding-fingerprint",
        "nothing to apply up to tier 1",
        "applied 0003-t2-integrity",
        "applied 0008-t2-identity-checks",
        "applied 0011-t2-catalog-checks",
        "applied 0013-t2-catalog-families",
        "applied 0014-t2-catalog-kinds",
        "applied 0016-t2-catalog-based-on",
        "applied 0017-t2-catalog-language",
        "applied 0018-t2-catalog-bindings",
        "applied 0019-t2-catalog-name-removal",
        "applied 0020-t2-catalog-translations",
        "applied 0021-t2-catalog-tools",
        "applied 0022-t2-catalog-binding-source",
        "applied 0024-t2-catalog-languages",
        "applied 0026-t2-catalog-binding-fingerprint",
    ]
    with pytest.raises(SystemExit):
        main(["migrate", "--tier", "3"])


def test_person_commands(
    db: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DB_NAME", db)
    main(["person", "add", "alice", "Alice", "--role", "admin", "--role", "ops"])
    answers = iter(["secret", "secret", "secret", "typo"])

    def getpass(_prompt: str) -> str:
        return next(answers)

    monkeypatch.setattr("getpass.getpass", getpass)
    main(["person", "password", "alice"])
    with pytest.raises(SystemExit, match="don't match"):
        main(["person", "password", "alice"])
    assert capsys.readouterr().out.splitlines() == [
        "added alice (id 1)",
        "password set for alice",
    ]
    with psycopg.connect(settings.database(), dbname=db) as conn:
        person = People(conn).authenticate("alice", "secret")
    assert person is not None and person.roles == {Role.ADMIN, Role.OPS}
