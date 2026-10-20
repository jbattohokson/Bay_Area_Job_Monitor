"""Loading config/companies.yaml — the single source of truth for which
companies to scrape and what counts as a matching role, read fresh on every
run so edits take effect on the next invocation with no code change."""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "companies.yaml"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("settings", {})
    data.setdefault("companies", [])
    return data


def load_companies(path: Path = DEFAULT_CONFIG_PATH) -> list[dict]:
    return load_config(path).get("companies", [])


def get_enabled_companies(companies: list[dict]) -> list[dict]:
    return [c for c in companies if c.get("enabled", True)]


def load_settings(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    return load_config(path).get("settings", {})
