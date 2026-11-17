"""Stable search, skip-immature, short-circuit, and the yarn docs case."""

import json
from datetime import date, datetime, timezone

from app.agents.blocker import evaluate_blocker
from app.agents.registry_maturity import attach_registry_metadata
from app.agents.relevance import RelevanceClient, filter_relevant_alternatives
from app.agents.stable_search import cached_github_search, keyword_queries, merge_ranked
from app.trace_log import HackathonTrace


def _repo(name, stars, pushed="2026-10-01T00:00:00Z", licence="MIT"):
    return {
        "alternative_id": name,
        "url": f"https://github.com/{name}",
        "description": "A library.",
        "readme": "A library that does the job.",
        "source": "github",
        "stars": stars,
        "pushed_at": pushed,
        "archived": False,
        "licence_spdx": licence,
        "same_problem": True,
    }


def test_merge_order_is_stars_then_name():
    first = merge_ranked([
        _repo("b/low", 3),
        _repo("a/high", 100),
        {"alternative_id": "web:https://github.com/a/high", "url": "https://github.com/a/high/blob/main/README.md", "source": "web", "stars": 0, "readme": "extra"},
    ])
    second = merge_ranked(list(reversed(first)))
    assert [row["alternative_id"] for row in first] == [row["alternative_id"] for row in second]
    assert first[0]["alternative_id"] == "a/high"
    assert len(first) == 2
    assert [row["stars"] for row in first] == [100, 3]


def test_search_cache_reuses_the_same_candidate_set(tmp_path):
    calls = {"n": 0}

    def getter(url, token, accept="application/vnd.github+json"):
        calls["n"] += 1
        assert "sort=stars" in url
        return {"items": [
            {"full_name": "sindresorhus/got", "html_url": "https://github.com/sindresorhus/got",
             "description": "HTTP requests", "stargazers_count": 14000, "pushed_at": "2026-11-01T00:00:00Z",
             "archived": False, "license": {"spdx_id": "MIT"}},
        ]}

    candidate = {"repo_id": "request/request", "description": "Simplified HTTP request client", "core_terms": "http|request"}
    queries = keyword_queries(candidate)
    assert queries == keyword_queries(candidate)
    assert len(queries) <= 3
    first = cached_github_search("request/request", "abc", queries[0], getter, cache_dir=tmp_path)
    second = cached_github_search("request/request", "abc", queries[0], getter, cache_dir=tmp_path)
    assert calls["n"] == 1
    assert [row["alternative_id"] for row in first] == [row["alternative_id"] for row in second] == ["sindresorhus/got"]


def test_an_immature_alternative_is_not_sent_to_the_model(tmp_path):
    calls = {"n": 0}

    def handler(prompt, _system):
        calls["n"] += 1
        return json.dumps({"same_problem": False, "overlap": "none", "quotes": []})

    trace = HackathonTrace(run_id="skip", log_dir=tmp_path)
    rows = filter_relevant_alternatives(
        {"problem": "Do a job", "target_user": "people", "category": "tools"},
        "example/app",
        [_repo("old/thing", 2, pushed="2010-01-01T00:00:00Z"), _repo("young/thing", 500)],
        client=RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "cache"),
        trace=trace,
        candidate={"repo_id": "example/app", "description": "Do a job", "stars": 10},
        skip_immature=True,
    )
    assert calls["n"] == 1
    assert rows == [] or all(row["alternative_id"] != "old/thing" for row in rows)
    text = trace.path.read_text(encoding="utf-8")
    assert "ALTERNATIVES_SKIPPED" in text
    assert "old/thing" in text


def test_short_circuit_stops_after_a_blocker(tmp_path):
    calls = {"n": 0}
    quote = "does the same job for the same user"

    def handler(prompt, _system):
        calls["n"] += 1
        body = {
            "same_core_job": True,
            "same_input_output": True,
            "same_target_user": True,
            "covers_main_features": False,
            "same_problem": True,
            "quotes": [quote],
            "reason": "same job",
        }
        if "BATCH" in prompt or prompt.count("Alternative:") > 1:
            return json.dumps({"results": [
                {**body, "index": 0},
                {**body, "index": 1},
            ]})
        return json.dumps(body)

    mature = _repo("big/one", 900)
    mature["description"] = quote
    mature["readme"] = quote
    other = _repo("big/two", 800)
    other["description"] = quote
    other["readme"] = quote
    third = _repo("big/three", 700)
    third["description"] = quote
    third["readme"] = quote
    trace = HackathonTrace(run_id="stop", log_dir=tmp_path)
    found = filter_relevant_alternatives(
        {"problem": "Do a job", "target_user": "people", "category": "tools"},
        "example/app",
        [mature, other, third],
        client=RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "cache"),
        trace=trace,
        candidate={"repo_id": "example/app", "description": "Do a job", "stars": 10, "readme_excerpt": quote},
        skip_immature=True,
    )
    assert calls["n"] == 1
    assert any(row["alternative_id"] == "big/one" for row in found)
    assert "ALTERNATIVES_SHORT_CIRCUIT" in trace.path.read_text(encoding="utf-8")


def test_yarnpkg_docs_block_when_the_npm_package_is_mature():
    row = {
        "alternative_id": "web:https://yarnpkg.com/",
        "url": "https://yarnpkg.com/",
        "description": "Fast, reliable, and secure dependency management.",
        "excerpt": "Fast, reliable, and secure dependency management.",
        "readme": "Fast, reliable, and secure dependency management.",
        "quotes": ["Fast, reliable, and secure dependency management."],
        "quote_verified": True,
        "overlap": "full",
        "same_problem": True,
        "same_job": True,
        "source": "web",
        "page_kind": "product",
    }

    def getter(url):
        if "registry.npmjs.org/yarn" in url:
            return {
                "license": "BSD-2-Clause",
                "dist-tags": {"latest": "1.22.22"},
                "time": {"1.22.22": "2026-10-01T00:00:00Z"},
            }
        if "downloads/point" in url:
            return {"downloads": 2000000}
        raise RuntimeError(url)

    attach_registry_metadata([row], getter=getter, now=datetime(2026, 11, 17, tzinfo=timezone.utc))
    assert row["registry_package"] == "yarn"
    assert row["licence_spdx"] == "BSD-2-Clause"
    assert row["registry_mature"] is True
    decision = evaluate_blocker(
        {"repo_id": "bower/bower", "description": "A package manager for the web", "stars": 100},
        row,
        as_of=date(2026, 11, 17),
    )
    assert decision["blocks"] is True
