"""Recall of the stable search, skip wording, full skip reasons, and a stable blocker."""

import json
from datetime import date, timedelta

from app.agents.relevance import RelevanceClient, judge_discovery
from app.agents.stable_search import (
    cached_github_search,
    is_junk_repo,
    keyword_queries,
    merge_preserving_web,
)
from app.trace_log import HackathonTrace
from scripts.verify_trace import verify

QUOTE = "directly solves the core problem"
RECENT = (date.today() - timedelta(days=10)).isoformat() + "T00:00:00Z"


def _blocker(name: str, stars: int) -> dict:
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
    }


def test_queries_use_category_words_and_drop_dotfile_junk():
    candidate = {
        "repo_id": "request/request",
        "description": "Simplified HTTP request client",
        "primary_language": "JavaScript",
        "topics": ["http-client"],
    }
    queries = keyword_queries(candidate)
    assert len(queries) <= 3
    assert queries == keyword_queries(candidate)
    assert "stars:>=50" in queries[0]
    assert "language:JavaScript" in queries[0]
    assert "http" in queries[0].casefold()
    assert "topic:http-client" in " ".join(queries)
    assert is_junk_repo("zszszszsz/.config")
    items = [
        {"full_name": "zszszszsz/.config", "stargazers_count": 400, "description": "dotfiles"},
        {"full_name": "sindresorhus/got", "html_url": "https://github.com/sindresorhus/got",
         "stargazers_count": 14000, "pushed_at": RECENT, "description": "HTTP", "license": {"spdx_id": "MIT"}},
    ]
    rows = cached_github_search(
        "request/request", "abc", queries[0],
        lambda url, token, accept="": {"items": items},
        cache_dir=__import__("pathlib").Path("/tmp/round9-search"),
    )
    assert [row["alternative_id"] for row in rows] == ["sindresorhus/got"]


def test_web_hits_are_not_crowded_out_by_github_fill():
    web = [{
        "alternative_id": "sindresorhus/got",
        "url": "https://github.com/sindresorhus/got",
        "source": "web",
        "stars": None,
        "description": "HTTP client",
    }]
    github = [
        {"alternative_id": f"org/junk{i}", "url": f"https://github.com/org/junk{i}", "source": "github", "stars": 80 + i}
        for i in range(15)
    ]
    merged = merge_preserving_web(web, github, limit=15)
    assert merged[0]["alternative_id"] != "sindresorhus/got" or any(
        row["alternative_id"] == "sindresorhus/got" for row in merged
    )
    assert any(row["alternative_id"] == "sindresorhus/got" for row in merged)
    assert len(merged) == 15


def test_all_skipped_does_not_say_there_was_no_prior_art():
    from scripts.score_candidates import _skipped_reason

    text = _skipped_reason([
        {"alternative": "org/old", "reason": "last update 2010-01-01 is 5000 days before 2026-11-17"},
        {"alternative": "org/tiny", "reason": "not mature or official: stars=2"},
    ])
    assert text.startswith("2 alternatives were checked")
    assert "org/old" in text and "org/tiny" in text
    assert "Zero alternatives" not in text
    assert "no prior art" not in text.casefold()


def test_skip_reasons_fit_in_the_hash_chain(tmp_path):
    trace = HackathonTrace(run_id="skips", log_dir=tmp_path)
    skipped = [
        {"alternative": f"org/repo-{index}", "reason": "not mature or official: stars=1, candidate_stars=10, official=False"}
        for index in range(15)
    ]
    trace.event(
        agent_name="orchestrator",
        action="ALTERNATIVES_SKIPPED",
        input_summary={"repo_id": "example/app", "count": len(skipped)},
        output_summary={"skipped": skipped},
        status="ok",
        summary_limit=20000,
    )
    trace.seal()
    ok, detail = verify(trace.path)
    assert ok, detail
    record = json.loads(trace.path.read_text(encoding="utf-8").splitlines()[0])
    summary = json.loads(record["output_summary"])
    assert len(summary["skipped"]) == 15
    assert len(record["output_summary"]) > 500
    assert summary["skipped"][-1]["alternative"] == "org/repo-14"


def test_the_blocker_is_the_higher_star_repo_in_either_input_order(tmp_path):
    low = _blocker("org/lesser", 200)
    high = _blocker("org/greater", 9000)
    client = RelevanceClient(
        cache_dir=tmp_path / "cache",
        mock_handler=lambda _p, _s: json.dumps({
            "verdict": "CANDIDATE",
            "reason": "looks new",
            "problem_quote": QUOTE,
        }),
    )
    candidate = {"repo_id": "example/app", "description": QUOTE, "readme_excerpt": QUOTE, "stars": 10}
    forward, reason_forward = judge_discovery(candidate, [low, high], [], client=client)
    backward, reason_backward = judge_discovery(candidate, [high, low], [], client=RelevanceClient(
        cache_dir=tmp_path / "cache-b",
        mock_handler=lambda _p, _s: json.dumps({
            "verdict": "CANDIDATE",
            "reason": "looks new",
            "problem_quote": QUOTE,
        }),
    ))
    assert forward == backward == "ALREADY_EXISTS"
    assert "org/greater" in reason_forward
    assert reason_forward.split("Method:")[0] == reason_backward.split("Method:")[0]
