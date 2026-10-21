# Bay Area Job Monitor

A fully local, open-source job-monitoring pipeline. It scrapes the career
pages of 16 tech companies, filters down to entry-level (0-1 years or no experience) SDR /
BDR / BDA / Data-Analyst-and-adjacent roles in the San Francisco Bay Area,
and emails you an HTML digest. Two [CrewAI](https://www.crewai.com/)
agents running against a local [Ollama](https://ollama.com/) model run it. No paid
APIs, no cloud LLM calls, no data leaving your machine except the outbound
Gmail SMTP send.

## What it does

- Scrapes: Salesforce, Intel (Workday), GoFundMe, Databricks, Waymo, BILL
  (Greenhouse), AWS, Google, Atlassian, Uber, NVIDIA, Microsoft, LinkedIn,
  Splashtop, Toss, SAP (browser-rendered).
- Filters to San Francisco, San Jose, Sunnyvale, Mountain View, Palo Alto,
  Santa Clara, Redwood City, Oakland, Menlo Park, Cupertino, San Mateo,
  Foster City, Burlingame, San Bruno, Milpitas, Fremont, Berkeley,
  Emeryville, or Bay-Area-qualified Remote.
- Filters to SDR/BDR/BDA/Data-Analyst-and-adjacent titles under the names
  companies actually use (SDR, BDR, ADR, MDR, LDR, Inside Sales Rep,
  Associate Account Executive, Junior/Associate/Entry Level Data Analyst,
  BI Analyst, Reporting Analyst, Sales/RevOps Analyst, and more in
  `config/companies.yaml`), excludes seniority-qualified titles
  (Senior/Sr./Staff/Lead/Manager/Director/II/2/etc.), and excludes roles whose
  JD requires more than 1 year of experience anywhere. "Preferred" years
  don't count against a job.
- Shows, per job: title, company, location, work mode (Remote/Hybrid/Onsite),
  years of experience, and a pay range: the employer's own stated figure
  when the JD includes one, otherwise a clearly-labeled LLM-generated
  estimate grounded in your resume.
- Tracks what it's already shown you across runs, so scheduled runs surface
  **new** postings instead of re-sending the same list every time. The
  email has both a "New since last run" section and a full "All current
  openings" section. Jobs only count as seen after a real email goes out, so
  a `--dry-run` or a failed send doesn't use up the "new" list.
- Flags (never silently drops) any application link that fails an automated
  reachability check, since corporate bot-detection routinely blocks
  automated requests to pages that work fine in a real browser.

## How your data stays yours

`config/companies.yaml` and everything in `resumes/` are ordinary files on
disk. Edit, add to, or replace either one whenever you like. Every run
(including scheduled ones) reads whatever's currently there. There's no
upload step and nothing is baked into the code. **`resumes/` is gitignored
and its contents are never committed**. Resumes contain your name, contact
details, and work history, and this is meant to be a public repo.

## Prerequisites

- **Python 3.10-3.13**, not 3.14. CrewAI does not yet support 3.14. Check
  what you have:
  ```bash
  python3 --version
  ```
  If you're on 3.14 (or don't have Python at all), install a compatible
  version via [Homebrew](https://brew.sh/) or [pyenv](https://github.com/pyenv/pyenv):
  ```bash
  brew install python@3.13
  ```
- **Ollama**, installed and running, with a model pulled. Two install paths:

  **If you have Homebrew** (or admin/sudo rights to install it):
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  brew install ollama
  brew services start ollama   # or: ollama serve
  ```

  **If you don't** (e.g. a standard, non-administrator macOS account, where Homebrew's
  installer needs `sudo`, which will fail with "Need sudo access... needs to be
  an Administrator" on those accounts), download and run Ollama directly, no
  admin rights required:
  ```bash
  curl -fsSL -o /tmp/Ollama-darwin.zip https://ollama.com/download/Ollama-darwin.zip
  mkdir -p ~/Applications && unzip -q /tmp/Ollama-darwin.zip -d ~/Applications

  # Put the CLI on your PATH (~/.local/bin is on PATH by default on most setups;
  # add it to your shell profile if `which ollama` doesn't find it afterward)
  mkdir -p ~/.local/bin
  ln -sf ~/Applications/Ollama.app/Contents/Resources/ollama ~/.local/bin/ollama

  # Run the server directly, no GUI/login-item needed
  nohup ollama serve > /tmp/ollama-serve.log 2>&1 &
  disown
  ```

  Either way, then pull the model:
  ```bash
  ollama pull llama3.2
  ```
  Confirm it's reachable:
  ```bash
  curl http://localhost:11434/api/tags
  ```

  For scheduled/unattended runs (see Scheduling below), the server needs to
  already be running when `main.py` executes. Either keep the `ollama serve`
  process running persistently (a `launchd` agent works well for this on
  macOS, the same mechanism used for the job itself), or add a check at the
  start of your cron job that starts it if it isn't already up.

## Installation

```bash
cd bay-area-job-monitor
python3.13 -m venv .venv        # use your compatible Python, not a 3.14 default
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Configuration

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Meaning |
|---|---|
| `OLLAMA_MODEL` | Defaults to `ollama/llama3.2`. Also supports e.g. `ollama/mistral`. |
| `OLLAMA_BASE_URL` | Defaults to `http://localhost:11434`. |
| `GMAIL_SENDER` | The Gmail address the digest is sent from. |
| `GMAIL_APP_PASSWORD` | A 16-character **App Password** (see below). Never your real password. |
| `GMAIL_RECIPIENT` | Where the digest is sent (can be the same address). |
| `DRY_RUN` | Optional. `true` writes the digest to a local file instead of sending. |

### Generating a Gmail App Password

App Passwords require 2-Step Verification to be enabled on your Google
Account first.

1. Turn on 2-Step Verification: <https://myaccount.google.com/security> → "2-Step Verification".
2. Go to <https://myaccount.google.com/apppasswords> (or Security → App
   Passwords, if prompted for your password again).
3. Create a new app password and name it something like "Bay Area Job Monitor".
4. Google shows you a 16-character code (spaces don't matter). Copy it into
   `GMAIL_APP_PASSWORD` in `.env`.

### Adding your resume

Drop one or more resume files (`.pdf`, `.docx`, `.md`, or `.txt`) into
`resumes/`. They're read fresh on every run and used only to ground the
LLM's pay-range estimate when a job's own posting doesn't state one. Swap
them out anytime, no config change needed. If the folder is empty, pay
estimates are simply labeled unavailable; nothing breaks.

### Customizing companies and search criteria

Edit `config/companies.yaml` anytime:
- Set `enabled: false` on a company to skip it.
- Add a new company under the `render` strategy with its own `url`.
- Tune `settings.role_keywords` / `settings.seniority_exclude_keywords` /
  `settings.max_years_experience` to change what counts as a match.
- If a `render`-strategy company keeps returning zero jobs (check `logs/run.log`
  for a warning naming it), add a `selectors` override for it:
  ```yaml
  selectors:
    job_card: "div.job-card"    # one CSS selector per posting
    title: ".job-title"          # relative to job_card
    location: ".job-location"    # relative to job_card
    link: "a"                    # relative to job_card
    link_attr: "href"
    description: ".job-description"  # on the job's own detail page
  ```

## Running

```bash
python main.py --dry-run   # writes output/digest_preview.html, sends nothing
python main.py             # sends the real email
```

## Scheduling

### cron (macOS/Linux)

```bash
crontab -e
```
```
# Every weekday at 8am
0 8 * * 1-5 cd /path/to/bay-area-job-monitor && .venv/bin/python main.py >> logs/cron.log 2>&1
```

### systemd timer (Linux servers)

`/etc/systemd/system/bay-area-job-monitor.service`:
```ini
[Unit]
Description=Bay Area Job Monitor

[Service]
Type=oneshot
WorkingDirectory=/path/to/bay-area-job-monitor
ExecStart=/path/to/bay-area-job-monitor/.venv/bin/python main.py
```

`/etc/systemd/system/bay-area-job-monitor.timer`:
```ini
[Unit]
Description=Run Bay Area Job Monitor daily

[Timer]
OnCalendar=Mon..Fri 08:00
Persistent=true

[Install]
WantedBy=timers.target
```
```bash
sudo systemctl enable --now bay-area-job-monitor.timer
```

### launchd (macOS-native alternative to cron)

Create `~/Library/LaunchAgents/com.user.bayareajobmonitor.plist` with a
`ProgramArguments` array pointing at `.venv/bin/python main.py` and a
`WorkingDirectory` set to the project path, then:
```bash
launchctl load ~/Library/LaunchAgents/com.user.bayareajobmonitor.plist
```

## Known limitations

- **Render-strategy selectors are best-effort.** AWS, Google, Atlassian,
  Uber, NVIDIA, Microsoft, LinkedIn, Splashtop, Toss, and SAP are scraped
  generically (schema.org JSON-LD first, then a heuristic scan) since these
  sites' DOM structures aren't hand-verified and change over time. If a
  company keeps returning zero results, check `logs/run.log` and add a
  `selectors` override.
- **Workday facets currently cover one city per company.** Salesforce and
  Intel's location facets are set to their primary Bay Area office only, so
  postings tagged under a different Bay Area city in Workday's own taxonomy
  won't surface until that facet ID is added to `companies.yaml` too.
- **Pay-range estimates are LLM-generated guesses**, not real compensation
  data, whenever a job's own posting doesn't state a range. They're always
  visibly labeled "(estimated)" in the email. Never treat them as accurate.
- **Experience-level filtering is regex-based pattern matching**, not true
  language understanding. It can misfire on unusually worded job
  descriptions. A JD that never mentions years of experience at all is
  included by default rather than excluded, since the title filters have
  already removed senior-sounding roles. A range like "1-2 years" counts as
  1 year and is kept.
- **Link verification has a false-positive rate.** Many corporate career
  sites block automated requests even when the link works fine in a real
  browser, so a "verify before applying" marker is often not a sign of an
  actually broken link.
- **Toss returning zero matches most runs is expected**, not a bug: its
  postings are almost entirely Seoul-based.
- This scrapes public career pages for personal job-search use. Respect
  each site's Terms of Service and `robots.txt`. The scraper uses a
  descriptive User-Agent and a delay between companies, but you're
  responsible for how you run it.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `crewai` install fails, or the venv behaves oddly | You're on Python 3.14. Recreate the venv with 3.10-3.13. |
| Homebrew install fails with "Need sudo access... needs to be an Administrator" | Your macOS account isn't an admin. Use the direct-download Ollama install path above instead. It needs no admin rights. |
| Crew run fails with a connection error | Ollama isn't running. `ollama serve` or `brew services start ollama`, then confirm `curl http://localhost:11434/api/tags` responds. |
| Playwright errors about a missing browser | Run `playwright install chromium` inside the activated venv. |
| Email send fails with an auth error | You're using your real Gmail password instead of an App Password, or 2-Step Verification isn't enabled. |
| A company always returns 0 jobs | Check `logs/run.log` for a warning. It may genuinely have no matching roles right now, or it may need a `selectors` override (see Customizing above). |

**Write-up:** [`Bay_Area_Job_Monitor.html`](Bay_Area_Job_Monitor.html) (also `docs/Bay_Area_Job_Monitor_Report.pdf`).

## Related project

[AI_Resume_Tailor](https://github.com/jbattohokson/AI_Resume_Tailor) takes a job description plus a resume, works the job's keywords into the resume where the experience supports them, and fact-checks every draft. This project's layout (`src/tools/` vs `src/utils/`, stable job links in `data/seen_jobs.json`, a single `run_crew()` entry point) is kept clean so the tailor can consume its output later. That hand-off isn't built yet.

## Testing status (2026-10-20)

- `tests/test_filters.py` and `tests/test_seen_store.py`: 42 tests (93 subtests) on the deterministic filtering logic (location, role keywords, seniority exclusions, years-of-experience parsing, the entry-level title list in `companies.yaml`, pay-range parsing) plus the seen-jobs store. All pass, run 3 times with identical results.
- **Real dry run (2026-10-20):** 3 minutes end to end with `llama3.2`. 5 open roles matched (4 Salesforce business development roles, 1 LinkedIn SDR role, all San Francisco), with employer-stated pay ranges checked against a live posting. Uber and SAP blocked the requests (403). The digest is saved unedited in `examples/`, with notes.
- Not covered by tests: the live scrapers (career sites change their pages, so results vary run to run), the CrewAI agents, and the Gmail send. Those are checked by running `python main.py --dry-run` and reading `output/digest_preview.html`.
- Secret check before publishing: no credentials in the repo. `.env` and everything in `resumes/`, `data/`, `output/` and `logs/` (except placeholder files) are gitignored. The Greenhouse `board_token` values in `companies.yaml` are public board names, not secrets.

## Project structure

```
bay-area-job-monitor/
├── main.py                  # entry point
├── config/companies.yaml    # companies + search-criteria settings
├── resumes/                 # drop your resume(s) here (gitignored)
├── src/
│   ├── crew.py               # CrewAI agents/tasks/LLM config
│   ├── tools/
│   │   ├── scraper.py         # CareerPageScraperTool (Workday/Greenhouse/render)
│   │   ├── digest_tool.py     # JobFilterAndDiffTool (deterministic filtering)
│   │   └── mailer.py          # HTML/text digest rendering + SMTP send
│   └── utils/
│       ├── config.py          # companies.yaml loading
│       ├── filters.py         # location/role/seniority/experience/pay logic
│       ├── seen_store.py      # cross-run "what's new" tracking
│       └── resume.py          # resumes/ text extraction
├── data/seen_jobs.json       # generated at runtime, gitignored
├── output/digest_preview.html # --dry-run output, gitignored
├── tests/test_filters.py     # unit tests for the deterministic filtering logic
└── tests/test_seen_store.py  # dry runs and failed sends don't mark jobs as seen
```

## License

MIT (see `LICENSE`).
