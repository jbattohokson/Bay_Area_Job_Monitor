#!/usr/bin/env python3
"""Entry point: runs the crew, verifies links, and sends (or previews) the
digest email. Exit code 0 on success, 1 on failure — cron/systemd-friendly.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_PATH = PROJECT_ROOT / "logs" / "run.log"
PREVIEW_PATH = PROJECT_ROOT / "output" / "digest_preview.html"


def _configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
    )


def _is_dry_run(cli_flag: bool) -> bool:
    return cli_flag or os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def _verify_all_links(digest) -> None:
    """Best-effort reachability check on every job's link, run entirely
    outside the LLM/tool-calling flow. A failure flags the job in the email
    (see mailer.py) — it never removes it (see plan: bot-detection routinely
    403s automated requests to links that work fine in a browser)."""
    from src.tools.scraper import verify_link
    from src.utils.config import load_settings

    settings = load_settings()
    for group in list(digest.new_jobs_by_company) + list(digest.all_jobs_by_company):
        for job in group.jobs:
            job.link_verified = verify_link(job.link, settings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bay Area Job Monitor")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the digest to output/digest_preview.html instead of sending a real email.",
    )
    args = parser.parse_args()

    _configure_logging()
    load_dotenv(PROJECT_ROOT / ".env")
    logger = logging.getLogger("main")

    dry_run = _is_dry_run(args.dry_run)

    try:
        from src.crew import run_crew

        digest = run_crew()
    except Exception:
        logger.exception("Crew run failed")
        return 1

    try:
        _verify_all_links(digest)
    except Exception:
        logger.exception("Link verification pass failed — continuing with link_verified left as-is")

    try:
        from src.tools.mailer import render_html_digest, send_digest_email

        if dry_run:
            PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
            PREVIEW_PATH.write_text(render_html_digest(digest), encoding="utf-8")
            logger.info("Dry run: digest written to %s, no email sent", PREVIEW_PATH)
        else:
            send_digest_email(
                digest,
                sender=os.environ["GMAIL_SENDER"],
                app_password=os.environ["GMAIL_APP_PASSWORD"],
                recipient=os.environ["GMAIL_RECIPIENT"],
            )
            logger.info("Digest emailed to %s", os.environ["GMAIL_RECIPIENT"])
    except KeyError as exc:
        logger.error("Missing required environment variable: %s (check your .env)", exc)
        return 1
    except Exception:
        logger.exception("Rendering/sending the digest failed")
        return 1

    logger.info(
        "Done: %d new, %d total open roles matched.", digest.total_new, digest.total_open
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
