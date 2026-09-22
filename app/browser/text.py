from __future__ import annotations

import trafilatura


def html_to_markdown(html: str, *, base_url: str | None = None) -> str:
    try:
        extracted = trafilatura.extract(
            html,
            url=base_url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            favor_recall=True,
        )
    except Exception:
        return ""
    return extracted or ""
