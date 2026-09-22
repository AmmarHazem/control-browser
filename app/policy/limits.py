from __future__ import annotations


def clamp_limit(requested: int | None, *, default: int, cap: int) -> int:
    if requested is None or requested <= 0:
        return min(default, cap)
    return min(requested, cap)


def clamp_seconds(requested: float | None, *, default: float, cap: float) -> float:
    if requested is None or requested <= 0:
        return min(default, cap)
    return min(requested, cap)
