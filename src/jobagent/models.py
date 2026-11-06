from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    source: str = "list"          # "list" = you supplied it; otherwise the source plugin's name
    fit_score: int | None = None
    fit_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)  # plugin-specific data


@dataclass
class Question:
    key: str                      # how the applier finds the field (an id, name or selector)
    label: str
    kind: str                     # text | textarea | select | multiselect | radio | checkbox | file
    required: bool = False
    options: list[str] = field(default_factory=list)
    description: str = ""         # extra text shown with the question (e.g. an agreement)


@dataclass
class Answer:
    question: Question
    value: str | list[str] | None
    origin: str                   # profile | rule | memory | you | draft | file | skip
    needs_user: bool = False
    reason: str = ""

    @property
    def display(self) -> str:
        if self.value is None:
            return "(left blank)"
        if isinstance(self.value, list):
            return ", ".join(self.value)
        return self.value
