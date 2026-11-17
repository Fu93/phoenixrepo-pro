"""Committed pre-judged examples. They load from disk and do not call a model."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
WINDOW_START = date(2026, 11, 7)
OLD_RECORDING = "2026-10-26"


def recorded_on(row: dict) -> str:
    """The day an example was judged. Old engine recordings carry no date field."""

    stamp = str(row.get("recorded_on") or "")
    if not stamp and str(row.get("source") or "") == "recorded-engine-trace":
        stamp = OLD_RECORDING
    return stamp


def recorded_label(stamp: str) -> str:
    try:
        day = date.fromisoformat(stamp)
    except ValueError:
        return ""
    text = f"{day.day} {day.strftime('%b')} {day.year}"
    return f"Recorded {text}, during SLAY HACKS" if day >= WINDOW_START else f"Recorded {text}, before SLAY HACKS"


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
        # Examples judged inside the build window come first, newest day first.
        rows.sort(key=lambda row: str(row.get("id") or ""))
        rows.sort(key=recorded_on, reverse=True)
        return rows

    def get(self, example_id: str) -> dict | None:
        for row in self.all():
            if row.get("id") == example_id:
                label = recorded_label(recorded_on(row))
                return {**row, "recorded_label": label} if label else row
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
            recorded = recorded_on(row)
            cards.append(
                {
                    "id": str(row.get("id") or ""),
                    "repo_id": str(row.get("repo_id") or ""),
                    "repo_url": str(row.get("repo_url") or ""),
                    "verdict": str(row.get("verdict") or ""),
                    "title": str(row.get("title") or row.get("repo_id") or ""),
                    "source": str(row.get("source") or ""),
                    "recorded_on": str(recorded),
                    "recorded_label": recorded_label(recorded),
                }
            )
        return cards
