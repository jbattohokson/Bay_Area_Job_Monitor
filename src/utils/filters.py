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
    "menlo park",
    "cupertino",
    "san mateo",
    "foster city",
    "burlingame",
    "san bruno",
    "milpitas",
    "fremont",
    "berkeley",
    "emeryville",
]

_SENIORITY_LEVEL_SUFFIX_RE = re.compile(r"\b(II|III|IV|V|[2-5])\s*$")


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
    # \b only on edges that are word characters: a trailing \b after "sr." would
    # require a letter right after the period, so "Sr. Data Analyst" never matched.
    parts = []
    for k in keywords:
        start = r"\b" if re.match(r"\w", k) else ""
        end = r"\b" if re.search(r"\w$", k) else ""
        parts.append(start + re.escape(k) + end)
    return re.compile("(?:" + "|".join(parts) + ")", re.IGNORECASE)


def matches_role_keywords(title: Optional[str], keywords: list[str]) -> bool:
    """Word-boundary match against the configured role-keyword list. Uses
    \\b-anchored regex rather than plain substring specifically because short
    acronyms (sdr/bdr/bda/adr) are collision-prone as bare substrings."""
    if not title or not keywords:
        return False
    return bool(_keyword_pattern(keywords).search(title))


def has_seniority_keyword(
    title: Optional[str], exclude_keywords: list[str], role_keywords: Optional[list[str]] = None
) -> bool:
    """True if `title` carries a seniority qualifier (Senior/Staff/Lead/...)
    or a trailing level suffix (II/III/IV/V or 2-5). "Junior" and
    "Associate" are intentionally never treated as seniority signals — they
    are pro-entry-level, not exclusions.

    The role-keyword phrase itself is removed first, so an entry-level title
    that happens to contain a seniority word ("Lead Development
    Representative") isn't excluded, while "Senior LDR" still is."""
    if not title:
        return False
    if role_keywords:
        title = _keyword_pattern(role_keywords).sub(" ", title)
    if exclude_keywords and _keyword_pattern(exclude_keywords).search(title):
        return True
    return bool(_SENIORITY_LEVEL_SUFFIX_RE.search(title.strip()))


_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
# "two (2) years" / "two years" -> "2 years", so written-out numbers are caught too.
_NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(_NUMBER_WORDS) + r")\b(?:\s*\(\s*\d+\s*\))?", re.IGNORECASE
)
_YEARS_RANGE_RE = re.compile(r"(\d+)\s*(?:-|to|–|—)\s*(\d+)\s*\+?\s*years?", re.IGNORECASE)
_YEARS_PLUS_RE = re.compile(r"(\d+)\s*\+\s*years?", re.IGNORECASE)
_YEARS_MINIMUM_RE = re.compile(
    r"(?:at least|minimum(?: of)?|min\.?)\s*(\d+)\s*\+?\s*years?", re.IGNORECASE
)
_YEARS_OF_RE = re.compile(r"(?<![\d+\-–])(\d+)\s*years?\s+(?:of\s+)?(?:\w+\s+){0,4}experience", re.IGNORECASE)
_ENTRY_LEVEL_PHRASES_RE = re.compile(
    r"entry[\s-]?level|new grad|recent (?:college )?graduate|early[\s-]career|"
    r"no (?:prior |previous )?experience (?:is )?(?:required|necessary|needed)|"
    r"0\s*(?:-|to|–)\s*1\s*years?",
    re.IGNORECASE,
)
# A sentence only counts as a requirement if it talks about the candidate's
# experience — this skips company blurbs like "10+ years in the industry".
_REQUIREMENT_CONTEXT_RE = re.compile(
    r"experience|minimum|at least|required|requires|background|quota|track record|"
    r"in a similar role|in an? \w+ role|working in",
    re.IGNORECASE,
)
# Nice-to-haves don't decide eligibility: "3+ years preferred" on a 0-1 year role.
_PREFERRED_RE = re.compile(r"prefer|nice to have|a plus|bonus|ideally", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;!?\n])\s+|\s*[•·]\s*")


def extract_min_years_required(jd_text: Optional[str]) -> Optional[int]:
    """Best-effort, deterministic extraction of the years of experience a JD
    requires. Looks at each sentence that reads as a requirement (mentions
    experience, a minimum, a role, etc.) and isn't marked preferred, and
    pulls explicit ranges ("1-2 years": 1), "N+ years", "at least/minimum N
    years" and "N years of ... experience", including written-out numbers.
    Returns the LARGEST stated minimum, since a posting that requires 3 years
    anywhere isn't a 0-1 year role. If no number is found but the JD uses an
    entry-level phrase ("entry level", "new grad", "no experience required"),
    returns 0. Returns None if nothing matches at all.

    This is regex-based pattern matching over freeform prose, not language
    understanding — it can misfire on unusually worded JDs. The caller
    treats an absent signal as "include" by default (see is_entry_level).
    """
    if not jd_text:
        return None

    text = _NUMBER_WORD_RE.sub(lambda m: str(_NUMBER_WORDS[m.group(1).lower()]), jd_text)
    candidates: list[int] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not _REQUIREMENT_CONTEXT_RE.search(sentence) or _PREFERRED_RE.search(sentence):
            continue
        for regex in (_YEARS_RANGE_RE, _YEARS_PLUS_RE, _YEARS_MINIMUM_RE, _YEARS_OF_RE):
            candidates.extend(int(m.group(1)) for m in regex.finditer(sentence))

    if candidates:
        return max(candidates)
    if _ENTRY_LEVEL_PHRASES_RE.search(text):
        return 0
    return None


def is_entry_level(jd_text: Optional[str], max_years: int = 1) -> bool:
    """None (unstated) -> True (include, per confirmed default: many
    genuinely entry-level postings simply don't mention a year range; the
    title filters have already removed senior-sounding roles)."""
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
