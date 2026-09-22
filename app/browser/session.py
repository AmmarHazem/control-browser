from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import BrowserContext, Frame, Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api._generated import Dialog

from app.browser.snapshot import (
    COLLECT_JS,
    RenderedSnapshot,
    SnapNode,
    iter_nodes,
    parse_nodes,
    render_snapshot,
)
from app.browser.text import html_to_markdown
from app.policy.network import DomainPolicy, NetworkPolicyError, check_url
from app.policy.ratelimit import HostRateLimiter
from app.policy.risky import RiskFinding, RiskSource, is_submit_control, matches_risky_url

STALE_REF_HINT = "take a new snapshot and use a ref from it"

RiskGate = Callable[[RiskFinding, str, dict[str, Any]], Awaitable[None]]


class ActionRejected(Exception):
    """A risk gate refused the Action; the message is fed back to the caller."""


@dataclass
class ActionResult:
    ok: bool
    detail: str = ""
    error: str | None = None
    observation: str | None = None
    screenshot: bytes | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.detail:
            payload["detail"] = self.detail
        if self.error:
            payload["error"] = self.error
        if self.observation is not None:
            payload["observation"] = self.observation
        payload.update(self.data)
        return payload


@dataclass
class RefTarget:
    frame: Frame
    ref: str


@dataclass
class SessionLimits:
    snapshot_token_budget: int = 8000
    text_token_budget: int = 16000
    action_timeout_seconds: float = 30.0


