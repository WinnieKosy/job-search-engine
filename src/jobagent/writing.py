"""Keeps generated writing sounding like a person wrote it."""
from __future__ import annotations

import re

STYLE = """Write in plain, direct first person, the way a competent professional writes an email.
- Use specific facts from the CV (projects, numbers, tools) instead of adjectives.
- Vary sentence length. No bullet points, no headings, no emojis.
- Never use em dashes. Never use these words or phrases: {banned}.
- Do not open with "I am writing to", "I am excited", or restating the job title.
- Do not invent anything that is not in the CV or profile."""

BANNED = [
    "delve", "thrilled", "excited to apply", "passionate", "leverage", "synergy",
    "fast-paced", "dynamic team", "i am writing to express", "tapestry", "testament",
    "spearheaded", "game-changer", "cutting-edge", "seamless", "robust", "furthermore",
    "moreover", "in today's", "unwavering", "honed", "eager to contribute",
    "align perfectly", "aligns perfectly", "perfect fit", "invaluable", "embark",
    "navigate the complexities", "ever-evolving", "showcase", "hit the ground running",
]

STYLE_PROMPT = STYLE.format(banned=", ".join(f'"{b}"' for b in BANNED))


def tells(text: str) -> list[str]:
    low = text.lower()
    found = [b for b in BANNED if b in low]
    if "—" in text:
        found.append("em dash")
    return found


def tidy(text: str) -> str:
    text = re.sub(r"\s*—\s*", ", ", text)
    text = text.replace("**", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def write(llm, system: str, user: str, temperature: float = 0.6, tries: int = 3) -> str:
    """Generate text, regenerating if it contains phrases that read as machine-written."""
    text = ""
    feedback = ""
    for _ in range(tries):
        text = llm.chat(system + "\n\n" + STYLE_PROMPT, user + feedback, temperature=temperature)
        found = tells(text)
        if not found:
            break
        feedback = f"\n\nYour last draft used: {', '.join(found)}. Rewrite without them."
    return tidy(text)
