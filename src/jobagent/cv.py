"""Reading, scoring against, and tailoring the CV. Tailoring may reword, reorder
and trim, but every claim is checked against the original CV."""
from __future__ import annotations

import copy
import difflib
import json
from pathlib import Path

from .llm import LLM
from .models import Job
from . import writing

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}

CV_SCHEMA = {
    "type": "object",
    "properties": {
        "name": _STR,
        "headline": _STR,
        "contact": {"type": "object", "properties": {
            k: _STR for k in ("email", "phone", "location", "linkedin", "website", "github")}},
        "summary": _STR,
        "experience": {"type": "array", "items": {"type": "object", "properties": {
            "title": _STR, "company": _STR, "location": _STR, "start": _STR, "end": _STR,
            "bullets": _STRS}, "required": ["title", "company", "start", "end", "bullets"]}},
        "education": {"type": "array", "items": {"type": "object", "properties": {
            "degree": _STR, "school": _STR, "start": _STR, "end": _STR, "details": _STR},
            "required": ["degree", "school"]}},
        "skills": _STRS,
        "projects": {"type": "array", "items": {"type": "object", "properties": {
            "name": _STR, "bullets": _STRS}, "required": ["name", "bullets"]}},
        "certifications": _STRS,
    },
    "required": ["name", "summary", "experience", "education", "skills"],
}


def read_cv_text(path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"CV not found at {path}. Set cv_path in config/profile.yaml.")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages).strip()
    if suffix == ".docx":
        import docx
        return "\n".join(p.text for p in docx.Document(path).paragraphs).strip()
    return path.read_text().strip()


def parse_cv(llm: LLM, text: str) -> dict:
    return llm.json(
        "Convert this CV into JSON. Copy wording exactly; do not summarise, improve or add "
        "anything. Keep every job, degree, bullet and skill. Use '' for missing fields.",
        text, CV_SCHEMA)


def score_fit(llm: LLM, cv: dict, job: Job) -> dict:
    return llm.json(
        "You screen jobs for a candidate. Score 0-100 how well the candidate fits, weighting "
        "hard requirements (years, must-have skills, location/work authorisation, seniority) "
        "most. Be strict: 70+ means a recruiter would likely shortlist them.",
        f"CANDIDATE CV:\n{json.dumps(cv)}\n\nJOB: {job.title} at {job.company} ({job.location})\n"
        f"{job.description[:6000]}",
        {"type": "object", "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reason": _STR, "missing": _STRS}, "required": ["score", "reason", "missing"]})


def unsupported(llm: LLM, cv_text: str, statements: list[str]) -> set[int]:
    """Indices of statements the original CV doesn't support."""
    if not statements:
        return set()
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(statements))
    out = llm.json(
        "You are a strict fact-checker. For each numbered statement, decide whether the CV "
        "supports it. Rewording is fine. A statement is unsupported if it adds a skill, tool, "
        "number, responsibility, employer, title or achievement that the CV does not state.",
        f"CV:\n{cv_text}\n\nSTATEMENTS:\n{numbered}",
        {"type": "object", "properties": {"unsupported": {"type": "array", "items": {
            "type": "object", "properties": {"index": {"type": "integer"}, "why": _STR},
            "required": ["index", "why"]}}}, "required": ["unsupported"]})
    return {u["index"] for u in out["unsupported"] if 0 <= u["index"] < len(statements)}


def tailor_cv(llm: LLM, cv: dict, cv_text: str, job: Job) -> tuple[dict, list[str]]:
    """Return (tailored_cv, notes about what was reverted)."""
    draft = llm.json(
        "Tailor this CV to the job. You may: rewrite the summary for this role, reorder and "
        "reword bullets to use the job's terminology where the CV genuinely supports it, put "
        "the most relevant bullets first, trim less relevant bullets (keep at least two per "
        "job), and reorder skills. You may NOT add skills, tools, numbers, achievements, or "
        "change titles, companies or dates. Keep every job and degree.\n\n"
        + writing.STYLE_PROMPT,
        f"CV JSON:\n{json.dumps(cv)}\n\nJOB: {job.title} at {job.company}\n{job.description[:6000]}",
        CV_SCHEMA, temperature=0.3)

    out, notes = copy.deepcopy(cv), []

    # Titles, companies, dates and job order always come from the original; only
    # bullets are taken from the draft.
    def key(e):
        return (e.get("company", "").lower().strip(), e.get("title", "").lower().strip())
    drafted = {key(e): e for e in draft.get("experience", [])}
    for orig in out["experience"]:
        new = drafted.get(key(orig))
        if new and new.get("bullets"):
            orig["bullets"] = new["bullets"]

    # Skills must exist in the original CV.
    low_text = cv_text.lower()
    allowed = {s.lower() for s in cv.get("skills", [])}
    skills = [s for s in draft.get("skills", []) if s.lower() in allowed or s.lower() in low_text]
    dropped = [s for s in draft.get("skills", []) if s not in skills]
    if dropped:
        notes.append(f"removed skills not in your CV: {', '.join(dropped)}")
    out["skills"] = skills or cv.get("skills", [])
    out["summary"] = writing.tidy(draft.get("summary") or cv.get("summary", ""))

    # Fact-check summary and every bullet; revert anything unsupported.
    statements = [out["summary"]] + [b for e in out["experience"] for b in e["bullets"]]
    bad = unsupported(llm, cv_text, statements)
    if 0 in bad:
        out["summary"] = cv.get("summary", "")
        notes.append("summary reverted to your original (tailored one made unsupported claims)")
    i = 1
    for e, orig in zip(out["experience"], cv["experience"]):
        fixed = []
        for b in e["bullets"]:
            if i in bad:
                best = difflib.get_close_matches(b, orig["bullets"], n=1, cutoff=0)
                if best and best[0] not in fixed:
                    fixed.append(best[0])
                notes.append(f"reverted a bullet at {e['company']}: {b[:70]}...")
            else:
                fixed.append(writing.tidy(b))
            i += 1
        e["bullets"] = fixed
    return out, notes


def cover_letter(llm: LLM, cv: dict, cv_text: str, job: Job, profile: dict) -> tuple[str, list[str]]:
    """Return (letter_body, unsupported_claims_still_present)."""
    sample = profile.get("writing_sample", "")
    voice = f"\n\nMatch the voice of this sample of the candidate's own writing:\n{sample}" if sample else ""
    system = ("Write the body of a cover letter (3 short paragraphs, under 280 words, no greeting "
              "and no sign-off). Paragraph 1: why this role at this company, specifically. "
              "Paragraph 2: two concrete things from the CV that match the job's main needs. "
              "Paragraph 3: a short, plain close." + voice)
    user = (f"CV:\n{json.dumps(cv)}\n\nJOB: {job.title} at {job.company} ({job.location})\n"
            f"{job.description[:6000]}")
    body = writing.write(llm, system, user)
    sentences = [s.strip() for s in body.replace("\n", " ").split(". ") if len(s.strip()) > 20]
    bad = unsupported(llm, cv_text, sentences)
    if bad:
        claims = [sentences[i] for i in sorted(bad)]
        body = writing.write(llm, system, user + "\n\nDo NOT include these claims, the CV does "
                             "not support them:\n- " + "\n- ".join(claims))
        sentences = [s.strip() for s in body.replace("\n", " ").split(". ") if len(s.strip()) > 20]
        bad = unsupported(llm, cv_text, sentences)
        return body, [sentences[i] for i in sorted(bad)]
    return body, []
