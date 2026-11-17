"""A deterministic GitHub candidate list beside the web search.

Web results change between runs. Two or three keyword queries, sorted by stars,
are cached by repository, commit, and query so the same commit is judged
against the same set.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus

from app.agents.alt_key import canonical_alternative_key, dedupe_alternatives

_STOP = frozenset(
    "a an the of to for and or with from your our this that free web online open source into over "
    "than then just more most some any all not but its app tool software using".split()
)


def keyword_queries(candidate: dict[str, Any]) -> list[str]:
    """Two or three queries, in a fixed order, from the repository's own words."""

    found: list[str] = []

    def add(text: str) -> None:
        words = [word for word in re.findall(r"[A-Za-z0-9]+", text) if len(word) > 2 and word.casefold() not in _STOP]
        query = " ".join(words[:6]).strip()
        if query and query.casefold() not in {item.casefold() for item in found}:
            found.append(query)

    core = str(candidate.get("core_terms") or "").replace("|", " ")
    if core.strip():
        add(core)
    add(str(candidate.get("description") or ""))
    topics = candidate.get("topics") or ""
    if isinstance(topics, str):
        add(topics.replace("|", " "))
    else:
        add(" ".join(str(topic) for topic in topics))
    return found[:3]


def rank_key(row: dict[str, Any]) -> tuple[int, str]:
    try:
        stars = int(row.get("stars"))
    except (TypeError, ValueError):
        stars = -1
    return (-stars, canonical_alternative_key(row))


def merge_ranked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = dedupe_alternatives([row for row in rows if isinstance(row, dict)])
    merged.sort(key=rank_key)
    return merged


def _cache_file(directory: Path, repo_id: str, commit: str, query: str) -> Path:
    digest = hashlib.sha256(f"{repo_id}\n{commit}\n{query}".encode("utf-8")).hexdigest()
    return directory / f"{digest}.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def cached_github_search(
    repo_id: str,
    commit: str,
    query: str,
    getter: Callable[..., Any],
    *,
    token: str = "",
    cache_dir: Path | None = None,
    store_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Search repositories sorted by stars. A hit in the durable cache does not call GitHub."""

    directory = cache_dir or Path(os.environ.get("PHOENIX_PRO_DIR") or "data/pro-runtime") / "search-cache"
    path = _cache_file(directory, repo_id, commit, query)
    stored = _read_json(path)
    if stored is None and store_dir is not None:
        stored = _read_json(_cache_file(store_dir, repo_id, commit, query))
        if stored is not None:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(stored), encoding="utf-8")
            except OSError:
                pass
    if stored is None:
        url = (
            "https://api.github.com/search/repositories?q="
            + quote_plus(query)
            + "&sort=stars&order=desc&per_page=5"
        )
        payload = getter(url, token)
        if not isinstance(payload, dict):
            return []
        stored = {"items": payload.get("items") or []}
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(stored), encoding="utf-8")
        except OSError:
            pass
    return _rows(stored.get("items") or [], query, repo_id)


def stable_github_candidates(
    candidate: dict[str, Any],
    *,
    getter: Callable[..., Any],
    commit: str = "",
    token: str = "",
    cache_dir: Path | None = None,
    store_dir: Path | None = None,
    trace: Any | None = None,
) -> list[dict[str, Any]]:
    repo_id = str(candidate.get("repo_id") or "")
    queries = keyword_queries(candidate)
    rows: list[dict[str, Any]] = []
    for query in queries:
        try:
            found = cached_github_search(
                repo_id, commit or "no-commit", query, getter,
                token=token, cache_dir=cache_dir, store_dir=store_dir,
            )
        except Exception as exc:  # noqa: BLE001 — a search miss keeps the web results
            if trace is not None:
                trace.event(
                    agent_name="orchestrator",
                    action="STABLE_SEARCH",
                    input_summary={"repo_id": repo_id, "query": query},
                    output_summary={"status": "metadata unavailable", "error": str(exc)[:180]},
                    status="error",
                )
            continue
        rows.extend(found)
        if trace is not None:
            trace.event(
                agent_name="orchestrator",
                action="STABLE_SEARCH",
                input_summary={"repo_id": repo_id, "query": query, "commit": commit or "no-commit"},
                output_summary={"status": "ok", "count": len(found), "repos": [row["alternative_id"] for row in found]},
                status="ok",
            )
    return merge_ranked(rows)


def _rows(items: list[Any], query: str, repo_id: str) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        full_name = str(item.get("full_name") or "").strip()
        if not full_name or full_name.casefold() == repo_id.casefold():
            continue
        licence = item.get("license") if isinstance(item.get("license"), dict) else {}
        rows.append({
            "alternative_id": full_name,
            "url": str(item.get("html_url") or f"https://github.com/{full_name}"),
            "description": str(item.get("description") or ""),
            "readme": "",
            "stars": item.get("stargazers_count"),
            "pushed_at": str(item.get("pushed_at") or ""),
            "archived": bool(item.get("archived")),
            "licence_spdx": str((licence or {}).get("spdx_id") or ""),
            "source": "github",
            "query": query,
            "fork": bool(item.get("fork")),
        })
    return rows
