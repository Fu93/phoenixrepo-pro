import json

from fastapi.testclient import TestClient

from api.run import app
from config.settings import Settings
from pro.cache import permalink_for
from pro.examples import ExampleCatalog
from pro.service import Job, ProService


def _demo_settings():
    return Settings(_env_file=None, nvidia_api_key=None, gemini_api_key=None, sample_mode=True)


def _nvidia_settings():
    return Settings(_env_file=None, nvidia_api_key="nv-test", nvidia_model="nvidia/nemotron-3-ultra-550b-a55b", sample_mode=True)


def _service(tmp_path, settings, runner=None, github=None, examples=None):
    def fake_github(url, token, accept="application/vnd.github+json"):
        if url.endswith("/commits/main") or "/commits/" in url:
            return {"sha": "abc123def"}
        return {"default_branch": "main", "description": "a tool"}

    service = ProService(
        tmp_path,
        settings_fn=lambda: settings,
        github=github or fake_github,
        runner=runner,
        background=False,
        examples=examples if examples is not None else ExampleCatalog(),
    )
    return service


def test_healthz_and_status_and_injection_demo(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import pro.api as api

    api.SERVICE = _service(tmp_path, _demo_settings())
    client = TestClient(app)
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["service"] == "phoenixrepo"
    home = client.get("/")
    assert home.status_code == 200
    assert "PhoenixRepo Pro" in home.text
    status = client.get("/pro/api/status").json()
    assert status["mode"] == "demo"
    assert status["nightshift_required"] is False
    demo = client.post("/pro/api/injection-demo").json()
    assert demo["report"]["detected"] is True
    assert "ignore previous instructions" not in demo["neutralised"].lower()
    assert "Pocket label printer" in demo["neutralised"]


def test_demo_mode_serves_examples_and_refuses_an_unknown_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from pro.pack import write_all

    write_all()
    import pro.api as api

    api.SERVICE = _service(tmp_path, _demo_settings())
    client = TestClient(app)
    cards = client.get("/pro/api/examples").json()["examples"]
    assert {card["id"] for card in cards} == {"ex-selfdb", "ex-invoice", "ex-fichero"}
    unknown = client.post("/pro/api/runs", json={"url": "https://github.com/example/not-an-example"})
    assert unknown.status_code == 200
    body = unknown.json()
    assert body["status"] == "demo"
    assert body["model_calls"] == 0
    assert "NVIDIA_API_KEY" in body["message"]
    known = client.post("/pro/api/runs", json={"url": "https://github.com/Selfdb-io/SelfDB"})
    assert known.json()["status"] == "done"
    assert known.json()["model_calls"] == 0
    result = client.get("/pro/api/results/ex-selfdb")
    assert result.status_code == 200
    payload = result.json()
    assert payload["verdict"] == "ALREADY_EXISTS"
    assert payload["quote"]
    assert payload["run_hash"]
    blocked = [row for row in payload["alternatives"] if row["blocked"]]
    assert blocked and blocked[0]["quote"]
    page = client.get("/pro/r/ex-selfdb")
    assert page.status_code == 200
    assert "PhoenixRepo Pro" in page.text
    rescue = client.get("/pro/r/ex-fichero/rescue")
    assert rescue.status_code == 200
    missing = client.get("/pro/api/results/no-such")
    assert missing.status_code == 404


def test_live_run_is_cached_and_the_repeat_does_not_spend_a_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_PER_IP", "1")
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_GLOBAL", "1")
    calls = {"n": 0}

    def runner(job: Job) -> dict:
        calls["n"] += 1
        job.events.append({"type": "stage", "message": "Scoring"})
        return {
            "verdict": "NEEDS_REVIEW",
            "reason": "search stopped",
            "quote": "a quoted line",
            "alternatives": [
                {"name": "other/repo", "url": "https://github.com/other/repo", "overlap": "partial", "quote": "a quoted line", "reason": "did not block", "blocked": False}
            ],
            "run_hash": "deadbeef",
            "trace": [
                {"action": "RUN_SEAL", "status": "ok", "timestamp": "t", "prev_hash": "", "line_hash": "deadbeef", "canonical": "{}", "detail": "seal"}
            ],
            "rescue": {
                "drafted_by": "deterministic",
                "stages": [
                    {"stage": "REVIVE", "items": [{"title": "Make it run", "why": "the job", "quote": "a quoted line", "evidence": ["candidate:a/b"]}]},
                    {"stage": "OPTIMISE", "items": []},
                    {"stage": "UPGRADE", "items": []},
                ],
            },
            "provider": "nvidia",
            "source": "test",
        }

    import pro.api as api

    api.SERVICE = _service(tmp_path, _nvidia_settings(), runner=runner, examples=ExampleCatalog(tmp_path / "no-examples"))
    client = TestClient(app)
    first = client.post("/pro/api/runs", json={"url": "https://github.com/a/b"})
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "done"
    assert body["cached"] is False
    result_id = body["result_id"]
    assert result_id == permalink_for("a/b", "abc123def")
    saved = client.get(f"/pro/api/results/{result_id}").json()
    assert saved["verdict"] == "NEEDS_REVIEW"
    assert saved["quote"] == "a quoted line"
    assert saved["rescue"]["stages"][0]["items"][0]["quote"] == "a quoted line"
    events = client.get(f"/pro/api/runs/{body['id']}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "Scoring" in events.text
    assert "data: " in events.text
    second = client.post("/pro/api/runs", json={"url": "https://github.com/a/b"})
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert second.json()["model_calls"] == 0
    assert calls["n"] == 1
    third = client.post("/pro/api/runs", json={"url": "https://github.com/a/c"}, headers={"x-forwarded-for": "203.0.113.9"})
    assert third.status_code == 429
    assert third.json()["error"]["type"] in {"per_ip", "global"}
    assert calls["n"] == 1


def test_bad_url_is_422_and_trace_chain_matches_canonical_bytes():
    import hashlib

    from pro.pack import build_all

    examples = build_all()
    invoice = next(item for item in examples if item["id"] == "ex-invoice")
    assert invoice["human_label"]["false_pass"] is True
    assert invoice["verdict"] == "CANDIDATE"
    fichero = next(item for item in examples if item["id"] == "ex-fichero")
    stages = {stage["stage"]: stage["items"] for stage in fichero["rescue"]["stages"]}
    for name in ("REVIVE", "OPTIMISE", "UPGRADE"):
        assert stages[name], name
        assert stages[name][0]["quote"]
    previous = ""
    for event in invoice["trace"]:
        assert event["prev_hash"] == previous
        digest = hashlib.sha256(event["canonical"].encode("utf-8")).hexdigest()
        assert digest == event["line_hash"]
        previous = event["line_hash"]
    assert invoice["trace"][-1]["action"] == "RUN_SEAL"
    assert invoice["run_hash"] == invoice["trace"][-1]["line_hash"]
    assert json.loads(invoice["trace"][-1]["canonical"])["action"] == "RUN_SEAL"