class Session:
    def __init__(
        self,
        session_id: str,
        context: BrowserContext,
        *,
        limits: SessionLimits | None = None,
        domain_policy: DomainPolicy | None = None,
        risky_url_patterns: list[str] | None = None,
        rate_limiter: HostRateLimiter | None = None,
        allow_private_addresses: bool = False,
        auto_snapshot: bool = True,
    ) -> None:
        self.id = session_id
        self.context = context
        self.limits = limits or SessionLimits()
        self.domain_policy = domain_policy or DomainPolicy()
        self.risky_url_patterns = risky_url_patterns or []
        self.rate_limiter = rate_limiter or HostRateLimiter(0.0)
        self.allow_private_addresses = allow_private_addresses
        self.auto_snapshot = auto_snapshot
        self.risk_gate: RiskGate | None = None

        self.created_at = time.monotonic()
        self.last_used = time.monotonic()
        self.idle_suspended = False
        self.visited_urls: list[str] = []

        self._pages: list[Page] = []
        self._active = 0
        self._refs: dict[str, RefTarget] = {}
        self._next_ref = 1
        self._known_refs: set[str] = set()
        self._prev_signatures: Counter[str] | None = None
        self._last_snapshot: str = ""
        self._pending_dialogs: list[str] = []
        self._closed = False

        context.on("page", self._on_page)
        for page in context.pages:
            self._attach(page)

    # ---- lifecycle -------------------------------------------------------

    def _on_page(self, page: Page) -> None:
        self._attach(page)

    def _attach(self, page: Page) -> None:
        if page in self._pages:
            return
        page.on("dialog", self._on_dialog)
        page.on("close", self._on_page_closed)
        self._pages.append(page)

    def _on_page_closed(self, page: Page) -> None:
        if page in self._pages:
            index = self._pages.index(page)
            self._pages.remove(page)
            if self._active >= len(self._pages):
                self._active = max(0, len(self._pages) - 1)
            elif index < self._active:
                self._active -= 1

    def _on_dialog(self, dialog: Dialog) -> None:
        message = f"{dialog.type}: {dialog.message}"
        self._pending_dialogs.append(message)
        asyncio.create_task(self._dismiss(dialog))

    async def _dismiss(self, dialog: Dialog) -> None:
        try:
            if dialog.type == "prompt":
                await dialog.dismiss()
            else:
                await dialog.accept()
        except PlaywrightError:
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.context.close()
        except PlaywrightError:
            pass

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def last_snapshot(self) -> str:
        return self._last_snapshot

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def idle_seconds(self) -> float:
        if self.idle_suspended:
            return 0.0
        return time.monotonic() - self.last_used

    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at

    # ---- pages -----------------------------------------------------------

    async def page(self) -> Page:
        if not self._pages:
            page = await self.context.new_page()
            self._attach(page)
        self._active = min(self._active, len(self._pages) - 1)
        return self._pages[self._active]

    async def list_tabs(self) -> ActionResult:
        self.touch()
        tabs = []
        for index, page in enumerate(self._pages):
            tabs.append(
                {
                    "index": index,
                    "url": page.url,
                    "title": await _safe_title(page),
                    "active": index == self._active,
                }
            )
        return ActionResult(ok=True, detail=f"{len(tabs)} tab(s) open", data={"tabs": tabs})

    async def switch_tab(self, index: int) -> ActionResult:
        self.touch()
        if index < 0 or index >= len(self._pages):
            return ActionResult(
                ok=False, error=f"no tab at index {index}; {len(self._pages)} tab(s) open"
            )
        self._active = index
        await self._pages[index].bring_to_front()
        return await self._finish(f"switched to tab {index}")

    async def close_tab(self, index: int) -> ActionResult:
        self.touch()
        if index < 0 or index >= len(self._pages):
            return ActionResult(
                ok=False, error=f"no tab at index {index}; {len(self._pages)} tab(s) open"
            )
        if len(self._pages) == 1:
            return ActionResult(ok=False, error="cannot close the last tab of a session")
        await self._pages[index].close()
        return await self._finish(f"closed tab {index}")

    # ---- navigation ------------------------------------------------------

    async def navigate(self, url: str) -> ActionResult:
        self.touch()
        try:
            await check_url(url, self.domain_policy, allow_private=self.allow_private_addresses)
        except NetworkPolicyError as exc:
            return ActionResult(ok=False, error=f"navigation blocked: {exc}")

        await self._gate_url(url, "navigate")
        await self.rate_limiter.acquire(url)

        page = await self.page()
        try:
            await page.goto(url, timeout=self.limits.action_timeout_seconds * 1000)
        except PlaywrightTimeout:
            return ActionResult(ok=False, error=f"navigation to {url} timed out")
        except PlaywrightError as exc:
            return ActionResult(ok=False, error=f"navigation to {url} failed: {_clean(exc)}")

        blocked = await self._enforce_landing(page)
        if blocked:
            return blocked
        return await self._finish(f"navigated to {page.url}")

    async def _enforce_landing(self, page: Page) -> ActionResult | None:
        try:
            await check_url(
                page.url, self.domain_policy, allow_private=self.allow_private_addresses
            )
        except NetworkPolicyError as exc:
            try:
                await page.goto("about:blank")
            except PlaywrightError:
                pass
            return ActionResult(ok=False, error=f"landed on a blocked destination: {exc}")
        return None

    async def go_back(self) -> ActionResult:
        self.touch()
        page = await self.page()
        await page.go_back(timeout=self.limits.action_timeout_seconds * 1000)
        return await self._finish("went back")

    async def go_forward(self) -> ActionResult:
        self.touch()
        page = await self.page()
        await page.go_forward(timeout=self.limits.action_timeout_seconds * 1000)
        return await self._finish("went forward")

    async def reload(self) -> ActionResult:
        self.touch()
        page = await self.page()
        await page.reload(timeout=self.limits.action_timeout_seconds * 1000)
        return await self._finish("reloaded")

    # ---- interaction -----------------------------------------------------

    async def click(self, ref: str) -> ActionResult:
        self.touch()
        locator, error = await self._locator(ref)
        if error:
            return error
        assert locator is not None

        probe = await _probe(locator)
        if probe and is_submit_control(
            tag=probe["tag"],
            input_type=probe.get("inputType"),
            explicit_type=probe.get("typeAttr"),
            in_form=bool(probe.get("inForm")),
        ):
            await self._gate(
                RiskFinding(
                    RiskSource.STRUCTURAL,
                    f'click on {probe["tag"]} "{probe.get("label") or ref}" submits a form',
                ),
                "click",
                {"ref": ref},
            )
        if probe and probe.get("href"):
            await self._gate_url(probe["href"], "click")

        page = await self.page()
        try:
            await locator.click(timeout=self.limits.action_timeout_seconds * 1000)
        except PlaywrightTimeout:
            return ActionResult(ok=False, error=f"clicking {ref} timed out")
        except PlaywrightError as exc:
            return ActionResult(ok=False, error=f"clicking {ref} failed: {_clean(exc)}")

        await _settle(page)
        blocked = await self._enforce_landing(await self.page())
        if blocked:
            return blocked
        return await self._finish(f"clicked {ref}")

    async def type_text(
        self, ref: str, text: str, *, clear: bool = True, press_enter: bool = False
    ) -> ActionResult:
        self.touch()
        locator, error = await self._locator(ref)
        if error:
            return error
        assert locator is not None

        if press_enter:
            probe = await _probe(locator)
            if probe and probe.get("inForm"):
                await self._gate(
                    RiskFinding(
                        RiskSource.STRUCTURAL,
                        f"pressing Enter in {ref} submits the surrounding form",
                    ),
                    "type",
                    {"ref": ref},
                )

        try:
            if clear:
                await locator.fill(text, timeout=self.limits.action_timeout_seconds * 1000)
            else:
                await locator.click(timeout=self.limits.action_timeout_seconds * 1000)
                await locator.type(text, timeout=self.limits.action_timeout_seconds * 1000)
            if press_enter:
                await locator.press("Enter", timeout=self.limits.action_timeout_seconds * 1000)
        except PlaywrightTimeout:
            return ActionResult(ok=False, error=f"typing into {ref} timed out")
        except PlaywrightError as exc:
            return ActionResult(ok=False, error=f"typing into {ref} failed: {_clean(exc)}")

        page = await self.page()
        await _settle(page)
        blocked = await self._enforce_landing(await self.page())
        if blocked:
            return blocked
        suffix = " and pressed Enter" if press_enter else ""
        return await self._finish(f"typed into {ref}{suffix}")

    async def select_option(self, ref: str, value: str) -> ActionResult:
        self.touch()
        locator, error = await self._locator(ref)
        if error:
            return error
        assert locator is not None
        try:
            await locator.select_option(
                value=value, timeout=self.limits.action_timeout_seconds * 1000
            )
        except PlaywrightError:
            try:
                await locator.select_option(
                    label=value, timeout=self.limits.action_timeout_seconds * 1000
                )
            except PlaywrightError as exc:
                return ActionResult(
                    ok=False, error=f"selecting {value!r} in {ref} failed: {_clean(exc)}"
                )
        return await self._finish(f"selected {value!r} in {ref}")

    async def hover(self, ref: str) -> ActionResult:
        self.touch()
        locator, error = await self._locator(ref)
        if error:
            return error
        assert locator is not None
        try:
            await locator.hover(timeout=self.limits.action_timeout_seconds * 1000)
        except PlaywrightError as exc:
            return ActionResult(ok=False, error=f"hovering {ref} failed: {_clean(exc)}")
        return await self._finish(f"hovered {ref}")

    async def press_key(self, key: str) -> ActionResult:
        self.touch()
        page = await self.page()
        try:
            await page.keyboard.press(key)
        except PlaywrightError as exc:
            return ActionResult(ok=False, error=f"pressing {key!r} failed: {_clean(exc)}")
        await _settle(page)
        return await self._finish(f"pressed {key!r}")

    async def scroll(self, direction: str = "down", ref: str | None = None) -> ActionResult:
        self.touch()
        page = await self.page()
        if ref:
            locator, error = await self._locator(ref)
            if error:
                return error
            assert locator is not None
            try:
                await locator.scroll_into_view_if_needed(
                    timeout=self.limits.action_timeout_seconds * 1000
                )
            except PlaywrightError as exc:
                return ActionResult(ok=False, error=f"scrolling to {ref} failed: {_clean(exc)}")
            return await self._finish(f"scrolled to {ref}")

        moves = {
            "down": "window.scrollBy(0, window.innerHeight * 0.85)",
            "up": "window.scrollBy(0, -window.innerHeight * 0.85)",
            "top": "window.scrollTo(0, 0)",
            "bottom": "window.scrollTo(0, document.body.scrollHeight)",
        }
        if direction not in moves:
            return ActionResult(
                ok=False, error=f"unknown scroll direction {direction!r}; use {sorted(moves)}"
            )
        await page.evaluate(moves[direction])
        await page.wait_for_timeout(120)
        return await self._finish(f"scrolled {direction}")

    async def submit(self, ref: str, purpose: str) -> ActionResult:
        self.touch()
        await self._gate(RiskFinding(RiskSource.DECLARED, purpose), "submit", {"ref": ref})
        locator, error = await self._locator(ref)
        if error:
            return error
        assert locator is not None
        page = await self.page()
        try:
            await locator.click(timeout=self.limits.action_timeout_seconds * 1000)
        except PlaywrightError as exc:
            return ActionResult(ok=False, error=f"submitting via {ref} failed: {_clean(exc)}")
        await _settle(page)
        blocked = await self._enforce_landing(await self.page())
        if blocked:
            return blocked
        return await self._finish(f"submitted via {ref} ({purpose})")

    async def wait_for(
        self, *, text: str | None = None, ref: str | None = None, seconds: float | None = None
    ) -> ActionResult:
        self.touch()
        page = await self.page()
        timeout_ms = self.limits.action_timeout_seconds * 1000
        if text:
            try:
                await page.get_by_text(text).first.wait_for(state="visible", timeout=timeout_ms)
            except PlaywrightTimeout:
                return ActionResult(ok=False, error=f"text {text!r} did not appear in time")
            return await self._finish(f"text {text!r} appeared")
        if ref:
            target = self._refs.get(ref)
            if target is None:
                return ActionResult(ok=False, error=f"unknown ref {ref}; {STALE_REF_HINT}")
            try:
                await target.frame.locator(_selector(ref)).wait_for(
                    state="visible", timeout=timeout_ms
                )
            except PlaywrightTimeout:
                return ActionResult(ok=False, error=f"{ref} did not become visible in time")
            return await self._finish(f"{ref} is visible")
        if seconds is not None:
            await asyncio.sleep(min(seconds, self.limits.action_timeout_seconds))
            return await self._finish(f"waited {seconds}s")
        return ActionResult(ok=False, error="wait_for needs one of text, ref or seconds")

    # ---- observation -----------------------------------------------------

    async def snapshot(self, *, mark_new: bool = True) -> ActionResult:
        self.touch()
        rendered = await self._take_snapshot(mark_new=mark_new)
        return ActionResult(ok=True, detail="snapshot taken", observation=rendered.text)

    async def screenshot(self) -> ActionResult:
        self.touch()
        page = await self.page()
        try:
            image = await page.screenshot(type="png", timeout=15000)
        except PlaywrightError as exc:
            return ActionResult(ok=False, error=f"screenshot failed: {_clean(exc)}")
        return ActionResult(
            ok=True, detail="screenshot taken", screenshot=image, data={"bytes": len(image)}
        )

    async def get_text(self) -> ActionResult:
        self.touch()
        page = await self.page()
        html = await page.content()
        markdown = html_to_markdown(html, base_url=page.url)
        if not markdown.strip():
            markdown = await page.evaluate("document.body ? document.body.innerText : ''")
        budget_chars = self.limits.text_token_budget * 4
        truncated = len(markdown) > budget_chars
        if truncated:
            markdown = markdown[:budget_chars] + "\n\n... truncated at the text budget."
        return ActionResult(
            ok=True,
            detail=f"read {page.url}",
            observation=markdown,
            data={"truncated": truncated, "url": page.url},
        )

    async def find(self, query: str) -> ActionResult:
        self.touch()
        if not self._last_snapshot:
            await self._take_snapshot(mark_new=False)
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        matches = [line for line in self._last_snapshot.splitlines() if pattern.search(line)]
        if not matches:
            return ActionResult(
                ok=True, detail=f"no snapshot line matches {query!r}", data={"matches": []}
            )
        return ActionResult(
            ok=True,
            detail=f"{len(matches)} match(es) for {query!r}",
            observation="\n".join(matches[:100]),
            data={"matches": matches[:100]},
        )

    # ---- internals -------------------------------------------------------

    async def _finish(self, detail: str) -> ActionResult:
        dialogs = self._drain_dialogs()
        if dialogs:
            detail = f"{detail}; dialog handled ({'; '.join(dialogs)})"
        observation = None
        if self.auto_snapshot:
            rendered = await self._take_snapshot(mark_new=True)
            observation = rendered.text
        return ActionResult(ok=True, detail=detail, observation=observation)

    def _drain_dialogs(self) -> list[str]:
        dialogs = list(self._pending_dialogs)
        self._pending_dialogs.clear()
        return dialogs

    async def _locator(self, ref: str):
        target = self._refs.get(ref)
        if target is None:
            if ref in self._known_refs:
                return None, ActionResult(
                    ok=False,
                    error=f"ref {ref} is from an older snapshot and is no longer on the page; "
                    f"{STALE_REF_HINT}",
                )
            return None, ActionResult(ok=False, error=f"unknown ref {ref}; {STALE_REF_HINT}")
        locator = target.frame.locator(_selector(ref))
        try:
            count = await locator.count()
        except PlaywrightError:
            count = 0
        if count == 0:
            return None, ActionResult(
                ok=False, error=f"ref {ref} is no longer on the page; {STALE_REF_HINT}"
            )
        return locator.first, None

    async def _gate(self, finding: RiskFinding, action: str, context: dict[str, Any]) -> None:
        if self.risk_gate is not None:
            await self.risk_gate(finding, action, context)

    async def _gate_url(self, url: str, action: str) -> None:
        if matches_risky_url(url, self.risky_url_patterns):
            await self._gate(
                RiskFinding(RiskSource.URL_PATTERN, f"{url} matches a risky url pattern"),
                action,
                {"url": url},
            )

    async def _take_snapshot(self, *, mark_new: bool) -> RenderedSnapshot:
        page = await self.page()
        await _settle(page)
        payload, refs = await self._collect(page)
        nodes = parse_nodes(payload["nodes"])
        rendered = render_snapshot(
            url=payload["url"],
            title=payload["title"],
            nodes=nodes,
            tab_index=self._active,
            tab_count=len(self._pages),
            scroll_percent=int(payload.get("scrollPercent") or 0),
            previous_signatures=self._prev_signatures if mark_new else None,
            token_budget=self.limits.snapshot_token_budget,
        )
        self._refs = refs
        self._known_refs.update(refs)
        self._prev_signatures = rendered.signatures
        self._last_snapshot = rendered.text
        url = payload["url"]
        if url and (not self.visited_urls or self.visited_urls[-1] != url):
            self.visited_urls.append(url)
        return rendered

    async def _collect(self, page: Page) -> tuple[dict, dict[str, RefTarget]]:
        refs: dict[str, RefTarget] = {}
        payload, next_ref = await self._collect_frame(page.main_frame, self._next_ref, refs)
        self._next_ref = next_ref
        return payload, refs

    async def _collect_frame(
        self, frame: Frame, start_ref: int, refs: dict[str, RefTarget]
    ) -> tuple[dict, int]:
        payload = await frame.evaluate(COLLECT_JS, start_ref)
        nodes = parse_nodes(payload["nodes"])
        for node in iter_nodes(nodes):
            if node.ref:
                refs[node.ref] = RefTarget(frame=frame, ref=node.ref)
        next_ref = int(payload.get("nextRef") or start_ref)
        next_ref = await self._attach_child_frames(frame, nodes, refs, next_ref)
        payload["nodes"] = [_to_dict(node) for node in nodes]
        return payload, next_ref

    async def _attach_child_frames(
        self, frame: Frame, nodes: list[SnapNode], refs: dict[str, RefTarget], next_ref: int
    ) -> int:
        frame_nodes = {node.frame_key: node for node in iter_nodes(nodes) if node.frame_key}
        if not frame_nodes:
            return next_ref
        for child in frame.child_frames:
            key = await _frame_key(child)
            node = frame_nodes.get(key) if key else None
            if node is None:
                continue
            try:
                child_payload, next_ref = await self._collect_frame(child, next_ref, refs)
            except PlaywrightError:
                node.text = "cross-origin frame, not readable"
                continue
            node.children = parse_nodes(child_payload["nodes"])
        return next_ref


