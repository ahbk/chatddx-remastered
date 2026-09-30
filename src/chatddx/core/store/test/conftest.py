from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from chatddx.core import settings
from chatddx.core.store import migrate
from chatddx.core.store.store import Connection


def create_database(template: str | None = None) -> str:
    name = f"chatddx_test_{uuid4().hex[:12]}"
    create = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    if template is not None:
        create += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
    with psycopg.connect(settings.database(), autocommit=True) as admin:
        _ = admin.execute(create)
    return name


def drop_database(name: str) -> None:
    with psycopg.connect(settings.database(), autocommit=True) as admin:
        _ = admin.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )


def connect(name: str) -> Connection:
    return psycopg.connect(settings.database(), dbname=name)


@pytest.fixture(scope="session")
def migrated() -> Iterator[str]:
    name = create_database()
    try:
        with connect(name) as conn:
            _ = migrate(conn)
        yield name
    finally:
        drop_database(name)


@pytest.fixture
def conn(migrated: str) -> Iterator[Connection]:
    name = create_database(template=migrated)
    try:
        with connect(name) as c:
            yield c
    finally:
        drop_database(name)


@pytest.fixture
def empty() -> Iterator[Connection]:
    name = create_database()
    try:
        with connect(name) as c:
            yield c
    finally:
        drop_database(name)
