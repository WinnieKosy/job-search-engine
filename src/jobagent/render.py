"""Render the tailored CV and cover letter to clean, ATS-friendly PDFs."""
from __future__ import annotations

import datetime as dt
from html import escape as e
from pathlib import Path

CSS = """
@page { size: A4; margin: 16mm 18mm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #111; line-height: 1.38; }
h1 { font-size: 20pt; margin: 0 0 2px; font-weight: 600; }
.sub { color: #444; margin-bottom: 4px; }
.contact { color: #444; font-size: 9.5pt; margin-bottom: 14px; }
h2 { font-size: 10.5pt; text-transform: uppercase; letter-spacing: .06em; border-bottom: 1px solid #bbb;
     padding-bottom: 2px; margin: 16px 0 6px; }
.row { display: flex; justify-content: space-between; font-weight: 600; margin-top: 8px; }
.row span:last-child { font-weight: 400; color: #444; white-space: nowrap; padding-left: 12px; }
.org { color: #333; font-style: italic; }
ul { margin: 3px 0 0 16px; padding: 0; } li { margin: 2px 0; }
p { margin: 0 0 10px; }
"""


def _contact(cv: dict, personal: dict) -> str:
    c = {**{k: personal.get(k, "") for k in ("email", "phone", "location", "linkedin", "website", "github")},
         **{k: v for k, v in (cv.get("contact") or {}).items() if v}}
    return " &middot; ".join(e(v) for v in c.values() if v)


def cv_html(cv: dict, personal: dict) -> str:
    parts = [f"<h1>{e(cv.get('name') or personal['full_name'])}</h1>"]
    if cv.get("headline"):
        parts.append(f"<div class=sub>{e(cv['headline'])}</div>")
    parts.append(f"<div class=contact>{_contact(cv, personal)}</div>")
    if cv.get("summary"):
        parts.append(f"<h2>Summary</h2><p>{e(cv['summary'])}</p>")
    if cv.get("experience"):
        parts.append("<h2>Experience</h2>")
        for x in cv["experience"]:
            dates = " - ".join(d for d in (x.get("start"), x.get("end")) if d)
            org = ", ".join(v for v in (x.get("company"), x.get("location")) if v)
            parts.append(f"<div class=row><span>{e(x.get('title', ''))}</span><span>{e(dates)}</span></div>"
                         f"<div class=org>{e(org)}</div><ul>"
                         + "".join(f"<li>{e(b)}</li>" for b in x.get("bullets", [])) + "</ul>")
    if cv.get("projects"):
        parts.append("<h2>Projects</h2>")
        for pj in cv["projects"]:
            parts.append(f"<div class=row><span>{e(pj.get('name', ''))}</span></div><ul>"
                         + "".join(f"<li>{e(b)}</li>" for b in pj.get("bullets", [])) + "</ul>")
    if cv.get("education"):
        parts.append("<h2>Education</h2>")
        for ed in cv["education"]:
            dates = " - ".join(d for d in (ed.get("start"), ed.get("end")) if d)
            parts.append(f"<div class=row><span>{e(ed.get('degree', ''))}</span><span>{e(dates)}</span></div>"
                         f"<div class=org>{e(ed.get('school', ''))}</div>"
                         + (f"<div>{e(ed['details'])}</div>" if ed.get("details") else ""))
    if cv.get("skills"):
        parts.append(f"<h2>Skills</h2><p>{e(', '.join(cv['skills']))}</p>")
    if cv.get("certifications"):
        parts.append(f"<h2>Certifications</h2><p>{e(', '.join(cv['certifications']))}</p>")
    return _doc(cv.get("name") or personal["full_name"], "".join(parts))


def letter_html(body: str, company: str, cv: dict, personal: dict) -> str:
    today = dt.date.today().strftime("%-d %B %Y")
    paras = "".join(f"<p>{e(p.strip())}</p>" for p in body.split("\n\n") if p.strip())
    html = (f"<h1>{e(personal['full_name'])}</h1><div class=contact>{_contact(cv, personal)}</div>"
            f"<p>{today}</p><p>Dear {e(company)} hiring team,</p>{paras}"
            f"<p>Kind regards,<br>{e(personal['full_name'])}</p>")
    return _doc(personal["full_name"], html)


def _doc(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{e(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def to_pdf(browser, html: str, path: Path) -> Path:
    page = browser.new_page()
    page.set_content(html, wait_until="load")
    page.pdf(path=str(path), format="A4", print_background=True)
    page.close()
    return path
