"""Live-QA fixes: honest badges, markdown quotes, caps, and hosted leftovers."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app
from app.agents.relevance import quote_in_text
from pro.api import client_ip
from pro.present import annotate_alternative, plain_verdict_reason, public_result
from pro.service import ProError
from tests.pro.test_api import _nvidia_settings, _service


def test_markdown_is_stripped_before_a_quote_match_and_inventions_fail():
    source = "# Bower\n\n**Bower** is a `package` manager.\n\nSee [the docs][docs].\n\n[docs]: https://bower.io\n"
    assert quote_in_text("Bower is a package manager.", source)
    assert quote_in_text("See the docs", source)
    assert not quote_in_text("Bower is a database.", source)


def test_badges_follow_the_check_not_the_quote_string():
    blocked = annotate_alternative(
        {"name": "a/b", "quote": "real words", "reason": "already does the job"},
        blocked_flag=True,
    )
    assert blocked["state"] == "blocked"
    assert blocked["quote_verified"] is True
    missed = annotate_alternative(
        {"name": "a/b", "quote": "missing words", "reason": "couldn't verify: the quote was not in the alternative's text"},
        blocked_flag=True,
    )
    assert missed["state"] == "could_not_verify"
    assert missed["blocked"] is False
    assert missed["quote_verified"] is False
    open_row = annotate_alternative(
        {"name": "web:https://example.com", "quote": "same job", "reason": "did not block: no push or release date"},
        blocked_flag=False,
    )
    assert open_row["state"] == "did_not_block"
    assert open_row["blocked"] is False
    page = public_result({
        "verdict": "NEEDS_REVIEW",
        "reason": "unverified LLM quote: problem quote 'missing words' not found in the README",
        "quote": "missing words",
        "alternatives": [missed],
    })
    assert page["quote"] == ""
    assert page["quote_verified"] is False
    assert "not in the source" in page["plain_reason"].casefold() or "was not in the source" in page["plain_reason"].casefold()
    assert plain_verdict_reason("CANDIDATE", "nothing blocks")
    assert plain_verdict_reason("ALREADY_EXISTS", "blocked")
    assert "Needs" not in plain_verdict_reason("NOT_ENOUGH", "")


def test_daily_slot_is_held_at_admission_and_refunded_without_a_model(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_PER_IP", "1")
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_GLOBAL", "1")
    monkeypatch.setenv("PHOENIX_MAX_QUEUE", "2")

    def runner(job):
        raise ProError("the repository has no description", status=422, kind="CannotJudge")

    import pro.api as api

    service = _service(tmp_path, _nvidia_settings(), runner=runner)
    api.SERVICE = service
    client = TestClient(app)
    first = client.post("/pro/api/runs", json={"url": "https://github.com/a/b"})
    assert first.status_code == 200
    assert first.json()["status"] == "done"
    assert service.ledger.snapshot()["global_used"] == 0


def test_a_full_queue_is_429(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_PER_IP", "8")
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_GLOBAL", "8")
    monkeypatch.setenv("PHOENIX_MAX_INFLIGHT", "1")
    monkeypatch.setenv("PHOENIX_MAX_QUEUE", "0")
    import threading

    release = threading.Event()

    def runner(job):
        job.model_calls = 1
        release.wait(2)
        return {"verdict": "NEEDS_REVIEW", "reason": "kept", "quote": "q", "model_calls": 1}

    service = _service(tmp_path, _nvidia_settings(), runner=runner)
    service.background = True
    service.max_inflight = 1
    service.max_queue = 0
    first = service.submit("https://github.com/a/b", "203.0.113.9")
    assert first["status"] == "running"
    try:
        service.submit("https://github.com/a/c", "203.0.113.9")
        raised = False
    except ProError as exc:
        raised = exc.status == 429 and exc.kind == "queue"
    release.set()
    assert raised


def test_spoofed_forwarded_for_does_not_reset_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_PER_IP", "1")
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_GLOBAL", "5")

    def runner(job):
        job.model_calls = 1
        return {"verdict": "NEEDS_REVIEW", "reason": "kept", "quote": "q", "model_calls": 1}

    import pro.api as api

    api.SERVICE = _service(tmp_path, _nvidia_settings(), runner=runner)
    client = TestClient(app)
    headers = {"cf-connecting-ip": "203.0.113.50", "x-forwarded-for": "198.51.100.1, 203.0.113.50"}
    assert client.post("/pro/api/runs", json={"url": "https://github.com/a/b"}, headers=headers).status_code == 200
    spoofed = {"cf-connecting-ip": "203.0.113.50", "x-forwarded-for": "1.2.3.4, 198.51.100.9"}
    blocked = client.post("/pro/api/runs", json={"url": "https://github.com/a/c"}, headers=spoofed)
    assert blocked.status_code == 429

    class Request:
        headers = {"x-forwarded-for": "1.1.1.1, 203.0.113.8"}
        client = None

    assert client_ip(Request()) == "203.0.113.8"

    class Cloudflare:
        headers = {"cf-connecting-ip": "203.0.113.8", "x-forwarded-for": "1.1.1.1"}
        client = None

    assert client_ip(Cloudflare()) == "203.0.113.8"


def test_css_wraps_long_lines_for_a_390px_screen():
    css = Path("pro/ui/index.html").read_text(encoding="utf-8")
    assert "overflow-wrap: anywhere" in css
    assert "word-break: break-word" in css
    assert "max-width: 100%" in css
    assert "overflow-x: hidden" in css


def test_hosted_health_and_legacy_posts(monkeypatch):
    monkeypatch.setenv("PHOENIX_HOSTED", "true")
    client = TestClient(app)
    health = client.get("/healthz").json()
    assert health["service"] == "phoenixrepo-pro"
    assert "sample-standin" not in json.dumps(health)
    assert client.post("/run", json={"repository": "a/b", "mode": "sample"}).status_code == 404
    assert client.post("/judge", json={"url": "https://github.com/a/b"}).status_code == 404
    assert client.get("/api/replays").status_code == 404
    page = Path("pro/ui/index.html").read_text(encoding="utf-8")
    assert "Nightshift is not required" not in page
