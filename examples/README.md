# Sample digest (real run, unedited)

`sample_digest_2026-10-20.html` is the real output of `python main.py --dry-run` on 2026-10-20 (2m42s, local `llama3.2` via Ollama, 2-vCPU cloud machine). The PNG is a screenshot of it. Nothing was sent; `--dry-run` writes the HTML instead of emailing.

**Result:** 5 currently open roles matched the filters: 4 business development roles at Salesforce and a Sales Development Representative 1 role at LinkedIn, all in San Francisco. The postings are real and public, and may have closed since.

**Pay ranges are the employers' own figures**, not model estimates (none are marked "(estimated)"). I checked one against the live Salesforce posting: it lists $50,570 - $67,665 first, then a second range for other locations, and the scraper takes the first.

**Experience** is parsed from each posting's text. Two Salesforce roles show "Entry level / not specified" because their only years figure sits under "Even Better If" (a nice-to-have), which the filter deliberately doesn't treat as a requirement.

## The summary check, found and fixed during these runs

The one-paragraph summary at the top is written by the model. In two of four dry runs it invented "estimated salary ranges" such as $65K-$80K, even though every job had an employer-stated range and nothing was estimated. The tables below the summary were correct; only the prose was made up.

The summary is now rejected if it contains any number that isn't in the digest data (job fields, totals and per-company counts), and the digest falls back to a plain one-line summary. In the next run the check caught a second invented set of salaries; in the final run (this sample) the model's summary only repeated real numbers and was kept. Tests: `tests/test_summary_guard.py`.

## What didn't scrape from this machine

- Uber and SAP returned 403 Forbidden to the plain request. Uber's Playwright fallback found no matching roles; SAP's timed out after 20 s. Corporate bot detection is the usual cause, and the README's limits cover it.
- No posting from the other 14 companies passed the role, seniority, experience and Bay Area filters on this run. The run log doesn't record how many postings each company returned before filtering.

The `resumes/` folder was empty for this run (only `.gitkeep`), so no personal data was used.
