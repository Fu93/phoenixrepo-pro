"""Run lifecycle for PhoenixRepo Pro: demo, cache, caps, and a live judge."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from config.settings import Settings, usable_secret
from pro.cache import ResultCache, permalink_for
from pro.examples import ExampleCatalog
from pro.injection import neutralise
from pro.limits import LimitConfig, LimitError, RunLedger
from pro.present import (
    alternatives_from_discovery,
    discovery_from_events,
    primary_quote,
    problem_from_events,
    public_result,
    trace_events,
)

DEMO_MESSAGE = (
    "No NVIDIA_API_KEY or GEMINI_API_KEY is set, so this process cannot judge a new repository. "
    "The three recorded examples still load, and the injection demo still runs."
)


class ProError(RuntimeError):
    def __init__(self, message: str, *, status: int, kind: str) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind


class Job:
    def __init__(self, job_id: str, url: str, repo_id: str, ip: str) -> None:
        self.id = job_id
        self.url = url
        self.repo_id = repo_id
        self.ip = ip
        self.commit = ""
        self.status = "running"
        self.events: list[dict[str, Any]] = []
        self.result_id = ""
        self.error = ""
        self.trace_path: Path | None = None
        self.injection: dict[str, Any] = {
            "detected": False,
            "neutralised": False,
            "count": 0,
            "findings": [],
        }


class ProService:
    def __init__(
        self,
        root: Path,
        *,
        settings_fn: Callable[[], Settings] | None = None,
        github: Callable | None = None,
        runner: Callable[[Job], dict] | None = None,
        background: bool = True,
        examples: ExampleCatalog | None = None,
        clock=None,
    ) -> None:
        self.root = root
        self.settings_fn = settings_fn or Settings
        self.github = github or _github_get
        self.runner = runner
        self.background = background
        self.examples = examples or ExampleCatalog()
        self.cache = ResultCache(root / "cache")
        self.ledger = RunLedger(root / "limits.json", LimitConfig.from_env(), now=clock)
        self.results = root / "results"
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        from pro.hosted import provider_mode

        settings = self.settings_fn()
        mode = provider_mode(settings)
        return {
            "mode": mode,
            "nvidia": bool(settings.nvidia_api_key),
            "gemini": bool(settings.gemini_api_key),
            "nightshift_required": False,
            "model": _public_model(settings, mode),
            "limits": self.ledger.snapshot(),
            "examples": self.examples.cards(),
        }

    def submit(self, url: str, ip: str, *, prefer_live: bool = False) -> dict[str, Any]:
        from pro.hosted import provider_mode
        from scripts.judge_repo import JudgeError, parse_repo_url

        try:
            repo_id = parse_repo_url(url)
        except JudgeError as exc:
            raise ProError(str(exc), status=422, kind="CannotJudge") from exc
        mode = provider_mode(self.settings_fn())
        example = self.examples.match_repo(repo_id)
        if mode == "demo":
            if example and not prefer_live:
                return self._instant(example)
            return {
                "status": "demo",
                "demo": True,
                "cached": False,
                "model_calls": 0,
                "message": DEMO_MESSAGE,
                "examples": self.examples.cards(),
            }
        commit = ""
        try:
            commit = self._commit(repo_id)
        except ProError:
            commit = ""
        if commit:
            cached = self.cache.get(repo_id, commit)
            if cached:
                self._write_result(cached)
                return {
                    "status": "done",
                    "id": cached["id"],
                    "result_id": cached["id"],
                    "demo": False,
                    "cached": True,
                    "model_calls": 0,
                    "verdict": cached.get("verdict"),
                }
        try:
            self.ledger.reserve(ip or "unknown")
        except LimitError as exc:
            raise ProError(str(exc), status=429, kind=exc.kind) from exc
        job = Job(f"run-{uuid.uuid4().hex[:12]}", url, repo_id, ip or "unknown")
        job.commit = commit
        job.events.append({"type": "stage", "message": "Queued"})
        with self._lock:
            self.jobs[job.id] = job
        if self.background:
            threading.Thread(target=self._execute, args=(job,), daemon=True).start()
            return {
                "status": "running",
                "id": job.id,
                "demo": False,
                "cached": False,
                "events": f"/pro/api/runs/{job.id}/events",
            }
        self._execute(job)
        return self._job_body(job)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise ProError("Unknown run.", status=404, kind="NotFound")
        return self._job_body(job)

    def iter_events(self, job_id: str):
        import time

        sent = 0
        traced = 0
        while True:
            job = self.jobs.get(job_id)
            if job is None:
                yield {"type": "error", "message": "Unknown run."}
                return
            with self._lock:
                pending = list(job.events[sent:])
            for event in pending:
                yield event
                sent += 1
            extra, traced = read_trace_events(job, traced)
            for event in extra:
                yield event
            if job.status in {"done", "error"} and sent >= len(job.events):
                yield {
                    "type": "done",
                    "status": job.status,
                    "result_id": job.result_id,
                    "message": job.error,
                }
                return
            time.sleep(0.05)

    def get_result(self, result_id: str) -> dict[str, Any]:
        example = self.examples.get(result_id)
        if example:
            return example
        path = self.results / f"{_safe_id(result_id)}.json"
        if not path.exists():
            raise ProError("Unknown result.", status=404, kind="NotFound")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProError("Result file is not an object.", status=500, kind="Result")
        return data

    def _instant(self, example: dict) -> dict[str, Any]:
        return {
            "status": "done",
            "id": example["id"],
            "result_id": example["id"],
            "demo": True,
            "cached": True,
            "model_calls": 0,
            "verdict": example.get("verdict"),
        }

    def _commit(self, repo_id: str) -> str:
        token = _github_token()
        try:
            repo = self.github(f"https://api.github.com/repos/{repo_id}", token)
        except Exception as exc:  # noqa: BLE001 — the caller treats a missing sha as uncached
            raise ProError(f"Could not read {repo_id} from GitHub.", status=502, kind="GitHub") from exc
        if not isinstance(repo, dict):
            raise ProError("GitHub did not return a repository object.", status=502, kind="GitHub")
        branch = str(repo.get("default_branch") or "HEAD")
        try:
            commit = self.github(f"https://api.github.com/repos/{repo_id}/commits/{branch}", token)
        except Exception as exc:  # noqa: BLE001
            raise ProError(f"Could not read the head commit of {repo_id}.", status=502, kind="GitHub") from exc
        sha = ""
        if isinstance(commit, dict):
            sha = str(commit.get("sha") or "")
        if not sha:
            raise ProError("GitHub did not return a commit sha.", status=502, kind="GitHub")
        return sha

    def _execute(self, job: Job) -> None:
        try:
            self._event(job, {"type": "stage", "message": "Fetching the repository"})
            if self.runner is not None:
                result = self.runner(job)
            else:
                result = self._judge(job)
            result.setdefault("repo_id", job.repo_id)
            result.setdefault("injection", job.injection)
            result = public_result(result)
            if job.commit:
                result["commit"] = job.commit
                result["id"] = permalink_for(job.repo_id, job.commit)
                stored = self.cache.put(job.repo_id, job.commit, result)
            else:
                result["id"] = result.get("id") or job.id
                stored = result
            stored = self._write_result(stored)
            job.result_id = str(stored.get("id") or "")
            job.status = "done"
            self._event(job, {"type": "stage", "message": "Verdict ready"})
        except Exception as exc:  # noqa: BLE001 — the run page has to show a short public error
            job.status = "error"
            job.error = _public_error(exc, self.settings_fn())
            self._event(job, {"type": "stage", "message": "Judging stopped"})
        self._event(
            job,
            {"type": "status", "status": job.status, "result_id": job.result_id, "message": job.error},
        )

    def _judge(self, job: Job) -> dict[str, Any]:
        from pro.hosted import build_hosted_client
        from scripts.judge_repo import JudgeError, _get, judge

        settings = self.settings_fn()
        run_id = job.id
        log_dir = self.root / "logs"
        out_dir = self.root / "reports" / run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        job.trace_path = log_dir / f"trace-{run_id}.jsonl"
        client = build_hosted_client(
            settings,
            max_calls=self.ledger.config.max_model_calls,
            cache_dir=self.root / "model-cache",
            on_step=lambda step, provider: self._event(
                job,
                {
                    "type": "model",
                    "message": f"Model call {step or 'judge'} via {provider}",
                    "step": step,
                    "provider": provider,
                },
            ),
        )

        def getter(url: str, token: str, accept: str = "application/vnd.github+json"):
            body = _get(url, token, accept)
            if isinstance(body, str):
                cleaned, report = neutralise(body)
                job.injection = report
                self._event(
                    job,
                    {
                        "type": "injection",
                        "message": (
                            f"README injection detector: {report['count']} span(s) removed"
                            if report.get("detected")
                            else "README injection detector: nothing matched"
                        ),
                        "detected": bool(report.get("detected")),
                    },
                )
                return cleaned
            return body

        self._event(job, {"type": "stage", "message": "Running the judge"})
        try:
            judged = judge(
                job.url,
                out_dir=out_dir,
                use_llm=True,
                run_id=run_id,
                log_dir=log_dir,
                getter=getter,
                relevance_client=client,
            )
        except JudgeError as exc:
            raise ProError(str(exc), status=422, kind="CannotJudge") from exc
        return self._from_judge(job, judged, out_dir, client)

    def _from_judge(self, job: Job, judged: dict, out_dir: Path, client) -> dict[str, Any]:
        from app.rescue_plan import build_plan

        trace_path = job.trace_path
        events = trace_events(trace_path) if trace_path and trace_path.exists() else []
        discovery = discovery_from_events(events) if events else {}
        verdict = str(discovery.get("verdict") or judged.get("verdict") or "")
        reason = str(discovery.get("reason") or judged.get("reason") or "")
        alternatives = alternatives_from_discovery(discovery)
        problem = problem_from_events(events) if events else ""
        rescue = None
        rescue_note = "Gate 2 writes a rescue plan only after CANDIDATE."
        if verdict == "CANDIDATE":
            scores_path = out_dir / "scores.json"
            row = _candidate_row(scores_path, job.repo_id)
            if row:
                try:
                    rescue = build_plan(row, client=client, use_llm=True)
                    rescue_note = f"Drafted by {rescue.get('drafted_by')}. Every kept item quotes evidence."
                except Exception as exc:  # noqa: BLE001 — a plan failure must not hide the verdict
                    rescue = None
                    rescue_note = f"No rescue plan was kept: {type(exc).__name__}"
            else:
                rescue_note = "The score file had no CANDIDATE row to plan from."
        run_hash = str(judged.get("run_hash") or "")
        if events:
            run_hash = events[-1].get("line_hash") or run_hash
        return public_result(
            {
                "repo_id": job.repo_id,
                "repo_url": f"https://github.com/{job.repo_id}",
                "title": job.repo_id,
                "verdict": verdict,
                "reason": reason,
                "quote": primary_quote(verdict, reason, alternatives),
                "alternatives": alternatives,
                "problem": problem,
                "run_id": str(judged.get("run_id") or job.id),
                "run_hash": run_hash,
                "trace_file": str(trace_path) if trace_path else "",
                "trace": events,
                "rescue": rescue,
                "rescue_note": rescue_note,
                "injection": job.injection,
                "provider": _provider_label(client),
                "source": "live-judge",
                "source_note": "Judged in this process with the hosted provider. Nightshift was not required.",
                "demo": False,
                "model_calls": int(getattr(client, "_http_calls", 0)),
                "human_label": {},
            }
        )

    def _write_result(self, result: dict) -> dict:
        self.results.mkdir(parents=True, exist_ok=True)
        ident = _safe_id(str(result.get("id") or ""))
        if not ident:
            raise ProError("Result is missing an id.", status=500, kind="Result")
        result = dict(result)
        result["id"] = ident
        path = self.results / f"{ident}.json"
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return result

    def _event(self, job: Job, event: dict) -> None:
        with self._lock:
            job.events.append(event)

    def _job_body(self, job: Job) -> dict[str, Any]:
        with self._lock:
            events = list(job.events)
        return {
            "status": job.status,
            "id": job.id,
            "result_id": job.result_id,
            "repo_id": job.repo_id,
            "commit": job.commit,
            "error": job.error,
            "events": events,
            "cached": False,
            "demo": False,
        }


def read_trace_events(job: Job, seen: int) -> tuple[list[dict[str, Any]], int]:
    path = job.trace_path
    if path is None or not path.exists():
        return [], seen
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], seen
    if text and not text.endswith("\n"):
        text = text.rsplit("\n", 1)[0]
    lines = [line for line in text.splitlines() if line.strip()]
    fresh: list[dict[str, Any]] = []
    for line in lines[seen:]:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            break
        fresh.append(
            {
                "type": "trace",
                "message": str(record.get("action") or record.get("event") or "event"),
                "status": str(record.get("status") or ""),
            }
        )
    return fresh, seen + len(fresh)


def _candidate_row(path: Path, repo_id: str) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for row in data.get("candidates") or []:
        if isinstance(row, dict) and row.get("repo_id") == repo_id:
            return row
    return None


def _provider_label(client) -> str:
    used = list(getattr(client, "providers_used", []) or [])
    if not used:
        return "hosted"
    if used[-1] == "gemini" and "nvidia-direct" not in used:
        return "gemini"
    if "gemini" in used:
        return "nvidia+gemini"
    return "nvidia"


def _public_model(settings: Settings, mode: str) -> str:
    if mode == "nvidia":
        from pro.hosted import nemotron_model_id

        return nemotron_model_id(settings)
    if mode == "gemini":
        return settings.gemini_model or "gemini-2.5-flash"
    return ""


def _github_token() -> str:
    return usable_secret(os.environ.get("GITHUB_TOKEN")) or usable_secret(os.environ.get("GH_TOKEN")) or ""


def _github_get(url: str, token: str, accept: str = "application/vnd.github+json"):
    from scripts.judge_repo import _get

    return _get(url, token, accept)


def _public_error(exc: BaseException, settings: Settings) -> str:
    text = str(exc).replace("\n", " ")[:300]
    for secret in settings.secret_values():
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text or type(exc).__name__


def _safe_id(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "-_")
    return cleaned[:80]
