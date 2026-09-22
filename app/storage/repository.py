from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    status TEXT NOT NULL,
    outcome TEXT,
    result TEXT,
    error TEXT,
    sources TEXT NOT NULL DEFAULT '[]',
    session_id TEXT,
    tenant_id TEXT,
    steps INTEGER NOT NULL DEFAULT 0,
    request TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""


@dataclass
class RunRecord:
    id: str
    task: str
    status: str
    outcome: str | None = None
    result: Any = None
    error: str | None = None
    sources: list[str] = field(default_factory=list)
    session_id: str | None = None
    tenant_id: str | None = None
    steps: int = 0
    request: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "outcome": self.outcome,
            "result": self.result,
            "error": self.error,
            "sources": self.sources,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "steps": self.steps,
            "request": self.request,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


@dataclass
class TraceEvent:
    seq: int
    type: str
    payload: dict[str, Any]
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type, "at": self.at, **self.payload}


class RunRepository(Protocol):
    async def save(self, record: RunRecord) -> None: ...

    async def get(self, run_id: str) -> RunRecord | None: ...

    async def list_recent(self, limit: int) -> list[RunRecord]: ...

    async def append_event(self, run_id: str, event: TraceEvent) -> None: ...

    async def events(self, run_id: str) -> list[TraceEvent]: ...


class SqliteRunRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()
        self._lock = asyncio.Lock()

    def close(self) -> None:
        self._connection.close()

    async def _run(self, fn, *args):
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    async def save(self, record: RunRecord) -> None:
        await self._run(self._save, record)

    def _save(self, record: RunRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO runs (id, task, status, outcome, result, error, sources, session_id,
                              tenant_id, steps, request, created_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, outcome=excluded.outcome, result=excluded.result,
                error=excluded.error, sources=excluded.sources, session_id=excluded.session_id,
                steps=excluded.steps, finished_at=excluded.finished_at
            """,
            (
                record.id,
                record.task,
                record.status,
                record.outcome,
                json.dumps(record.result) if record.result is not None else None,
                record.error,
                json.dumps(record.sources),
                record.session_id,
                record.tenant_id,
                record.steps,
                json.dumps(record.request),
                record.created_at,
                record.finished_at,
            ),
        )
        self._connection.commit()

    async def get(self, run_id: str) -> RunRecord | None:
        return await self._run(self._get, run_id)

    def _get(self, run_id: str) -> RunRecord | None:
        row = self._connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _to_record(row) if row else None

    async def list_recent(self, limit: int) -> list[RunRecord]:
        return await self._run(self._list_recent, limit)

    def _list_recent(self, limit: int) -> list[RunRecord]:
        rows = self._connection.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_to_record(row) for row in rows]

    async def append_event(self, run_id: str, event: TraceEvent) -> None:
        await self._run(self._append_event, run_id, event)

    def _append_event(self, run_id: str, event: TraceEvent) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO events (run_id, seq, type, payload, at) VALUES (?, ?, ?, ?, ?)",
            (run_id, event.seq, event.type, json.dumps(event.payload), event.at),
        )
        self._connection.commit()

    async def events(self, run_id: str) -> list[TraceEvent]:
        return await self._run(self._events, run_id)

    def _events(self, run_id: str) -> list[TraceEvent]:
        rows = self._connection.execute(
            "SELECT seq, type, payload, at FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
        return [
            TraceEvent(seq=row["seq"], type=row["type"], payload=json.loads(row["payload"]), at=row["at"])
            for row in rows
        ]


def _to_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        task=row["task"],
        status=row["status"],
        outcome=row["outcome"],
        result=json.loads(row["result"]) if row["result"] else None,
        error=row["error"],
        sources=json.loads(row["sources"] or "[]"),
        session_id=row["session_id"],
        tenant_id=row["tenant_id"],
        steps=row["steps"],
        request=json.loads(row["request"] or "{}"),
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )
