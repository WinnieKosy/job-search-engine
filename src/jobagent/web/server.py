"""Local web UI: `jobagent ui` then open http://localhost:8000.

The agent runs in one background worker thread (the browser automation must
stay on a single thread). When it needs you, it posts a "pending" prompt into
shared state; the page shows it and posts your reply back to /api/respond."""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import uuid
from pathlib import Path

import httpx
import yaml
from flask import Flask, abort, jsonify, request, send_file

from .. import tasks
from ..config import DATA, DEFAULT_PROFILE, REQUIRED, ROOT
from ..models import Answer, Job
from ..ui import UI, Stop

STATIC = Path(__file__).parent / "static"
EXAMPLE_PROFILE = ROOT / "config" / "profile.example.yaml"
CV_EXTS = {".pdf", ".docx", ".md", ".txt"}
LIST_EXTS = {".xlsx", ".xlsm", ".csv", ".tsv", ".txt"}


# ---------------------------------------------------------------- shared state

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.logs: list[str] = []
        self.task: str | None = None        # "apply" | "search" | "parse-cv" while running
        self.job: dict = {}
        self.pending: dict | None = None
        self.replies: queue.Queue = queue.Queue()
        self.stop = threading.Event()

    def log(self, msg: str) -> None:
        with self.lock:
            self.logs.append(f"{time.strftime('%H:%M:%S')}  {msg}")
            del self.logs[:-2000]


STATE = State()


def _answer_dict(i: int, a: Answer) -> dict:
    q = a.question
    return {"index": i, "label": q.label, "kind": q.kind, "required": q.required,
            "options": q.options, "description": q.description[:3000], "value": a.value,
            "origin": a.origin, "needs_user": a.needs_user, "reason": a.reason}


class WebUI(UI):
    def __init__(self, state: State):
        self.s = state

    def _prompt(self, kind: str, **payload) -> dict:
        pid = uuid.uuid4().hex
        with self.s.lock:
            self.s.pending = {"id": pid, "kind": kind, **payload}
        try:
            while True:
                if self.s.stop.is_set():
                    raise Stop
                try:
                    reply = self.s.replies.get(timeout=0.5)
                except queue.Empty:
                    continue
                if reply.get("id") == pid:
                    return reply
        finally:
            with self.s.lock:
                self.s.pending = None

    def log(self, msg):
        self.s.log(msg)

    def job_started(self, info):
        with self.s.lock:
            self.s.job = dict(info)

    def ask(self, a, company):
        r = self._prompt("question", company=company, answer=_answer_dict(0, a))
        return r.get("value"), bool(r.get("remember"))

    def review(self, answers, problems, warnings, files, dry_run):
        r = self._prompt(
            "review", dry_run=dry_run, problems=problems, warnings=warnings,
            answers=[_answer_dict(i, a) for i, a in enumerate(answers)],
            cv_pdf=files["cv_pdf"], cover_pdf=files["cover_pdf"], cover_text=files["cover_text"])
        action = r.get("action")
        if action == "edit":
            return "edit", int(r["index"]), r.get("value")
        if action == "stop":
            raise Stop
        return ("done" if dry_run else action), None, None

    def confirm(self, msg):
        return bool(self._prompt("confirm", message=msg).get("yes"))

    def wait(self, msg):
        self._prompt("wait", message=msg)

    def text(self, msg):
        return (self._prompt("text", message=msg).get("value") or "").strip()

    def sleep(self, seconds):
        if self.s.stop.wait(seconds):
            raise Stop


def _start(name: str, fn) -> tuple[dict, int]:
    with STATE.lock:
        if STATE.task:
            return {"error": f"Already running: {STATE.task}. Wait for it or press Stop."}, 409
        STATE.task, STATE.job, STATE.pending = name, {}, None
        STATE.stop.clear()
        while not STATE.replies.empty():
            STATE.replies.get_nowait()

    def worker():
        try:
            fn()
        except Stop:
            STATE.log("Stopped.")
        except SystemExit as e:
            STATE.log(f"! {e}")
        except Exception as e:
            STATE.log(f"! {e.__class__.__name__}: {e}")
        finally:
            with STATE.lock:
                STATE.task, STATE.pending = None, None

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}, 200


