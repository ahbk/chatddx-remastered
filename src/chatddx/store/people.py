from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from chatddx.core import settings
from chatddx.core.identity import (
    Person,
    Role,
    hash_password,
    needs_rehash,
    new_token,
    token_digest,
    verify_password,
)

from .store import Connection

_COLUMNS = "p.id, p.login, p.name, p.roles, p.active"


def _person(row: tuple[Any, ...]) -> Person:
    id, login, name, roles, active = row
    return Person(
        id=id, login=login, name=name, roles=frozenset(map(Role, roles)), active=active
    )


class People:
    def __init__(self, conn: Connection) -> None:
        self._conn: Connection = conn

    def add(self, login: str, name: str, roles: Iterable[Role] = ()) -> Person:
        person = Person(id=0, login=login, name=name, roles=frozenset(roles))
        row = self._conn.execute(
            f"""
            INSERT INTO identity.person AS p (login, name, roles)
            VALUES (%s, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (person.login, person.name, sorted(person.roles)),
        ).fetchone()
        assert row is not None
        return _person(row)

    def get(self, id: int) -> Person:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM identity.person p WHERE p.id = %s", (id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no person with id {id}")
        return _person(row)

    def find(self, login: str) -> Person | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM identity.person p WHERE p.login = %s", (login,)
        ).fetchone()
        return None if row is None else _person(row)

    def update(
        self,
        id: int,
        *,
        name: str | None = None,
        roles: Iterable[Role] | None = None,
        active: bool | None = None,
    ) -> Person:
        with self._conn.transaction():
            row = self._conn.execute(
                f"""
                UPDATE identity.person AS p SET
                    name = coalesce(%s, p.name),
                    roles = coalesce(%s, p.roles),
                    active = coalesce(%s, p.active)
                WHERE p.id = %s
                RETURNING {_COLUMNS}
                """,
                (name, None if roles is None else sorted(set(roles)), active, id),
            ).fetchone()
            if row is None:
                raise LookupError(f"no person with id {id}")
            if active is False:
                _ = self._conn.execute(
                    "DELETE FROM identity.session WHERE person = %s", (id,)
                )
        return _person(row)

    def set_password(self, id: int, password: str) -> None:
        _ = self._conn.execute(
            """
            INSERT INTO identity.credential (person, hash) VALUES (%s, %s)
            ON CONFLICT (person) DO UPDATE SET hash = excluded.hash
            """,
            (id, hash_password(password)),
        )

    def authenticate(self, login: str, password: str) -> Person | None:
        row = self._conn.execute(
            f"""
            SELECT {_COLUMNS}, c.hash
            FROM identity.person p LEFT JOIN identity.credential c ON c.person = p.id
            WHERE p.login = %s
            """,
            (login,),
        ).fetchone()
        hash: str | None = None if row is None else row[-1]
        if not verify_password(hash, password) or row is None or hash is None:
            return None
        person = _person(row[:-1])
        if not person.active:
            return None
        if needs_rehash(hash):
            self.set_password(person.id, password)
        return person

    def open_session(self, id: int, ttl: timedelta = settings.SESSION_TTL) -> str:
        token = new_token()
        _ = self._conn.execute(
            """
            INSERT INTO identity.session (token_digest, person, expires)
            VALUES (%s, %s, now() + %s)
            """,
            (token_digest(token), id, ttl),
        )
        return token

    def session(self, token: str) -> Person | None:
        row = self._conn.execute(
            f"""
            SELECT {_COLUMNS}
            FROM identity.session s JOIN identity.person p ON p.id = s.person
            WHERE s.token_digest = %s AND s.expires > now() AND p.active
            """,
            (token_digest(token),),
        ).fetchone()
        return None if row is None else _person(row)

    def close_session(self, token: str) -> None:
        _ = self._conn.execute(
            "DELETE FROM identity.session WHERE token_digest = %s",
            (token_digest(token),),
        )

    def purge_sessions(self) -> int:
        cur = self._conn.execute("DELETE FROM identity.session WHERE expires <= now()")
        return cur.rowcount
