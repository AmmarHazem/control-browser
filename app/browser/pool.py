from __future__ import annotations

import asyncio
import logging
import uuid
from enum import StrEnum

import psutil
from playwright.async_api import Browser, Playwright, async_playwright

from app.browser.session import Session, SessionLimits
from app.config import Settings
from app.policy.network import DomainPolicy
from app.policy.ratelimit import HostRateLimiter

logger = logging.getLogger(__name__)


class MemoryPressure(StrEnum):
    NORMAL = "normal"
    CAUTION = "caution"
    HIGH = "high"
    CRITICAL = "critical"


class PoolExhaustedError(Exception):
    pass


class MemoryPressureError(Exception):
    pass


class SessionPool:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._janitor: asyncio.Task | None = None
        self._rate_limiter = HostRateLimiter(settings.navigation_min_interval_seconds)

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._settings.browser_headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._janitor = asyncio.create_task(self._janitor_loop())

    async def shutdown(self) -> None:
        if self._janitor:
            self._janitor.cancel()
            try:
                await self._janitor
            except asyncio.CancelledError:
                pass
            self._janitor = None
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    # ---- sessions --------------------------------------------------------

    async def open(
        self,
        *,
        domain_policy: DomainPolicy | None = None,
        risky_url_patterns: list[str] | None = None,
        limits: SessionLimits | None = None,
        auto_snapshot: bool = True,
    ) -> Session:
        if self._browser is None:
            raise RuntimeError("session pool is not started")

        async with self._lock:
            self._reap_closed()
            if len(self._sessions) >= self._settings.max_sessions:
                raise PoolExhaustedError(
                    f"all {self._settings.max_sessions} session slots are in use"
                )
            pressure = self._pressure()
            if pressure is MemoryPressure.CRITICAL:
                await self._reclaim_idle()
                if self._pressure() is MemoryPressure.CRITICAL:
                    raise MemoryPressureError("memory pressure is critical; try again shortly")
            elif pressure is MemoryPressure.HIGH:
                raise MemoryPressureError("memory pressure is high; try again shortly")
            elif pressure is MemoryPressure.CAUTION:
                await asyncio.sleep(0.25)

        settings = self._settings
        context = await self._browser.new_context(
            locale=settings.browser_locale,
            timezone_id=settings.browser_timezone,
            viewport={
                "width": settings.browser_viewport_width,
                "height": settings.browser_viewport_height,
            },
            user_agent=settings.browser_user_agent,
            accept_downloads=False,
        )
        session = Session(
            session_id=uuid.uuid4().hex,
            context=context,
            limits=limits
            or SessionLimits(
                snapshot_token_budget=settings.snapshot_token_budget,
                text_token_budget=settings.text_token_budget,
                action_timeout_seconds=settings.action_timeout_seconds,
            ),
            domain_policy=domain_policy
            or DomainPolicy(operator_blocked=list(settings.blocked_domains)),
            risky_url_patterns=risky_url_patterns,
            rate_limiter=self._rate_limiter,
            allow_private_addresses=settings.allow_private_addresses,
            auto_snapshot=auto_snapshot,
        )
        async with self._lock:
            self._sessions[session.id] = session
        logger.info("session opened", extra={"session_id": session.id})
        return session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session and session.closed:
            self._sessions.pop(session_id, None)
            return None
        return session

    async def close(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await session.close()
        logger.info("session closed", extra={"session_id": session_id})
        return True

    def count(self) -> int:
        return len(self._sessions)

    def snapshot_state(self) -> list[dict]:
        return [
            {
                "session_id": session.id,
                "idle_seconds": round(session.idle_seconds(), 1),
                "age_seconds": round(session.age_seconds(), 1),
            }
            for session in self._sessions.values()
        ]

    # ---- housekeeping ----------------------------------------------------

    def _reap_closed(self) -> None:
        for session_id, session in list(self._sessions.items()):
            if session.closed:
                self._sessions.pop(session_id, None)

    def _pressure(self) -> MemoryPressure:
        try:
            percent = psutil.virtual_memory().percent
        except Exception:
            return MemoryPressure.NORMAL
        settings = self._settings
        if percent >= settings.memory_critical_percent:
            return MemoryPressure.CRITICAL
        if percent >= settings.memory_high_percent:
            return MemoryPressure.HIGH
        if percent >= settings.memory_caution_percent:
            return MemoryPressure.CAUTION
        return MemoryPressure.NORMAL

    async def _reclaim_idle(self) -> None:
        victims = sorted(self._sessions.values(), key=lambda s: s.idle_seconds(), reverse=True)
        for session in victims[: max(1, len(victims) // 4)]:
            if session.idle_suspended:
                continue
            await session.close()
            self._sessions.pop(session.id, None)
            logger.info("session reclaimed under memory pressure", extra={"session_id": session.id})

    async def _janitor_loop(self) -> None:
        settings = self._settings
        while True:
            await asyncio.sleep(settings.janitor_interval_seconds)
            for session_id, session in list(self._sessions.items()):
                expired = session.age_seconds() > settings.session_max_lifetime_seconds
                idle = session.idle_seconds() > settings.session_idle_seconds
                if session.closed or expired or idle:
                    reason = "closed" if session.closed else ("expired" if expired else "idle")
                    await session.close()
                    self._sessions.pop(session_id, None)
                    logger.info(
                        "session evicted", extra={"session_id": session_id, "reason": reason}
                    )
