"""Best-effort applier for single-page forms no specific plugin handles. It reads
the visible fields from the page, so it works on simple forms but not on
multi-step wizards or sites that need an account. Always review its work."""
from __future__ import annotations

import re

from playwright.sync_api import Page

from ..models import Answer, Job, Question
from ..registry import Applier, register_applier

EXTRACT = r"""
() => {
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) || el.type === 'file';
  const labelOf = el => {
    if (el.labels && el.labels.length) return el.labels[0].innerText;
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const by = el.getAttribute('aria-labelledby');
    if (by && document.getElementById(by)) return document.getElementById(by).innerText;
    const wrap = el.closest('fieldset, .field, .form-group, div');
    const l = wrap && wrap.querySelector('label, legend');
    return (l && l.innerText) || el.placeholder || el.name || el.id;
  };
  const out = []; const radios = {};
  document.querySelectorAll('input, textarea, select').forEach((el, i) => {
    if (['hidden','submit','button','image','reset','search'].includes(el.type) || !visible(el)) return;
    if (!el.id && !el.name) el.setAttribute('data-jobagent', String(i));
    const sel = el.id ? `[id="${el.id}"]` : el.name ? `[name="${el.name}"]` : `[data-jobagent="${i}"]`;
    const required = el.required || el.getAttribute('aria-required') === 'true';
    if (el.type === 'radio') {
      const g = radios[el.name] = radios[el.name] || {key: `[name="${el.name}"]`, kind: 'radio', options: [], required,
        label: (el.closest('fieldset') && el.closest('fieldset').querySelector('legend')?.innerText) || el.name};
      g.options.push((el.labels && el.labels[0] && el.labels[0].innerText) || el.value);
      return;
    }
    const kind = el.tagName === 'TEXTAREA' ? 'textarea' : el.tagName === 'SELECT' ? 'select'
               : el.type === 'file' ? 'file' : el.type === 'checkbox' ? 'checkbox' : 'text';
    const options = el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()).filter(t => t) : [];
    out.push({key: sel, kind, label: labelOf(el).trim().slice(0, 300), required, options});
  });
  return out.concat(Object.values(radios));
}
"""


@register_applier
class Generic(Applier):
    name = "generic"
    description = "Best-effort filler for any simple single-page application form not covered by another tool."

    def open(self, page: Page, job: Job) -> None:
        page.goto(job.url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        apply = page.get_by_role("link", name=re.compile(r"^\s*apply( now| for this job)?\s*$", re.I))
        if apply.count() and not page.locator("form input:visible").count():
            apply.first.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2500)

    def load_job(self, page: Page, job: Job) -> Job:
        if not job.description:
            main = page.locator("main")
            job.description = (main.first.inner_text() if main.count() else page.inner_text("body"))[:8000]
        job.title = job.title or page.title()
        job.company = job.company or re.sub(r"^www\.", "", page.url.split("/")[2]).split(".")[0].title()
        return job

    def questions(self, page: Page, job: Job) -> list[Question]:
        return [Question(key=f["key"], label=f["label"], kind=f["kind"], required=f["required"],
                         options=f["options"]) for f in page.evaluate(EXTRACT)]

    def fill(self, page: Page, answers: list[Answer]) -> list[str]:
        problems = []
        for a in answers:
            if a.value in (None, "", []):
                continue
            q = a.question
            try:
                el = page.locator(q.key)
                if q.kind == "file":
                    el.first.set_input_files(a.value)
                elif q.kind in ("text", "textarea"):
                    el.first.fill(a.value)
                elif q.kind == "select":
                    el.first.select_option(label=a.value)
                elif q.kind == "radio":
                    page.locator(f"label:has({q.key})", has_text=a.value).first.click()
                elif q.kind == "checkbox":
                    if str(a.value).lower() in ("yes", "true", "1"):
                        el.first.check()
            except Exception as e:
                problems.append(f"couldn't fill '{q.label[:60]}': {str(e)[:120]}")
        return problems

    def submit(self, page: Page) -> None:
        btn = page.locator("button[type=submit], input[type=submit]")
        if not btn.count():
            btn = page.get_by_role("button", name=re.compile(r"submit|apply|send", re.I))
        btn.last.click()
        page.wait_for_timeout(4000)
