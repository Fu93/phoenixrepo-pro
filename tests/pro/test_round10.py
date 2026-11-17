"""The blocker gate, a stable candidate set, and the short all-skipped page."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.agents.alt_key import github_owner_repo
from app.agents.blocker import block_obstacle
from app.agents.github_metadata import attach_github_metadata
from app.agents.registry_maturity import attach_registry_metadata
from app.agents.relevance import RelevanceClient, _cached_web_call, judge_discovery, search_prior_art
from app.agents.stable_search import cached_payload, is_junk_repo, keyword_queries, merge_preserving_web
from scripts.score_candidates import _skipped_reason

QUOTE = "directly solves the core problem"
RECENT = (date.today() - timedelta(days=10)).isoformat() + "T00:00:00Z"
README = "This tool lists objects in a bucket from the browser."


def _alt(name: str, stars: int) -> dict:
    return {
        "alternative_id": name,
        "url": f"https://github.com/{name}",
        "description": QUOTE,
        "excerpt": QUOTE,
        "readme": QUOTE,
        "quotes": [QUOTE],
        "quote_verified": True,
        "overlap": "full",
        "same_problem": True,
        "same_job": True,
        "source": "github",
        "stars": stars,
        "pushed_at": RECENT,
        "archived": False,
        "licence_spdx": "MIT",
        "page_kind": "product",
        "rubric": {
            "same_core_job": True,
            "same_input_output": True,
            "same_target_user": True,
            "covers_main_features": True,
        },
    }


def _judge(priors, tmp_path, name):
    def fake_llm(_prompt, _system):
        return json.dumps({
            "verdict": "NO_GO",
            "reason": "Nothing else does this.",
            "problem_quote": "lists objects in a bucket",
            "alternative_repo": None,
            "alternative_quote": None,
        })

    client = RelevanceClient(cache_dir=tmp_path / name, mock_handler=fake_llm)
    return judge_discovery(
        {
            "repo_id": "example/s3browser",
            "description": "List a bucket in the browser.",
            "readme_excerpt": README,
            "stars": 12,
        },
        prior_rows=priors,
        demand_rows=[],
        client=client,
    )


def test_no_go_still_blocks_on_a_mature_full_overlap_and_lists_every_check(tmp_path):
    low = _alt("qoomon/aws-s3-bucket-browser", 200)
    high = _alt("awslabs/aws-js-s3-explorer", 863)
    first = _judge([low, high], tmp_path, "a")
    second = _judge([high, low], tmp_path, "b")
    assert first[0] == second[0] == "ALREADY_EXISTS"
    assert first.evidence[0]["alternative"] == second.evidence[0]["alternative"] == "awslabs/aws-js-s3-explorer"
    shown = {row["alternative"] for row in first.evidence + first.must_beat}
    assert shown == {"awslabs/aws-js-s3-explorer", "qoomon/aws-s3-bucket-browser"}


def test_equal_stars_pick_the_canonical_key(tmp_path):
    decision = _judge([_alt("m/later", 500), _alt("a/earlier", 500)], tmp_path, "tie")
    assert decision.evidence[0]["alternative"] == "a/earlier"


def test_same_cached_inputs_give_the_same_verdict_and_blocker(tmp_path):
    calls = {"n": 0}

    class _Budget:
        def is_exhausted(self):
            return False

    class _Box:
        def __init__(self):
            self.budget = _Budget()
            self.tools = {"web_search": object()}

        def has(self, name):
            return name == "web_search"

        def call(self, _name, _payload):
            calls["n"] += 1
            return type("Hit", (), {
                "ok": True,
                "error": "",
                "data": {"items": [{
                    "url": "https://github.com/pnpm/pnpm",
                    "title": "pnpm",
                    "snippet": "Fast package manager",
                }]},
            })()

    candidate = {
        "repo_id": "bower/bower",
        "description": "A package manager for the web",
        "_head_commit": "abc",
        "_search_cache": tmp_path / "web",
    }
    queries = ["package manager"]
    one = search_prior_art(candidate, queries, _Box())
    two = search_prior_art(candidate, queries, _Box())
    assert calls["n"] == 1
    assert [row["url"] for row in one] == [row["url"] for row in two]

    github = [
        {"alternative_id": "org/low", "url": "https://github.com/org/low", "source": "github", "stars": 80},
        {"alternative_id": "pnpm/pnpm", "url": "https://github.com/pnpm/pnpm", "source": "github", "stars": 20000},
    ]
    web = [{"alternative_id": "web:https://github.com/pnpm/pnpm", "url": "https://github.com/pnpm/pnpm", "source": "web", "stars": 20000}]
    left = merge_preserving_web(web, github, limit=15)
    right = merge_preserving_web(list(reversed(github)), web, limit=15)
    assert [row["alternative_id"] for row in left] == [row["alternative_id"] for row in right]
    assert left[0]["alternative_id"] == "pnpm/pnpm"

    stored = cached_payload("web", "bower/bower", "abc", "package manager", lambda: {"ok": True, "items": []}, cache_dir=tmp_path / "web")
    again = cached_payload("web", "bower/bower", "abc", "package manager", lambda: {"ok": False, "items": [{"url": "nope"}]}, cache_dir=tmp_path / "web")
    assert stored == again
    assert stored["ok"] is True

    winner_a = _judge([_alt("awslabs/aws-js-s3-explorer", 863), _alt("qoomon/aws-s3-bucket-browser", 200)], tmp_path, "c1")
    winner_b = _judge([_alt("qoomon/aws-s3-bucket-browser", 200), _alt("awslabs/aws-js-s3-explorer", 863)], tmp_path, "c2")
    assert (winner_a[0], winner_a.evidence[0]["alternative"]) == (winner_b[0], winner_b.evidence[0]["alternative"])


def test_bower_keeps_the_package_manager_query_and_junk_is_dropped():
    queries = keyword_queries({
        "repo_id": "bower/bower",
        "description": "A package manager for the web",
        "primary_language": "JavaScript",
    })
    assert "package manager" in queries
    assert is_junk_repo("sindresorhus/awesome")
    assert is_junk_repo("org/awesome-s3")
    assert is_junk_repo("org/s3-mirror")
    assert not is_junk_repo("pnpm/pnpm")


def test_mirror_hosts_resolve_to_github():
    assert github_owner_repo("https://qgithub.com/qoomon/aws-s3-bucket-browser") == "qoomon/aws-s3-bucket-browser"
    assert github_owner_repo("https://github.com.cn/awslabs/aws-js-s3-explorer") == "awslabs/aws-js-s3-explorer"


def test_npm_page_without_a_date_uses_the_github_repository(tmp_path):
    row = {
        "alternative_id": "web:https://www.npmjs.com/package/got",
        "url": "https://www.npmjs.com/package/got",
        "description": "Human-friendly HTTP request library",
        "source": "web",
        "stars": None,
        "pushed_at": "",
    }

    def registry(url):
        if url.startswith("https://registry.npmjs.org/"):
            return {"license": "MIT", "repository": {"url": "git+https://github.com/sindresorhus/got.git"}}
        raise RuntimeError(url)

    attach_registry_metadata([row], getter=registry, now=datetime(2026, 11, 17, tzinfo=timezone.utc))
    assert "github.com/sindresorhus/got" in row["repository_url"]

    def github(url, token, accept=""):
        assert url.endswith("/repos/sindresorhus/got")
        return {"stargazers_count": 14963, "pushed_at": RECENT, "archived": False, "license": {"spdx_id": "MIT"}}

    attach_github_metadata([row], getter=github, limit=5)
    assert row["stars"] == 14963
    assert row["pushed_at"] == RECENT
    reason = block_obstacle({"repo_id": "request/request", "stars": 10}, row, as_of=date.today())
    assert "no push or release date" not in reason


def test_a_github_url_inside_the_page_fills_the_date():
    row = {
        "alternative_id": "web:https://example.com/s3",
        "url": "https://example.com/s3",
        "description": "See https://qgithub.com/awslabs/aws-js-s3-explorer for the source.",
        "source": "web",
        "stars": None,
    }

    def github(url, token, accept=""):
        assert url.endswith("/repos/awslabs/aws-js-s3-explorer")
        return {"stargazers_count": 863, "pushed_at": RECENT, "archived": False, "license": {"spdx_id": "Apache-2.0"}}

    attach_github_metadata([row], getter=github, limit=5)
    assert row["pushed_at"] == RECENT
    assert block_obstacle({"stars": 10}, row, as_of=date.today()) != "no push or release date"


def test_all_skipped_reason_is_one_sentence_and_the_page_can_expand_the_list():
    text = _skipped_reason([
        {"alternative": "org/old", "reason": "last update 2010-01-01 is 5000 days before 2026-11-17"},
        {"alternative": "org/tiny", "reason": "not mature or official: stars=2"},
    ])
    assert text == "2 alternatives were checked and none could block."
    page = Path("pro/ui/index.html").read_text(encoding="utf-8")
    assert "function skippedList" in page
    assert "<details" not in page
    assert 'el("details"' in page


def test_a_failed_problem_quote_is_retried_once_against_the_source(tmp_path):
    calls = {"n": 0}

    def fake_llm(prompt, _system):
        calls["n"] += 1
        if "exact source text" in prompt:
            return json.dumps({"sentence_indexes": [0]})
        return json.dumps({
            "verdict": "CANDIDATE",
            "reason": "Novel.",
            "problem_quote": "this sentence was invented",
            "alternative_repo": None,
        })

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    verdict, reason = judge_discovery(
        {"repo_id": "example/app", "readme_excerpt": "This tool lists objects in a bucket from the browser."},
        prior_rows=[],
        demand_rows=[],
        client=client,
    )
    assert calls["n"] == 2
    assert verdict == "CANDIDATE"
    assert "unverified LLM quote" not in reason


def test_web_cache_helper_does_not_call_the_tool_twice(tmp_path):
    calls = {"n": 0}

    class _Box:
        def call(self, _name, _payload):
            calls["n"] += 1
            return type("Hit", (), {"ok": True, "error": "", "data": {"items": [{"url": "https://example.com"}]}})()

    candidate = {"_head_commit": "abc", "_search_cache": tmp_path}
    _cached_web_call(candidate, "bower/bower", _Box(), "web_search", "package manager")
    _cached_web_call(candidate, "bower/bower", _Box(), "web_search", "package manager")
    assert calls["n"] == 1
