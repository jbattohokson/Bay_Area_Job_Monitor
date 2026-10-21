"""Persisted "have we emailed this job before" tracking across runs.

Append-only, never pruned (fine at this scale — pruning-by-age is a future
enhancement, not built now). If the store file is ever lost or corrupted,
it's treated as empty rather than crashing — the visible symptom is one
noisy "everything looks new" digest, not a failure.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.utils.filters import job_key

logger = logging.getLogger(__name__)

DEFAULT_STORE_PATH = Path(__file__).resolve().parents[2] / "data" / "seen_jobs.json"


def load_seen_store(path: Path = DEFAULT_STORE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("seen-jobs store at %s unreadable (%s) — treating as empty", path, exc)
        return {}


def save_seen_store(store: dict, path: Path = DEFAULT_STORE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)  # atomic — a crash mid-write can't truncate the real file


def diff_jobs(
    current_jobs: list[dict], path: Path = DEFAULT_STORE_PATH
) -> tuple[list[dict], list[dict]]:
    """Returns (new_jobs, all_jobs) without touching the store. First run ever
    (empty store) makes new_jobs == all_jobs for free — nothing is "in store"
    yet, so every key counts as new; no special-case branch needed."""
    store = load_seen_store(path)
    new_jobs = [job for job in current_jobs if job_key(job) not in store]
    return new_jobs, current_jobs


def mark_seen(jobs: list[dict], path: Path = DEFAULT_STORE_PATH) -> None:
    """Records jobs as emailed. main.py calls this only after a real send
    succeeds, so a dry run or a failed send doesn't use up the "new" list."""
    store = load_seen_store(path)
    now = datetime.now(timezone.utc).isoformat()
    for job in jobs:
        store.setdefault(job_key(job), {
            "first_seen": now,
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
        })
    save_seen_store(store, path)
