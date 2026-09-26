from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

COLLECT_JS = (Path(__file__).parent / "collect.js").read_text(encoding="utf-8")

EMPTY_CONTAINER_ROLES = frozenset(
    {"listbox", "menu", "grid", "tree", "table", "list", "tabpanel", "dialog", "feed"}
)

TRUNCATION_TRAILER = (
    "- ... truncated at the snapshot budget. Scroll, use find(), or read the page with get_text()."
)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class SnapNode:
    role: str
    name: str = ""
    ref: str | None = None
    frame_key: str | None = None
    level: int | None = None
    value: str | None = None
    options: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    text: str | None = None
    children: list[SnapNode] = field(default_factory=list)

    @property
    def signature(self) -> str:
        return f"{self.role}|{self.name}"


@dataclass
class RenderedSnapshot:
    text: str
    signatures: Counter[str]
    truncated: bool


def parse_nodes(raw: list[dict]) -> list[SnapNode]:
    return [_parse_node(item) for item in raw]


def _parse_node(item: dict) -> SnapNode:
    return SnapNode(
        role=item.get("role", "generic"),
        name=item.get("name", "") or "",
        ref=item.get("ref"),
        frame_key=item.get("frameKey"),
        level=item.get("level"),
        value=item.get("value"),
        options=list(item.get("options") or []),
        states=list(item.get("states") or []),
        text=item.get("text"),
        children=[_parse_node(child) for child in item.get("children") or []],
    )


def iter_nodes(nodes: list[SnapNode]):
    for node in nodes:
        yield node
        yield from iter_nodes(node.children)


def render_snapshot(
    *,
    url: str,
    title: str,
    nodes: list[SnapNode],
    tab_index: int,
    tab_count: int,
    scroll_percent: int,
    previous_signatures: Counter[str] | None,
    token_budget: int,
) -> RenderedSnapshot:
    header = (
        f"# url={url} | title={title!r} | tab={tab_index + 1}/{tab_count} "
        f"| scroll={scroll_percent}%"
    ).replace("'", '"')

    signatures: Counter[str] = Counter()
    unmatched = Counter(previous_signatures) if previous_signatures is not None else None

    lines: list[str] = []
    for node in nodes:
        _render_node(node, 0, lines, signatures, unmatched)

    body, truncated = _fit_to_budget(header, lines, token_budget)
    return RenderedSnapshot(text=body, signatures=signatures, truncated=truncated)


def _render_node(
    node: SnapNode,
    depth: int,
    lines: list[str],
    signatures: Counter[str],
    unmatched: Counter[str] | None,
) -> None:
    indent = "  " * depth
    if node.text and not node.children and not node.name and not node.ref:
        lines.append(f"{indent}- text: {node.text}")
        return

    rendered_children: list[str] = []
    for child in node.children:
        _render_node(child, depth + 1, rendered_children, signatures, unmatched)

    parts = [f"- {node.role}"]
    if node.name:
        parts.append(f'"{node.name}"')
    if node.level:
        parts.append(f"[level={node.level}]")
    if node.ref:
        parts.append(f"[ref={node.ref}]")
    if node.value is not None:
        parts.append(f'[value="{node.value}"]')
    if node.options:
        parts.append(f"[options={','.join(node.options)}]")
    for state in node.states:
        parts.append(f"[{state}]")
    if node.role in EMPTY_CONTAINER_ROLES and not node.children and not node.text:
        parts.append("[empty]")

    if node.ref:
        signature = node.signature
        signatures[signature] += 1
        if unmatched is not None:
            if unmatched.get(signature, 0) > 0:
                unmatched[signature] -= 1
            else:
                parts.append("[new]")

    line = indent + " ".join(parts)
    if node.text or rendered_children:
        lines.append(line + ":")
        if node.text:
            lines.append(f"{indent}  - text: {node.text}")
        lines.extend(rendered_children)
    else:
        lines.append(line)


def _fit_to_budget(header: str, lines: list[str], token_budget: int) -> tuple[str, bool]:
    kept: list[str] = [header]
    used = estimate_tokens(header)
    reserve = estimate_tokens(TRUNCATION_TRAILER)
    for line in lines:
        cost = estimate_tokens(line)
        if used + cost + reserve > token_budget:
            kept.append(TRUNCATION_TRAILER)
            return "\n".join(kept), True
        kept.append(line)
        used += cost
    return "\n".join(kept), False
