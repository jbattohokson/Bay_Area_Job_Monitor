import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.filters import (
    BAY_AREA_CITIES,
    classify_work_mode,
    deduplicate_jobs,
    extract_min_years_required,
    extract_pay_range_from_text,
    format_years_experience,
    has_seniority_keyword,
    is_bay_area_location,
    is_entry_level,
    matches_role_keywords,
    sort_companies_by_count,
)

ROLE_KEYWORDS = [
    "sdr",
    "sales development representative",
    "bdr",
    "business development representative",
    "business development associate",
    "business development analyst",
    "bda",
    "adr",
    "account development representative",
    "data analyst",
    "business analyst",
    "sales analyst",
    "sales operations analyst",
    "revenue operations analyst",
]

SENIORITY_KEYWORDS = [
    "senior",
    "sr.",
    "sr ",
    "staff",
    "lead",
    "principal",
    "manager",
    "director",
    "vp",
    "vice president",
    "chief",
    "head of",
]


class TestIsBayAreaLocation(unittest.TestCase):
    def test_all_named_cities_match(self):
        for city in BAY_AREA_CITIES:
            with self.subTest(city=city):
                self.assertTrue(is_bay_area_location(f"{city.title()}, CA"))

    def test_remote_qualified_with_city_matches(self):
        for city in BAY_AREA_CITIES:
            with self.subTest(city=city):
                self.assertTrue(is_bay_area_location(f"Remote - {city.title()}, CA"))

    def test_bare_remote_excluded(self):
        for loc in ["Remote", "Remote - US", "Remote, USA", "Remote - Anywhere"]:
            with self.subTest(loc=loc):
                self.assertFalse(is_bay_area_location(loc))

    def test_remote_california_included(self):
        self.assertTrue(is_bay_area_location("Remote - California"))

    def test_california_but_not_bay_area_excluded(self):
        for loc in ["Sacramento, CA", "Los Angeles, CA", "San Diego, CA", "Fresno, CA"]:
            with self.subTest(loc=loc):
                self.assertFalse(is_bay_area_location(loc))

    def test_empty_and_none(self):
        self.assertFalse(is_bay_area_location(""))
        self.assertFalse(is_bay_area_location(None))

    def test_multi_location_string(self):
        self.assertTrue(is_bay_area_location("San Francisco, CA; New York, NY"))

    def test_bay_area_literal(self):
        self.assertTrue(is_bay_area_location("Remote (SF Bay Area)"))


class TestDeduplication(unittest.TestCase):
    def test_same_link_different_query_collapses(self):
        jobs = [
            {"company": "A", "title": "SDR", "location": "SF", "link": "https://x.com/job/1?src=li"},
            {"company": "A", "title": "SDR", "location": "SF", "link": "https://x.com/job/1?src=gh"},
        ]
        self.assertEqual(len(deduplicate_jobs(jobs)), 1)

    def test_different_links_same_title_location_stays_two(self):
        jobs = [
            {"company": "A", "title": "SDR", "location": "SF", "link": "https://x.com/job/1"},
            {"company": "A", "title": "SDR", "location": "SF", "link": "https://x.com/job/2"},
        ]
        self.assertEqual(len(deduplicate_jobs(jobs)), 2)

    def test_two_blank_link_jobs_collapse_via_fallback(self):
        jobs = [
            {"company": "A", "title": "SDR", "location": "SF", "link": ""},
            {"company": "A", "title": "SDR", "location": "SF", "link": ""},
        ]
        self.assertEqual(len(deduplicate_jobs(jobs)), 1)


class TestSortCompaniesByCount(unittest.TestCase):
    def test_descending_count_alphabetical_tiebreak(self):
        grouped = {
            "Zebra": [{"title": "a"}],
            "Acme": [{"title": "a"}],
            "Big": [{"title": "a"}, {"title": "b"}],
        }
        result = sort_companies_by_count(grouped)
        self.assertEqual([name for name, _ in result], ["Big", "Acme", "Zebra"])


class TestRoleAndSeniorityKeywords(unittest.TestCase):
    def test_matching_titles(self):
        for title in [
            "Sales Development Representative",
            "SDR - Enterprise",
            "Business Development Associate",
            "Data Analyst II",
        ]:
            with self.subTest(title=title):
                self.assertTrue(matches_role_keywords(title, ROLE_KEYWORDS))

    def test_unrelated_title(self):
        self.assertFalse(matches_role_keywords("Software Engineer", ROLE_KEYWORDS))

    def test_seniority_excluded_titles(self):
        for title in ["Senior SDR", "Data Analyst III", "BDR Manager"]:
            with self.subTest(title=title):
                self.assertTrue(has_seniority_keyword(title, SENIORITY_KEYWORDS))

    def test_non_seniority_titles(self):
        for title in ["SDR", "Junior Data Analyst", "Data Analyst", "Associate BDR"]:
            with self.subTest(title=title):
                self.assertFalse(has_seniority_keyword(title, SENIORITY_KEYWORDS))


class TestExperienceExtraction(unittest.TestCase):
    def test_explicit_range(self):
        self.assertEqual(extract_min_years_required("1-3 years of experience"), 1)

    def test_plus_form(self):
        self.assertEqual(extract_min_years_required("5+ years required"), 5)

    def test_entry_level_phrase(self):
        self.assertEqual(
            extract_min_years_required("entry level position, no experience necessary"), 0
        )

    def test_minimum_across_matches(self):
        text = "Minimum 2 years in a related field. Our company has 10+ years in the industry."
        self.assertEqual(extract_min_years_required(text), 2)

    def test_no_mention_returns_none(self):
        self.assertIsNone(extract_min_years_required("We are looking for a great teammate."))

    def test_is_entry_level_defaults_to_include_when_unstated(self):
        self.assertTrue(is_entry_level(None))
        self.assertTrue(is_entry_level("no mention of years here"))

    def test_is_entry_level_respects_max_years(self):
        self.assertTrue(is_entry_level("1-3 years of experience", max_years=3))
        self.assertFalse(is_entry_level("5+ years required", max_years=3))

    def test_format_years_experience(self):
        self.assertEqual(format_years_experience(None), "Entry level / not specified")
        self.assertEqual(format_years_experience(0), "Entry level (0 years)")
        self.assertEqual(format_years_experience(2), "2+ years")


class TestWorkModeClassification(unittest.TestCase):
    def test_remote(self):
        self.assertEqual(classify_work_mode("Remote - San Francisco, CA"), "Remote")

    def test_hybrid(self):
        self.assertEqual(classify_work_mode("Hybrid - San Jose, CA"), "Hybrid")

    def test_onsite_default(self):
        self.assertEqual(classify_work_mode("San Francisco, CA"), "Onsite")

    def test_unspecified(self):
        self.assertEqual(classify_work_mode(""), "Unspecified")
        self.assertEqual(classify_work_mode(None), "Unspecified")


class TestPayRangeExtraction(unittest.TestCase):
    def test_annual_range(self):
        self.assertEqual(
            extract_pay_range_from_text("The pay range for this role is $65,000 - $85,000 annually."),
            "$65,000 - $85,000 annually",
        )

    def test_hourly_range(self):
        self.assertEqual(
            extract_pay_range_from_text("Compensation: $28/hr - $32/hr depending on experience"),
            "$28/hr - $32/hr",
        )

    def test_no_pay_mentioned(self):
        self.assertIsNone(extract_pay_range_from_text("We are looking for a great teammate."))


if __name__ == "__main__":
    unittest.main()
