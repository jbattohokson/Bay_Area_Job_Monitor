"""CareerPageScraperTool — the Playwright/httpx-backed CrewAI tool that pulls
Bay Area, role-matching job postings out of each configured company's career
page.

Two-stage pipeline per company, to avoid fetching a full job description for
every single posting at every company:
  Stage A (cheap, list-page data only): get the raw job list -> filter to
    Bay Area locations -> filter to matching role titles -> drop seniority
    titles. Only survivors move on.
  Stage B (detail fetch, survivors only): attach the full JD text to each
    survivor so is_entry_level() can be checked later in digest_tool.py.

No single company's failure is allowed to propagate — every handler is
wrapped so an exception is logged as a warning and that company simply
contributes zero jobs to the run.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional, Type

import httpx
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from src.utils.config import get_enabled_companies, load_companies, load_settings
from src.utils.filters import filter_bay_area_jobs, has_seniority_keyword, matches_role_keywords

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "BayAreaJobMonitor/1.0 (personal job-search monitor)"
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_PLAYWRIGHT_TIMEOUT_MS = 20000
DEFAULT_INTER_COMPANY_DELAY_SECONDS = 1.5
MAX_WORKDAY_OFFSET = 500

# Line-classification helpers for _extract_heuristic (see its docstring).
_JOB_ID_LINE_RE = re.compile(r"^[A-Z]{1,5}[-_]?\d{3,}[-\dA-Za-z]*$")
_EMPLOYMENT_TYPE_RE = re.compile(
    r"^(full[\s-]?time|part[\s-]?time|contract|intern(?:ship)?|temporary)$", re.IGNORECASE
)
_LOCATION_LINE_RE = re.compile(
    r"(san francisco|san jose|sunnyvale|mountain view|palo alto|santa clara|"
    r"redwood city|oakland|remote|bay area)",
    re.IGNORECASE,
)
_CITY_STATE_RE = re.compile(r"^[A-Za-z][A-Za-z .]*,\s*[A-Z]{2}(\s*\+\s*\d+\s*more)?$")


def _settings() -> dict:
    return load_settings()


def _user_agent(settings: dict) -> str:
    return settings.get("user_agent", DEFAULT_USER_AGENT)


def _timeout(settings: dict) -> float:
    return float(settings.get("request_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))


def _playwright_timeout_ms(settings: dict) -> int:
    return int(settings.get("playwright_timeout_ms", DEFAULT_PLAYWRIGHT_TIMEOUT_MS))


def _inter_company_delay(settings: dict) -> float:
    return float(settings.get("inter_company_delay_seconds", DEFAULT_INTER_COMPANY_DELAY_SECONDS))


def verify_link(url: str, settings: Optional[dict] = None) -> bool:
    """Best-effort reachability check — HEAD with a GET fallback (some sites
    reject HEAD), short timeout, follows redirects, treats any 2xx/3xx as
    reachable. Corporate bot-detection routinely 403s automated requests to
    pages that work fine in a real browser, so a False here feeds a
    flag-don't-drop UI marker, never an exclusion."""
    if not url:
        return False
    settings = settings or {}
    headers = {"User-Agent": _user_agent(settings)}
    timeout = _timeout(settings)
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            resp = client.head(url)
            if resp.status_code >= 400:
                resp = client.get(url)
            return resp.status_code < 400
    except Exception as exc:
        logger.info("verify_link: could not reach %s (%s)", url, exc)
        return False


class CareerPageScraperInput(BaseModel):
    company_name: str = Field(
        ...,
        description=(
            "Exact company name from config/companies.yaml, or the literal string "
            "'ALL' to scrape every enabled company in a single call (recommended — "
            "always use 'ALL' unless specifically asked to re-check one company)."
        ),
    )


