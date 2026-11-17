"""Snapshot bytes, OSV at a commit, failed runs, and NVIDIA retries."""

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.nvidia import NvidiaError
from app.agents.relevance import RelevanceClient, RelevanceLLMError
from app.agents.search_snapshot import file_sha256, open_snapshot, snapshot_path
from api.run import app
from pro.examples import ExampleCatalog
from pro.osv import advisories_for_repo
from pro.service import ProService


def test_the_download_serves_the_sealed_snapshot_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_PRO_DIR", str(tmp_path / "pro"))
    session = open_snapshot("acme/widget", "abc123")
    session.complete = True
    session.capture(problem={"problem": "Lists a bucket."}, queries=["bucket"], results=[{"alternative_id": "a/b"}])
    session.finish(None)
    raw = session.path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == file_sha256(session.path)

    service = ProService(tmp_path / "state", settings_fn=lambda: None, examples=ExampleCatalog(tmp_path / "examples"))
    service._copy_snapshot({"id": "run-1", "repo_id": "acme/widget", "commit": "abc123"}, "run-1")
    copied = service.results / "run-1.snapshot.json"
    assert copied.read_bytes() == raw

    import pro.api as api

    api.SERVICE = service
    response = TestClient(app).get("/pro/api/results/run-1/snapshot")
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == digest


def test_osv_uses_the_commit_and_a_replay_does_not_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_PRO_DIR", str(tmp_path / "pro"))
    seen: list[str] = []

    def getter(repo, path, ref):
        seen.append(f"{repo}:{path}:{ref}")
        if path == "requirements.txt":
            return "flask==2.0.0\n"
        return ""

    import httpx

    def _handle(request):
        assert request.url.host == "api.osv.dev"
        return httpx.Response(200, json={"results": [{"vulns": [{"id": "PYSEC-1"}]}]})

    session = open_snapshot("acme/widget", "abc123")
    rows = advisories_for_repo(
        "acme/widget", commit="abc123", getter=getter, transport=httpx.MockTransport(_handle), session=session,
    )
    assert seen == ["acme/widget:requirements.txt:abc123", "acme/widget:package.json:abc123"]
    assert rows and rows[0]["ids"] == ["PYSEC-1"]
    assert session.osv["commit"] == "abc123"
    assert "flask==2.0.0" in session.osv["manifests"]["requirements.txt"]

    session.replaying = True

    def explode(*_args, **_kwargs):
        raise AssertionError("replay fetched a manifest")

    again = advisories_for_repo(
        "acme/widget", commit="abc123", getter=explode, transport=httpx.MockTransport(_handle), session=session,
    )
    assert again == rows


def test_a_failed_run_does_not_leave_a_snapshot_to_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_PRO_DIR", str(tmp_path / "pro"))
    session = open_snapshot("acme/widget", "abc123")
    session.put("github_http", "https://api.github.com/repos/acme/widget", {"description": "x"})
    session.capture(problem=None, queries=[], results=[])
    session.close()
    assert not snapshot_path("acme/widget", "abc123").is_file()
    again = open_snapshot("acme/widget", "abc123")
    assert again.replaying is False


