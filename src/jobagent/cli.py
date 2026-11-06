from __future__ import annotations

import argparse
from pathlib import Path

from . import tasks
from .config import load_profile
from .models import Job
from .registry import APPLIERS, SOURCES, load_plugins

def _setup(args):
    return tasks.setup(args.profile)


def cmd_parse_cv(args):
    profile, llm = _setup(args)
    tasks.parse_cv(profile, llm)
    print(f"Open {tasks.CV_JSON} and fix anything that's wrong; everything is built from it.")


def cmd_search(args):
    profile, llm = _setup(args)
    tasks.search(profile, llm)
    print("\nApply to the good ones with: jobagent apply --found")


def cmd_apply(args):
    from .agent import Agent
    from .tracker import Tracker
    profile, llm = _setup(args)
    cv, cv_text = tasks.load_cv(profile)
    from .joblist import is_google_link, read_jobs
    jobs: list[Job] = []
    if args.list:
        jobs += read_jobs(args.list)
    for u in args.urls:  # a URL, or a file of URLs (.txt/.csv/.xlsx)
        jobs += read_jobs(u) if Path(u).exists() or is_google_link(u) else [Job(url=u, source="list")]
    if args.found:
        jobs += Tracker().found(int(profile["search"].get("min_fit_score", 65)))
    print(f"{len(jobs)} job(s) to process.")
    if not jobs:
        raise SystemExit("Nothing to apply to. Pass URLs, --list FILE, or --found.")
    auto = args.auto or bool(profile["apply"].get("auto_submit", False))
    Agent(profile, llm, cv, cv_text, auto=auto, dry_run=args.dry_run).run(jobs)


def cmd_route(args):
    from .router import route
    _, llm = _setup(args)
    for url in args.urls:
        applier, final, why = route(url, llm)
        print(f"{url}\n  -> {applier.name if applier else 'none'} ({why})\n     opens {final}")


def cmd_tools(args):
    load_plugins()
    print("Sources (find jobs):")
    for s in SOURCES.values():
        print(f"  {s.name:<12} {s.description}")
    print("Appliers (fill forms):")
    for a in APPLIERS.values():
        print(f"  {a.name:<12} {a.description}")


def cmd_status(args):
    from .tracker import Tracker
    load_profile(args.profile)
    for status, score, company, title, url, when in Tracker().rows():
        print(f"{when[:16]}  {status:<16} {score if score is not None else '-':>3}  {company} - {title}\n{'':>40}{url}")


def cmd_applied(args):
    from .tracker import Tracker
    load_profile(args.profile)
    rows = [r[:4] for r in Tracker().applied(args.company)]
    if not rows:
        print("No submitted applications yet.")
    current = None
    for company, title, url, when in rows:
        if company != current:
            print(f"\n{company}")
            current = company
        print(f"  {when[:10]}  {title}\n              {url}")


def cmd_ui(args):
    from .web.server import serve
    serve(port=args.port, open_browser=not args.no_browser)


def main():
    ap = argparse.ArgumentParser(prog="jobagent", description="Find jobs, tailor your CV, fill applications.")
    ap.add_argument("--profile", type=Path, help="path to profile.yaml (default config/profile.yaml)")
    sub = ap.add_subparsers(required=True)

    sub.add_parser("parse-cv", help="read your CV into data/cv.json").set_defaults(fn=cmd_parse_cv)
    sub.add_parser("search", help="find and score jobs from the enabled sources").set_defaults(fn=cmd_search)

    p = sub.add_parser("apply", help="apply to job URLs, a list file, or found jobs")
    p.add_argument("urls", nargs="*", help="job URLs, job-list files, or Google Sheet/Doc links")
    p.add_argument("--list", help="job links: a .txt/.csv/.xlsx file, or a Google Sheet/Doc link")
    p.add_argument("--found", action="store_true", help="apply to jobs from `search` above min_fit_score")
    p.add_argument("--auto", action="store_true",
                   help="submit without review when nothing needs you and nothing failed")
    p.add_argument("--dry-run", action="store_true", help="fill forms but never submit")
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("route", help="show which tool would handle each URL")
    p.add_argument("urls", nargs="+")
    p.set_defaults(fn=cmd_route)

    sub.add_parser("tools", help="list installed sources and appliers").set_defaults(fn=cmd_tools)
    sub.add_parser("status", help="show every tracked job and its status").set_defaults(fn=cmd_status)
    p = sub.add_parser("applied", help="jobs you applied to, grouped by company")
    p.add_argument("company", nargs="?", help="filter by company name")
    p.set_defaults(fn=cmd_applied)

    p = sub.add_parser("ui", help="open the web interface in your browser")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab automatically")
    p.set_defaults(fn=cmd_ui)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
