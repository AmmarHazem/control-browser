from __future__ import annotations

import logging

import httpx

from app.browser.session import ActionResult

logger = logging.getLogger(__name__)


class SearxngSearch:
    """Web search over a SearXNG instance."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def search(self, query: str, *, max_results: int = 8) -> ActionResult:
        params = {"q": query, "format": "json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base_url}/search", params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            return ActionResult(ok=False, error=f"search failed: {exc}")

        results = []
        for item in (payload.get("results") or [])[:max_results]:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": (item.get("content") or "")[:400],
                }
            )
        rendered = "\n".join(
            f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
            for i, r in enumerate(results)
        )
        return ActionResult(
            ok=True,
            detail=f"{len(results)} result(s) for {query!r}",
            observation=rendered or "no results",
            data={"results": results},
        )
