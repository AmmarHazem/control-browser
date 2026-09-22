from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agent.loop import RunLoop
from app.agent.run import Autonomy, Run, RunSpec, Vision
from app.browser.pool import SessionPool
from app.browser.session import SessionLimits
from app.config import Settings
from app.policy.limits import clamp_limit, clamp_seconds
from app.policy.network import DomainPolicy
from app.storage.repository import RunRecord, RunRepository
from app.storage.screenshots import ScreenshotStore

logger = logging.getLogger(__name__)


class UnknownSessionError(Exception):
    pass


class RunManager:
    def __init__(
        self,
        settings: Settings,
        pool: SessionPool,
        repository: RunRepository,
        screenshots: ScreenshotStore,
        search_client: Any = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._repository = repository
        self._screenshots = screenshots
        self._search = search_client
        self._runs: dict[str, Run] = {}

    # ---- request -> spec -------------------------------------------------

    def build_spec(self, payload: dict[str, Any], *, tenant_id: str | None = None) -> RunSpec:
        settings = self._settings
        return RunSpec(
            task=payload["task"],
            output_schema=payload.get("output_schema"),
            session_id=payload.get("session_id"),
            autonomy=Autonomy(payload.get("autonomy") or Autonomy.AUTONOMOUS),
            vision=Vision(payload.get("vision") or Vision.AUTO),
            allowed_domains=list(payload.get("allowed_domains") or []),
            blocked_domains=list(payload.get("blocked_domains") or []),
            risky_url_patterns=list(payload.get("risky_url_patterns") or []),
            store_screenshots=bool(payload.get("store_screenshots", False)),
            max_steps=clamp_limit(
                payload.get("max_steps"),
                default=settings.run_max_steps,
                cap=settings.run_max_steps_cap,
            ),
            timeout_seconds=clamp_seconds(
                payload.get("timeout_seconds"),
                default=settings.run_timeout_seconds,
                cap=settings.run_timeout_cap_seconds,
            ),
            confirmation_timeout_seconds=clamp_seconds(
                payload.get("confirmation_timeout_seconds"),
                default=settings.confirmation_timeout_seconds,
                cap=settings.confirmation_timeout_cap_seconds,
            ),
            snapshot_token_budget=clamp_limit(
                payload.get("snapshot_token_budget"),
                default=settings.snapshot_token_budget,
                cap=settings.snapshot_token_cap,
            ),
            text_token_budget=clamp_limit(
                payload.get("text_token_budget"),
                default=settings.text_token_budget,
                cap=settings.text_token_cap,
            ),
            model=payload.get("model") or settings.llm_model,
            base_url=payload.get("base_url") or settings.llm_base_url,
            api_key=payload.get("api_key") or settings.llm_api_key,
            tenant_id=tenant_id,
        )

    # ---- lifecycle -------------------------------------------------------

    async def start(self, spec: RunSpec) -> Run:
        settings = self._settings
        limits = SessionLimits(
            snapshot_token_budget=spec.snapshot_token_budget,
            text_token_budget=spec.text_token_budget,
            action_timeout_seconds=settings.action_timeout_seconds,
        )

        if spec.session_id:
            session = self._pool.get(spec.session_id)
            if session is None:
                raise UnknownSessionError(f"unknown session {spec.session_id}")
            session.limits = limits
            session.risky_url_patterns = [
                *session.risky_url_patterns,
                *spec.risky_url_patterns,
            ]
        else:
            session = await self._pool.open(
                domain_policy=DomainPolicy(
                    allowed=spec.allowed_domains,
                    blocked=spec.blocked_domains,
                    operator_blocked=list(settings.blocked_domains),
                ),
                risky_url_patterns=spec.risky_url_patterns,
                limits=limits,
            )

        run = Run(spec, self._repository)
        run.record.session_id = session.id
        await run.persist()
        self._runs[run.id] = run

        loop = RunLoop(
            run,
            session,
            search_client=self._search,
            screenshots=self._screenshots,
            stuck_threshold=settings.stuck_failure_threshold,
        )
        run.task_handle = asyncio.create_task(self._execute(run, loop, session.id))
        return run

    async def _execute(self, run: Run, loop: RunLoop, session_id: str) -> None:
        try:
            await loop.execute()
        finally:
            if run.owns_session:
                await self._pool.close(session_id)
            logger.info(
                "run finished",
                extra={"run_id": run.id, "outcome": run.record.outcome, "steps": run.record.steps},
            )

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    async def record(self, run_id: str) -> RunRecord | None:
        run = self._runs.get(run_id)
        if run is not None:
            return run.record
        return await self._repository.get(run_id)

    async def list_recent(self, limit: int = 25) -> list[RunRecord]:
        return await self._repository.list_recent(limit)

    async def abort(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.finished:
            return False
        run.abort()
        return True

    async def confirm(self, run_id: str, decision: str, note: str | None) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return False
        return run.resolve_confirmation(decision, note)

    async def shutdown(self) -> None:
        for run in list(self._runs.values()):
            if run.task_handle and not run.task_handle.done():
                run.abort()
                run.task_handle.cancel()
                try:
                    await run.task_handle
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
