"""Daily quota, in-flight de-duplication, and public trace paths."""

import json
import threading
import time

from fastapi.testclient import TestClient

from api.run import app
from pro.service import Job
from tests.pro.test_api import _nvidia_settings, _service


def _payload(job: Job) -> dict:
    job.model_calls = 1
    return {
        "verdict": "NEEDS_REVIEW",
        "reason": "kept",
        "quote": "a quoted line",
        "model_calls": 1,
        "trace_file": "/opt/render/project/src/data/pro-runtime/logs/trace-run-1.jsonl",
    }


def test_a_run_that_never_calls_a_model_does_not_spend_the_daily_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_PER_IP", "1")
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_GLOBAL", "1")
    mode = {"fail": True}

    def runner(job: Job) -> dict:
        if mode["fail"]:
            raise RuntimeError("repository is too small to judge")
        return _payload(job)

    import pro.api as api

    service = _service(tmp_path, _nvidia_settings(), runner=runner)
    api.SERVICE = service
    client = TestClient(app)
    failed = client.post("/pro/api/runs", json={"url": "https://github.com/a/b"})
    assert failed.status_code == 200
    assert failed.json()["status"] == "error"
    assert "too small" in failed.json()["error"]
    assert service.ledger.snapshot()["global_used"] == 0
    mode["fail"] = False
    judged = client.post("/pro/api/runs", json={"url": "https://github.com/a/c"})
    assert judged.status_code == 200
    assert judged.json()["status"] == "done"
    assert service.ledger.snapshot()["global_used"] == 1
    blocked = client.post("/pro/api/runs", json={"url": "https://github.com/a/d"})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["type"] == "per_ip"


def test_a_second_post_for_the_same_commit_attaches_to_the_running_job(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_GLOBAL", "2")
    release = threading.Event()

    def runner(job: Job) -> dict:
        release.wait(3)
        return _payload(job)

    service = _service(tmp_path, _nvidia_settings(), runner=runner)
    service.background = True
    first = service.submit("https://github.com/a/b", "10.0.0.1")
    second = service.submit("https://github.com/a/b", "10.1.0.9")
    assert first["status"] == "running"
    assert second["id"] == first["id"]
    assert second["attached"] is True
    release.set()
    deadline = time.time() + 3
    while time.time() < deadline and service.get_job(first["id"])["status"] == "running":
        time.sleep(0.05)
    assert service.get_job(first["id"])["status"] == "done"
    assert service.ledger.snapshot()["global_used"] == 1


def test_public_result_does_not_contain_an_absolute_server_path(tmp_path):
    def runner(job: Job) -> dict:
        return _payload(job)

    import pro.api as api

    api.SERVICE = _service(tmp_path, _nvidia_settings(), runner=runner)
    client = TestClient(app)
    started = client.post("/pro/api/runs", json={"url": "https://github.com/a/b"})
    assert started.status_code == 200
    saved = client.get("/pro/api/results/" + started.json()["result_id"])
    body = saved.text
    assert "/opt/render" not in body
    assert "pro-runtime" not in body
    assert saved.json()["trace_file"] == "trace-run-1.jsonl"
    assert saved.json()["responsibility"]["trace_file"] == "trace-run-1.jsonl"


def test_state_dir_follows_phoenix_pro_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_PRO_DIR", str(tmp_path / "keep"))
    import pro.api as api

    api.SERVICE = None
    service = api.get_service()
    try:
        assert service.root == tmp_path / "keep"
        assert service.cache.directory == tmp_path / "keep" / "cache"
        assert service.ledger.path == tmp_path / "keep" / "limits.json"
    finally:
        api.SERVICE = None


def test_relative_replay_paths_stay_relative():
    from pro.present import public_trace_ref

    assert public_trace_ref("data/replays/selfdb-io-selfdb/trace.jsonl") == "data/replays/selfdb-io-selfdb/trace.jsonl"
    assert public_trace_ref("/var/data/trace-abc.jsonl") == "trace-abc.jsonl"
    assert json.dumps({"trace_file": public_trace_ref("C:/srv/trace-abc.jsonl")}) == '{"trace_file": "trace-abc.jsonl"}'
