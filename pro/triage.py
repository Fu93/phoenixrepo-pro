"""List a GitHub user's public repositories that look abandoned."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

STALE_DAYS = 365


class TriageError(RuntimeError):
    def __init__(self, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def abandoned_repos(user: str, *, getter: Callable[[str, str], Any] | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    name = (user or "").strip().strip("/")
    if not name or "/" in name or not name.replace("-", "").replace("_", "").isalnum():
        raise TriageError("Give a GitHub username, not a repository URL.")
    fetch = getter or _github_get
    try:
        payload = fetch(f"https://api.github.com/users/{name}/repos?per_page=100&type=owner&sort=pushed", "")
    except Exception as exc:  # noqa: BLE001 — the page shows the reason
        text = str(exc)
        if "404" in text or "not found" in text.casefold():
            raise TriageError(f"GitHub user {name} was not found.", status=404) from exc
        if "rate limit" in text.casefold():
            raise TriageError(text, status=429) from exc
        raise TriageError(f"GitHub did not return repositories for {name}.", status=502) from exc
    if isinstance(payload, dict) and payload.get("message"):
        raise TriageError(str(payload.get("message")), status=404)
    if not isinstance(payload, list):
        raise TriageError("GitHub did not return a repository list.", status=502)
    moment = now or datetime.now(timezone.utc)
    rows = []
    for repo in payload:
        if not isinstance(repo, dict) or repo.get("private") or repo.get("fork"):
            continue
        pushed = _parse(repo.get("pushed_at"))
        if pushed is None:
            continue
        age = (moment - pushed).days
        if age < STALE_DAYS:
            continue
        stars = int(repo.get("stargazers_count") or 0)
        description = str(repo.get("description") or "")
        score = stars + (5 if description else 0) + min(age // 365, 5)
        full = str(repo.get("full_name") or "")
        rows.append({
            "repo_id": full,
            "url": str(repo.get("html_url") or f"https://github.com/{full}"),
            "description": description,
            "pushed_at": pushed.date().isoformat(),
            "stale_days": age,
            "stars": stars,
            "score": score,
        })
    rows.sort(key=lambda row: (-row["score"], -row["stale_days"], row["repo_id"]))
    return rows


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _github_get(url: str, token: str, accept: str = "application/vnd.github+json"):
    from scripts.judge_repo import _get

    return _get(url, token, accept)
