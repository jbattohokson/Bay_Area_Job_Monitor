"""JobFilterAndDiffTool — the deterministic filtering/formatting step the
Digest Synthesizer agent calls. Everything here is plain Python; the agent's
job is to call this once and write a short summary on top of its output, not
to re-derive any of the filtering itself (see src/crew.py).
"""
from __future__ import annotations

import json
import logging
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from src.utils.config import load_settings
from src.utils.filters import (
    classify_work_mode,
    deduplicate_jobs,
    extract_min_years_required,
    extract_pay_range_from_text,
    filter_bay_area_jobs,
    format_years_experience,
    group_by_company,
    has_seniority_keyword,
    is_entry_level,
    matches_role_keywords,
    sort_companies_by_count,
)
from src.utils.seen_store import diff_and_update

logger = logging.getLogger(__name__)


class JobFilterAndDiffInput(BaseModel):
    jobs_json: str = Field(
        ..., description="JSON string: the flat list of raw scraped jobs from career_page_scraper."
    )


class JobFilterAndDiffTool(BaseTool):
    name: str = "filter_and_diff_jobs"
    description: str = (
        "Deterministically filters to Bay Area, role-matching, entry-level jobs, "
        "deduplicates, diffs against the seen-jobs store, groups by company "
        "(descending count), and persists the store. Returns a JSON object with keys "
        "new_jobs_by_company and all_jobs_by_company, where each job already has "
        "work_mode, years_experience, and stated_pay_range (may be empty/null if the "
        "JD didn't state one) filled in. Treat this tool's output as ground truth — "
        "do not re-filter, re-sort, or invent job postings on top of it."
    )
    args_schema: Type[BaseModel] = JobFilterAndDiffInput

    def _run(self, jobs_json: str) -> str:
        try:
            raw_jobs = json.loads(jobs_json)
        except json.JSONDecodeError as exc:
            logger.warning("filter_and_diff_jobs: bad input JSON (%s)", exc)
            raw_jobs = []

        settings = load_settings()
        role_keywords = settings.get("role_keywords", [])
        seniority_keywords = settings.get("seniority_exclude_keywords", [])
        max_years = settings.get("max_years_experience", 3)

        # Re-applied here even though the scraper's Stage A already did this —
        # cheap and idempotent, and keeps this tool the single authoritative
        # filter regardless of what upstream did.
        jobs = filter_bay_area_jobs(raw_jobs)
        jobs = [j for j in jobs if matches_role_keywords(j.get("title"), role_keywords)]
        jobs = [j for j in jobs if not has_seniority_keyword(j.get("title"), seniority_keywords)]
        jobs = [j for j in jobs if is_entry_level(j.get("description"), max_years)]

        for job in jobs:
            description = job.get("description")
            job["work_mode"] = classify_work_mode(job.get("location"), description)
            job["years_experience"] = format_years_experience(
                extract_min_years_required(description)
            )
            job["stated_pay_range"] = extract_pay_range_from_text(description)
            job.pop("description", None)

        jobs = deduplicate_jobs(jobs)
        new_jobs, all_jobs = diff_and_update(jobs)

        return json.dumps(
            {
                "new_jobs_by_company": [
                    {"company": company, "jobs": company_jobs}
                    for company, company_jobs in sort_companies_by_count(group_by_company(new_jobs))
                ],
                "all_jobs_by_company": [
                    {"company": company, "jobs": company_jobs}
                    for company, company_jobs in sort_companies_by_count(group_by_company(all_jobs))
                ],
            }
        )
