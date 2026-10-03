from dataclasses import dataclass
from pathlib import Path
from typing import LiteralString, cast

import psycopg

MIGRATIONS = Path(__file__).parent / "migrations"

TOP_TIER = 2


@dataclass(frozen=True)
class Migration:
    name: str
    tier: int
    path: Path


def migrations() -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        _, tier, _ = path.stem.split("-", 2)
        found.append(Migration(path.stem, int(tier.removeprefix("t")), path))
    return found


def pending(conn: psycopg.Connection, tier: int = TOP_TIER) -> list[Migration]:
    applied: set[str] = set()
    row = conn.execute("SELECT to_regclass('public.migration')").fetchone()
    if row is not None and row[0] is not None:
        applied = {n for (n,) in conn.execute("SELECT name FROM public.migration")}
    return [m for m in migrations() if m.tier <= tier and m.name not in applied]


def migrate(conn: psycopg.Connection, tier: int = TOP_TIER) -> list[str]:
    with conn.transaction():
        _ = conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.migration (
                name text PRIMARY KEY,
                tier integer NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        todo = pending(conn, tier)
    done: list[str] = []
    for m in todo:
        with conn.transaction():
            _ = conn.execute(cast(LiteralString, m.path.read_text()))
            _ = conn.execute(
                "INSERT INTO public.migration (name, tier) VALUES (%s, %s)",
                (m.name, m.tier),
            )
        done.append(m.name)
    return done
