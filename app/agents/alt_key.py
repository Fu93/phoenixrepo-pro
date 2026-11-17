"""One key for the same GitHub repository, however the URL was written."""

from __future__ import annotations

import re
from typing import Any

_MIRROR_HOST = re.compile(
    r"(?:https?://)?(?:www\.)?(?:qgithub\.com|kgithub\.com|github\.com\.cn)",
    re.IGNORECASE,
)
_GITHUB = re.compile(r"github\.com/([^/\s?#]+)/([^/\s?#]+)", re.IGNORECASE)
_BARE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
_NPM = re.compile(r"(?:www\.)?npmjs\.com/package/([^/\s?#]+)", re.IGNORECASE)
_SKIP = {"topics", "orgs", "features", "settings", "marketplace", "sponsors", "org", "apps"}


def _clean_repo(repo: str) -> str:
    name = repo[:-4] if repo.casefold().endswith(".git") else repo
    return name.strip()


def github_owner_repo(value: str) -> str | None:
    text = str(value or "").strip()
    if text.startswith("web:"):
        text = text[4:]
    text = _MIRROR_HOST.sub("github.com", text)
    match = _GITHUB.search(text)
    if match:
        owner, repo = match.group(1), _clean_repo(match.group(2))
        if owner.casefold() in _SKIP or not repo:
            return None
        return f"{owner}/{repo}"
    if "://" in text or text.startswith("npm:") or text.startswith("pypi:"):
        return None
    bare = _BARE.match(text)
    if bare is None:
        return None
    owner, repo = bare.group(1), _clean_repo(bare.group(2))
    if "." in repo or owner.casefold() in _SKIP or not repo:
        return None
    return f"{owner}/{repo}"


def canonical_alternative_key(row: dict[str, Any] | str) -> str:
    if isinstance(row, str):
        fields = [row]
    else:
        fields = [str(row.get(name) or "") for name in ("url", "alternative_id", "html_url")]
    for field in fields:
        repo = github_owner_repo(field)
        if repo:
            return "github.com/" + repo.casefold()
        text = field[4:] if field.startswith("web:") else field
        npm = _NPM.search(text)
        if npm:
            return "npm:" + npm.group(1).casefold()
        if text.startswith("npm:"):
            return "npm:" + text[4:].casefold()
    if isinstance(row, str):
        return row.strip().casefold().rstrip("/")
    return str(row.get("url") or row.get("alternative_id") or "").strip().casefold().rstrip("/")


def dedupe_alternatives(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first row for each repository and copy in any facts the duplicate had."""

    kept: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = canonical_alternative_key(row)
        if not key:
            kept.append(row)
            continue
        current = index.get(key)
        if current is None:
            index[key] = row
            kept.append(row)
            continue
        current_id = str(current.get("alternative_id") or "")
        incoming_id = str(row.get("alternative_id") or "")
        if github_owner_repo(incoming_id) == incoming_id and github_owner_repo(current_id) != current_id:
            current["alternative_id"] = incoming_id
            if row.get("url"):
                current["url"] = row["url"]
            if row.get("source"):
                current["source"] = row["source"]
        for field in ("readme", "description", "excerpt", "pushed_at", "licence_spdx", "released_at", "stars"):
            if not current.get(field) and row.get(field):
                current[field] = row[field]
        if current.get("stars") in (None, "", 0) and row.get("stars") not in (None, "", 0):
            current["stars"] = row["stars"]
        if row.get("overlap") == "full" and current.get("overlap") != "full":
            current["overlap"] = "full"
        if row.get("overlap_reads") == "disagree":
            current["overlap_reads"] = "disagree"
    return kept
