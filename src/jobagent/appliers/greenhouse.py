"""Greenhouse: job-boards.greenhouse.io / boards.greenhouse.io, plus company
careers pages that embed a Greenhouse form (?gh_jid=...).

Questions come from Greenhouse's public API (exact labels, options and field
ids); the form itself is filled in the browser because submitting through the
API needs the employer's private key."""
from __future__ import annotations

import html as htmllib
import re
from urllib.parse import parse_qs, urlparse

import httpx
from playwright.sync_api import Page

from ..models import Answer, Job, Question
from ..registry import Applier, register_applier
from ._dom import by_id, check_by_label, choose_combobox

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true"
FORM = "https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
KINDS = {"input_text": "text", "textarea": "textarea", "input_file": "file",
         "multi_value_single_select": "select", "multi_value_multi_select": "multiselect"}


def strip_html(s: str) -> str:
    s = htmllib.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = htmllib.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", s)).strip()


def parse_ids(url: str) -> tuple[str, str] | None:
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url)
    if m:
        return m.group(1), m.group(2)
    q = parse_qs(urlparse(url).query)
    if "for" in q and "token" in q:
        return q["for"][0], q["token"][0]
    return None


@register_applier
class Greenhouse(Applier):
    name = "greenhouse"
    description = "Greenhouse application forms (job-boards.greenhouse.io, boards.greenhouse.io, or pages embedding them)."
    url_patterns = (r"(job-)?boards\.greenhouse\.io/", r"boards-api\.greenhouse\.io/")

    def detect(self, url, html):
        job_id = parse_qs(urlparse(url).query).get("gh_jid", [None])[0]
        m = (re.search(r"greenhouse\.io/embed/job_app\?for=([\w-]+)(?:&amp;|&)token=(\d+)", html)
             or re.search(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", html)
             or re.search(r"greenhouse\.io/([\w-]+)/jobs/(\d+)", html))
        if not m:
            return None
        board = m.group(1)
        job_id = job_id or (m.group(2) if m.lastindex and m.lastindex >= 2 else None)
        return FORM.format(board=board, job_id=job_id) if job_id else None

    def _data(self, job: Job) -> dict:
        if "gh" not in job.extra:
            ids = parse_ids(job.url)
            if not ids:
                raise ValueError(f"Can't find a Greenhouse board and job id in {job.url}")
            r = httpx.get(API.format(board=ids[0], job_id=ids[1]), timeout=30)
            r.raise_for_status()
            job.extra["gh"] = r.json()
            job.extra["gh_ids"] = ids
        return job.extra["gh"]

    def open(self, page: Page, job: Job) -> None:
        self._data(job)
        page.goto(FORM.format(board=job.extra["gh_ids"][0], job_id=job.extra["gh_ids"][1]),
                  wait_until="domcontentloaded")
        page.wait_for_selector(by_id("first_name"), timeout=30000)
        # The form is server-rendered, then React takes over and resets inputs;
        # filling before that finishes loses the values.
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

    def load_job(self, page: Page, job: Job) -> Job:
        d = self._data(job)
        job.title = job.title or d.get("title", "")
        job.company = job.company or d.get("company_name", "") or job.extra["gh_ids"][0]
        job.location = job.location or (d.get("location") or {}).get("name", "")
        job.description = job.description or strip_html(d.get("content", ""))
        return job

    def questions(self, page: Page, job: Job) -> list[Question]:
        d = self._data(job)
        groups = [d.get("questions") or [], d.get("location_questions") or []]
        for block in d.get("compliance") or []:
            groups.append(block.get("questions") or [])
        out, seen = [], set()
        for group in groups:
            for q in group:
                fields = [f for f in q.get("fields", []) if f.get("type") in KINDS]
                if not fields:
                    continue
                f = fields[0]  # e.g. resume offers file OR textarea; take the first (file)
                if f["name"] in seen:
                    continue
                seen.add(f["name"])
                if f["type"] != "input_file" and not page.locator(by_id(f["name"])).count() \
                        and not page.locator(f'[name^="{f["name"]}"]').count():
                    continue  # conditional field not shown (e.g. race appears only after ethnicity)
                label = q.get("label", f["name"])
                if " " not in label:  # compliance labels come as "VeteranStatus"
                    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label)
                out.append(Question(
                    key=f["name"], label=label, kind=KINDS[f["type"]],
                    required=bool(q.get("required")),
                    options=[v["label"] for v in f.get("values") or []],
                    description=strip_html(q.get("description") or "")[:3000]))
        # The phone-country picker isn't in the API but is required on the form.
        if page.locator(by_id("country")).count() and "country" not in seen:
            out.insert(4, Question(key="country", label="Country", kind="select", required=True))
        return out

    def fill(self, page: Page, answers: list[Answer]) -> list[str]:
        problems = []
        for a in answers:
            if a.value in (None, "", []):
                continue
            q, sel = a.question, by_id(a.question.key)
            try:
                if q.kind == "file":
                    page.set_input_files(sel, a.value)
                elif q.kind in ("text", "textarea"):
                    box = page.locator(sel).first
                    box.fill(a.value)
                    if box.input_value() != a.value:
                        page.wait_for_timeout(500)
                        box.fill(a.value)
                elif q.kind in ("select", "multiselect"):
                    values = a.value if isinstance(a.value, list) else [a.value]
                    for v in values:
                        ok = (choose_combobox(page, sel, v) if page.locator(sel).count()
                              else check_by_label(page, f'fieldset:has([name^="{q.key}"])', v))
                        if not ok:
                            problems.append(f"couldn't select '{v}' for: {q.label}")
            except Exception as e:  # keep going; the review step shows what failed
                problems.append(f"couldn't fill '{q.label}': {e.__class__.__name__}: {str(e)[:120]}")
        return problems

    def submit(self, page: Page) -> None:
        page.locator("button[type=submit]").last.click()
        page.wait_for_timeout(4000)

    def confirmed(self, page: Page) -> bool:
        return "confirmation" in page.url or super().confirmed(page)
