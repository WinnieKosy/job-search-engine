# jobagent

A local agent that finds jobs, tailors your CV and cover letter for each one, fills in the
application form, and submits after you approve. It runs on your own model through Ollama.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/playwright install chromium
ollama pull qwen3:14b
cp config/profile.example.yaml config/profile.yaml   # fill it in, put your CV in config/
.venv/bin/jobagent parse-cv                           # then check data/cv.json
```

## Web interface

```bash
source .venv/bin/activate
jobagent ui                 # opens http://127.0.0.1:8000 in your browser
```

- **Setup**: Ollama status, edit your profile, upload your CV, check what the model read from it.
- **Apply**: paste job links or a Google Sheet/Doc link, or upload a spreadsheet; press Start.
  Questions that need you, and the review before submitting, appear here as cards.
- **Found jobs**: run a search, tick jobs, apply to the selected ones.
- **Applications**: what you've applied to, by company, plus everything the agent has seen.

Leave the terminal running while you use it (Ctrl+C stops it). It only listens on your own
machine. A separate Chrome window opens to fill the forms; you can watch and edit there too.

## Use from the terminal

```bash
jobagent apply --dry-run URL           # fill a form, never submit: start here
jobagent apply --list jobs.xlsx        # apply to every link in a spreadsheet (.xlsx/.csv/.txt)
jobagent applied                       # what you've applied to, by company
jobagent search                        # find + score jobs on your Greenhouse boards
jobagent apply --found                 # apply to the ones above min_fit_score
jobagent route URL                     # which tool would handle this URL?
jobagent tools | jobagent status
```

For each job it: routes the URL to a tool, reads the job, scores your fit, writes a tailored
CV and cover letter (both fact-checked against your CV), answers the form, asks you anything
only you can answer, fills the form in a visible browser, and waits for `s` to submit.
Everything is saved under `data/applications/<date>-<company>-<role>/`.

**Always asks you:** questions about AI use, legal agreements (arbitration, attestations),
anything your CV and profile can't answer, CAPTCHAs and email verification codes.
`--auto` only skips the final review when none of those came up and nothing failed.

## Spreadsheets

Each row needs a job link: either the URL typed in a cell or a clickable hyperlink (the
cell text can be anything, e.g. "Apply"). Optional header columns `Company`,
`Title`/`Role`/`Position` and `Location` are picked up. Google Sheets: File > Download > .xlsx or .csv.

## Sites

| Tool | What it does |
|---|---|
| `greenhouse` | Fills Greenhouse forms; `search` finds jobs on the boards you list |
| `hiringcafe` | Opens a hiringcafe.com job, follows its Apply link to the employer's form, hands off to that tool. The site blocks automated access, so it can't search; if its "verify you are human" check appears, you complete it in the browser window. |
| `generic` | Best-effort filler for simple one-page forms |

## Adding a site

Create `src/jobagent/appliers/<site>.py`; it's discovered automatically:

```python
from ..registry import Applier, register_applier

@register_applier
class Lever(Applier):
    name = "lever"
    description = "Lever forms (jobs.lever.co)"   # the model reads this when routing
    url_patterns = (r"jobs\.lever\.co/",)

    def open(self, page, job): ...        # go to the form
    def questions(self, page, job): ...   # -> list[Question]
    def fill(self, page, answers): ...    # -> list of problems
    def submit(self, page): ...
```

Routing order: URL patterns -> embedded-form detection (`detect`) -> the model picks from
the tool descriptions -> `generic` best-effort filler. Job-finding sites (aggregators)
go in `src/jobagent/sources/` as a `Source` with a `search(profile)` method.