def _selector(ref: str) -> str:
    return f'[data-cb-ref="{ref}"]'


def _clean(exc: Exception) -> str:
    return str(exc).splitlines()[0][:300]


async def _safe_title(page: Page) -> str:
    try:
        return await page.title()
    except PlaywrightError:
        return ""


async def _settle(page: Page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except PlaywrightError:
        pass
    try:
        await page.wait_for_timeout(120)
    except PlaywrightError:
        pass


async def _frame_key(frame: Frame) -> str | None:
    try:
        element = await frame.frame_element()
        return await element.get_attribute("data-cb-frame")
    except PlaywrightError:
        return None


_PROBE_JS = """
(el) => {
  const tag = el.tagName.toLowerCase();
  return {
    tag,
    typeAttr: el.getAttribute('type'),
    inputType: tag === 'input' ? (el.getAttribute('type') || 'text') : null,
    inForm: !!el.closest('form'),
    href: el.tagName === 'A' ? el.href : null,
    label: (el.textContent || '').trim().slice(0, 60),
  };
}
"""


async def _probe(locator: Any) -> dict[str, Any] | None:
    try:
        return await locator.evaluate(_PROBE_JS)
    except PlaywrightError:
        return None


def _to_dict(node: SnapNode) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": node.role, "name": node.name}
    if node.ref:
        payload["ref"] = node.ref
    if node.frame_key:
        payload["frameKey"] = node.frame_key
    if node.level:
        payload["level"] = node.level
    if node.value is not None:
        payload["value"] = node.value
    if node.options:
        payload["options"] = node.options
    if node.states:
        payload["states"] = node.states
    if node.text:
        payload["text"] = node.text
    if node.children:
        payload["children"] = [_to_dict(child) for child in node.children]
    return payload
