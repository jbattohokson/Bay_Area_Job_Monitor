# Sample digest (real run, unedited)

`sample_digest_2026-10-20.html` is the real output of `python main.py --dry-run` on 2026-10-20 (3m08s, local `llama3.2` via Ollama, 2-vCPU cloud machine). The PNG is a screenshot of it. Nothing was sent; `--dry-run` writes the HTML instead of emailing.

**Result:** 5 currently open roles matched the filters, all new on a first run: 4 at Salesforce (Business Development Representative and Associate roles, San Francisco) and 1 at LinkedIn (Sales Development Representative 1, San Francisco). The postings are real, public, and may have closed since.

**Pay ranges are the employers' own figures**, not model estimates (none are marked "(estimated)"). Checked one against the live Salesforce posting: it lists $50,570 - $67,665 first, then a second range for other locations; the scraper takes the first range it finds.

**What didn't scrape from this machine:**
- Uber and SAP returned 403 Forbidden to the plain request. Uber's Playwright fallback ran but found no matching roles; SAP's timed out after 20 s. Corporate bot detection is the usual cause, and the README's limits cover it.
- No posting from the other 14 companies passed the role, seniority, experience and Bay Area filters on this run, so they don't appear. The run log doesn't record how many postings each company returned before filtering.

The `resumes/` folder was empty for this run (only `.gitkeep`), so no pay estimates were needed or made from personal data.
