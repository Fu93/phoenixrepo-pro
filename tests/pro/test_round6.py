"""GitHub metadata for web alternatives, and the labels around it."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app
from app.agents.github_metadata import attach_github_metadata
from app.agents.relevance import RelevanceClient, judge_discovery
from app.trace_log import HackathonTrace
from scripts.judge_repo import JudgeError


def _s3_payload(*, archived=False, stars=709):
    return {
        "stargazers_count": stars,
        "archived": archived,
        "pushed_at": "2026-11-01T00:00:00Z",
        "description": "List the contents of an S3 bucket.",
        "license": {"spdx_id": "MIT"},
    }


def _web_row(repo="rufuspollock/s3-bucket-listing", overlap="full"):
    return {
        "alternative_id": f"web:https://github.com/{repo}",
        "url": f"https://github.com/{repo}",
        "source": "web",
        "description": "A static listing of an S3 bucket.",
        "excerpt": "List the contents of an S3 bucket in the browser.",
        "readme": "List the contents of an S3 bucket in the browser.",
        "overlap": overlap,
        "same_problem": True,
        "same_job": True,
        "quotes": ["List the contents of an S3 bucket in the browser."],
        "quote_verified": True,
        "stars": 0,
        "pushed_at": "",
        "licence_spdx": "",
        "page_kind": "product",
        "self_hosted": True,
    }


def _client(tmp_path, payload):
    return RelevanceClient(cache_dir=tmp_path, mock_handler=lambda _p, _s: json.dumps(payload))


def test_a_mature_github_web_hit_can_already_exist(tmp_path):
    calls = []

    def getter(url, token, accept="application/vnd.github+json"):
        calls.append(url)
        assert "rufuspollock/s3-bucket-listing" in url
        return _s3_payload()

    row = _web_row()
    attach_github_metadata([row], getter=getter, limit=5, token="gh-test")
    assert calls and row["stars"] == 709
    assert row["pushed_at"] == "2026-11-01T00:00:00Z"
    assert row["archived"] is False
    assert row["licence_spdx"] == "MIT"
    judged = judge_discovery(
        candidate={
            "repo_id": "example/s3browser",
            "description": "Browse an S3 bucket from a static page.",
            "readme_excerpt": "Browse an S3 bucket from a static page.",
            "stars": 12,
        },
        prior_rows=[row],
        demand_rows=[],
        client=_client(tmp_path, {
            "verdict": "ALREADY_EXISTS",
            "reason": "s3-bucket-listing already lists a bucket.",
            "problem_quote": "Browse an S3 bucket from a static page.",
            "alternative_repo": "rufuspollock/s3-bucket-listing",
            "alternative_quote": "List the contents of an S3 bucket in the browser.",
        }),
    )
    assert judged[0] == "ALREADY_EXISTS"


def test_an_archived_alternative_does_not_count_as_mature(tmp_path):
    row = _web_row()
    attach_github_metadata([row], getter=lambda url, token, accept="": _s3_payload(archived=True), limit=5)
    assert row["archived"] is True
    judged = judge_discovery(
        candidate={
            "repo_id": "example/s3browser",
            "description": "Browse an S3 bucket from a static page.",
            "readme_excerpt": "Browse an S3 bucket from a static page.",
            "stars": 12,
        },
        prior_rows=[row],
        demand_rows=[],
        client=_client(tmp_path, {
            "verdict": "ALREADY_EXISTS",
            "reason": "the archived listing already does this.",
            "problem_quote": "Browse an S3 bucket from a static page.",
            "alternative_repo": "rufuspollock/s3-bucket-listing",
            "alternative_quote": "List the contents of an S3 bucket in the browser.",
        }),
    )
    assert judged[0] != "ALREADY_EXISTS"


def test_a_failed_lookup_does_not_invent_stars_and_is_traced(tmp_path):
    trace = HackathonTrace(run_id="meta", log_dir=tmp_path)

    def getter(url, token, accept="application/vnd.github+json"):
        raise JudgeError("GitHub rate limit reached. Anonymous access allows about 60 requests an hour.")

    row = _web_row()
    other = {
        "alternative_id": "web:https://example.com/docs",
        "url": "https://example.com/docs",
        "source": "web",
        "stars": 0,
        "overlap": "none",
    }
    attach_github_metadata([row, other], getter=getter, trace=trace, limit=5)
    assert row["stars"] is None
    assert row["metadata_status"] == "unavailable"
    assert other["stars"] is None
    lines = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["action"] == "GITHUB_METADATA"
    assert "metadata unavailable" in lines[0]["output_summary"]
    assert "709" not in lines[0]["output_summary"]


def test_at_most_five_lookups_prefer_a_full_overlap(tmp_path):
    calls = []

    def getter(url, token, accept="application/vnd.github+json"):
        calls.append(url)
        name = url.rstrip("/").split("/")[-1]
        return _s3_payload(stars=100 + len(calls))

    rows = [_web_row(repo=f"org/partial-{i}", overlap="partial") for i in range(5)]
    rows.append(_web_row(repo="awslabs/aws-js-s3-explorer", overlap="full"))
    attach_github_metadata(rows, getter=getter, limit=5)
    assert len(calls) == 5
    assert any("aws-js-s3-explorer" in url for url in calls)
    untouched = [row for row in rows if row.get("metadata_status") == "not_looked_up"]
    assert untouched
    assert all(row["stars"] is None for row in untouched)


def test_og_png_is_also_served_at_the_root():
    client = TestClient(app)
    root = client.get("/og.png")
    nested = client.get("/pro/og.png")
    assert root.status_code == 200
    assert root.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert root.content == nested.content


def test_committed_examples_are_untouched():
    diff = __import__("subprocess").check_output(["git", "diff", "--", "pro/examples"], text=True)
    assert diff == ""
    page = Path("pro/ui/index.html").read_text(encoding="utf-8")
    assert "LLM_FALLBACK is from an earlier scoring step" in page
    assert "alternatives_seconds" in Path("pro/present.py").read_text(encoding="utf-8")
