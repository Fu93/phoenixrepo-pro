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
    "than then just more most some any all not but its app tool software using simplified simple "
    "modern fast better best new minimal tiny easy lightweight library libraries package".split()
)
MIN_STARS = 50
_JUNK_REPO = re.compile(r"^\.|(^dotfiles?$)|(^config$)|(^\.config$)", re.IGNORECASE)


def _topics(candidate: dict[str, Any]) -> list[str]:
    raw = candidate.get("topics") or ""
    if isinstance(raw, str):
        parts = raw.replace("|", " ").replace(",", " ").split()
    else:
        parts = [str(topic) for topic in raw]
    out: list[str] = []
    for part in parts:
        token = re.sub(r"[^A-Za-z0-9+-]", "", part).strip("-")
        if len(token) < 2 or token.casefold() in _STOP:
            continue
        if token.casefold() not in {item.casefold() for item in out}:
            out.append(token)
    return out[:3]


def _phrase(text: str, limit: int = 3) -> str:
    words: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9+#-]*", text or ""):
        if len(word) <= 2 or word.casefold() in _STOP:
            continue
        if word.casefold() not in {item.casefold() for item in words}:
            words.append(word)
        if len(words) >= limit:
            break
    return " ".join(words)


def _qualify(text: str, language: str) -> str:
    parts = [text.strip()]
    if language:
        lang = language.strip()
        parts.append(f'language:"{lang}"' if " " in lang else f"language:{lang}")
    parts.append(f"stars:>={MIN_STARS}")
    parts.append("fork:false")
    return " ".join(part for part in parts if part)


def keyword_queries(candidate: dict[str, Any]) -> list[str]:
    """Two or three GitHub queries: category words, topics, and a language qualifier.

    A long README phrase is not used. Each query asks GitHub for starred, non-fork
    repositories so a dotfile or an empty config repo does not lead the list.
    """

    language = str(candidate.get("primary_language") or candidate.get("language") or "").strip()
    found: list[str] = []
    plain = ecosystem_query(candidate)
    if plain:
        found.append(plain)

    def add(text: str, *, with_language: bool = False) -> None:
        query = _qualify(text, language if with_language else "")
        if text.strip() and query.casefold() not in {item.casefold() for item in found}:
            found.append(query)

    phrase = _phrase(str(candidate.get("description") or ""))
    if phrase:
        add(phrase, with_language=True)
    topics = _topics(candidate)
    if topics:
        add(" ".join(f"topic:{topic}" for topic in topics[:2]))
    if len(found) < 2:
        core = _phrase(str(candidate.get("core_terms") or "").replace("|", " "))
        if core and core.casefold() != (phrase or "").casefold():
            add(core)
    name = str(candidate.get("repo_id") or "").split("/")[-1]
    name_phrase = _phrase(name.replace("-", " ").replace("_", " "), limit=2)
    if len(found) < 2 and name_phrase and name_phrase.casefold() != (phrase or "").casefold():
        add(name_phrase)
    library = library_query(candidate)
    if library and library.casefold() not in {item.casefold() for item in found}:
        found.append(library)
    return found[:4]


def is_junk_repo(full_name: str) -> bool:
    repo = str(full_name or "").split("/")[-1]
    folded = repo.casefold()
    if not repo or repo.startswith("."):
        return True
    if _JUNK_REPO.search(repo):
        return True
    if folded == "awesome" or folded.startswith("awesome-") or "awesome-list" in folded:
        return True
    if folded in {"mirror", "mirrors"} or folded.endswith("-mirror") or folded.endswith(".mirror"):
        return True
    return False


_ECOSYSTEM_STOP = frozenset(
    "a an the of to for and or with from your our this that free web online open source into over "
    "than then just more most some any all not but its app tool software using".split()
)


def ecosystem_query(candidate: dict[str, Any]) -> str:
    """The short description query from round 8. It is what surfaced pnpm for Bower."""

    words: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9+#-]*", str(candidate.get("description") or "")):
        if len(word) <= 2 or word.casefold() in _ECOSYSTEM_STOP:
            continue
        if word.casefold() not in {item.casefold() for item in words}:
            words.append(word)
        if len(words) >= 6:
            break
    return " ".join(words)


def library_query(candidate: dict[str, Any]) -> str:
    """``<language> <category> library``, from the category or the registry-style description words.

    This is the query that keeps HTTP libraries such as got and axios in the set for request.
    """

    language = str(candidate.get("primary_language") or candidate.get("language") or "").strip()
    category = str(candidate.get("category") or candidate.get("_category") or "")
    words = _phrase(category, limit=2).split() if category else []
    if not words:
        words = _phrase(str(candidate.get("description") or ""), limit=2).split()
    if not language or not words:
        return ""
    return f"{language} {' '.join(words)} library"


def same_ecosystem(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
    """Same language, or the registry that language publishes to."""

    cand = str(candidate.get("primary_language") or candidate.get("language") or "").casefold()
    other = str(row.get("language") or row.get("primary_language") or "").casefold()
    if cand and other and cand == other:
        return True
    registry = str(row.get("registry") or row.get("source") or "").casefold()
    package = str(row.get("registry_package") or "")
    if cand in {"javascript", "typescript"} and (registry == "npm" or package):
        if registry == "npm" or "npm" in str(row.get("url") or ""):
            return True
    if cand == "python" and (registry == "pypi" or "pypi.org" in str(row.get("url") or "")):
        return True
    return False


def blocker_rank(candidate: dict[str, Any]):
    """Same language first, then stars, then the repository key."""

    def key(row: dict[str, Any]) -> tuple[int, int, str]:
        try:
            stars = int(row.get("stars"))
        except (TypeError, ValueError):
            stars = -1
        return (0 if same_ecosystem(candidate, row) else 1, -stars, canonical_alternative_key(row))

    return key


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


def merge_preserving_web(
    web_rows: list[dict[str, Any]],
    github_rows: list[dict[str, Any]],
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Sort the union by stars, then by repository key, and only then keep ``limit`` rows."""

    return merge_ranked(list(web_rows) + list(github_rows))[:limit]


def cached_payload(
    kind: str,
    repo_id: str,
    commit: str,
    query: str,
    produce: Callable[[], dict[str, Any]],
    *,
    cache_dir: Path | None = None,
    store_dir: Path | None = None,
) -> dict[str, Any]:
    """Read or store one search body. ``kind`` keeps web results apart from GitHub results."""

    directory = cache_dir or Path(os.environ.get("PHOENIX_PRO_DIR") or "data/pro-runtime") / "search-cache"
    digest = hashlib.sha256(f"{kind}\n{repo_id}\n{commit}\n{query}".encode("utf-8")).hexdigest()
    path = directory / f"{digest}.json"
    stored = _read_json(path)
    if stored is None and store_dir is not None:
        stored = _read_json(store_dir / f"{digest}.json")
    if stored is None:
        stored = produce()
        if not isinstance(stored, dict):
            stored = {}
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(stored), encoding="utf-8")
        except OSError:
            pass
    return stored


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
                from app.agents.search_snapshot import note_search_event

                note_search_event(trace.event(
                    agent_name="orchestrator",
                    action="STABLE_SEARCH",
                    input_summary={"repo_id": repo_id, "query": query},
                    output_summary={"status": "metadata unavailable", "error": str(exc)[:180]},
                    status="error",
                ))
            continue
        rows.extend(found)
        if trace is not None:
            from app.agents.search_snapshot import note_search_event

            note_search_event(trace.event(
                agent_name="orchestrator",
                action="STABLE_SEARCH",
                input_summary={"repo_id": repo_id, "query": query, "commit": commit or "no-commit"},
                output_summary={"status": "ok", "count": len(found), "repos": [row["alternative_id"] for row in found]},
                status="ok",
            ))
    return merge_ranked(rows)


def _rows(items: list[Any], query: str, repo_id: str) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        full_name = str(item.get("full_name") or "").strip()
        if not full_name or full_name.casefold() == repo_id.casefold() or is_junk_repo(full_name):
            continue
        try:
            stars = int(item.get("stargazers_count"))
        except (TypeError, ValueError):
            stars = -1
        if stars < MIN_STARS:
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
            "language": str(item.get("language") or ""),
            "query": query,
            "fork": bool(item.get("fork")),
        })
    return rows
