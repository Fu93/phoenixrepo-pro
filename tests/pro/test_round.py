"""Speed, quota edges, injection table, hosted surface, and gate 2 stay out of Pro."""

import json
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from api.run import app
from app.agents.relevance import capability_hold
from pro.injection import attack_table, pipeline_check
from pro.osv import parse_requirements, query_batch
from pro.pace import map_ordered
from pro.hosted import trim_prompt
from pro.service import ProError
from pro.triage import abandoned_repos
from tests.pro.test_api import _demo_settings, _nvidia_settings, _service


def test_parallel_map_is_faster_than_one_thread():
    def work(_item):
        time.sleep(0.05)
        return 1

    started = time.perf_counter()
    assert map_ordered(work, range(8), workers=1) == [1] * 8
    sequential = time.perf_counter() - started
    started = time.perf_counter()
    assert map_ordered(work, range(8), workers=4) == [1] * 8
    parallel = time.perf_counter() - started
    # Recorded for the report. 8 sleeps of 0.05s.
    assert parallel < sequential * 0.7, (sequential, parallel)


def test_trim_prompt_keeps_the_instructions_at_the_end():
    body = "A" * 20000 + "\nQUOTE THIS EXACT TAIL"
    trimmed = trim_prompt(body, limit=3000)
    assert len(trimmed) < len(body)
    assert trimmed.endswith("QUOTE THIS EXACT TAIL")
    assert "[trimmed]" in trimmed


def test_nvidia_429_is_retried_once(tmp_path):
    from pro.hosted import HostedRelevanceClient

    calls = {"n": 0}

    def nvidia(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "slow down"})
        body = json.loads(request.content)
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": "ok"}}]})

    client = HostedRelevanceClient(
        nvidia_api_key="nv-test",
        cache_dir=tmp_path,
        max_calls=4,
        nvidia_transport=httpx.MockTransport(nvidia),
    )
    client.retry_backoff = 0
    text, _requested, served = client.complete("judge", "", repo_id="a/b", step="problem")
    assert text == "ok"
    assert served.startswith("nvidia/nemotron-")
    assert calls["n"] == 2


