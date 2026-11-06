"""Decide which applier (tool) handles a job URL.

1. URL rules: each applier declares the URLs it owns. Exact and instant.
2. Page check: follow redirects and look for an embedded form (e.g. a company
   careers page that embeds Greenhouse).
3. The model: if nothing matched, the LLM reads the page and picks a tool from
   the registry's descriptions, or says none fits.
4. Otherwise the generic applier makes a best-effort attempt.
"""
from __future__ import annotations

import re

import httpx

from .llm import LLM
from .registry import APPLIERS, Applier

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def _page_text(html: str, limit: int = 2500) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))[:limit]


def route(url: str, llm: LLM | None) -> tuple[Applier | None, str, str]:
    """Return (applier, url_to_open, how_it_was_chosen)."""
    specific = [a for n, a in APPLIERS.items() if n != "generic"]
    for a in specific:
        if a.matches(url):
            return a, url, "url rule"

    try:
        r = httpx.get(url, follow_redirects=True, timeout=30, headers={"User-Agent": UA})
        final, html = str(r.url), r.text
    except httpx.HTTPError as e:
        return APPLIERS.get("generic"), url, f"page fetch failed ({e.__class__.__name__})"

    for a in specific:
        if a.matches(final):
            return a, final, "url rule after redirect"
        canonical = a.detect(final, html)
        if canonical:
            return a, canonical, "embedded form detected"

    if llm is not None:
        names = [a.name for a in APPLIERS.values()]
        tools = "\n".join(f"- {a.name}: {a.description}" for a in APPLIERS.values())
        choice = llm.json(
            "You route job application pages to the right form-filling tool. "
            "Pick 'none' if the page is not a job posting or requires something no tool can do.",
            f"Tools:\n{tools}\n- none: no tool fits\n\nURL: {final}\n\nPage text:\n{_page_text(html)}",
            {"type": "object",
             "properties": {"tool": {"type": "string", "enum": names + ["none"]},
                            "reason": {"type": "string"}},
             "required": ["tool", "reason"]},
        )
        if choice["tool"] == "none":
            return None, final, f"model: {choice['reason']}"
        return APPLIERS[choice["tool"]], final, f"model: {choice['reason']}"

    return APPLIERS.get("generic"), final, "fallback"
