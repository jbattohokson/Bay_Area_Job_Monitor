import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.seen_store import diff_jobs, mark_seen

JOBS = [
    {"company": "Acme", "title": "SDR", "location": "San Francisco, CA", "link": "https://acme.com/jobs/1"},
    {"company": "Acme", "title": "BDR", "location": "Oakland, CA", "link": "https://acme.com/jobs/2"},
]


class TestSeenStore(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "seen.json"

    def test_diff_does_not_persist(self):
        # A dry run only diffs, so the same jobs must still be new next time.
        diff_jobs(JOBS, self.path)
        new, _ = diff_jobs(JOBS, self.path)
        self.assertEqual(len(new), 2)
        self.assertFalse(self.path.exists())

    def test_mark_seen_after_send(self):
        mark_seen(JOBS[:1], self.path)
        new, all_jobs = diff_jobs(JOBS, self.path)
        self.assertEqual([j["title"] for j in new], ["BDR"])
        self.assertEqual(len(all_jobs), 2)


if __name__ == "__main__":
    unittest.main()
