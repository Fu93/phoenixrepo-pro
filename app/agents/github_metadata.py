"""Stars and push dates for web hits that point at GitHub.

A web result does not include stargazers_count. Filling that with 0 makes the
maturity rule fail for every GitHub URL found by search. This looks up at most
a few repositories, through the same per-run GitHub cache as the rest of the
judge, and leaves the number unknown when the API does not answer.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable

_GITHUB_REPO = re.compile(r"github\.com/([^/\s?#]+)/([^/\s?#]+)", re.IGNORECASE)
_SKIP_OWNERS = {"topics", "orgs", "features", "settings", "marketplace", "sponsors"}


def _linked_github_repo(row: dict[str, Any]) -> str | None:
    """A github.com path on the row, including a mirror host or a URL inside the page text."""

    for field in ("repository_url", "url", "alternative_id", "description", "readme", "excerpt"):
        repo = github_repo_from_url(str(row.get(field) or ""))
        if repo:
            return repo
    return None


def github_repo_from_url(value: str) -> str | None:
    text = str(value or "")
    if text.startswith("web:"):
        text = text[4:]
    text = re.sub(
        r"(?:https?://)?(?:www\.)?(?:qgithub\.com|kgithub\.com|github\.com\.cn)",
        "https://github.com",
        text,
        flags=re.IGNORECASE,
    )
    match = _GITHUB_REPO.search(text)
    if match is None:
        return None
    owner, repo = match.group(1), match.group(2)
    if owner.casefold() in _SKIP_OWNERS:
        return None
    if repo.casefold().endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def _relevance(row: dict[str, Any]) -> int:
    score = 0
    if row.get("same_problem") is True or row.get("quote_verified") is True:
        score += 2
    if str(row.get("overlap") or "") == "full":
        score += 2
    return score


def _known(row: dict[str, Any]) -> bool:
    """A GitHub search row already carries stars and a push date. A web row does not."""

    if str(row.get("source") or "") == "web":
        return False
    if str(row.get("alternative_id") or "").startswith("web:"):
        return False
    return bool(row.get("pushed_at")) and row.get("stars") not in (None, "")


def _mark_unknown(row: dict[str, Any]) -> None:
    if row.get("stars") in (0, "0", None, ""):
        row["stars"] = None


def attach_github_metadata(
    rows: list[dict[str, Any]],
    *,
    getter: Callable[..., Any] | None = None,
    trace: Any | None = None,
    limit: int = 5,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Fill stars, archived, pushed_at and description for up to ``limit`` GitHub URLs.

    Failures leave the stars unknown and record ``metadata unavailable``. They do
    not write a stand-in count.
    """

    if getter is None:
        from scripts.judge_repo import _get

        getter = _get
    if token is None:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    order = sorted(range(len(rows)), key=lambda index: _relevance(rows[index]), reverse=True)
    used = 0
    for index in order:
        row = rows[index]
        repo = _linked_github_repo(row)
        if repo is None:
            if str(row.get("source") or "") != "github":
                _mark_unknown(row)
            continue
        if _known(row):
            continue
        if used >= limit:
            _mark_unknown(row)
            row["metadata_status"] = "not_looked_up"
            continue
        used += 1
        try:
            data = getter(f"https://api.github.com/repos/{repo}", token)
        except Exception as exc:  # noqa: BLE001 — a rate limit must not invent a star count
            _unavailable(row, trace, repo, exc)
            continue
        if not isinstance(data, dict) or "stargazers_count" not in data:
            _unavailable(row, trace, repo, "GitHub did not return stargazers_count")
            continue
        row["stars"] = data.get("stargazers_count")
        if "archived" in data:
            row["archived"] = bool(data.get("archived"))
        if data.get("pushed_at"):
            row["pushed_at"] = str(data.get("pushed_at"))
        description = data.get("description")
        if isinstance(description, str) and description.strip() and not row.get("description"):
            row["description"] = description.strip()
        licence = data.get("license") if isinstance(data.get("license"), dict) else {}
        spdx = (licence or {}).get("spdx_id")
        if spdx and not row.get("licence_spdx"):
            row["licence_spdx"] = spdx
        row["metadata_status"] = "ok"
        row["fetch_status"] = "ok"
        if trace is not None:
            trace.event(
                agent_name="orchestrator",
                action="GITHUB_METADATA",
                input_summary={"repo": repo},
                output_summary={
                    "status": "ok",
                    "stars": row.get("stars"),
                    "pushed_at": row.get("pushed_at") or "",
                    "archived": row.get("archived"),
                },
                status="ok",
            )
    return rows


def _unavailable(row: dict[str, Any], trace: Any | None, repo: str, exc: BaseException | str) -> None:
    _mark_unknown(row)
    row["metadata_status"] = "unavailable"
    row["fetch_status"] = "metadata_unavailable"
    if trace is None:
        return
    trace.event(
        agent_name="orchestrator",
        action="GITHUB_METADATA",
        input_summary={"repo": repo},
        output_summary={"status": "metadata unavailable", "error": str(exc)[:180]},
        status="error",
    )
