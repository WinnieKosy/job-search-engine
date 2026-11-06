"""hiringcafe.com (formerly hiring.cafe) is a job aggregator: it doesn't host
application forms, it links to the employer's own (Greenhouse, Lever, Workday...).
This tool opens the hiringcafe page, finds where its Apply button leads, and
hands the job to the tool for that site.

The site sits behind Cloudflare's bot check. If the check appears, you complete
it yourself in the browser window; this tool does not try to get around it."""
from __future__ import annotations

import re
import time

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from ..models import Answer, Job, Question
from ..registry import Applier, register_applier

HOSTS = re.compile(r"(^|\.)(hiring\.cafe|hiringcafe\.com)$", re.I)


def _blocked(page: Page) -> bool:
    return "just a moment" in page.title().lower() or "security verification" in page.title().lower()


def _external(href: str | None) -> bool:
    if not href or not href.startswith("http"):
        return False
    host = href.split("/")[2]
    return not HOSTS.search(host)


@register_applier
class HiringCafe(Applier):
    name = "hiringcafe"
    description = ("hiringcafe.com / hiring.cafe job pages. An aggregator: finds the employer's "
                   "real application link and passes the job to that site's tool.")
    url_patterns = (r"https?://(www\.)?(hiring\.cafe|hiringcafe\.com)/",)
    aggregator = True

    def open(self, page: Page, job: Job) -> None:
        page.goto(job.url, wait_until="domcontentloaded")
        if _blocked(page):
            # Shown in the browser window; you complete it there.
            deadline = time.time() + 180
            while _blocked(page) and time.time() < deadline:
                page.wait_for_timeout(2000)
        page.wait_for_timeout(2500)

    def resolve(self, page: Page, job: Job) -> str | None:
        """Return the employer's application URL."""
        if not _blocked(page):
            if not job.title:
                h1 = page.locator("h1")
                job.title = h1.first.inner_text().strip() if h1.count() else ""
            apply = page.get_by_role("link", name=re.compile(r"apply", re.I))
            for i in range(apply.count()):
                href = apply.nth(i).get_attribute("href")
                if _external(href):
                    return href
            # Some Apply buttons open the employer page in a new tab instead of linking.
            button = page.get_by_role("button", name=re.compile(r"apply", re.I))
            if button.count():
                try:
                    with page.context.expect_page(timeout=10000) as new:
                        button.first.click()
                    url = new.value.url
                    new.value.close()
                    if _external(url):
                        return url
                except PWTimeout:
                    if _external(page.url):
                        return page.url
        return None  # the agent asks you to paste the link

    # hiringcafe never hosts the form itself; the agent re-routes after resolve().
    def questions(self, page: Page, job: Job) -> list[Question]:
        return []

    def fill(self, page: Page, answers: list[Answer]) -> list[str]:
        return []

    def submit(self, page: Page) -> None:
        raise RuntimeError("hiringcafe pages have no form; resolve() should have redirected")
