import json
from collections.abc import Iterable
from typing import LiteralString
from uuid import UUID

import psycopg
from psycopg.rows import TupleRow

from chatddx.factors.base import Component, StructuralError, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.request import Compilation
from chatddx.ledger.ledger import (
    CanaryCall,
    Record,
    Run,
    RunFinished,
    RunItem,
    RunStarted,
    Score,
    ScoreFinished,
    ScoreItem,
    ScoreStarted,
)

Connection = psycopg.Connection[TupleRow]


class Store:
    def __init__(self, conn: Connection) -> None:
        self._conn: Connection = conn

    def add(self, registry: Registry, roots: Iterable[str]) -> list[str]:
        digests = registry.closure(roots)
        registry.check(digests)
        with self._conn.transaction(), self._conn.cursor() as cur:
            for d in digests:
                raw = registry.raw(d).decode()
                doc = json.loads(raw)
                _ = cur.execute(
                    """
                    INSERT INTO factor.component (digest, kind, v, canonical, doc)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT DO NOTHING
                    """,
                    (d, doc["kind"], doc["v"], raw, raw),
                )
                cur.executemany(
                    """
                    INSERT INTO factor.component_ref (src, path, dst, kinds)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (d, site.path, site.digest, list(site.kinds))
                        for site in registry.get(d).refs()
                    ],
                )
        return digests

    def get(self, digest: str) -> Component:
        row = self._conn.execute(
            "SELECT canonical FROM factor.component WHERE digest = %s", (digest,)
        ).fetchone()
        if row is None:
            raise StructuralError(f"{digest} is not in the store")
        return Registry().add_raw(digest, str(row[0]).encode())

    def load(self, roots: Iterable[str]) -> Registry:
        roots = list(roots)
        rows = self._conn.execute(
            """
            WITH RECURSIVE closure (digest) AS (
                SELECT unnest(%s::text[])
                UNION
                SELECT r.dst FROM factor.component_ref r
                JOIN closure c ON r.src = c.digest
            )
            SELECT c.digest, c.canonical
            FROM closure JOIN factor.component c USING (digest)
            """,
            (roots,),
        ).fetchall()
        registry = Registry()
        for digest, canonical in rows:
            _ = registry.add_raw(str(digest), str(canonical).encode())
        if missing := [r for r in roots if r not in registry]:
            raise StructuralError(f"not in the store: {missing}")
        return registry

    def append(self, *records: Record) -> None:
        with self._conn.transaction(), self._conn.cursor() as cur:
            for record in records:
                payload = record.canonical.decode()
                match record:
                    case RunStarted() | RunFinished():
                        trial = record.trial if isinstance(record, RunStarted) else None
                        _ = cur.execute(
                            """
                            INSERT INTO ledger.run_stage (run, stage, trial, payload, doc)
                            VALUES (%s, %s, %s, %s, %s::jsonb)
                            """,
                            (record.run, record.stage, trial, payload, payload),
                        )
                    case RunItem():
                        _ = cur.execute(
                            """
                            INSERT INTO ledger.run_item (run, "case", replicate, payload, doc)
                            VALUES (%s, %s, %s, %s, %s::jsonb)
                            """,
                            (
                                record.run,
                                record.key.case,
                                record.key.replicate,
                                payload,
                                payload,
                            ),
                        )
                    case CanaryCall():
                        _ = cur.execute(
                            """
                            INSERT INTO ledger.canary_call (run, phase, canary, payload, doc)
                            VALUES (%s, %s, %s, %s, %s::jsonb)
                            """,
                            (record.run, record.phase, record.canary, payload, payload),
                        )
                    case ScoreStarted():
                        _ = cur.execute(
                            """
                            INSERT INTO ledger.score_stage (score, stage, run, run_stage, scoring, payload, doc)
                            VALUES (%s, %s, %s, 'started', %s, %s, %s::jsonb)
                            """,
                            (
                                record.score,
                                record.stage,
                                record.run,
                                record.scoring,
                                payload,
                                payload,
                            ),
                        )
                    case ScoreFinished():
                        _ = cur.execute(
                            """
                            INSERT INTO ledger.score_stage (score, stage, payload, doc)
                            VALUES (%s, %s, %s, %s::jsonb)
                            """,
                            (record.score, record.stage, payload, payload),
                        )
                    case ScoreItem():
                        _ = cur.execute(
                            """
                            INSERT INTO ledger.score_item (score, "case", replicate, view, payload, doc)
                            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                            """,
                            (
                                record.score,
                                record.key.case,
                                record.key.replicate,
                                record.view,
                                payload,
                                payload,
                            ),
                        )
                    case _:
                        raise TypeError(f"no table for {type(record).__name__}")

    def run(self, run: UUID) -> Run:
        stages = self._payloads(
            """
            SELECT payload FROM ledger.run_stage
            WHERE run = %s
            ORDER BY stage = 'finished'
            """,
            run,
        )
        if not stages:
            raise LookupError(f"run {run} is not in the store")
        return Run(
            stages=(
                RunStarted.parse(stages[0]),
                *(RunFinished.parse(p) for p in stages[1:]),
            ),
            items=tuple(
                RunItem.parse(p)
                for p in self._payloads(
                    "SELECT payload FROM ledger.run_item WHERE run = %s", run
                )
            ),
            canaries=tuple(
                CanaryCall.parse(p)
                for p in self._payloads(
                    "SELECT payload FROM ledger.canary_call WHERE run = %s", run
                )
            ),
        )

    def score(self, score: UUID) -> Score:
        stages = self._payloads(
            """
            SELECT payload FROM ledger.score_stage
            WHERE score = %s
            ORDER BY stage = 'finished'
            """,
            score,
        )
        if not stages:
            raise LookupError(f"score {score} is not in the store")
        return Score(
            stages=(
                ScoreStarted.parse(stages[0]),
                *(ScoreFinished.parse(p) for p in stages[1:]),
            ),
            items=tuple(
                ScoreItem.parse(p)
                for p in self._payloads(
                    "SELECT payload FROM ledger.score_item WHERE score = %s", score
                )
            ),
        )

    def compilations(self, skeleton: str) -> list[Compilation]:
        rows = self._conn.execute(
            """
            SELECT r.src FROM factor.component_ref r
            JOIN factor.component c ON c.digest = r.src
            WHERE c.kind = 'compilation' AND r.path = '/skeleton' AND r.dst = %s
            ORDER BY r.src
            """,
            (skeleton,),
        ).fetchall()
        return [resolve(self.get, str(src), Compilation) for (src,) in rows]

    def _payloads(self, query: LiteralString, key: object) -> list[str]:
        return [str(p) for (p,) in self._conn.execute(query, (key,))]
