"""One sealed record of the search and metadata a commit was judged against.

The first run of a repository and commit stores every web query, GitHub query,
and metadata lookup. The sha256 of that record is written to the trace before
any overlap judgement. A later run loads the record and does not call the
network. ``refresh`` starts a new record and marks it as refreshed.

Model replies are stored on the same object, keyed by the prompt hash, the
model id, and the temperature. They are not part of the sealed hash: the hash
is fixed before those calls run. A replay returns the stored reply.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

_SESSION: ContextVar["SnapshotSession | None"] = ContextVar("search_snapshot", default=None)

_SEARCH_ACTIONS = ("SEARCH_SNAPSHOT", "PRIOR_ART_SEARCH", "STABLE_SEARCH", "QUERY_GENERATION", "GITHUB_METADATA", "REGISTRY_METADATA")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_RUN_ID = re.compile(r"\brun[-_][0-9a-fA-F]{6,}\b")


def canonical_prompt(system: str, prompt: str) -> str:
    """Prompt text with timestamps and run ids removed, so a replay hits the same key."""

    text = f"{system}\n{prompt}"
    text = _TIMESTAMP.sub(" ", text)
    text = _RUN_ID.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def model_cache_key(system: str, prompt: str, model: str, temperature: float) -> str:
    body = canonical_prompt(system, prompt)
    return hashlib.sha256(f"{model}\n{float(temperature):.1f}\n{body}".encode("utf-8")).hexdigest()


def default_dir() -> Path:
    return Path(os.environ.get("PHOENIX_PRO_DIR") or "data/pro-runtime") / "search-snapshots"


def snapshot_path(repo_id: str, commit: str, directory: Path | None = None) -> Path:
    digest = hashlib.sha256(f"{repo_id}\n{commit}".encode("utf-8")).hexdigest()
    return (directory or default_dir()) / f"{digest}.json"


def _jsonish(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str))


def search_document(doc: dict[str, Any]) -> dict[str, Any]:
    """The fields the sealed hash covers. Model replies are not among them."""

    records = list(doc.get("records") or [])
    records.sort(key=lambda item: (str(item.get("kind") or ""), str(item.get("key") or "")))
    return {
        "repo_id": str(doc.get("repo_id") or ""),
        "commit": str(doc.get("commit") or ""),
        "fetched_at": str(doc.get("fetched_at") or ""),
        "refreshed": bool(doc.get("refreshed")),
        "records": records,
    }


def canonical_search_json(doc: dict[str, Any]) -> str:
    return json.dumps(search_document(doc), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def snapshot_hash(doc: dict[str, Any]) -> str:
    """Hash of the canonical search body. The sealed file hash is sha256 of the stored bytes."""

    return hashlib.sha256(canonical_search_json(doc).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_session() -> "SnapshotSession | None":
    return _SESSION.get()


def as_of_date() -> date:
    session = current_session()
    if session is None or not session.fetched_at:
        return date.today()
    return date.fromisoformat(session.fetched_at[:10])


def as_of_datetime() -> datetime:
    session = current_session()
    if session is None or not session.fetched_at:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(session.fetched_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class SnapshotSession:
    def __init__(
        self,
        repo_id: str,
        commit: str,
        path: Path,
        *,
        refreshed: bool,
        fetched_at: str,
        records: list[dict[str, Any]],
        model_calls: list[dict[str, Any]],
        replaying: bool,
        collected_at: str = "",
        problem: Any = None,
        queries: list[str] | None = None,
        search_results: list[dict[str, Any]] | None = None,
        source_counts: dict[str, int] | None = None,
        source_status: dict[str, str] | None = None,
        search_events: list[dict[str, str]] | None = None,
        captured: bool = False,
        rescue: Any = None,
    ) -> None:
        self.repo_id = repo_id
        self.commit = commit
        self.path = path
        self.refreshed = refreshed
        self.fetched_at = fetched_at
        self.collected_at = collected_at
        self.records = records
        self.model_calls = model_calls
        self.problem = problem
        self.queries = list(queries or [])
        self.search_results = list(search_results or [])
        self.source_counts = dict(source_counts or {})
        self.source_status = dict(source_status or {})
        self.search_events = list(search_events or [])
        self.captured = captured
        self.rescue = rescue
        self.replaying = replaying
        self.live_calls = 0
        self.blocked = 0
        self.incomplete = False
        self.sealed_hash = ""
        self._finished = False
        self._index = {(str(item.get("kind")), str(item.get("key"))): item for item in records}

    def find(self, kind: str, key: str) -> dict[str, Any] | None:
        return self._index.get((kind, key))

    def put(self, kind: str, key: str, payload: Any) -> None:
        record = {
            "kind": kind,
            "key": key,
            "fetched_at": self.fetched_at,
            "payload": _jsonish(payload),
        }
        self.records.append(record)
        self._index[(kind, key)] = record

    def ready(self) -> bool:
        return self.replaying and self.captured

    def capture(
        self,
        *,
        problem: Any,
        queries: list[str],
        results: list[dict[str, Any]],
        source_counts: dict[str, int] | None = None,
        source_status: dict[str, str] | None = None,
    ) -> None:
        if self.replaying:
            return
        self.problem = _jsonish(problem) if problem is not None else None
        self.queries = list(queries)
        self.search_results = _jsonish(results)
        self.source_counts = {str(key): int(value) for key, value in (source_counts or {}).items()}
        self.source_status = {str(key): str(value) for key, value in (source_status or {}).items()}
        self.captured = True

    def note(self, record: dict[str, Any] | None) -> None:
        if self.replaying or not isinstance(record, dict):
            return
        action = str(record.get("action") or "")
        if action not in _SEARCH_ACTIONS or action == "SEARCH_SNAPSHOT":
            return
        self.search_events.append({
            "action": action,
            "input_summary": str(record.get("input_summary") or ""),
            "output_summary": str(record.get("output_summary") or ""),
        })

    def document(self) -> dict[str, Any]:
        return {
            "captured": self.captured,
            "collected_at": self.collected_at,
            "commit": self.commit,
            "fetched_at": self.fetched_at,
            "model_calls": self.model_calls,
            "problem": self.problem,
            "queries": self.queries,
            "records": self.records,
            "refreshed": self.refreshed,
            "repo_id": self.repo_id,
            "rescue": self.rescue,
            "search_events": self.search_events,
            "search_results": self.search_results,
            "source_counts": self.source_counts,
            "source_status": self.source_status,
        }

    def canonical_text(self) -> str:
        return json.dumps(self.document(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def finish(self, trace: Any | None) -> str:
        """Write the snapshot bytes once and seal sha256 of those exact bytes."""

        if self._finished and self.sealed_hash:
            return self.sealed_hash
        text = self.canonical_text()
        raw = text.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        self.sealed_hash = digest
        self._finished = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(raw)
        except OSError:
            pass
        if trace is not None:
            trace.event(
                agent_name="orchestrator",
                action="SEARCH_SNAPSHOT",
                input_summary={"repo_id": self.repo_id, "commit": self.commit},
                output_summary={
                    "snapshot_sha256": digest,
                    "refreshed": self.refreshed,
                    "records": len(self.records),
                    "model_calls": len(self.model_calls),
                },
                status="ok",
            )
        return digest

    def seal(self, trace: Any | None) -> str:
        return self.finish(trace)

    def close(self) -> None:
        if self._finished:
            return
        if self.captured or self.records or self.model_calls:
            self.finish(None)

    def model_get(self, prompt_sha256: str, model: str, temperature: float) -> dict[str, Any] | None:
        for item in self.model_calls:
            if item.get("key") == prompt_sha256 or (
                item.get("model") == model and item.get("temperature") == float(temperature) and item.get("key") == prompt_sha256
            ):
                return item
        return None

    def model_put(self, prompt_sha256: str, model: str, temperature: float, text: str, requested: str, served: str) -> None:
        if self.replaying or any(item.get("key") == prompt_sha256 for item in self.model_calls):
            return
        self.model_calls.append({
            "key": prompt_sha256,
            "model": model,
            "temperature": float(temperature),
            "response_text": text,
            "requested_model": requested,
            "served_model": served,
        })


def open_snapshot(repo_id: str, commit: str, *, refresh: bool = False, directory: Path | None = None) -> SnapshotSession | None:
    if not str(repo_id or "").strip() or not str(commit or "").strip():
        return None
    path = snapshot_path(repo_id, commit, directory)
    if path.is_file() and not refresh:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict) and (saved.get("records") or saved.get("captured") or saved.get("model_calls")):
            return SnapshotSession(
                repo_id,
                commit,
                path,
                refreshed=bool(saved.get("refreshed")),
                fetched_at=str(saved.get("fetched_at") or ""),
                records=list(saved.get("records") or []),
                model_calls=list(saved.get("model_calls") or []),
                replaying=True,
                collected_at=str(saved.get("collected_at") or ""),
                problem=saved.get("problem"),
                queries=list(saved.get("queries") or []),
                search_results=list(saved.get("search_results") or []),
                source_counts=dict(saved.get("source_counts") or {}),
                source_status=dict(saved.get("source_status") or {}),
                search_events=list(saved.get("search_events") or []),
                captured=bool(saved.get("captured")),
                rescue=saved.get("rescue"),
            )
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return SnapshotSession(
        repo_id,
        commit,
        path,
        refreshed=bool(refresh and path.is_file()),
        fetched_at=now,
        records=[],
        model_calls=[],
        replaying=False,
    )


def bind(session: SnapshotSession | None):
    return _SESSION.set(session)


def reset(token) -> None:
    _SESSION.reset(token)


def load_snapshot(repo_id: str, commit: str, directory: Path | None = None) -> dict[str, Any] | None:
    path = snapshot_path(repo_id, commit, directory)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def search_events_hash(trace_path: Path) -> str:
    """Hash search-event input and output. Run id, timestamps, and the chain are left out."""

    parts: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("action") not in _SEARCH_ACTIONS:
            continue
        parts.append(
            str(record.get("action"))
            + "\n"
            + str(record.get("input_summary") or "")
            + "\n"
            + str(record.get("output_summary") or "")
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def sealed_hash_from_trace(trace_path: Path) -> str:
    claimed = ""
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("action") != "SEARCH_SNAPSHOT":
            continue
        summary = record.get("output_summary")
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except json.JSONDecodeError:
                summary = {}
        if isinstance(summary, dict):
            claimed = str(summary.get("snapshot_sha256") or "")
    return claimed


def verify_snapshot(trace_path: Path, snapshot_file: Path) -> tuple[bool, str]:
    """The SEARCH_SNAPSHOT line must be the sha256 of the snapshot file bytes."""

    try:
        digest = file_sha256(snapshot_file)
    except OSError as exc:
        return False, f"snapshot is not readable: {exc}"
    claimed = sealed_hash_from_trace(trace_path)
    if not claimed:
        return False, "trace has no SEARCH_SNAPSHOT hash"
    if claimed != digest:
        return False, f"sealed snapshot hash {claimed} does not match the stored snapshot {digest}"
    return True, digest


class SnapshotToolbox:
    """Replay or record toolbox.call. A replay never calls the inner tool."""

    def __init__(self, inner: Any, session: SnapshotSession) -> None:
        self.inner = inner
        self.session = session
        self.tools = getattr(inner, "tools", {})
        self.budget = _OpenBudget() if session.replaying else getattr(inner, "budget", None)
        self.trace = getattr(inner, "trace", None)

    def has(self, name: str) -> bool:
        if hasattr(self.inner, "has"):
            return bool(self.inner.has(name))
        return name in (self.tools or {})

    def call(self, name: str, args: dict[str, Any] | None = None):
        from app.tools.base import ToolResult

        key = json.dumps({"tool": name, "args": args or {}}, sort_keys=True, separators=(",", ":"), default=str)
        found = self.session.find("tool", key)
        if found is not None:
            return _tool_result(found.get("payload") or {})
        if self.session.replaying:
            self.session.blocked += 1
            self.session.incomplete = True
            return ToolResult(
                ok=False, data={}, error="search snapshot is incomplete",
                source_url="", fetched_at=self.session.fetched_at,
            )
        self.session.live_calls += 1
        result = self.inner.call(name, args)
        self.session.put("tool", key, {
            "ok": bool(result.ok),
            "data": result.data,
            "error": str(result.error or ""),
            "source_url": str(result.source_url or ""),
            "fetched_at": str(result.fetched_at or self.session.fetched_at),
        })
        return result


class _OpenBudget:
    """Replay does not re-apply the wall clock. Stored calls are returned as they were."""

    calls_used = 0
    max_calls = 10**6
    max_seconds = 10**6

    @property
    def remaining_calls(self) -> int:
        return self.max_calls

    @property
    def remaining_seconds(self) -> float:
        return float(self.max_seconds)

    def is_exhausted(self) -> bool:
        return False

    def consume(self) -> bool:
        return True


def _tool_result(payload: dict[str, Any]):
    from app.tools.base import ToolResult

    return ToolResult(
        ok=bool(payload.get("ok")),
        data=payload.get("data"),
        error=str(payload.get("error") or ""),
        source_url=str(payload.get("source_url") or ""),
        fetched_at=str(payload.get("fetched_at") or ""),
    )


def wrap_github(session: SnapshotSession, getter: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(url: str, token: str = "", accept: str = "application/vnd.github+json"):
        found = session.find("github_http", str(url))
        if found is not None:
            return found.get("payload")
        if session.replaying:
            session.blocked += 1
            return {"message": "not in snapshot"}
        session.live_calls += 1
        payload = getter(url, token, accept)
        session.put("github_http", str(url), payload)
        return payload

    return wrapped


def replay_model(system: str, prompt: str, model: str, temperature: float) -> dict[str, Any] | None:
    """Return a stored reply, or {'incomplete': True} when a replay has no match.

    A recording run returns None so the caller talks to the model and then ``remember_model``.
    """

    session = current_session()
    if session is None:
        return None
    key = model_cache_key(system, prompt, model, temperature)
    found = session.model_get(key, model, temperature)
    if found is not None:
        return found
    if session.replaying:
        session.blocked += 1
        session.incomplete = True
        return {"incomplete": True}
    return None


def remember_model(system: str, prompt: str, model: str, temperature: float, text: str, requested: str, served: str) -> None:
    session = current_session()
    if session is None or session.replaying:
        return
    session.model_put(model_cache_key(system, prompt, model, temperature), model, temperature, text, requested, served)


def note_search_event(record: dict[str, Any] | None) -> None:
    session = current_session()
    if session is not None:
        session.note(record)


def wrap_registry(session: SnapshotSession, getter: Callable[[str], Any]) -> Callable[[str], Any]:
    def wrapped(url: str):
        found = session.find("registry_http", str(url))
        if found is not None:
            return found.get("payload")
        if session.replaying:
            session.blocked += 1
            raise RuntimeError("registry lookup is not in the snapshot")
        session.live_calls += 1
        payload = getter(url)
        session.put("registry_http", str(url), payload)
        return payload

    return wrapped
