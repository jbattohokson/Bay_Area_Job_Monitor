"""Deterministic filtering, deduplication, and grouping for scraped jobs.

Pure functions only — no network, no filesystem, no LLM calls — so this module
stays fast and fully unit-testable. This is where "does this job actually
belong in the digest" gets decided; the CrewAI agents never re-derive any of
this themselves (see src/tools/digest_tool.py).
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

BAY_AREA_CITIES = [
    "san francisco",
    "san jose",
    "sunnyvale",
    "mountain view",
    "palo alto",
    "santa clara",
    "redwood city",
    "oakland",
]

_SENIORITY_LEVEL_SUFFIX_RE = re.compile(r"\b(II|III|IV|V)\b\s*$")


def is_bay_area_location(location: Optional[str]) -> bool:
    """True if `location` names a Bay Area city, "Bay Area" itself, or a
    remote role explicitly qualified to California/a Bay Area city.

    A bare "california" check outside the remote branch is deliberately
    avoided — it would wrongly admit Sacramento, LA, San Diego, Fresno, etc.
    Substring (not token-anchored) matching is a deliberate tradeoff for
    resilience against wildly inconsistent location formatting across many
    different ATSs, accepted as a known limitation rather than building out
    full city/state parsing.
    """
    if not location:
        return False
    loc = location.lower().strip()

    if any(city in loc for city in BAY_AREA_CITIES):
        return True
    if "bay area" in loc:
        return True
    if "remote" in loc:
        if re.search(r"\bca\b", loc) or "california" in loc:
            return True
        return False
    return False


def normalize_link(link: Optional[str]) -> str:
    """Strip query string/fragment and lowercase scheme+host so two links to
    the same posting (differing only by tracking params) dedupe together."""
    if not link:
        return ""
    parts = urlsplit(link)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def job_key(job: dict) -> str:
    """Stable dedup key for a job — the normalized application link when
    present (a specific requisition), falling back to a hash of
    company|title|location only when a scraper produced no link at all
    (should be rare; worth investigating if it fires often)."""
    link = normalize_link(job.get("link"))
    if link:
        return link
    fallback = f"{job.get('company', '')}|{job.get('title', '')}|{job.get('location', '')}".lower()
    fallback = re.sub(r"\s+", " ", fallback).strip()
    return "fallback:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:16]


def filter_bay_area_jobs(jobs: list[dict]) -> list[dict]:
    return [j for j in jobs if is_bay_area_location(j.get("location"))]


def deduplicate_jobs(jobs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for job in jobs:
        key = job_key(job)
        if key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out


def group_by_company(jobs: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for job in jobs:
        grouped.setdefault(job.get("company", "Unknown"), []).append(job)
    return grouped


def sort_companies_by_count(grouped: dict[str, list[dict]]) -> list[tuple[str, list[dict]]]:
    """Descending by open-role count, alphabetical tie-break — deterministic
    so email ordering doesn't jitter run-to-run for no reason."""
    return sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))


def _keyword_pattern(keywords: list[str]) -> re.Pattern:
    escaped = [re.escape(k) for k in keywords]
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


def matches_role_keywords(title: Optional[str], keywords: list[str]) -> bool:
    """Word-boundary match against the configured role-keyword list. Uses
    \\b-anchored regex rather than plain substring specifically because short
    acronyms (sdr/bdr/bda/adr) are collision-prone as bare substrings."""
    if not title or not keywords:
        return False
    return bool(_keyword_pattern(keywords).search(title))


def has_seniority_keyword(title: Optional[str], exclude_keywords: list[str]) -> bool:
    """True if `title` carries a seniority qualifier (Senior/Staff/Lead/...)
    or a trailing roman-numeral level suffix (II/III/IV/V). "Junior" and
    "Associate" are intentionally never treated as seniority signals — they
    are pro-entry-level, not exclusions."""
    if not title:
        return False
    if exclude_keywords and _keyword_pattern(exclude_keywords).search(title):
        return True
    return bool(_SENIORITY_LEVEL_SUFFIX_RE.search(title.strip()))


