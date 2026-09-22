from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.manager import UnknownSessionError
from app.agent.run import Run
from app.policy.network import DomainPolicy

router = APIRouter()


class OpenSessionRequest(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    risky_url_patterns: list[str] = Field(default_factory=list)
    auto_snapshot: bool = True


class RunRequest(BaseModel):
    task: str
    output_schema: dict[str, Any] | None = None
    session_id: str | None = None
    autonomy: Literal["autonomous", "confirm_risky"] = "autonomous"
    vision: Literal["auto", "on", "off"] = "auto"
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    risky_url_patterns: list[str] = Field(default_factory=list)
    store_screenshots: bool = False
    max_steps: int | None = None
    timeout_seconds: float | None = None
    confirmation_timeout_seconds: float | None = None
    snapshot_token_budget: int | None = None
    text_token_budget: int | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ConfirmRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = None


def _container(request: Request):
    return request.app.state.container


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    container = _container(request)
    return {
        "status": "ok",
        "sessions": container.pool.count(),
        "max_sessions": container.settings.max_sessions,
        "search": container.search is not None,
    }


@router.post("/sessions", status_code=201)
async def open_session(payload: OpenSessionRequest, request: Request) -> dict[str, Any]:
    container = _container(request)
    policy = DomainPolicy(
        allowed=payload.allowed_domains,
        blocked=payload.blocked_domains,
        operator_blocked=list(container.settings.blocked_domains),
    )
    try:
        session = await container.pool.open(
            domain_policy=policy,
            risky_url_patterns=payload.risky_url_patterns,
            auto_snapshot=payload.auto_snapshot,
        )
    except Exception as exc:  # noqa: BLE001 - capacity and pressure are caller-visible
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"session_id": session.id}


@router.get("/sessions")
async def list_sessions(request: Request) -> dict[str, Any]:
    return {"sessions": _container(request).pool.snapshot_state()}


@router.delete("/sessions/{session_id}")
async def close_session(session_id: str, request: Request) -> dict[str, Any]:
    closed = await _container(request).pool.close(session_id)
    if not closed:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    return {"closed": True}


@router.post("/runs", status_code=202)
async def create_run(payload: RunRequest, request: Request) -> dict[str, Any]:
    container = _container(request)
    tenant_id = request.headers.get("x-tenant-id")
    spec = container.runs.build_spec(payload.model_dump(exclude_none=True), tenant_id=tenant_id)
    try:
        run = await container.runs.start(spec)
    except UnknownSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - capacity and pressure are caller-visible
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"run_id": run.id, "status": run.record.status}


@router.get("/runs")
async def list_runs(request: Request, limit: int = 25) -> dict[str, Any]:
    records = await _container(request).runs.list_recent(limit)
    return {"runs": [record.as_dict() for record in records]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    container = _container(request)
    record = await container.runs.record(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    payload = record.as_dict()
    live = container.runs.get(run_id)
    if live is not None:
        payload["confirmation"] = live.confirmation_view()
    return payload


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request) -> StreamingResponse:
    container = _container(request)
    run = container.runs.get(run_id)
    if run is None:
        events = await container.repository.events(run_id)
        if not events:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
        return StreamingResponse(_replay(events), media_type="text/event-stream")
    return StreamingResponse(_follow(run, request), media_type="text/event-stream")


async def _replay(events) -> Any:
    for event in events:
        yield _sse(event.as_dict())


async def _follow(run: Run, request: Request) -> Any:
    queue = run.subscribe()
    try:
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                if run.finished and queue.empty():
                    return
                yield ": keepalive\n\n"
                continue
            yield _sse(event.as_dict())
            if event.type.startswith("run.") and event.type != "run.started":
                return
    finally:
        run.unsubscribe(queue)


def _sse(payload: dict[str, Any]) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/runs/{run_id}/confirm")
async def confirm_run(run_id: str, payload: ConfirmRequest, request: Request) -> dict[str, Any]:
    ok = await _container(request).runs.confirm(run_id, payload.decision, payload.note)
    if not ok:
        raise HTTPException(status_code=409, detail="this run is not waiting for a confirmation")
    return {"accepted": True}


@router.post("/runs/{run_id}/abort")
async def abort_run(run_id: str, request: Request) -> dict[str, Any]:
    ok = await _container(request).runs.abort(run_id)
    if not ok:
        raise HTTPException(status_code=409, detail="this run is not running")
    return {"aborted": True}


@router.get("/runs/{run_id}/steps/{step}/screenshot")
async def get_screenshot(run_id: str, step: int, request: Request) -> FileResponse:
    path = _container(request).screenshots.path(run_id, step)
    if not path.exists():
        raise HTTPException(status_code=404, detail="no screenshot stored for this step")
    return FileResponse(path, media_type="image/png")
