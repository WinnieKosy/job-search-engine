"""Runs one job at a time: route -> read job -> score -> tailor CV and letter ->
answer questions -> ask you what only you can answer -> fill -> review -> submit.
All interaction goes through a UI object (terminal or web)."""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import cv as cvlib
from . import render
from .answers import Answerer, is_ai_question, remember
from .config import DATA
from .llm import LLM
from .models import Answer, Job
from .router import route
from .tracker import Tracker
from .ui import UI, Stop, TerminalUI


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "job"


class Agent:
    def __init__(self, profile: dict, llm: LLM, cv: dict, cv_text: str, *,
                 auto: bool = False, dry_run: bool = False, ui: UI | None = None):
        self.profile, self.llm, self.cv, self.cv_text = profile, llm, cv, cv_text
        self.auto, self.dry_run = auto, dry_run
        self.ui = ui or TerminalUI()
        self.answerer = Answerer(llm, profile, cv, cv_text)
        self.tracker = Tracker()
        self.min_score = int(profile["search"].get("min_fit_score", 65))
        self.personal = profile["personal"]

    def run(self, jobs: list[Job]) -> None:
        settings = self.profile["apply"]
        log = self.ui.log
        with sync_playwright() as p:
            pdf_browser = p.chromium.launch()
            ctx = p.chromium.launch_persistent_context(
                str(DATA / "browser"), headless=bool(settings.get("headless", False)),
                viewport={"width": 1280, "height": 900})
            try:
                for n, job in enumerate(jobs, 1):
                    self.info = {"index": n, "total": len(jobs), "url": job.url, "title": job.title,
                                 "company": job.company}
                    self.ui.job_started(self.info)
                    try:
                        submitted = self.process(job, ctx, pdf_browser)
                    except Stop:
                        log("Stopped.")
                        break
                    except Exception as e:
                        log(f"  ! failed: {e.__class__.__name__}: {e}")
                        self.tracker.save(job, "error")
                        continue
                    if submitted and n < len(jobs):
                        lo, hi = settings.get("delay_seconds", [30, 90])
                        wait = random.uniform(lo, hi)
                        log(f"  waiting {wait:.0f}s before the next one")
                        self.ui.sleep(wait)
                log("All done.")
            finally:
                ctx.close()
                pdf_browser.close()

    def _update(self, **kw) -> None:
        self.info.update(kw)
        self.ui.job_started(self.info)

    def process(self, job: Job, ctx, pdf_browser) -> bool:
        log = self.ui.log
        if self.tracker.status(job.url) == "submitted":
            log("  already applied, skipping")
            return False

        applier, url, why = route(job.url, self.llm)
        if applier is None:
            log(f"  no tool can handle this page ({why})")
            self.tracker.save(job, "unsupported")
            return False
        log(f"  tool: {applier.name} ({why})")
        job.url = url

        page = ctx.new_page()
        try:
            applier.open(page, job)
            # Aggregators (e.g. hiringcafe) point at the employer's form: follow and re-route.
            for _ in range(3):
                target = applier.resolve(page, job)
                if target is None and applier.aggregator:
                    target = self.ui.text(
                        "Couldn't find the employer's application link. Open the job in your own "
                        "browser, click Apply, and paste the URL it takes you to (leave empty to skip):")
                if not target:
                    break
                applier, url, why = route(target, self.llm)
                if applier is None:
                    log(f"  employer page {target}: no tool can handle it ({why})")
                    self.tracker.save(job, "unsupported")
                    return False
                log(f"  employer form: {applier.name} ({why})")
                job.url = url
                applier.open(page, job)
            if applier.aggregator:
                log("  no employer application link, skipping")
                self.tracker.save(job, "skipped", applier.name)
                return False
            applier.load_job(page, job)
            log(f"  {job.title} at {job.company} - {job.location}")
            self._update(title=job.title, company=job.company, location=job.location, tool=applier.name)

            if job.fit_score is None:
                log("  scoring fit...")
                fit = cvlib.score_fit(self.llm, self.cv, job)
                job.fit_score, job.fit_reason = fit["score"], fit["reason"]
            log(f"  fit {job.fit_score}/100: {job.fit_reason}")
            self._update(fit=job.fit_score, fit_reason=job.fit_reason)
            if job.fit_score < self.min_score:
                if job.source != "list" or not self.ui.confirm(
                        f"Low fit ({job.fit_score}/100) for {job.title} at {job.company}. Apply anyway?"):
                    self.tracker.save(job, "skipped-low-fit", applier.name)
                    return False

            folder = DATA / "applications" / f"{dt.date.today()}-{slug(job.company)}-{slug(job.title)}"
            folder.mkdir(parents=True, exist_ok=True)
            files, warnings = self.documents(job, folder, pdf_browser)

            questions = applier.questions(page, job)
            log(f"  answering {len(questions)} questions...")
            answers = self.answerer.answer_all(questions, job, files)
            pending = [a for a in answers if a.needs_user]
            if pending:
                log(f"  {len(pending)} question(s) need you")
            for a in pending:
                self._ask(a, job.company)

            log("  filling the form...")
            problems = applier.fill(page, answers)

            if not self.dry_run and self.auto and not problems and not warnings and not any(
                    a.origin == "you" or a.needs_user for a in answers):
                action = "submit"
            else:
                while True:
                    action, i, value = self.ui.review(answers, problems, warnings, files, self.dry_run)
                    if action != "edit":
                        break
                    a = answers[i]
                    a.value, a.origin, a.needs_user, a.reason = value, "you", False, "edited by you"
                    problems[:] = applier.fill(page, [a])
            self._save_answers(folder, answers)

            if action == "done":  # dry run
                self.tracker.save(job, "dry-run", applier.name, str(folder))
                return False
            if action == "skip":
                self.tracker.save(job, "skipped", applier.name, str(folder))
                return False

            applier.submit(page)
            if not applier.confirmed(page):
                self.ui.wait("I didn't see a confirmation. If there's a CAPTCHA, verification code "
                             "or error, sort it out in the browser window.")
            ok = applier.confirmed(page)
            self.tracker.save(job, "submitted" if ok else "unconfirmed", applier.name, str(folder))
            log("  submitted" if ok else "  couldn't confirm; marked 'unconfirmed', check the browser")
            return ok
        finally:
            if not self.dry_run:
                page.close()

    def _ask(self, a: Answer, company: str) -> None:
        value, keep = self.ui.ask(a, company)
        a.value, a.origin, a.needs_user = value, "you", False
        if keep and value and not is_ai_question(a.question) and a.question.kind != "file":
            remember(a.question, value)

    @staticmethod
    def _save_answers(folder: Path, answers: list[Answer]) -> None:
        (folder / "answers.json").write_text(json.dumps(
            [{"question": a.question.label, "answer": a.value, "source": a.origin} for a in answers],
            indent=2))

    def documents(self, job: Job, folder: Path, pdf_browser) -> tuple[dict, list[str]]:
        log = self.ui.log
        log("  tailoring CV...")
        tailored, notes = cvlib.tailor_cv(self.llm, self.cv, self.cv_text, job)
        log("  writing cover letter...")
        letter, bad_claims = cvlib.cover_letter(self.llm, tailored, self.cv_text, job, self.profile)
        base = f"{self.personal['first_name']}_{self.personal['last_name']}".replace(" ", "_")
        cv_pdf = render.to_pdf(pdf_browser, render.cv_html(tailored, self.personal), folder / f"{base}_CV.pdf")
        cover_pdf = render.to_pdf(pdf_browser, render.letter_html(letter, job.company, tailored, self.personal),
                                  folder / f"{base}_Cover_Letter.pdf")
        (folder / "cv.json").write_text(json.dumps(tailored, indent=2))
        (folder / "cover_letter.txt").write_text(letter)
        (folder / "job.md").write_text(f"# {job.title} - {job.company}\n{job.url}\n\n{job.description}")
        for n in notes:
            log(f"  note: {n}")
        warnings = [f"cover letter may contain an unsupported claim: {c}" for c in bad_claims]
        return {"cv_pdf": str(cv_pdf), "cover_pdf": str(cover_pdf), "cover_text": letter}, warnings
