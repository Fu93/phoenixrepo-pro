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
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

_SESSION: ContextVar["SnapshotSession | None"] = ContextVar("search_snapshot", default=None)

_SEARCH_ACTIONS = ("SEARCH_SNAPSHOT", "PRIOR_ART_SEARCH", "STABLE_SEARCH", "QUERY_GENERATION", "GITHUB_METADATA", "REGISTRY_METADATA")


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
    return hashlib.sha256(canonical_search_json(doc).encode("utf-8")).hexdigest()


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
    def __init__(self, repo_id: str, commit: str, path: Path, *, refreshed: bool, fetched_at: str, records: list[dict[str, Any]], model_calls: list[dict[str, Any]], replaying: bool) -> None:
        self.repo_id = repo_id
        self.commit = commit
        self.path = path
        self.refreshed = refreshed
        self.fetched_at = fetched_at
        self.records = records
        self.model_calls = model_calls
        self.replaying = replaying
        self.live_calls = 0
        self.blocked = 0
        self.sealed_hash = ""
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

    def document(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "commit": self.commit,
            "fetched_at": self.fetched_at,
            "refreshed": self.refreshed,
            "records": self.records,
            "model_calls": self.model_calls,
            "snapshot_sha256": self.sealed_hash,
        }

    def seal(self, trace: Any | None) -> str:
        digest = snapshot_hash(self.document())
        self.sealed_hash = digest
        self._write()
        if trace is not None:
            trace.event(
                agent_name="orchestrator",
                action="SEARCH_SNAPSHOT",
                input_summary={"repo_id": self.repo_id, "commit": self.commit},
                output_summary={
                    "snapshot_sha256": digest,
                    "refreshed": self.refreshed,
                    "records": len(self.records),
                },
                status="ok",
            )
        return digest

    def close(self) -> None:
        if not self.sealed_hash:
            return
        self._write()

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.document(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            pass

    def model_key(self, prompt_sha256: str, model: str, temperature: float) -> str:
        return f"{model}\n{temperature}\n{prompt_sha256}"

    def model_get(self, prompt_sha256: str, model: str, temperature: float) -> dict[str, Any] | None:
        key = self.model_key(prompt_sha256, model, temperature)
        for item in self.model_calls:
            if item.get("key") == key:
                return item
        return None

    def model_put(self, prompt_sha256: str, model: str, temperature: float, text: str, requested: str, served: str) -> None:
        key = self.model_key(prompt_sha256, model, temperature)
        if any(item.get("key") == key for item in self.model_calls):
            return
        self.model_calls.append({
            "key": key,
            "prompt_sha256": prompt_sha256,
            "model": model,
            "temperature": temperature,
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
        if isinstance(saved, dict) and saved.get("records"):
            return SnapshotSession(
                repo_id,
                commit,
                path,
                refreshed=bool(saved.get("refreshed")),
                fetched_at=str(saved.get("fetched_at") or ""),
                records=list(saved.get("records") or []),
                model_calls=list(saved.get("model_calls") or []),
                replaying=True,
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
    """Hash the search-event summaries. Timestamps and the chain are left out."""

    parts: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("action") not in _SEARCH_ACTIONS:
            continue
        parts.append(str(record.get("action")) + "\n" + str(record.get("output_summary") or ""))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def verify_snapshot(trace_path: Path, snapshot_file: Path) -> tuple[bool, str]:
    """The SEARCH_SNAPSHOT line must name the hash of the stored search record."""

    try:
        doc = json.loads(snapshot_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"snapshot is not readable JSON: {exc}"
    if not isinstance(doc, dict):
        return False, "snapshot is not an object"
    digest = snapshot_hash(doc)
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
            return ToolResult(ok=False, data={}, error="BUDGET_EXHAUSTED", source_url="", fetched_at=self.session.fetched_at)
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
