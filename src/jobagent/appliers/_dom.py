"""Form-filling helpers shared by appliers."""
from __future__ import annotations

import re

from playwright.sync_api import Page

from ..answers import pick_option


def by_id(key: str) -> str:
    return f'[id="{key}"]'


def choose_combobox(page: Page, selector: str, value: str) -> bool:
    """Pick an option in a react-select style combobox."""
    box = page.locator(selector).first
    box.click()
    box.fill(value[:40])
    page.wait_for_timeout(300)
    options = page.locator("[role=option]")
    texts = [t.strip() for t in options.all_inner_texts()]
    chosen = pick_option(texts, value)
    if chosen is None:
        # filtering by typed text can hide the match; clear and look at everything
        box.fill("")
        page.wait_for_timeout(300)
        texts = [t.strip() for t in options.all_inner_texts()]
        chosen = pick_option(texts, value)
    if chosen is None:
        page.keyboard.press("Escape")
        return False
    options.nth(texts.index(chosen)).click()
    return True


def check_by_label(page: Page, scope: str, value: str) -> bool:
    label = page.locator(f"{scope} label", has_text=re.compile(re.escape(value), re.I)).first
    if label.count() == 0:
        return False
    label.click()
    return True
