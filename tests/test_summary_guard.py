"""The model-written digest summary may only repeat numbers that are in the digest data."""
from src.crew import CompanyJobGroup, DigestResult, JobPosting, ungrounded_numbers


def _result() -> DigestResult:
    job = JobPosting(company="Salesforce", title="Business Development Representative",
                     location="California - San Francisco", link="https://example.com/jr364975",
                     years_experience="Entry level / not specified", pay_range="$50,570 - $67,665 annually")
    groups = [CompanyJobGroup(company="Salesforce", jobs=[job])]
    return DigestResult(summary="", new_jobs_by_company=groups, all_jobs_by_company=groups,
                        total_new=1, total_open=1)


def test_invented_salary_ranges_are_caught():
    # Reproduces a real llama3.2 summary: no job was estimated, but the prose said otherwise.
    summary = "The estimated salary ranges for the positions are $65K-$80K, $55K-$70K, and $75K-$90K per year."
    assert ungrounded_numbers(summary, _result()) == {"65", "80", "55", "70", "75", "90"}


def test_summary_quoting_the_digest_passes():
    summary = "1 new posting at Salesforce, paying $50,570 - $67,665 annually."
    assert ungrounded_numbers(summary, _result()) == set()


def test_per_company_counts_are_allowed():
    assert ungrounded_numbers("1 new posting, 1 at Salesforce.", _result()) == set()
    assert ungrounded_numbers("4 postings at Salesforce.", _result()) == {"4"}
