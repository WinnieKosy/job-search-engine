"""Search the public job boards of the Greenhouse companies listed in
profile.yaml (search.greenhouse_boards)."""
from __future__ import annotations

import re

import httpx

from ..appliers.greenhouse import FORM, strip_html
from ..models import Job
from ..registry import Source, register_source

LIST = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


def _hit(text: str, words: list[str]) -> bool:
    return not words or any(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in words)


@register_source
class GreenhouseSource(Source):
    name = "greenhouse"
    description = "Public Greenhouse job boards for the companies you list."

    def search(self, profile: dict) -> list[Job]:
        s = profile.get("search", {})
        keywords, locations = s.get("keywords", []), s.get("locations", [])
        exclude = s.get("exclude_keywords", [])
        jobs = []
        for board in s.get("greenhouse_boards", []):
            try:
                r = httpx.get(LIST.format(board=board), timeout=30)
                r.raise_for_status()
            except httpx.HTTPError as e:
                print(f"  ! greenhouse board '{board}': {e}")
                continue
            for j in r.json().get("jobs", []):
                title, loc = j.get("title", ""), (j.get("location") or {}).get("name", "")
                if not _hit(title, keywords) or (exclude and _hit(title, exclude)):
                    continue
                if locations and not _hit(loc, locations):
                    continue
                # canonical Greenhouse form URL, even when the company links its own careers site
                jobs.append(Job(url=FORM.format(board=board, job_id=j["id"]), title=title, company=j.get("company_name") or board,
                                location=loc, description=strip_html(j.get("content", "")),
                                source=self.name))
        return jobs