# ---------------------------------------------------------------- helpers

def _profile_text() -> str:
    return DEFAULT_PROFILE.read_text() if DEFAULT_PROFILE.exists() else EXAMPLE_PROFILE.read_text()


def _validate_profile(text: str) -> str | None:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        return f"That isn't valid YAML: {e}"
    personal = data.get("personal") or {}
    missing = [k for k in REQUIRED if not personal.get(k)]
    if missing:
        return f"Fill in personal: {', '.join(missing)}"
    return None


def _set_cv_path(rel: str) -> None:
    text = _profile_text()
    line = f"cv_path: {rel}"
    if re.search(r"(?m)^cv_path:.*$", text):
        text = re.sub(r"(?m)^cv_path:.*$", line, text, count=1)
    else:
        text = line + "\n" + text
    DEFAULT_PROFILE.write_text(text)


def _ollama_status(profile: dict | None) -> dict:
    cfg = (profile or {}).get("llm") or yaml.safe_load(EXAMPLE_PROFILE.read_text())["llm"]
    host, model = cfg.get("host", "http://localhost:11434").rstrip("/"), cfg.get("model", "")
    try:
        r = httpx.get(f"{host}/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return {"running": False, "model": model, "installed": False, "models": []}
    installed = model in models or f"{model}:latest" in models
    return {"running": True, "model": model, "installed": installed, "models": models}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- app

def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
    DATA.mkdir(exist_ok=True)

    @app.get("/")
    def index():
        return send_file(STATIC / "index.html")

    @app.get("/api/status")
    def status():
        profile, profile_error = None, None
        if DEFAULT_PROFILE.exists():
            try:
                profile = tasks.load_profile()
            except SystemExit as e:
                profile_error = str(e)
        cv_path = Path(profile["cv_path"]) if profile else None
        return jsonify({
            "profile": DEFAULT_PROFILE.exists() and not profile_error,
            "profile_error": profile_error,
            "cv_file": cv_path.name if cv_path and cv_path.exists() else None,
            "cv_parsed": tasks.CV_JSON.exists(),
            "ollama": _ollama_status(profile),
        })

    @app.get("/api/state")
    def state():
        since = int(request.args.get("since", 0))
        with STATE.lock:
            return jsonify({"task": STATE.task, "job": STATE.job, "pending": STATE.pending,
                            "logs": STATE.logs[since:], "log_count": len(STATE.logs)})

    @app.post("/api/respond")
    def respond():
        STATE.replies.put(request.get_json(force=True))
        return jsonify({"ok": True})

    @app.post("/api/stop")
    def stop():
        STATE.stop.set()
        return jsonify({"ok": True})

    # -- profile & CV
    @app.get("/api/profile")
    def get_profile():
        return jsonify({"text": _profile_text(), "exists": DEFAULT_PROFILE.exists()})

    @app.post("/api/profile")
    def save_profile():
        text = request.get_json(force=True).get("text", "")
        err = _validate_profile(text)
        if err:
            return jsonify({"error": err}), 400
        DEFAULT_PROFILE.write_text(text)
        return jsonify({"ok": True})

    @app.post("/api/cv")
    def upload_cv():
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "Choose a file."}), 400
        ext = Path(f.filename).suffix.lower()
        if ext not in CV_EXTS:
            return jsonify({"error": f"CV must be one of {', '.join(sorted(CV_EXTS))}"}), 400
        if not DEFAULT_PROFILE.exists():
            return jsonify({"error": "Save your profile first (Setup > Profile)."}), 400
        dest = ROOT / "config" / f"my_cv{ext}"
        f.save(dest)
        _set_cv_path(f"config/{dest.name}")

        def run():
            profile, llm = tasks.setup()
            tasks.parse_cv(profile, llm, STATE.log)
        body, code = _start("parse-cv", run)
        return jsonify(body), code

    @app.post("/api/cv/parse")
    def reparse_cv():
        def run():
            profile, llm = tasks.setup()
            tasks.parse_cv(profile, llm, STATE.log)
        body, code = _start("parse-cv", run)
        return jsonify(body), code

    @app.get("/api/cvjson")
    def get_cvjson():
        if not tasks.CV_JSON.exists():
            return jsonify({"text": ""})
        return jsonify({"text": tasks.CV_JSON.read_text()})

    @app.post("/api/cvjson")
    def save_cvjson():
        text = request.get_json(force=True).get("text", "")
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            return jsonify({"error": f"Not valid JSON: {e}"}), 400
        tasks.CV_JSON.write_text(text)
        return jsonify({"ok": True})

    # -- running
    @app.post("/api/apply")
    def apply():
        from ..joblist import is_google_link, read_jobs
        links = request.form.get("links", "")
        dry_run = request.form.get("dry_run") == "true"
        auto = request.form.get("auto") == "true"
        jobs: list[Job] = []
        try:
            f = request.files.get("file")
            if f and f.filename:
                ext = Path(f.filename).suffix.lower()
                if ext not in LIST_EXTS:
                    return jsonify({"error": f"List must be one of {', '.join(sorted(LIST_EXTS))}"}), 400
                up = DATA / "uploads"
                up.mkdir(exist_ok=True)
                dest = up / f"joblist-{int(time.time())}{ext}"
                f.save(dest)
                jobs += read_jobs(dest)
            for line in links.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                jobs += read_jobs(line) if is_google_link(line) else [Job(url=line, source="list")]
        except SystemExit as e:
            return jsonify({"error": str(e)}), 400
        seen, unique = set(), []
        for j in jobs:
            if j.url not in seen:
                seen.add(j.url)
                unique.append(j)
        if not unique:
            return jsonify({"error": "No job links found."}), 400

        def run():
            from ..agent import Agent
            profile, llm = tasks.setup()
            cv, cv_text = tasks.load_cv(profile)
            STATE.log(f"{len(unique)} job(s) to process{' (dry run)' if dry_run else ''}.")
            Agent(profile, llm, cv, cv_text, auto=auto, dry_run=dry_run, ui=WebUI(STATE)).run(unique)
        body, code = _start("apply", run)
        body["count"] = len(unique)
        return jsonify(body), code

    @app.post("/api/search")
    def search():
        def run():
            profile, llm = tasks.setup()
            tasks.search(profile, llm, STATE.log, STATE.stop.is_set)
            STATE.log("Search finished. See the Found jobs list.")
        body, code = _start("search", run)
        return jsonify(body), code

    # -- records
    @app.get("/api/found")
    def found():
        from ..tracker import Tracker
        return jsonify([{"url": j.url, "title": j.title, "company": j.company, "location": j.location,
                         "fit": j.fit_score, "reason": j.fit_reason} for j in Tracker().found(0)])

    @app.get("/api/jobs")
    def jobs():
        from ..tracker import Tracker
        return jsonify([{"status": s, "fit": f, "company": c, "title": t, "url": u, "updated": w}
                        for s, f, c, t, u, w in Tracker().rows()])

    @app.get("/api/applications")
    def applications():
        from ..tracker import Tracker
        return jsonify([{"company": c, "title": t, "url": u, "applied": w, "folder": fo}
                        for c, t, u, w, fo in Tracker().applied()])

    @app.get("/files")
    def files():
        path = Path(request.args.get("path", ""))
        if not _inside(path, DATA / "applications") or not path.is_file():
            abort(404)
        return send_file(path)

    return app


def serve(port: int = 8000, open_browser: bool = True) -> None:
    import webbrowser
    app = create_app()
    url = f"http://127.0.0.1:{port}"
    print(f"Job agent UI running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)
