from datetime import timedelta

import pytest
from psycopg import errors
from pydantic import ValidationError

from chatddx.core.identity import Role
from chatddx.store import People
from chatddx.store.store import Connection


def test_people(conn: Connection) -> None:
    people = People(conn)
    alice = people.add("alice", "Alice", [Role.CLINICIAN])
    bob = people.add("bob", "Bob")
    assert bob.id > alice.id
    assert people.find("alice") == alice
    assert people.find("nobody") is None
    assert people.get(bob.id) == bob
    alice = people.update(alice.id, name="Alice B", roles=[Role.CLINICIAN, Role.OPS])
    assert people.get(alice.id) == alice
    assert alice.roles == {Role.CLINICIAN, Role.OPS}
    with pytest.raises(LookupError):
        _ = people.update(999, name="other")
    with pytest.raises(ValidationError):
        _ = people.add("Carol", "Carol")
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        _ = people.add("alice", "Another Alice")


def test_passwords(conn: Connection) -> None:
    people = People(conn)
    alice = people.add("alice", "Alice")
    assert people.authenticate("alice", "secret") is None
    people.set_password(alice.id, "secret")
    assert people.authenticate("alice", "secret") == alice
    assert people.authenticate("alice", "wrong") is None
    assert people.authenticate("nobody", "secret") is None
    people.set_password(alice.id, "changed")
    assert people.authenticate("alice", "secret") is None
    assert people.authenticate("alice", "changed") == alice
    _ = people.update(alice.id, active=False)
    assert people.authenticate("alice", "changed") is None


def test_sessions(conn: Connection) -> None:
    people = People(conn)
    alice = people.add("alice", "Alice")
    token = people.open_session(alice.id)
    assert people.session(token) == alice
    assert people.session("guessed") is None
    people.close_session(token)
    assert people.session(token) is None

    stale = people.open_session(alice.id, ttl=timedelta(microseconds=1))
    conn.commit()
    assert people.session(stale) is None
    assert people.purge_sessions() == 1

    token = people.open_session(alice.id)
    _ = people.update(alice.id, active=False)
    assert people.session(token) is None
    _ = people.update(alice.id, active=True)
    assert people.session(token) is None


def test_session_tokens_are_not_stored(conn: Connection) -> None:
    people = People(conn)
    token = people.open_session(people.add("alice", "Alice").id)
    row = conn.execute("SELECT token_digest FROM identity.session").fetchone()
    assert row is not None and token not in row[0]


def test_database_guards_people(conn: Connection, admin: Connection) -> None:
    people = People(conn)
    alice = people.add("alice", "Alice")
    people.set_password(alice.id, "secret")
    conn.commit()
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        _ = conn.execute("DELETE FROM identity.person")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        _ = conn.execute("DELETE FROM identity.credential")
    with pytest.raises(errors.GeneratedAlways), conn.transaction():
        _ = conn.execute(
            "INSERT INTO identity.person (id, login, name) VALUES (99, 'bob', 'Bob')"
        )
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _ = conn.execute(
            "INSERT INTO identity.person (login, name, roles) VALUES ('bob', 'Bob', %s)",
            (["guest"],),
        )
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _ = conn.execute("UPDATE identity.person SET login = 'Alice'")
    with pytest.raises(errors.CheckViolation):
        _ = admin.execute(
            "INSERT INTO identity.session VALUES ('x', %s, now(), now())", (alice.id,)
        )


def test_reader_sees_people_but_not_secrets(conn: Connection) -> None:
    privileges = conn.execute(
        """
        SELECT
            has_table_privilege('chatddx_reader', 'identity.person', 'SELECT'),
            has_table_privilege('chatddx_reader', 'identity.person', 'INSERT'),
            has_table_privilege('chatddx_reader', 'identity.credential', 'SELECT'),
            has_table_privilege('chatddx_reader', 'identity.session', 'SELECT')
        """
    ).fetchone()
    assert privileges == (True, False, False, False)


def test_roles_match_the_database_check(admin: Connection) -> None:
    row = admin.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'identity.person'::regclass AND contype = 'c'
            AND pg_get_constraintdef(oid) LIKE '%%roles%%'
        """
    ).fetchone()
    assert row is not None
    assert all(f"'{r.value}'" in row[0] for r in Role)
    assert str(row[0]).count("'") == 2 * len(Role)