_YEARS_RANGE_RE = re.compile(r"(\d+)\s*(?:-|to|–)\s*(\d+)\+?\s*\+?\s*years?", re.IGNORECASE)
_YEARS_PLUS_RE = re.compile(r"(\d+)\s*\+\s*years?", re.IGNORECASE)
_YEARS_MINIMUM_RE = re.compile(
    r"(?:at least|minimum(?: of)?)\s*(\d+)\s*years?", re.IGNORECASE
)
_ENTRY_LEVEL_PHRASES_RE = re.compile(
    r"entry[\s-]?level|new grad|recent graduate|no prior experience(?:\s+(?:is\s+)?required)?|"
    r"no experience necessary",
    re.IGNORECASE,
)


def extract_min_years_required(jd_text: Optional[str]) -> Optional[int]:
    """Best-effort, deterministic extraction of the minimum years of
    experience a JD asks for. Checks explicit ranges, "N+ years", "at
    least/minimum N years", and no-number entry-level phrases (→ 0). If
    multiple matches are found anywhere in the text, returns the MINIMUM
    across all of them — a simple, inclusion-biased heuristic that avoids
    over-excluding a job just because unrelated boilerplate elsewhere in the
    JD mentions a large number (e.g. "10+ years in business" as a
    company-history blurb). Returns None if nothing matches at all.

    This is regex-based pattern matching over freeform prose, not language
    understanding — it can misfire on unusually worded JDs. Accepted
    tradeoff for a niche filter; the caller treats an unclear/absent signal
    as "include" by default (see is_entry_level).
    """
    if not jd_text:
        return None

    candidates: list[int] = []
    for match in _YEARS_RANGE_RE.finditer(jd_text):
        candidates.append(int(match.group(1)))
    for match in _YEARS_PLUS_RE.finditer(jd_text):
        candidates.append(int(match.group(1)))
    for match in _YEARS_MINIMUM_RE.finditer(jd_text):
        candidates.append(int(match.group(1)))
    if _ENTRY_LEVEL_PHRASES_RE.search(jd_text):
        candidates.append(0)

    if not candidates:
        return None
    return min(candidates)


def is_entry_level(jd_text: Optional[str], max_years: int = 3) -> bool:
    """None (unstated) -> True (include, per confirmed default: many
    genuinely entry-level postings simply don't mention a year range)."""
    min_years = extract_min_years_required(jd_text)
    if min_years is None:
        return True
    return min_years <= max_years


def format_years_experience(min_years: Optional[int]) -> str:
    """Surfaces the experience signal in the final digest instead of only
    using it internally for filtering."""
    if min_years is None:
        return "Entry level / not specified"
    if min_years == 0:
        return "Entry level (0 years)"
    return f"{min_years}+ years"


def classify_work_mode(location: Optional[str], jd_text: Optional[str] = None) -> str:
    """Deterministic Remote/Hybrid/Onsite/Unspecified classification.

    A physical city given with no remote/hybrid language defaults to
    Onsite — a reasonable assumption for a location-tagged posting.
    """
    combined = f"{location or ''} {jd_text or ''}".lower()
    if not combined.strip():
        return "Unspecified"

    if "hybrid" in combined:
        return "Hybrid"
    if "remote" in combined or "fully remote" in combined or "100% remote" in combined:
        return "Remote"
    if "onsite" in combined or "on-site" in combined or "in-office" in combined or "in office" in combined:
        return "Onsite"
    if location and location.strip():
        return "Onsite"
    return "Unspecified"


_PAY_UNIT = r"(?:/\s?(?:hr|hour|yr|year)|per\s+(?:hour|year)|annually)"
_PAY_RANGE_RE = re.compile(
    r"\$\s?[\d,]+(?:\.\d{2})?(?:\s*" + _PAY_UNIT + r")?"
    r"\s*(?:-|–|to)\s*"
    r"\$?\s?[\d,]+(?:\.\d{2})?(?:\s*" + _PAY_UNIT + r")?",
    re.IGNORECASE,
)


def extract_pay_range_from_text(jd_text: Optional[str]) -> Optional[str]:
    """Regex extraction of an explicit dollar range from JD text, returned
    verbatim (not reformatted) so the email shows the company's own stated
    figure. California's pay-transparency law means many of these postings
    state a range directly. Returns None when nothing matches — that's the
    signal crew.py uses to decide the job needs an LLM-generated estimate
    instead."""
    if not jd_text:
        return None
    match = _PAY_RANGE_RE.search(jd_text)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(0)).strip()
