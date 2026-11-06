"""Work out an answer for every form question, and decide which ones need you.

Order: your files -> your personal details -> AI-use questions (always you) ->
your rules in profile.yaml -> answers you gave before -> EEO/salary/legal
policies -> a model draft grounded in your CV (or you, if it isn't sure).
"""
from __future__ import annotations

import json
import re

from .config import DATA
from .llm import LLM
from .models import Answer, Job, Question
from . import writing

MEMORY = DATA / "answer_memory.json"

AI_RE = re.compile(r"(\bA\.?I\.?\b|artificial intelligence|chat ?gpt|generative|\bLLMs?\b|"
                   r"language model)", re.I)
AI_CONTEXT = re.compile(r"use|used|using|assist|policy|generat|written|wrote|disclos|tool|help", re.I)
LEGAL_RE = re.compile(r"arbitrat|agreement|agree to|terms (and|&) conditions|privacy (policy|notice)|"
                      r"consent|certify|attest|acknowledg|accurate and complete|background check", re.I)
EEO_RE = re.compile(r"gender|\bsex\b|\brace\b|ethnic|hispanic|latin|veteran|disabilit|"
                    r"sexual orientation|pronoun|transgender", re.I)
SALARY_RE = re.compile(r"salary|compensation|pay (range|expectation)|desired pay|expected (pay|comp)", re.I)
DECLINE_RE = re.compile(r"decline|don.?t wish|do not wish|prefer not|not to (say|answer|disclose)", re.I)

PERSONAL = [  # (label pattern, profile.personal key)
    (r"^first\s*name|^given name|^first_name$", "first_name"),
    (r"^last\s*name|surname|family name|^last_name$", "last_name"),
    (r"^(full\s*)?name\*?$|^legal name", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"website|portfolio|personal (site|url)", "website"),
    (r"^country\*?$", "country"),
    (r"^(current )?(city|location)\*?$|where are you (currently )?(located|based)", "location"),
]


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", label.lower()).strip()


def _load_memory() -> dict:
    return json.loads(MEMORY.read_text()) if MEMORY.exists() else {}


def remember(q: Question, value) -> None:
    mem = _load_memory()
    mem[_norm(q.label)] = value
    MEMORY.write_text(json.dumps(mem, indent=2))


def pick_option(options: list[str], wanted: str) -> str | None:
    if not options:
        return wanted
    w = wanted.strip().lower()
    for test in (lambda o: o.lower() == w, lambda o: o.lower().startswith(w),
                 lambda o: w in o.lower()):
        hits = [o for o in options if test(o)]
        if hits:
            return hits[0]
    return None


def is_ai_question(q: Question) -> bool:
    text = f"{q.label} {q.description}"
    return bool(AI_RE.search(q.label) and AI_CONTEXT.search(text))


