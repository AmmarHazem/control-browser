from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit


class HostRateLimiter:
    """Spaces navigations to the same host by a minimum interval."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._interval = max(0.0, min_interval_seconds)
        self._last: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, url: str) -> float:
        if self._interval <= 0:
            return 0.0
        host = (urlsplit(url).hostname or "").lower()
        async with self._lock:
            now = time.monotonic()
            previous = self._last.get(host)
            wait = 0.0 if previous is None else max(0.0, self._interval - (now - previous))
            self._last[host] = now + wait
        if wait > 0:
            await asyncio.sleep(wait)
        return wait
