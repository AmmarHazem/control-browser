from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import StrEnum

SUBMIT_INPUT_TYPES = {"submit", "image"}


class RiskSource(StrEnum):
    DECLARED = "declared"
    STRUCTURAL = "structural"
    URL_PATTERN = "url_pattern"


@dataclass(frozen=True)
class RiskFinding:
    source: RiskSource
    detail: str


def is_submit_control(
    *, tag: str, input_type: str | None, explicit_type: str | None, in_form: bool
) -> bool:
    if not in_form:
        return False
    tag = tag.lower()
    if tag == "button":
        return (explicit_type or "submit").lower() == "submit"
    if tag == "input":
        return (input_type or "").lower() in SUBMIT_INPUT_TYPES
    return False


def matches_risky_url(url: str, patterns: list[str]) -> bool:
    lowered = url.lower()
    return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in patterns)
