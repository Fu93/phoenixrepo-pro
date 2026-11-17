"""Stable overlap labels, one key per repository, and registry maturity."""

import json
from datetime import datetime, timezone

from app.agents.alt_key import canonical_alternative_key, dedupe_alternatives
from app.agents.overlap import derive_overlap, is_borderline
from app.agents.registry_maturity import NPM_WEEKLY_DOWNLOADS, attach_registry_metadata
from app.agents.relevance import RelevanceClient, judge_alternative_relevance, judge_discovery
from app.trace_log import HackathonTrace


def _answers(**flags):
    base = {
        "same_core_job": True,
        "same_input_output": True,
        "same_target_user": True,
        "covers_main_features": True,
    }
    base.update(flags)
    return base


def test_full_and_partial_come_from_the_four_answers():
    assert derive_overlap(_answers()) == "full"
    assert derive_overlap(_answers(covers_main_features=False)) == "partial"
    assert derive_overlap(_answers(same_core_job=False, covers_main_features=False)) == "none"
    assert is_borderline(_answers(covers_main_features=False)) is True
    assert is_borderline(_answers()) is False
    assert is_borderline(_answers(same_input_output=False, covers_main_features=False)) is False


def test_a_clear_full_is_not_asked_twice_and_a_split_reread_needs_review(tmp_path):
    text = "Browse an S3 bucket from a static page."
    calls = {"n": 0}

    def handler(prompt, _system):
        calls["n"] += 1
        if "Second read" in prompt:
            body = _answers()
        else:
            body = _answers(covers_main_features=False)
        body.update({
            "reason": "same listing tool",
            "quotes": [text],
        })
        return json.dumps(body)

    trace = HackathonTrace(run_id="overlap", log_dir=tmp_path)
    client = RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "cache", trace=trace)
    row, _meta = judge_alternative_relevance(
        {"problem": "Browse an S3 bucket", "target_user": "operators", "category": "tools"},
        "example/s3browser",
        {"alternative_id": "awslabs/aws-js-s3-explorer", "url": "https://github.com/awslabs/aws-js-s3-explorer", "description": text, "readme": text},
        client=client,
        trace=trace,
    )
    assert calls["n"] == 2
    assert row["overlap_reads"] == "disagree"
    assert row["overlap"] == "unresolved"
    lines = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    rubric = [line for line in lines if line["action"] == "OVERLAP_RUBRIC"]
    assert rubric and "disagree" in rubric[-1]["output_summary"]
    judged = judge_discovery(
        candidate={"repo_id": "example/s3browser", "description": text, "readme_excerpt": text, "stars": 12},
        prior_rows=[row],
        demand_rows=[],
        client=RelevanceClient(
            cache_dir=tmp_path / "cache2",
            mock_handler=lambda _p, _s: json.dumps({
                "verdict": "CANDIDATE",
                "reason": "nothing blocks",
                "problem_quote": text,
            }),
        ),
    )
    assert judged[0] == "NEEDS_REVIEW"
    assert "overlap judged differently on two reads" in judged[1]


def test_two_agreeing_borderline_reads_keep_the_stricter_label(tmp_path):
    text = "Browse an S3 bucket from a static page."
    calls = {"n": 0}

    def handler(prompt, _system):
        calls["n"] += 1
        body = _answers(covers_main_features=False)
        body.update({"reason": "missing one feature", "quotes": [text]})
        return json.dumps(body)

    client = RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "cache")
    row, _meta = judge_alternative_relevance(
        {"problem": "Browse an S3 bucket", "target_user": "operators", "category": "tools"},
        "example/s3browser",
        {"alternative_id": "awslabs/aws-js-s3-explorer", "description": text, "readme": text},
        client=client,
    )
    assert calls["n"] == 2
    assert row["overlap"] == "partial"
    assert row["overlap_reads"] == "agree"


def test_github_urls_for_one_repository_collapse_to_one_key():
    rows = dedupe_alternatives([
        {"alternative_id": "obfusk/jiten", "url": "https://github.com/obfusk/jiten", "source": "github", "stars": 10, "readme": ""},
        {"alternative_id": "web:https://github.com/obfusk/jiten/blob/master/README.md", "url": "https://github.com/obfusk/jiten/blob/master/README.md", "source": "web", "stars": 0, "readme": "A japanese dictionary."},
        {"alternative_id": "web:https://github.com/obfusk/jiten", "url": "https://github.com/obfusk/jiten/", "source": "web"},
    ])
    assert len(rows) == 1
    assert canonical_alternative_key(rows[0]) == "github.com/obfusk/jiten"
    assert rows[0]["readme"] == "A japanese dictionary."
    assert rows[0]["stars"] == 10


def test_npm_and_pypi_maturity_use_real_fields_only():
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)

    def getter(url):
        if url.startswith("https://registry.npmjs.org/yarn"):
            return {"dist-tags": {"latest": "1.22.22"}, "time": {"1.22.22": "2026-10-01T00:00:00Z"}}
        if "downloads/point" in url and url.endswith("/yarn"):
            raise RuntimeError("downloads unavailable")
        if url.startswith("https://registry.npmjs.org/leftpad"):
            return {"dist-tags": {"latest": "1.0.0"}, "time": {"1.0.0": "2026-10-01T00:00:00Z"}}
        if url.endswith("/leftpad"):
            return {"downloads": 12}
        if "pypi.org/pypi/flask" in url:
            return {"info": {"version": "3.0.0"}, "releases": {"3.0.0": [{"upload_time": "2026-09-01T00:00:00Z"}]}}
        raise RuntimeError("no such package")

    yarn = {"url": "https://yarnpkg.com/getting-started", "alternative_id": "web:https://yarnpkg.com/getting-started"}
    small = {"url": "https://www.npmjs.com/package/leftpad", "alternative_id": "npm:leftpad"}
    missing = {"url": "https://www.npmjs.com/package/nope", "alternative_id": "npm:nope"}
    flask = {"url": "https://pypi.org/project/flask/", "alternative_id": "pypi:flask"}
    attach_registry_metadata([yarn, small, missing, flask], getter=getter, now=now)
    assert yarn["registry_mature"] is True
    assert yarn["registry_package"] == "yarn"
    assert "weekly_downloads" not in yarn
    assert small["registry_mature"] is False
    assert small["weekly_downloads"] == 12
    assert small["weekly_downloads"] < NPM_WEEKLY_DOWNLOADS
    assert missing["registry_status"] == "unavailable"
    assert "released_at" not in missing
    assert "stars" not in missing
    assert flask["released_at"].startswith("2026-09-01")
    assert flask["registry_mature"] is False
