"""Reads whatever resume file(s) the user has dropped into resumes/.

Read fresh on every run (never cached) so swapping in a new resume before
the next scheduled run picks it up automatically — no config change, no
re-upload step. Degrades gracefully to "" when the folder is empty or every
file fails to parse; the pay-estimate feature then just labels itself
unavailable for that run rather than crashing.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_RESUMES_DIR = Path(__file__).resolve().parents[2] / "resumes"
MAX_COMBINED_CHARS = 4000
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".md", ".txt"}


def _extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx_text(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs)


def _extract_plain_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _clean_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def load_resume_text(resumes_dir: Path = DEFAULT_RESUMES_DIR) -> str:
    if not resumes_dir.exists():
        logger.warning("resumes/ directory not found at %s — pay estimates will be unavailable", resumes_dir)
        return ""

    files = sorted(
        p for p in resumes_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        logger.warning(
            "No resume files found in %s (supported: %s) — pay estimates will be unavailable",
            resumes_dir,
            ", ".join(sorted(SUPPORTED_SUFFIXES)),
        )
        return ""

    sections: list[str] = []
    for path in files:
        try:
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                text = _extract_pdf_text(path)
            elif suffix == ".docx":
                text = _extract_docx_text(path)
            else:
                text = _extract_plain_text(path)
            text = _clean_whitespace(text)
            if text:
                sections.append(f"--- {path.name} ---\n{text}")
            else:
                logger.warning("Resume file %s parsed to empty text", path.name)
        except Exception as exc:
            logger.warning("Failed to read resume file %s: %s", path.name, exc)

    if not sections:
        return ""

    combined = "\n\n".join(sections)
    if len(combined) > MAX_COMBINED_CHARS:
        combined = combined[:MAX_COMBINED_CHARS] + "\n[...truncated...]"
    return combined
