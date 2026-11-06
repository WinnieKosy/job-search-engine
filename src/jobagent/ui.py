"""How the agent talks to you. The agent only calls these methods, so the same
agent runs in the terminal (TerminalUI) or behind the web page (web.WebUI)."""
from __future__ import annotations

import re
import textwrap
import time

from .models import Answer


class Stop(Exception):
    """You asked the agent to stop."""


class UI:
    def log(self, msg: str) -> None:
        raise NotImplementedError

    def job_started(self, info: dict) -> None:
        """info: index, total, url, and later title/company/fit/etc. as they become known."""

    def ask(self, answer: Answer, company: str) -> tuple[object, bool]:
        """Ask you a form question. Return (value, remember). Value None = leave blank."""
        raise NotImplementedError

    def review(self, answers: list[Answer], problems: list[str], warnings: list[str],
               files: dict, dry_run: bool) -> tuple[str, int | None, object]:
        """Return ("submit"|"skip"|"done", None, None) or ("edit", index, new_value)."""
        raise NotImplementedError

    def confirm(self, msg: str) -> bool:
        raise NotImplementedError

    def wait(self, msg: str) -> None:
        """Pause until you've done something in the browser window."""
        raise NotImplementedError

    def text(self, msg: str) -> str:
        raise NotImplementedError

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


class TerminalUI(UI):
    def log(self, msg):
        print(msg)

    def job_started(self, info):
        print(f"\n=== [{info['index']}/{info['total']}] {info.get('title') or info['url']}")

    def ask(self, a, company):
        q = a.question
        print(f"\n  > {q.label}{' *' if q.required else ''}   [{a.reason}]")
        if q.description:
            print(textwrap.indent(textwrap.shorten(q.description, 600), "    "))
        if a.value:
            print(f"    suggested: {a.display}")
        for i, o in enumerate(q.options, 1):
            print(f"    {i}. {o}")
        raw = _input("    your answer (number/text, Enter = keep suggestion, '-' = leave blank, 'q' = quit): ")
        if raw == "q":
            raise Stop
        value = a.value
        if raw == "-":
            value = None
        elif raw:
            if q.options and raw.isdigit() and 1 <= int(raw) <= len(q.options):
                raw = q.options[int(raw) - 1]
            value = [raw] if q.kind == "multiselect" else raw
        remember = bool(value) and _input("    remember this answer for future applications? [y/N] ").lower() == "y"
        return value, remember

    def _show(self, answers, problems, warnings, files):
        print(f"\n  CV:           {files['cv_pdf']}\n  Cover letter: {files['cover_pdf']}\n")
        for i, a in enumerate(answers, 1):
            val = textwrap.shorten(a.display.replace("\n", " "), 70)
            print(f"  {i:>2}. [{a.origin:<7}] {textwrap.shorten(a.question.label, 48):<48} {val}")
        for w in problems + warnings:
            print(f"  ! {w}")

    def review(self, answers, problems, warnings, files, dry_run):
        while True:
            self._show(answers, problems, warnings, files)
            if dry_run:
                _input("  Dry run: form filled but not submitted. Press Enter to continue. ")
                return "done", None, None
            cmd = _input("\n  [s] submit  [e N] edit answer N  [v N] view answer N  [k] skip  [q] quit\n"
                         "  (you can also change anything directly in the browser before submitting) > ")
            if cmd == "s":
                return "submit", None, None
            if cmd == "k":
                return "skip", None, None
            if cmd == "q":
                raise Stop
            m = re.fullmatch(r"([ev])\s*(\d+)", cmd)
            if m and 1 <= int(m.group(2)) <= len(answers):
                i = int(m.group(2)) - 1
                if m.group(1) == "v":
                    print(textwrap.indent(answers[i].display, "    "))
                    _input("    Enter to go back ")
                else:
                    value, _ = self.ask(answers[i], "")
                    return "edit", i, value

    def confirm(self, msg):
        return _input(f"  {msg} [y/N] ").lower() == "y"

    def wait(self, msg):
        _input(f"  {msg} Press Enter when done. ")

    def text(self, msg):
        return _input(f"  {msg} ")
