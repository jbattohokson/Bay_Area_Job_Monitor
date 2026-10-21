"""Defines the two-agent CrewAI pipeline.

IMPORTANT DESIGN NOTE (found empirically, not assumed — see the dry-run test
that surfaced this): the scraping and filtering steps are called DIRECTLY in
Python, not through an LLM-driven agent tool-call loop. A live test against
llama3.2:3b showed that even when the agent successfully called the real
scraper tool and got real data back, CrewAI's ReAct-style "final answer" step
is LLM-generated free text — and the small model twice fabricated a
plausible-looking but fake job posting (a nonexistent linkedin.com URL with
a fake sequential ID) instead of faithfully relaying the tool's actual JSON
output. output_pydantic did not prevent this: it happily coerced the
fabricated text into a valid-looking DigestResult. This is a more severe
version of the "deterministic filtering" risk than originally planned for —
it's not that the LLM might mis-filter, it's that the mere act of relaying a
large JSON blob through the model's own final-answer text is itself
unreliable at this model size.

The fix: scraping (career_page_scraper) and filtering (filter_and_diff_jobs)
are called directly as Python functions. Zero LLM involvement in the data
path — there is no free-text relay step for real job data to be corrupted
in. The two CrewAI agents are kept, per the original design, but rescoped to
the two things a 3B local model actually can be trusted with: estimating pay
for the small, bounded set of jobs missing a stated range (grounded in the
resume), and writing a short narrative summary. Both consume small,
pre-filtered inputs and their output is merged back into the Python-built
digest by key (job link) — never trusted wholesale, and never able to
introduce a job that wasn't actually scraped.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from crewai import LLM, Agent, Crew, Process, Task
from pydantic import BaseModel

from src.tools.digest_tool import JobFilterAndDiffTool
from src.tools.scraper import CareerPageScraperTool
from src.utils.resume import load_resume_text

logger = logging.getLogger(__name__)

FALLBACK_SUMMARY = "{total_new} new entry-level role(s) found this run out of {total_open} total open position(s) matching your criteria."


class JobPosting(BaseModel):
    company: str
    title: str
    department: Optional[str] = None
    location: str
    link: str
    work_mode: str = "Unspecified"
    years_experience: str = "Entry level / not specified"
    pay_range: str = "Not available"
    pay_range_is_estimate: bool = False
    link_verified: bool = False


class CompanyJobGroup(BaseModel):
    company: str
    jobs: list[JobPosting]


class DigestResult(BaseModel):
    summary: str
    new_jobs_by_company: list[CompanyJobGroup]
    all_jobs_by_company: list[CompanyJobGroup]
    total_new: int = 0
    total_open: int = 0


class PayEstimateItem(BaseModel):
    link: str
    estimated_range: str


class PayEstimateResult(BaseModel):
    estimates: list[PayEstimateItem] = []


class SummaryResult(BaseModel):
    summary: str


_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    # "$65K" and "65,000" are different tokens on purpose: a summary may only repeat
    # figures exactly as the digest shows them.
    return {m.group().replace(",", "") for m in _NUMBER_RE.finditer(text)}


def ungrounded_numbers(summary: str, result: DigestResult) -> set[str]:
    """Numbers in the model-written summary that don't appear anywhere in the digest data.

    Found in a real run: every job had an employer-stated pay range, yet the summary claimed
    "estimated salary ranges ... $65K-$80K, $55K-$70K, and $75K-$90K". The digest table was
    right; only the prose was invented. Anything not traceable to the data is rejected.
    """
    source = result.model_dump_json(exclude={"summary"})
    # Per-company counts ("4 postings at Salesforce") are derived, not stored, so add them.
    counts = {str(len(g.jobs)) for g in result.new_jobs_by_company + result.all_jobs_by_company}
    return _numbers(summary) - _numbers(source) - counts


def build_llm() -> LLM:
    # See the module docstring's design note re: additional_params /
    # context_window_size — both were tried and both crash on this CrewAI
    # version's OpenAI-compatible client path, so neither is set here.
    return LLM(
        model=os.getenv("OLLAMA_MODEL", "ollama/llama3.2"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


def build_resume_context() -> str:
    text = load_resume_text()
    if not text:
        return "(No resume file found in resumes/ — estimate pay ranges from role/location/level alone.)"
    return text


def scrape_all_jobs() -> list[dict]:
    """Direct call, no LLM — see module docstring for why."""
    tool = CareerPageScraperTool()
    return json.loads(tool._run(company_name="ALL"))


def filter_and_diff(raw_jobs: list[dict]) -> dict:
    """Direct call, no LLM — see module docstring for why."""
    tool = JobFilterAndDiffTool()
    return json.loads(tool._run(jobs_json=json.dumps(raw_jobs)))


def _build_groups(groups_data: list[dict]) -> list[CompanyJobGroup]:
    groups = []
    for g in groups_data:
        jobs = []
        for j in g["jobs"]:
            stated = j.get("stated_pay_range")
            jobs.append(
                JobPosting(
                    company=j.get("company", g["company"]),
                    title=j.get("title", ""),
                    department=j.get("department"),
                    location=j.get("location", ""),
                    link=j.get("link", ""),
                    work_mode=j.get("work_mode", "Unspecified"),
                    years_experience=j.get("years_experience", "Entry level / not specified"),
                    pay_range=stated if stated else "Not available",
                    pay_range_is_estimate=False,
                )
            )
        groups.append(CompanyJobGroup(company=g["company"], jobs=jobs))
    return groups


def _enrich_with_llm(result: DigestResult) -> None:
    """Narrow, bounded LLM step: pay estimates for jobs missing a stated
    range, plus a written summary. Mutates `result` in place. Any failure
    here degrades to a deterministic fallback summary and leaves
    "Not available" pay ranges as-is — it never blocks the digest, since
    this whole step is enrichment, not the data path itself."""
    gap_jobs = {
        job.link: job
        for group in result.all_jobs_by_company
        for job in group.jobs
        if job.pay_range == "Not available" and job.link
    }
    company_breakdown = ", ".join(
        f"{g.company} ({len(g.jobs)})" for g in result.all_jobs_by_company
    ) or "none"

    try:
        llm = build_llm()

        estimator_agent = Agent(
            role="Compensation Estimator",
            goal="Provide a realistic, clearly-labeled pay range estimate for jobs that don't state one.",
            backstory=(
                "You estimate typical Bay Area compensation for entry-level sales-development "
                "and data-analyst-adjacent roles, using the candidate's own background as context "
                "for what level of offer they'd realistically see."
            ),
            llm=llm,
            verbose=True,
            allow_delegation=False,
        )
        summarizer_agent = Agent(
            role="Digest Synthesizer",
            goal="Write a short, accurate summary of this run's job digest.",
            backstory="You write brief, factual summaries of already-finalized job digests.",
            llm=llm,
            verbose=True,
            allow_delegation=False,
        )

        gap_list_json = json.dumps(
            [
                {"link": link, "title": job.title, "company": job.company, "location": job.location, "work_mode": job.work_mode}
                for link, job in gap_jobs.items()
            ]
        )
        estimate_task = Task(
            description=(
                "Here is a JSON list of jobs with no employer-stated pay range: "
                f"{gap_list_json}\n\n"
                "For EACH job in that list (matched by its 'link'), provide a realistic estimated "
                "pay range as a short string like '~$60K-$75K/year', based on the role/location/level "
                "and the candidate's background below. Return exactly one estimate per link listed — "
                "no more, no fewer, and never invent a job or link that isn't in the list above.\n\n"
                f"Candidate's background (from resumes/):\n{build_resume_context()}"
            ),
            expected_output="A PayEstimateResult with one PayEstimateItem per input link.",
            agent=estimator_agent,
            output_pydantic=PayEstimateResult,
        )
        summary_task = Task(
            description=(
                f"This job digest run found {result.total_new} new posting(s) since last run, out of "
                f"{result.total_open} total currently-open matching position(s), across these companies: "
                f"{company_breakdown}. Write a 2-4 sentence summary of this result — do not invent "
                "any companies, counts, or jobs beyond what's stated here."
            ),
            expected_output="A SummaryResult with a 2-4 sentence summary.",
            agent=summarizer_agent,
            context=[estimate_task],
            output_pydantic=SummaryResult,
        )

        crew = Crew(
            agents=[estimator_agent, summarizer_agent],
            tasks=[estimate_task, summary_task],
            process=Process.sequential,
            verbose=True,
        )
        crew_output = crew.kickoff()


        estimate_result = estimate_task.output.pydantic
        if isinstance(estimate_result, PayEstimateResult):
            est_by_link = {
                e.link: e.estimated_range
                for e in estimate_result.estimates
                if e.link in gap_jobs  # never accept an estimate for a link we didn't ask about
            }
            for group in list(result.new_jobs_by_company) + list(result.all_jobs_by_company):
                for job in group.jobs:
                    if job.link in est_by_link:
                        job.pay_range = est_by_link[job.link]
                        job.pay_range_is_estimate = True
        else:
            logger.warning("Pay-estimate task did not return usable output — leaving gap jobs unestimated")

        # Checked after estimates are applied, so a summary may quote an estimate it was given.
        summary_result = crew_output.pydantic
        if not (isinstance(summary_result, SummaryResult) and summary_result.summary):
            raise ValueError("summary task did not return a usable SummaryResult")
        invented = ungrounded_numbers(summary_result.summary, result)
        if invented:
            raise ValueError(f"summary contains numbers not in the digest: {sorted(invented)}")
        result.summary = summary_result.summary

    except Exception:
        logger.exception("LLM enrichment (pay estimates/summary) failed — using deterministic fallback")
        result.summary = FALLBACK_SUMMARY.format(total_new=result.total_new, total_open=result.total_open)


def run_crew() -> DigestResult:
    raw_jobs = scrape_all_jobs()
    diff_result = filter_and_diff(raw_jobs)

    new_groups = _build_groups(diff_result["new_jobs_by_company"])
    all_groups = _build_groups(diff_result["all_jobs_by_company"])

    result = DigestResult(
        summary="",
        new_jobs_by_company=new_groups,
        all_jobs_by_company=all_groups,
        total_new=sum(len(g.jobs) for g in new_groups),
        total_open=sum(len(g.jobs) for g in all_groups),
    )

    _enrich_with_llm(result)
    return result