class CareerPageScraperTool(BaseTool):
    name: str = "career_page_scraper"
    description: str = (
        "Scrapes configured company career pages for Bay Area job postings matching "
        "the configured role keywords, already filtered by location/role/seniority "
        "and with full job-description text attached for surviving candidates. "
        "Returns a JSON string: a flat list of "
        "{company, title, department, location, link, description} objects. "
        "Never raises — a company that fails to scrape simply contributes zero jobs "
        "and is logged as a warning, so one broken site never stops the run. "
        "Pass company_name='ALL' to scrape every enabled company in one call."
    )
    args_schema: Type[BaseModel] = CareerPageScraperInput

    def _run(self, company_name: str) -> str:
        settings = _settings()
        companies = get_enabled_companies(load_companies())

        if company_name.strip().upper() == "ALL":
            jobs: list[dict] = []
            for cfg in companies:
                jobs.extend(self._scrape_one(cfg, settings))
                time.sleep(_inter_company_delay(settings))
            return json.dumps(jobs)

        cfg = next((c for c in companies if c["name"].lower() == company_name.strip().lower()), None)
        if cfg is None:
            logger.warning("Scraper: unknown or disabled company '%s'", company_name)
            return json.dumps([])
        return json.dumps(self._scrape_one(cfg, settings))

    def _scrape_one(self, cfg: dict, settings: dict) -> list[dict]:
        handler = {
            "workday_api": self._scrape_workday,
            "greenhouse_api": self._scrape_greenhouse,
            "render": self._scrape_render,
        }.get(cfg.get("strategy"))
        if handler is None:
            logger.warning("Scraper: unknown strategy '%s' for '%s'", cfg.get("strategy"), cfg.get("name"))
            return []
        try:
            return handler(cfg, settings)
        except Exception as exc:
            logger.warning("'%s' scrape failed: %s", cfg.get("name"), exc)
            return []

    # ------------------------------------------------------------------
    # Stage A pre-filter, shared by every strategy
    # ------------------------------------------------------------------
    def _stage_a_filter(self, jobs: list[dict], settings: dict) -> list[dict]:
        role_keywords = settings.get("role_keywords", [])
        seniority_keywords = settings.get("seniority_exclude_keywords", [])
        survivors = filter_bay_area_jobs(jobs)
        survivors = [j for j in survivors if matches_role_keywords(j.get("title"), role_keywords)]
        survivors = [j for j in survivors if not has_seniority_keyword(j.get("title"), seniority_keywords)]
        return survivors

    # ------------------------------------------------------------------
    # Workday
    # ------------------------------------------------------------------
    def _scrape_workday(self, cfg: dict, settings: dict) -> list[dict]:
        headers = {
            "User-Agent": _user_agent(settings),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        page_size = cfg.get("page_size", 20)
        raw_jobs: list[dict] = []

        with httpx.Client(timeout=_timeout(settings), headers=headers) as client:
            offset = 0
            while True:
                body = {
                    "appliedFacets": {"locations": cfg.get("location_facets", [])},
                    "limit": page_size,
                    "offset": offset,
                    "searchText": "",
                }
                resp = client.post(cfg["api_url"], json=body)
                resp.raise_for_status()
                data = resp.json()
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                for p in postings:
                    raw_jobs.append(
                        {
                            "company": cfg["name"],
                            "title": (p.get("title") or "").strip(),
                            "department": None,
                            "location": p.get("locationsText", ""),
                            "link": cfg["base_url"].rstrip("/") + p.get("externalPath", ""),
                            "_external_path": p.get("externalPath", ""),
                        }
                    )
                if len(postings) < page_size or offset > MAX_WORKDAY_OFFSET:
                    break
                offset += page_size

            survivors = self._stage_a_filter(raw_jobs, settings)

            # Stage B: fetch each survivor's own detail JSON for the full JD text.
            # NOTE: this exact endpoint/field shape (jobPostingInfo.jobDescription)
            # is the standard documented Workday CxS pattern but was not
            # independently live-verified during planning — if it 404s/shapes
            # differently, the job is still kept (description=None), consistent
            # with the "unstated experience defaults to include" decision.
            detail_base = cfg["api_url"].rsplit("/jobs", 1)[0]
            for job in survivors:
                external_path = job.pop("_external_path", "")
                if not external_path:
                    job["description"] = None
                    continue
                try:
                    detail_resp = client.get(detail_base + external_path)
                    detail_resp.raise_for_status()
                    detail = detail_resp.json()
                    posting_info = detail.get("jobPostingInfo", {})
                    job["description"] = posting_info.get("jobDescription")
                    if not job.get("department"):
                        job["department"] = posting_info.get("jobPostingSuperCategory") or None
                except Exception as exc:
                    logger.warning(
                        "'%s': Workday detail fetch failed for %s (%s) — keeping job without description",
                        cfg["name"],
                        job.get("title"),
                        exc,
                    )
                    job["description"] = None

        for job in survivors:
            job.pop("_external_path", None)
        return survivors

    # ------------------------------------------------------------------
    # Greenhouse
    # ------------------------------------------------------------------
    def _scrape_greenhouse(self, cfg: dict, settings: dict) -> list[dict]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{cfg['board_token']}/jobs?content=true"
        headers = {"User-Agent": _user_agent(settings)}
        with httpx.Client(timeout=_timeout(settings), headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

        raw_jobs = []
        for j in data.get("jobs", []):
            content_html = j.get("content", "") or ""
            raw_jobs.append(
                {
                    "company": cfg["name"],
                    "title": (j.get("title") or "").strip(),
                    "department": None,
                    "location": (j.get("location") or {}).get("name", ""),
                    "link": j.get("absolute_url", ""),
                    "description": BeautifulSoup(content_html, "html.parser").get_text(separator=" ").strip()
                    if content_html
                    else None,
                }
            )
        # Greenhouse already has description for every job (content=true), so
        # Stage B is a no-op here — the description just came along for free.
        return self._stage_a_filter(raw_jobs, settings)

    # ------------------------------------------------------------------
    # Render (httpx+BeautifulSoup first, escalate to Playwright)
    # ------------------------------------------------------------------
    def _scrape_render(self, cfg: dict, settings: dict) -> list[dict]:
        html = self._fetch_static_html(cfg["url"], settings)
        cards = self._extract_job_cards(html, cfg)
        if not cards:
            logger.info("'%s': static fetch yielded 0 job cards, escalating to Playwright", cfg["name"])
            html = self._fetch_rendered_html(cfg["url"], settings)
            cards = self._extract_job_cards(html, cfg)

        if not cards:
            logger.warning(
                "'%s' yielded 0 jobs from JSON-LD, selectors, and heuristic — "
                "add a selectors override in companies.yaml",
                cfg["name"],
            )
            return []

        for card in cards:
            card["company"] = cfg["name"]
            card.setdefault("department", None)

        survivors = self._stage_a_filter(cards, settings)

        for job in survivors:
            job["description"] = self._fetch_job_description(job.get("link"), cfg, settings)

        return survivors

    def _fetch_job_description(self, link: Optional[str], cfg: dict, settings: dict) -> Optional[str]:
        if not link:
            return None
        try:
            html = self._fetch_static_html(link, settings)
            desc = self._extract_description(html, cfg)
            if desc:
                return desc
            html = self._fetch_rendered_html(link, settings)
            return self._extract_description(html, cfg)
        except Exception as exc:
            logger.warning("'%s': detail-page fetch failed for %s (%s)", cfg["name"], link, exc)
            return None

    def _fetch_static_html(self, url: str, settings: dict) -> Optional[str]:
        headers = {"User-Agent": _user_agent(settings)}
        try:
            with httpx.Client(timeout=_timeout(settings), headers=headers, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            logger.info("Static fetch failed for %s (%s)", url, exc)
            return None

    def _fetch_rendered_html(self, url: str, settings: dict) -> Optional[str]:
        # Playwright's SYNC API is used deliberately (not async) — a CrewAI
        # tool's _run is invoked synchronously, and calling asyncio.run()
        # from inside code that might already be running under an event loop
        # (e.g. kickoff_async(), FastAPI, notebooks) raises. The sync API
        # sidesteps that entirely.
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page(user_agent=_user_agent(settings))
                    page.goto(url, timeout=_playwright_timeout_ms(settings), wait_until="networkidle")
                    page.wait_for_timeout(1500)  # settle time for late XHR-rendered lists
                    return page.content()
                finally:
                    browser.close()
        except Exception as exc:
            logger.warning("Playwright fetch failed for %s: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # Extraction: JSON-LD first, then configured selectors, then heuristic
    # ------------------------------------------------------------------
    def _extract_job_cards(self, html: Optional[str], cfg: dict) -> list[dict]:
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        cards = self._extract_json_ld_postings(soup)
        if cards:
            return cards
        selectors = cfg.get("selectors")
        if selectors:
            cards = self._extract_via_selectors(soup, selectors)
            if cards:
                return cards
        return self._extract_heuristic(soup)

    def _extract_description(self, html: Optional[str], cfg: dict) -> Optional[str]:
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                    return BeautifulSoup(item["description"], "html.parser").get_text(separator=" ").strip()

        selectors = cfg.get("selectors") or {}
        description_selector = selectors.get("description")
        if description_selector:
            el = soup.select_one(description_selector)
            if el:
                return el.get_text(separator=" ").strip()

        # Heuristic last resort: largest text block on the page.
        blocks = soup.find_all(["div", "section", "article"])
        if not blocks:
            return None
        best = max(blocks, key=lambda b: len(b.get_text(strip=True)), default=None)
        if best is None:
            return None
        text = best.get_text(separator=" ", strip=True)
        return text if len(text) > 200 else None

    def _extract_json_ld_postings(self, soup: BeautifulSoup) -> list[dict]:
        cards = []
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            for item in data if isinstance(data, list) else [data]:
                if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                    continue
                cards.append(
                    {
                        "title": (item.get("title") or "").strip(),
                        "location": self._flatten_job_location(item.get("jobLocation", {})),
                        "link": item.get("url") or "",
                    }
                )
        return cards

    def _flatten_job_location(self, job_location) -> str:
        entries = job_location if isinstance(job_location, list) else [job_location]
        parts = []
        for e in entries:
            addr = (e or {}).get("address", {}) if isinstance(e, dict) else {}
            piece = ", ".join(p for p in [addr.get("addressLocality", ""), addr.get("addressRegion", "")] if p)
            if piece:
                parts.append(piece)
        return "; ".join(parts)

    def _extract_via_selectors(self, soup: BeautifulSoup, selectors: dict) -> list[dict]:
        card_selector = selectors.get("job_card")
        if not card_selector:
            return []
        cards = []
        for card_el in soup.select(card_selector):
            title_el = card_el.select_one(selectors["title"]) if selectors.get("title") else None
            location_el = card_el.select_one(selectors["location"]) if selectors.get("location") else None
            link_el = card_el.select_one(selectors["link"]) if selectors.get("link") else card_el
            link_attr = selectors.get("link_attr", "href")
            link = link_el.get(link_attr, "") if link_el else ""
            cards.append(
                {
                    "title": title_el.get_text(strip=True) if title_el else "",
                    "location": location_el.get_text(strip=True) if location_el else "",
                    "link": link,
                }
            )
        return [c for c in cards if c["title"]]

    def _extract_heuristic(self, soup: BeautifulSoup) -> list[dict]:
        """Low-precision last resort: scan <a href> tags whose href looks
        job-like, then classify each line of their (separator-joined) text
        into title / location / noise.

        A plain `get_text(strip=True)` glues adjacent child elements together
        with no separator at all (e.g. "...SolutionsSan Francisco, CAFull-time"),
        which silently breaks the Bay Area location filter downstream (it only
        ever sees an empty `location` field). Joining with "\\n" instead keeps
        each element's text on its own line so it can be classified — still
        best-effort (see the honest-flag note in the plan), but no longer
        structurally guaranteed to lose the location on every single card.
        """
        job_hint_re = ("job", "req", "position", "opening", "career")
        cards = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not any(hint in href.lower() for hint in job_hint_re):
                continue
            raw = a.get_text(separator="\n", strip=True)
            lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
            if not lines:
                continue

            location = ""
            title_lines = []
            for ln in lines:
                if _LOCATION_LINE_RE.search(ln) or _CITY_STATE_RE.match(ln):
                    if not location:
                        location = ln
                    continue
                if _JOB_ID_LINE_RE.match(ln) or _EMPLOYMENT_TYPE_RE.match(ln):
                    continue
                title_lines.append(ln)

            title = title_lines[0] if title_lines else ""
            if not title or len(title) < 4 or len(title) > 160:
                continue
            cards.append({"title": title, "location": location, "link": href})
        return cards
