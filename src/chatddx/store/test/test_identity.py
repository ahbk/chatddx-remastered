import pytest
from psycopg import errors

from chatddx.store.store import Connection


def test_people_are_mutable_but_not_deletable(conn: Connection) -> None:
    alice = conn.execute(
        "INSERT INTO identity.person (name) VALUES ('alice') RETURNING id"
    ).fetchone()
    bob = conn.execute(
        "INSERT INTO identity.person (name) VALUES ('bob') RETURNING id"
    ).fetchone()
    assert alice is not None and bob is not None
    assert bob[0] > alice[0]
    _ = conn.execute("UPDATE identity.person SET name = 'alice b' WHERE id = %s", alice)
    row = conn.execute("SELECT name FROM identity.person WHERE id = %s", alice)
    assert row.fetchone() == ("alice b",)
    conn.commit()
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        _ = conn.execute("DELETE FROM identity.person")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        _ = conn.execute("TRUNCATE identity.person")
    with pytest.raises(errors.GeneratedAlways), conn.transaction():
        _ = conn.execute("INSERT INTO identity.person (id, name) VALUES (99, 'carol')")


def test_reader_can_read_people(conn: Connection) -> None:
    privileges = conn.execute(
        """
        SELECT
            has_table_privilege('chatddx_reader', 'identity.person', 'SELECT'),
            has_table_privilege('chatddx_reader', 'identity.person', 'INSERT'),
            has_table_privilege('chatddx_writer', 'identity.person', 'DELETE')
        """
    ).fetchone()
    assert privileges == (True, False, False)
