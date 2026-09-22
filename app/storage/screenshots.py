from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class ScreenshotStore:
    """Per-Run screenshot files with a retention sweep."""

    def __init__(self, root: Path, retention_seconds: float) -> None:
        self._root = root
        self._retention = retention_seconds

    def path(self, run_id: str, step: int) -> Path:
        return self._root / run_id / f"{step:04d}.png"

    async def save(self, run_id: str, step: int, image: bytes) -> Path:
        target = self.path(run_id, step)
        await asyncio.to_thread(self._write, target, image)
        return target

    def _write(self, target: Path, image: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image)

    async def sweep(self) -> int:
        return await asyncio.to_thread(self._sweep)

    def _sweep(self) -> int:
        if not self._root.exists():
            return 0
        cutoff = time.time() - self._retention
        removed = 0
        for run_dir in self._root.iterdir():
            if not run_dir.is_dir():
                continue
            for file in run_dir.glob("*.png"):
                if file.stat().st_mtime < cutoff:
                    file.unlink(missing_ok=True)
                    removed += 1
            if not any(run_dir.iterdir()):
                run_dir.rmdir()
        return removed
