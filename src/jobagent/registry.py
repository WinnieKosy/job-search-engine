"""Plugin system. A *source* finds jobs; an *applier* knows how to fill one kind
of application form. Drop a module into `sources/` or `appliers/`, decorate the
class, and it is picked up automatically."""
from __future__ import annotations

import importlib
import pkgutil
import re
from abc import ABC, abstractmethod

from playwright.sync_api import Page

from .models import Answer, Job, Question

SOURCES: dict[str, "Source"] = {}
APPLIERS: dict[str, "Applier"] = {}


class Source(ABC):
    name = ""
    description = ""

    @abstractmethod
    def search(self, profile: dict) -> list[Job]:
        """Return candidate jobs (title/company/description filled where possible)."""


class Applier(ABC):
    name = ""
    description = ""            # shown to the model when it has to pick a tool
    url_patterns: tuple[str, ...] = ()
    aggregator = False          # True = lists jobs but hosts no forms (see resolve())

    def matches(self, url: str) -> bool:
        return any(re.search(p, url, re.I) for p in self.url_patterns)

    def detect(self, url: str, html: str) -> str | None:
        """If this page embeds a form this applier handles, return the form's canonical URL."""
        return None

    @abstractmethod
    def open(self, page: Page, job: Job) -> None:
        """Navigate to the application form."""

    def resolve(self, page: Page, job: Job) -> str | None:
        """For aggregator sites: return the employer's application URL so the
        agent can hand the job to the right tool. Form-hosting tools return None."""
        return None

    def load_job(self, page: Page, job: Job) -> Job:
        """Fill in title/company/description if missing."""
        return job

    @abstractmethod
    def questions(self, page: Page, job: Job) -> list[Question]:
        """List every field on the form."""

    @abstractmethod
    def fill(self, page: Page, answers: list[Answer]) -> list[str]:
        """Fill the form. Return human-readable problems (empty list = all good)."""

    @abstractmethod
    def submit(self, page: Page) -> None:
        """Click the final submit button."""

    def confirmed(self, page: Page) -> bool:
        text = page.inner_text("body")[:5000].lower()
        return any(s in text for s in (
            "thank you for applying", "application has been submitted",
            "application was submitted", "we have received your application",
            "application received", "thanks for applying"))


def register_source(cls):
    SOURCES[cls.name] = cls()
    return cls


def register_applier(cls):
    APPLIERS[cls.name] = cls()
    return cls


def load_plugins() -> None:
    from . import appliers, sources
    for pkg in (appliers, sources):
        for mod in pkgutil.iter_modules(pkg.__path__):
            if not mod.name.startswith("_"):
                importlib.import_module(f"{pkg.__name__}.{mod.name}")
