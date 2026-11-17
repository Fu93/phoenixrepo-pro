"""Committed pre-judged examples. They load from disk and do not call a model."""

from __future__ import annotations

import json
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"


class ExampleCatalog:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or EXAMPLES_DIR

    def all(self) -> list[dict]:
        rows: list[dict] = []
        if not self.directory.exists():
            return rows
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("id"):
                rows.append(data)
        return rows

    def get(self, example_id: str) -> dict | None:
        for row in self.all():
            if row.get("id") == example_id:
                return row
        return None

    def match_repo(self, repo_id: str) -> dict | None:
        wanted = (repo_id or "").strip().lower()
        for row in self.all():
            if str(row.get("repo_id") or "").lower() == wanted:
                return row
        return None

    def cards(self) -> list[dict[str, str]]:
        cards = []
        for row in self.all():
            recorded = row.get("recorded_on") or ""
            if not recorded and str(row.get("source") or "") == "recorded-engine-trace":
                recorded = "2026-10-26"
            cards.append(
                {
                    "id": str(row.get("id") or ""),
                    "repo_id": str(row.get("repo_id") or ""),
                    "repo_url": str(row.get("repo_url") or ""),
                    "verdict": str(row.get("verdict") or ""),
                    "title": str(row.get("title") or row.get("repo_id") or ""),
                    "source": str(row.get("source") or ""),
                    "recorded_on": str(recorded),
                    "recorded_label": "Recorded 26 Oct 2026, before SLAY HACKS" if recorded == "2026-10-26" else "",
                }
            )
        return cards