class Answerer:
    def __init__(self, llm: LLM, profile: dict, cv: dict, cv_text: str):
        self.llm, self.profile, self.cv, self.cv_text = llm, profile, cv, cv_text
        p = profile["personal"]
        p.setdefault("full_name", f"{p['first_name']} {p['last_name']}")
        self.memory = _load_memory()

    def answer_all(self, questions: list[Question], job: Job, files: dict) -> list[Answer]:
        return [self.answer(q, job, files) for q in questions]

    def answer(self, q: Question, job: Job, files: dict) -> Answer:
        label = q.label.strip()

        if q.kind == "file":
            if re.search(r"resume|cv\b|curriculum", f"{q.key} {label}", re.I):
                return Answer(q, files["cv_pdf"], "file")
            if re.search(r"cover", f"{q.key} {label}", re.I):
                return Answer(q, files["cover_pdf"], "file")
            return Answer(q, None, "skip", needs_user=q.required,
                          reason="asks for a file the agent doesn't have")

        if re.search(r"cover letter", label, re.I) and q.kind in ("text", "textarea"):
            return Answer(q, files["cover_text"], "file")

        if len(label) < 45:
            for pattern, key in PERSONAL:
                if re.search(pattern, label, re.I) or q.key == key:
                    value = self.profile["personal"].get(key)
                    if value:
                        return self._fit_option(q, str(value), "profile")
                    if not q.required:
                        return Answer(q, None, "skip")
                    return Answer(q, None, "you", needs_user=True,
                                  reason=f"add personal.{key} to profile.yaml to skip this")

        if is_ai_question(q):
            return Answer(q, None, "you", needs_user=True,
                          reason="asks about AI use - you decide how to answer")

        for rule in self.profile.get("answers", []):
            if re.search(rule["match"], f"{label} {q.description}", re.I):
                return self._fit_option(q, str(rule["answer"]), "rule")

        if _norm(label) in self.memory:
            return self._fit_option(q, self.memory[_norm(label)], "memory")

        if EEO_RE.search(label) and q.kind in ("select", "radio", "multiselect"):
            if self.profile.get("eeo", "decline") == "decline":
                choice = next((o for o in q.options if DECLINE_RE.search(o)), None)
                if choice:
                    return Answer(q, choice, "rule")
            if not q.required:
                return Answer(q, None, "skip")
            return Answer(q, None, "you", needs_user=True, reason="equal-opportunity question")

        if SALARY_RE.search(label):
            if self.profile.get("salary_expectation"):
                return self._fit_option(q, str(self.profile["salary_expectation"]), "profile")
            return Answer(q, None, "you", needs_user=q.required, reason="salary question")

        if LEGAL_RE.search(f"{label} {q.description[:500]}"):
            return Answer(q, None, "you", needs_user=True,
                          reason="legal agreement or attestation - needs your decision")

        return self._draft(q, job)

    def _fit_option(self, q: Question, value, origin: str) -> Answer:
        if q.kind in ("select", "radio") and q.options and isinstance(value, str):
            chosen = pick_option(q.options, value)
            if chosen is None:
                return Answer(q, None, "you", needs_user=True,
                              reason=f"your answer '{value}' isn't one of the options")
            return Answer(q, chosen, origin)
        return Answer(q, value, origin)

    def _draft(self, q: Question, job: Job) -> Answer:
        context = (f"CANDIDATE CV:\n{json.dumps(self.cv)}\n\nCANDIDATE FACTS:\n"
                   f"{json.dumps(self.profile.get('facts', {}))}\n\n"
                   f"JOB: {job.title} at {job.company} ({job.location})\n{job.description[:4000]}")
        ask = f"QUESTION: {q.label}\n{q.description[:1500]}"

        if q.kind in ("select", "radio", "multiselect", "checkbox"):
            options = q.options or ["Yes", "No"]
            out = self.llm.json(
                "Answer this application question for the candidate using only facts in their "
                "CV and profile. If those facts don't determine the answer, set confident=false.",
                f"{context}\n\n{ask}\nOPTIONS: {json.dumps(options)}",
                {"type": "object", "properties": {
                    "answer": {"type": "string", "enum": options},
                    "confident": {"type": "boolean"}}, "required": ["answer", "confident"]})
            value = [out["answer"]] if q.kind == "multiselect" else out["answer"]
            if out["confident"]:
                return Answer(q, value, "draft")
            if not q.required:
                return Answer(q, None, "skip")
            return Answer(q, value, "draft", needs_user=True, reason="the model wasn't sure")

        check = self.llm.json(
            "Can this application question be answered truthfully from the candidate's CV and "
            "profile facts alone? Questions about their history with this company, availability, "
            "personal circumstances or preferences usually cannot.",
            f"{context}\n\n{ask}",
            {"type": "object", "properties": {"answerable": {"type": "boolean"}},
             "required": ["answerable"]})
        if not check["answerable"]:
            if not q.required:
                return Answer(q, None, "skip")
            return Answer(q, None, "you", needs_user=True, reason="needs facts not in your CV")

        limit = "2-4 short paragraphs" if q.kind == "textarea" else "one short line"
        text = writing.write(
            self.llm,
            f"Answer this job application question as the candidate, in {limit}. "
            "Use only facts from the CV and profile.",
            f"{context}\n\n{ask}")
        return Answer(q, text, "draft")