def test_per_ip_cap_uses_the_last_forwarded_hop(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_PER_IP", "1")
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_GLOBAL", "5")

    def runner(job):
        job.model_calls = 1
        return {"verdict": "NEEDS_REVIEW", "reason": "kept", "quote": "q", "model_calls": 1}

    import pro.api as api

    api.SERVICE = _service(tmp_path, _nvidia_settings(), runner=runner)
    client = TestClient(app)
    first = client.post(
        "/pro/api/runs",
        json={"url": "https://github.com/a/b"},
        headers={"x-forwarded-for": "198.51.100.9, 203.0.113.5"},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "done"
    second = client.post(
        "/pro/api/runs",
        json={"url": "https://github.com/a/c"},
        headers={"x-forwarded-for": "198.51.100.9, 203.0.113.5"},
    )
    assert second.status_code == 429
    third = client.post(
        "/pro/api/runs",
        json={"url": "https://github.com/a/d"},
        headers={"x-forwarded-for": "198.51.100.9, 203.0.113.8"},
    )
    assert third.status_code == 200


def test_not_enough_to_judge_is_a_page_and_does_not_spend_a_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_PER_IP", "1")

    def runner(job):
        raise ProError("the repository has no description, so there is no stated problem to judge", status=422, kind="CannotJudge")

    import pro.api as api

    service = _service(tmp_path, _nvidia_settings(), runner=runner)
    api.SERVICE = service
    client = TestClient(app)
    response = client.post("/pro/api/runs", json={"url": "https://github.com/a/b"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    saved = client.get("/pro/api/results/" + body["result_id"]).json()
    assert saved["verdict"] == "NOT_ENOUGH"
    assert saved["would_help"]
    assert saved["model_calls"] == 0
    assert service.ledger.snapshot()["global_used"] == 0


def test_capability_hold_blocks_a_false_pass_and_leaves_a_stale_github_port():
    must = [{
        "alternative": "web:https://www.canva.com/invoice/",
        "overlap": "full",
        "quote": "Free online invoice generator with templates",
        "reason": "did not block: no push or release date; not mature or official: stars=0",
    }]
    text = "A web invoice generator with templates."
    hold = capability_hold("CANDIDATE", must, text)
    assert hold and "invoice" in hold and "generator" in hold
    assert capability_hold("CANDIDATE", [{
        "alternative": "hionay/tailer",
        "overlap": "full",
        "quote": "a Go port of spacer",
        "reason": "did not block: not mature",
    }], "Add spacers between the characters of a file.") is None


def test_attack_table_is_honest_about_a_miss():
    rows = attack_table()
    assert 5 <= len(rows) <= 8
    assert any(row["caught"] and row["honest"] for row in rows)
    assert any(not row["caught"] and row["honest"] for row in rows)
    checked = pipeline_check(Path("pro/fixtures/injected_readme.md").read_text(encoding="utf-8"))
    assert checked["hostile_still_a_quote"] is False
    assert checked["real_sentence_kept"] is True


def test_hosted_mode_sends_legacy_pages_to_pro(monkeypatch):
    monkeypatch.setenv("PHOENIX_HOSTED", "true")
    client = TestClient(app)
    health = client.get("/health", follow_redirects=False)
    assert health.status_code == 307
    assert health.headers["location"] == "/healthz"
    ui = client.get("/ui", follow_redirects=False)
    assert ui.status_code == 307
    assert ui.headers["location"] == "/pro"
    assert client.get("/healthz").status_code == 200


def test_pro_package_does_not_call_the_executing_gate():
    text = "\n".join(path.read_text(encoding="utf-8") for path in Path("pro").rglob("*.py"))
    assert "revive_loop" not in text
    assert "subprocess" not in text
    paths = [getattr(route, "path", "") for route in app.routes]
    assert not any("revive" in path for path in paths)


def test_triage_ranks_stale_public_repos():
    def getter(url, token, accept="application/vnd.github+json"):
        return [
            {"full_name": "ada/old", "private": False, "fork": False, "description": "A tool",
             "pushed_at": "2024-01-01T00:00:00Z", "stargazers_count": 3, "html_url": "https://github.com/ada/old"},
            {"full_name": "ada/new", "private": False, "fork": False, "description": "Fresh",
             "pushed_at": "2026-11-01T00:00:00Z", "stargazers_count": 100, "html_url": "https://github.com/ada/new"},
            {"full_name": "ada/secret", "private": True, "fork": False, "description": "",
             "pushed_at": "2020-01-01T00:00:00Z", "stargazers_count": 1, "html_url": "https://github.com/ada/secret"},
        ]

    rows = abandoned_repos("ada", getter=getter, now=__import__("datetime").datetime(2026, 11, 16, tzinfo=__import__("datetime").timezone.utc))
    assert [row["repo_id"] for row in rows] == ["ada/old"]


def test_osv_reports_only_packages_with_an_advisory():
    assert parse_requirements("requests==2.0\n# comment\n")[0]["name"] == "requests"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.osv.dev"
        return httpx.Response(200, json={"results": [{"vulns": [{"id": "PYSEC-1"}]}, {"vulns": []}]})

    rows = query_batch(
        [
            {"name": "requests", "ecosystem": "PyPI", "version": "2.0.0"},
            {"name": "ok", "ecosystem": "PyPI", "version": "1.0.0"},
            {"name": "flask", "ecosystem": "PyPI", "version": ""},
        ],
        transport=httpx.MockTransport(handler),
    )
    assert rows[0]["note"] == "version unknown"
    assert rows[0]["ids"] == []
    assert {"name": "requests", "ecosystem": "PyPI", "version": "2.0.0", "ids": ["PYSEC-1"]} in rows


def test_page_uses_plain_verdict_language():
    page = Path("pro/ui/index.html").read_text(encoding="utf-8")
    assert "Already exists" in page
    assert "Worth reviving" in page
    assert "tamper-evident receipt" in page
    assert "Found in source" in page
    assert "does not prove the verdict is correct" in page
    assert 'aria-live="polite"' in page
