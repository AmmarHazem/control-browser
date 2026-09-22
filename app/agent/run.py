from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.storage.repository import RunRecord, TraceEvent

EVENT_OBSERVATION_LIMIT = 2000


class Autonomy(StrEnum):
    AUTONOMOUS = "autonomous"
    CONFIRM_RISKY = "confirm_risky"


class Vision(StrEnum):
    AUTO = "auto"
    ON = "on"
    OFF = "off"


class Outcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    BLOCKED = "blocked"


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    FINISHED = "finished"


class RunAborted(Exception):
    """The caller aborted the Run, or a Confirmation went unanswered."""


class RunTimedOut(Exception):
    """The Run exceeded its wall-clock limit."""


@dataclass
class RunSpec:
    task: str
    output_schema: dict[str, Any] | None = None
    session_id: str | None = None
    autonomy: Autonomy = Autonomy.AUTONOMOUS
    vision: Vision = Vision.AUTO
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    risky_url_patterns: list[str] = field(default_factory=list)
    store_screenshots: bool = False
    max_steps: int = 50
    timeout_seconds: float = 600.0
    confirmation_timeout_seconds: float = 600.0
    snapshot_token_budget: int = 8000
    text_token_budget: int = 16000
    model: str = "gpt-4o-mini"
    base_url: str | None = None
    api_key: str = ""
    tenant_id: str | None = None

    def redacted(self) -> dict[str, Any]:
        payload = {
            "autonomy": str(self.autonomy),
            "vision": str(self.vision),
            "max_steps": self.max_steps,
            "timeout_seconds": self.timeout_seconds,
            "confirmation_timeout_seconds": self.confirmation_timeout_seconds,
            "snapshot_token_budget": self.snapshot_token_budget,
            "text_token_budget": self.text_token_budget,
            "store_screenshots": self.store_screenshots,
            "allowed_domains": self.allowed_domains,
            "blocked_domains": self.blocked_domains,
            "risky_url_patterns": self.risky_url_patterns,
            "model": self.model,
            "output_schema": self.output_schema,
        }
        return payload


@dataclass
class PendingConfirmation:
    step: int
    action: str
    purpose: str
    source: str
    snapshot: str
    future: asyncio.Future[tuple[str, str | None]]


class Run:
    def __init__(self, spec: RunSpec, repository: Any) -> None:
        self.id = uuid.uuid4().hex
        self.spec = spec
        self.record = RunRecord(
            id=self.id,
            task=spec.task,
            status=str(RunStatus.RUNNING),
            session_id=spec.session_id,
            tenant_id=spec.tenant_id,
            request=spec.redacted(),
        )
        self.events: list[TraceEvent] = []
        self.pending: PendingConfirmation | None = None
        self.task_handle: asyncio.Task | None = None
        self.owns_session = spec.session_id is None

        self._repository = repository
        self._subscribers: set[asyncio.Queue] = set()
        self._seq = 0
        self._finished = asyncio.Event()
        self._aborted = False

    # ---- trace -----------------------------------------------------------

    async def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> TraceEvent:
        self._seq += 1
        event = TraceEvent(seq=self._seq, type=event_type, payload=payload or {}, at=time.time())
        self.events.append(event)
        await self._repository.append_event(self.id, event)
        for queue in list(self._subscribers):
            queue.put_nowait(event)
        return event

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            queue.put_nowait(event)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    # ---- state -----------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.record.status == str(RunStatus.FINISHED)

    @property
    def aborted(self) -> bool:
        return self._aborted

    async def wait_finished(self) -> None:
        await self._finished.wait()

    async def persist(self) -> None:
        await self._repository.save(self.record)

    async def mark_finished(
        self, outcome: Outcome, *, result: Any = None, error: str | None = None
    ) -> None:
        self.record.status = str(RunStatus.FINISHED)
        self.record.outcome = str(outcome)
        self.record.result = result
        self.record.error = error
        self.record.finished_at = time.time()
        await self.persist()
        self._finished.set()

    def abort(self) -> None:
        self._aborted = True
        if self.pending and not self.pending.future.done():
            self.pending.future.set_result(("abort", None))

    def resolve_confirmation(self, decision: str, note: str | None) -> bool:
        if self.pending is None or self.pending.future.done():
            return False
        self.pending.future.set_result((decision, note))
        return True

    def confirmation_view(self) -> dict[str, Any] | None:
        if self.pending is None:
            return None
        return {
            "step": self.pending.step,
            "action": self.pending.action,
            "purpose": self.pending.purpose,
            "source": self.pending.source,
            "snapshot": self.pending.snapshot[:EVENT_OBSERVATION_LIMIT],
        }