def test_an_unjudged_model_failure_is_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_PRO_DIR", str(tmp_path / "pro"))
    readme = "Lists objects in a bucket for developers.\n\n- Browse a bucket from the browser today.\n"
    calls = {"model": 0}

    def getter(url, token="", accept=""):
        if str(url).endswith("/readme"):
            return readme
        if "/git/trees/" in str(url):
            return {"tree": [{"path": "src/main.js", "type": "blob"}, {"path": "src/list.js", "type": "blob"}]}
        return {
            "full_name": "acme/widget",
            "description": "Lists objects in a bucket for developers.",
            "html_url": "https://github.com/acme/widget",
            "default_branch": "main",
            "pushed_at": "2026-11-01T00:00:00Z",
            "created_at": "2020-01-01T00:00:00Z",
            "stargazers_count": 3,
            "forks_count": 0,
            "open_issues_count": 0,
            "license": {"spdx_id": "MIT"},
        }

    def fail(_prompt, _system):
        calls["model"] += 1
        raise RelevanceLLMError("hosted model call failed: NVIDIA chat failed with HTTP 503.")

    from app.tools.base import ToolResult
    from scripts.judge_repo import judge

    class _Box:
        def __init__(self):
            self.tools = {"web_search": object(), "github": object()}
            self.trace = None
            self.budget = type("B", (), {"is_exhausted": lambda self: False, "remaining_calls": 50, "consume": lambda self: True})()

        def has(self, name):
            return name in self.tools

        def call(self, name, _args):
            return ToolResult(ok=True, data={"items": []}, error="", source_url="", fetched_at="2026-11-01T00:00:00Z")

    first = judge(
        "acme/widget", out_dir=tmp_path / "a", log_dir=tmp_path / "la", run_id="fail",
        getter=getter, relevance_client=RelevanceClient(cache_dir=tmp_path / "m", mock_handler=fail),
        commit="abc123", toolbox=_Box(),
    )
    assert first["verdict"] == "UNJUDGED"
    assert not snapshot_path("acme/widget", "abc123").is_file()

    def ok(prompt, _system):
        calls["model"] += 1
        if "CANDIDATE, ALREADY_EXISTS, or NO_GO" in prompt:
            return json.dumps({
                "verdict": "NO_GO",
                "reason": "Not worth it.",
                "problem_quote": "Lists objects in a bucket for developers.",
            })
        return json.dumps({
            "problem": "Listing a bucket is awkward.",
            "target_user": "developers",
            "category": "http",
            "search_queries": ["bucket browser"],
            "quotes": ["Lists objects in a bucket for developers."],
        })

    before = calls["model"]
    second = judge(
        "acme/widget", out_dir=tmp_path / "b", log_dir=tmp_path / "lb", run_id="again",
        getter=getter, relevance_client=RelevanceClient(cache_dir=tmp_path / "m2", mock_handler=ok),
        commit="abc123", toolbox=_Box(),
    )
    assert calls["model"] > before
    assert second["verdict"] != "UNJUDGED"
    assert "PROBLEM_EXTRACTION_FAILED" not in second["reason"]
    assert "is incomplete" not in second["reason"]


def test_nvidia_503_is_retried_inside_the_time_budget(monkeypatch):
    from pro.hosted import HostedRelevanceClient

    calls = {"n": 0, "slept": []}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def complete(self, messages, model=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise NvidiaError("NVIDIA chat failed with HTTP 503.", status_code=503)
            self.last_served_model = "nvidia/nemotron-test"
            return "ok"

    monkeypatch.setattr("pro.hosted.NvidiaDirect", _Client)
    monkeypatch.setattr("pro.hosted.time.sleep", lambda seconds: calls["slept"].append(seconds))
    monkeypatch.setattr("pro.hosted.random.random", lambda: 0.0)
    client = HostedRelevanceClient(nvidia_api_key="nv-test", nvidia_model="nvidia/nemotron-test", timeout_seconds=30)
    text, served = client._call_nvidia([{"role": "user", "content": "hi"}], "nvidia/nemotron-test", "discovery")
    assert text == "ok"
    assert served == "nvidia/nemotron-test"
    assert calls["n"] == 3
    assert client.model_retries == 2
    assert len(calls["slept"]) == 2
    assert calls["slept"][1] > calls["slept"][0]


def test_a_packed_example_keeps_the_snapshot_bytes(tmp_path, monkeypatch):
    from app.trace_log import HackathonTrace
    import scripts.pro_add_live_example as packer

    monkeypatch.setattr(packer, "EXAMPLES", tmp_path / "examples")
    trace = HackathonTrace(run_id="pack", log_dir=tmp_path / "logs")
    trace.event(agent_name="orchestrator", action="SEARCH_SNAPSHOT", input_summary={"repo_id": "acme/widget"}, output_summary={"snapshot_sha256": "placeholder"}, status="ok")
    run_hash = trace.seal()
    raw = b'{"commit":"abc","complete":true,"repo_id":"acme/widget"}'
    snap = tmp_path / "snap.json"
    snap.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    result = tmp_path / "result.json"
    result.write_text(json.dumps({
        "run_hash": run_hash,
        "repo_id": "acme/widget",
        "verdict": "NO_GO",
        "snapshot_sha256": digest,
        "trace": [],
        "responsibility": {"trace_file": "old", "verify": []},
    }), encoding="utf-8")
    out = packer.pack(result, trace.path, "live-sample", "Sample", "2026-11-17", "note", snap)
    packed = json.loads(out.read_text(encoding="utf-8"))
    stored = Path(packer.EXAMPLES) / "snapshots" / "live-sample.snapshot.json"
    assert stored.read_bytes() == raw
    assert packed["snapshot_sha256"] == digest
    assert packed["snapshot_file"].endswith("live-sample.snapshot.json")

    service = ProService(tmp_path / "state", settings_fn=lambda: None, examples=ExampleCatalog(tmp_path / "examples"))
    assert service.snapshot_file("live-sample").read_bytes() == raw
