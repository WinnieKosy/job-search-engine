from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DEFAULT_PROFILE = ROOT / "config" / "profile.yaml"

REQUIRED = ["first_name", "last_name", "email"]


def load_profile(path: Path | None = None) -> dict:
    path = Path(path or DEFAULT_PROFILE)
    if not path.exists():
        raise SystemExit(f"No profile at {path}. Copy config/profile.example.yaml to "
                         f"config/profile.yaml and fill it in.")
    profile = yaml.safe_load(path.read_text()) or {}
    personal = profile.get("personal") or {}
    missing = [k for k in REQUIRED if not personal.get(k)]
    if missing:
        raise SystemExit(f"profile.yaml is missing personal.{', personal.'.join(missing)}")
    cv = Path(profile.get("cv_path", ""))
    profile["cv_path"] = str(cv if cv.is_absolute() else ROOT / cv)
    profile.setdefault("llm", {"provider": "ollama", "model": "qwen3:14b"})
    profile.setdefault("answers", [])
    profile.setdefault("search", {})
    profile.setdefault("apply", {})
    DATA.mkdir(exist_ok=True)
    return profile
