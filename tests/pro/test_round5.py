"""Seal order, rescue replies, sentence indexes, and the IP / 404 rules."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app
from app.adapters.meterless import message_text
from app.agents.relevance import RelevanceClient, extract_problem_statement
from app.rescue_plan import build_plan
from app.trace_log import HackathonTrace
from pro.api import client_ip
from pro.hosted import call_timeout
from pro.present import public_result
from pro.service import ProError
from scripts.judge_repo import JudgeError
from scripts.verify_trace import verify
from tests.pro.test_api import _nvidia_settings, _service


def test_a_candidate_rescue_plan_is_inside_the_sealed_trace(tmp_path):
    trace = HackathonTrace(run_id="seal-rescue", log_dir=tmp_path)

    def handler(_prompt, _system):
        return json.dumps({"stages": []})

    client = RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "cache")
    row = {
        "repo_id": "a/b",
        "verdict": "CANDIDATE",
        "total": 1,
        "flags": [],
        "evidence": [{"id": "candidate:readme", "text": "prints shipping labels", "url": "", "content_hash": "abc"}],
        "must_beat": [],
    }
    plan = build_plan(row, client=client, trace=trace, use_llm=True)
    trace.seal()
    ok, detail = verify(trace.path)
    assert ok, detail
    actions = [json.loads(line)["action"] for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert "LLM_CALL" in actions
    assert "RESCUE_PLAN" in actions
    assert actions[-1] == "RUN_SEAL"
    assert actions.index("LLM_CALL") < actions.index("RUN_SEAL")
    assert actions.index("RESCUE_PLAN") < actions.index("RUN_SEAL")
    assert plan["drafted_by"] in {"model", "deterministic"}


def test_reasoning_content_and_list_parts_are_text():
    assert message_text({"choices": [{"message": {"content": "", "reasoning_content": '{"stages": []}'}}]}) == '{"stages": []}'
    payload = {"choices": [{"message": {"content": [{"type": "text", "text": "hello"}]}}]}
    assert message_text(payload) == "hello"
    try:
        message_text({"choices": [{"message": {"content": None}}]})
    except Exception as exc:
        assert "not text" in str(exc)


def test_a_failed_problem_quote_retries_with_sentence_indexes(tmp_path):
    calls = {"n": 0}

    def handler(prompt, _system):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({
                "problem": "labels",
                "target_user": "shops",
                "category": "tools",
                "search_queries": ["labels"],
                "quotes": ["this sentence was invented"],
            })
        assert "sentence_indexes" in prompt
        return json.dumps({
            "problem": "prints shipping labels",
            "target_user": "shops",
            "category": "tools",
            "search_queries": ["labels"],
            "sentence_indexes": [0],
        })

    client = RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "cache")
    data, _meta = extract_problem_statement(
        {
            "repo_id": "a/b",
            "description": "",
            "readme_excerpt": "This tool prints shipping labels for small shops.",
        },
        client=client,
    )
    assert data is not None
    assert data["quotes"] == ["This tool prints shipping labels for small shops."]
    assert calls["n"] == 2


def test_rescue_call_retries_a_shorter_prompt(tmp_path):
    seen = []

    def handler(prompt, _system):
        seen.append(prompt)
        if len(seen) == 1:
            raise RuntimeError("chat content was not text")
        return json.dumps({"stages": [{"stage": "REVIVE", "items": []}]})

    client = RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "cache")
    row = {
        "repo_id": "a/b",
        "verdict": "CANDIDATE",
        "evidence": [{"id": "candidate:readme", "text": "prints shipping labels", "content_hash": "abc"}],
    }
    plan = build_plan(row, client=client, use_llm=True)
    assert len(seen) == 2
    assert len(seen[1]) < len(seen[0])
    assert plan["repo_id"] == "a/b"
    assert call_timeout("rescue_plan", 90) == 45
    assert call_timeout("rescue_plan", 20) == 20
    assert call_timeout("problem", 90) == 90


def test_a_missing_repo_is_404_before_a_slot_is_taken(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_PER_IP", "1")
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_GLOBAL", "1")

    def github(url, token, accept="application/vnd.github+json"):
        raise JudgeError("GitHub returned HTTP 404 for https://api.github.com/repos/missing/repo")

    service = _service(tmp_path, _nvidia_settings(), github=github)
    try:
        service.submit("https://github.com/missing/repo", "203.0.113.9")
    except ProError as exc:
        assert exc.status == 404
        assert "repository not found" in str(exc)
    else:
        raise AssertionError("missing repo was accepted")
    assert service.ledger.snapshot()["global_used"] == 0


def test_only_real_ips_count_and_cloudflare_needs_the_flag(monkeypatch):
    class Request:
        def __init__(self, headers):
            self.headers = headers
            self.client = None

    monkeypatch.delenv("PHOENIX_TRUST_CF", raising=False)
    ignored = Request({"cf-connecting-ip": "203.0.113.8", "x-forwarded-for": "not-an-ip, 198.51.100.10"})
    assert client_ip(ignored) == "198.51.100.10"
    garbage = Request({"x-forwarded-for": "attacker, also-not-an-ip"})
    assert client_ip(garbage) == "unknown"
    monkeypatch.setenv("PHOENIX_TRUST_CF", "true")
    trusted = Request({"cf-connecting-ip": "203.0.113.8", "x-forwarded-for": "198.51.100.10"})
    assert client_ip(trusted) == "203.0.113.8"
    spoofed_cf = Request({"cf-connecting-ip": "not-an-ip", "x-forwarded-for": "198.51.100.10"})
    assert client_ip(spoofed_cf) == "198.51.100.10"


def test_hosted_output_scrubs_provider_names(monkeypatch):
    monkeypatch.setenv("PHOENIX_HOSTED", "true")
    result = public_result({
        "verdict": "NEEDS_REVIEW",
        "reason": "Nightshift timed out and Meterless was not used.",
        "quote": "",
        "alternatives": [],
        "error": "Meterless chat content was not text.",
    })
    assert "nightshift" not in result["reason"].casefold()
    assert "meterless" not in result["error"].casefold()
    assert "the model route" in result["reason"]


def test_trace_stages_use_the_event_type():
    page = Path("pro/ui/index.html").read_text(encoding="utf-8")
    assert 'action === "LLM_CALL"' in page
    assert "byAction" in page
    assert 'return byAction[action] || "Other"' in page


def test_og_image_is_absolute_and_favicon_ico_exists(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://phoenixrepo-pro.onrender.com")
    client = TestClient(app)
    page = client.get("/pro").text
    assert 'property="og:image" content="https://phoenixrepo-pro.onrender.com/pro/og.png"' in page
    assert 'property="og:url" content="https://phoenixrepo-pro.onrender.com/pro"' in page
    assert 'name="twitter:card" content="summary_large_image"' in page
    icon = client.get("/favicon.ico")
    assert icon.status_code == 200
    assert icon.content[:4] == b"\x00\x00\x01\x00"
