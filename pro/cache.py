"""Result cache keyed by repository plus commit.

A repeat request for the same commit reads this file and does not call a model
or spend a daily run slot. The directory is local disk. On Render that disk is
ephemeral, so a restart empties the cache. That is stated in the deploy notes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def cache_key(repo_id: str, commit: str) -> str:
    material = f"{repo_id.strip().lower()}\n{commit.strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def permalink_for(repo_id: str, commit: str) -> str:
    return "c-" + cache_key(repo_id, commit)[:16]


class ResultCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, repo_id: str, commit: str) -> Path:
        return self.directory / f"{cache_key(repo_id, commit)}.json"

    def get(self, repo_id: str, commit: str) -> dict | None:
        if not repo_id or not commit:
            return None
        path = self.path_for(repo_id, commit)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def put(self, repo_id: str, commit: str, result: dict) -> dict:
        stored = dict(result)
        stored["repo_id"] = repo_id
        stored["commit"] = commit
        stored["cache_key"] = cache_key(repo_id, commit)
        stored["id"] = stored.get("id") or permalink_for(repo_id, commit)
        stored["cached"] = True
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path_for(repo_id, commit).write_text(
            json.dumps(stored, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return stored
