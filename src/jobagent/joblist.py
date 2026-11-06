"""Read job links from a .txt, .csv/.tsv or .xlsx file, or from a Google Sheet or
Google Doc link (shared as "Anyone with the link can view").

For spreadsheets it takes the link from each row (a URL typed in a cell, or a
clickable hyperlink whose text is something else, like "Apply"). If the first
row has headers such as Company, Title/Role/Position, Location, they're used too."""
from __future__ import annotations

import csv
import html as htmllib
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from .models import Job

URL = re.compile(r"https?://[^\s\"'<>]+", re.I)
HEADERS = {
    "company": ("company", "employer", "organisation", "organization"),
    "title": ("title", "job title", "role", "position", "job"),
    "location": ("location", "city", "where"),
}


def _field(headers: list[str], names: tuple[str, ...]) -> int | None:
    for i, h in enumerate(headers):
        if h.strip().lower() in names:
            return i
    return None


def _rows_to_jobs(rows: list[list[tuple[str, str | None]]]) -> list[Job]:
    """rows: each cell is (text, hyperlink_target_or_None)."""
    if not rows:
        return []
    headers = [t for t, _ in rows[0]]
    has_header = not any(URL.search(t or "") or link for t, link in rows[0])
    cols = {k: _field(headers, v) for k, v in HEADERS.items()} if has_header else {}
    jobs, seen = [], set()
    for row in rows[1:] if has_header else rows:
        url = next((link for _, link in row if link and URL.match(link)), None)
        if not url:
            url = next((m.group(0) for t, _ in row if t and (m := URL.search(t))), None)
        if not url or url in seen:
            continue
        seen.add(url)
        cell = lambda k: (row[cols[k]][0] or "").strip() if cols.get(k) is not None and cols[k] < len(row) else ""
        jobs.append(Job(url=url.rstrip(").,;"), company=cell("company"), title=cell("title"),
                        location=cell("location"), source="list"))
    return jobs


GOOGLE = re.compile(r"docs\.google\.com/(spreadsheets|document)/d/([\w-]+)")


def is_google_link(s: str) -> bool:
    return bool(GOOGLE.search(str(s)))


def _download(url: str) -> httpx.Response:
    r = httpx.get(url, follow_redirects=True, timeout=60)
    if "accounts.google.com" in str(r.url) or r.status_code in (401, 403, 404):
        raise SystemExit("Google wouldn't share that file. In Google Docs/Sheets click Share > "
                         "General access > 'Anyone with the link' (Viewer), then try again.")
    r.raise_for_status()
    return r


def _unwrap(href: str) -> str:
    """Google Docs wraps links as google.com/url?q=<real url>."""
    if "google.com/url" in href:
        return parse_qs(urlparse(href).query).get("q", [href])[0]
    return href


def read_google(link: str) -> list[Job]:
    kind, doc_id = GOOGLE.search(link).groups()
    if kind == "spreadsheets":
        # .xlsx export keeps links hidden behind cell text, which CSV would lose.
        r = _download(f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=xlsx")
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(r.content)
        return read_jobs(f.name)
    page = _download(f"https://docs.google.com/document/d/{doc_id}/export?format=html").text
    urls = [_unwrap(htmllib.unescape(h)) for h in re.findall(r'href="([^"]+)"', page)]
    text = re.sub(r"<[^>]+>", " ", page)
    urls += [htmllib.unescape(m) for m in URL.findall(text)]
    jobs, seen = [], set()
    for u in urls:
        u = u.rstrip(").,;")
        if u.startswith("http") and "google.com" not in u and u not in seen:
            seen.add(u)
            jobs.append(Job(url=u, source="list"))
    return jobs


def read_jobs(path: str | Path) -> list[Job]:
    if is_google_link(str(path)):
        return read_google(str(path))
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"No such file: {path}")
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=False, data_only=True)
        jobs = []
        for ws in wb.worksheets:
            rows = [[(str(c.value) if c.value is not None else "",
                      c.hyperlink.target if c.hyperlink else None) for c in r]
                    for r in ws.iter_rows()]
            jobs += _rows_to_jobs([r for r in rows if any(t or l for t, l in r)])
        return jobs
    if suffix in (".csv", ".tsv"):
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter="\t" if suffix == ".tsv" else ",")
            return _rows_to_jobs([[(c, None) for c in r] for r in reader if any(r)])
    if suffix == ".xls":
        raise SystemExit("Old .xls files aren't supported. Save it as .xlsx or .csv.")
    lines = [l.strip() for l in path.read_text().splitlines()]
    return [Job(url=l, source="list") for l in lines if l and not l.startswith("#")]
