"""The jobs both the CLI and the web UI run: parse the CV, search, apply."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .config import DATA, load_profile
from .llm import make_llm
from .models import Job
from .registry import SOURCES, load_plugins

CV_JSON = DATA / "cv.json"


def setup(profile_path: Path | None = None):
    load_plugins()
    profile = load_profile(profile_path)
    return profile, make_llm(profile["llm"])


def load_cv(profile: dict) -> tuple[dict, str]:
    from .cv import read_cv_text
    if not CV_JSON.exists():
        raise SystemExit("Your CV hasn't been read yet: run `jobagent parse-cv` (or upload it in the UI).")
    return json.loads(CV_JSON.read_text()), read_cv_text(profile["cv_path"])


def parse_cv(profile: dict, llm, log: Callable[[str], None] = print) -> dict:
    from .cv import parse_cv as parse, read_cv_text
    text = read_cv_text(profile["cv_path"])
    log(f"Read {len(text)} characters from {Path(profile['cv_path']).name}. "
        f"Parsing with {profile['llm']['model']} (this can take a minute or two)...")
    cv = parse(llm, text)
    CV_JSON.write_text(json.dumps(cv, indent=2))
    log(f"Saved: {len(cv['experience'])} jobs, {len(cv['education'])} degrees, "
        f"{len(cv['skills'])} skills. Check it and fix anything the model got wrong.")
    return cv


def search(profile: dict, llm, log: Callable[[str], None] = print,
           should_stop: Callable[[], bool] = lambda: False) -> list[Job]:
    from .cv import score_fit
    from .tracker import Tracker
    cv, _ = load_cv(profile)
    tracker = Tracker()
    jobs = []
    for name in profile["search"].get("sources") or list(SOURCES):
        log(f"Searching {name}...")
        jobs += [j for j in SOURCES[name].search(profile) if tracker.status(j.url) is None]
    limit = int(profile["search"].get("max_jobs_to_score", 30))
    log(f"{len(jobs)} new matching jobs; scoring up to {limit}.")
    for job in jobs[:limit]:
        if should_stop():
            log("Stopped.")
            break
        fit = score_fit(llm, cv, job)
        job.fit_score, job.fit_reason = fit["score"], fit["reason"]
        tracker.save(job, "found")
        log(f"  {job.fit_score:>3}  {job.company} - {job.title}  ({job.location})")
    return jobs
