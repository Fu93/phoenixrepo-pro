"""Sealed search snapshots, ecosystem ranking, and unnamed alternatives."""

import json
from datetime import date, timedelta

from app.agents.relevance import RelevanceClient, judge_discovery, search_prior_art
from app.agents.search_snapshot import (
    bind,
    open_snapshot,
    reset,
    search_events_hash,
    verify_snapshot,
    SnapshotToolbox,
)
from app.agents.stable_search import keyword_queries
from app.tools.base import ToolResult
from app.trace_log import HackathonTrace
from scripts.verify_trace import verify

QUOTE = "directly solves the core problem"
RECENT = (date.today() - timedelta(days=10)).isoformat() + "T00:00:00Z"


class _Budget:
    remaining_calls = 50
    remaining_seconds = 1000

    def is_exhausted(self):
        return False

    def consume(self):
        return True


def _box(calls):
    class _Box:
        def __init__(self):
            self.budget = _Budget()
            self.tools = {"web_search": object(), "github": object()}

        def has(self, name):
            return name in self.tools

        def call(self, name, args):
            calls["n"] += 1
            if calls.get("explode"):
                raise AssertionError("live call during replay")
            if name == "web_search":
                return ToolResult(
                    ok=True,
                    data={"items": [{
                        "url": "https://github.com/sindresorhus/got",
                        "title": "got",
                        "snippet": "HTTP request library",
                    }]},
                    error="",
                    source_url="https://example.test",
                    fetched_at=RECENT,
                )
            return ToolResult(
                ok=True,
                data={"items": [{
                    "full_name": "axios/axios",
                    "html_url": "https://github.com/axios/axios",
                    "description": "HTTP client",
                    "stargazers_count": 1000,
                }]},
                error="",
                source_url="https://api.github.com",
                fetched_at=RECENT,
            )

    return _Box()


def _alt(name, stars, language):
    return {
        "alternative_id": name,
        "url": f"https://github.com/{name}",
        "description": QUOTE,
        "excerpt": QUOTE,
        "readme": QUOTE,
        "quotes": [QUOTE],
        "overlap": "full",
        "same_problem": True,
        "same_job": True,
        "source": "github",
        "stars": stars,
        "language": language,
        "pushed_at": RECENT,
        "archived": False,
        "licence_spdx": "MIT",
        "page_kind": "product",
    }


def test_the_same_snapshot_replays_the_candidate_list_verdict_and_search_hash(tmp_path):
    calls = {"n": 0}
    trace_one = HackathonTrace(run_id="snap-a", log_dir=tmp_path / "a")
    session = open_snapshot("request/request", "abc", directory=tmp_path / "snaps")
    token = bind(session)
    try:
        first = search_prior_art(
            {"repo_id": "request/request", "description": "HTTP client"},
            ["http client"],
            SnapshotToolbox(_box(calls), session),
        )
        client = RelevanceClient(
            cache_dir=tmp_path / "model-a",
            mock_handler=lambda _p, _s: json.dumps({
                "verdict": "NO_GO",
                "reason": "Nothing else.",
                "problem_quote": "lists objects in a bucket",
            }),
        )
        decision = judge_discovery(
            {
                "repo_id": "example/s3browser",
                "description": "List a bucket.",
                "readme_excerpt": "This tool lists objects in a bucket from the browser.",
                "stars": 12,
                "primary_language": "JavaScript",
            },
            [_alt("python/huge", 9000, "Python"), _alt("js/smaller", 400, "JavaScript")],
            [],
            client=client,
        )
        session.finish(trace_one)
    finally:
        session.close()
        reset(token)
    trace_one.seal()
    assert decision[0] == "ALREADY_EXISTS"
    assert "js/smaller" in decision[1]
    ids = [row["url"] for row in first]

    calls["explode"] = True
    trace_two = HackathonTrace(run_id="snap-b", log_dir=tmp_path / "b")
    replay = open_snapshot("request/request", "abc", directory=tmp_path / "snaps")
    assert replay.replaying
    token = bind(replay)
    try:
        second = search_prior_art(
            {"repo_id": "request/request", "description": "HTTP client"},
            ["http client"],
            SnapshotToolbox(_box(calls), replay),
        )
        replay.seal(trace_two)
        again = judge_discovery(
            {
                "repo_id": "example/s3browser",
                "description": "List a bucket.",
                "readme_excerpt": "This tool lists objects in a bucket from the browser.",
                "stars": 12,
                "primary_language": "JavaScript",
            },
            [_alt("python/huge", 9000, "Python"), _alt("js/smaller", 400, "JavaScript")],
            [],
            client=RelevanceClient(
                cache_dir=tmp_path / "model-b",
                mock_handler=lambda _p, _s: (_ for _ in ()).throw(AssertionError("model was called on replay")),
            ),
        )
    finally:
        replay.close()
        reset(token)
    trace_two.seal()
    assert replay.live_calls == 0
    assert [row["url"] for row in second] == ids
    assert again[0] == decision[0]
    assert again.evidence[0]["alternative"] == decision.evidence[0]["alternative"] == "js/smaller"
    assert search_events_hash(trace_one.path) == search_events_hash(trace_two.path)
    ok, detail = verify(trace_one.path)
    assert ok, detail
    snap_ok, snap_detail = verify_snapshot(trace_one.path, replay.path)
    assert snap_ok, snap_detail


def test_refresh_records_a_new_snapshot(tmp_path):
    first = open_snapshot("bower/bower", "abc", directory=tmp_path)
    first.put("tool", "query-a", {"ok": True, "data": {"items": [{"url": "https://example.com/a"}]}})
    first.seal(None)
    second = open_snapshot("bower/bower", "abc", refresh=True, directory=tmp_path)
    assert second.replaying is False
    assert second.refreshed is True
    second.put("tool", "query-b", {"ok": True, "data": {"items": [{"url": "https://example.com/b"}]}})
    second.seal(None)
    assert second.sealed_hash != first.sealed_hash
    saved = json.loads(second.path.read_text(encoding="utf-8"))
    assert saved["refreshed"] is True


def test_verify_detects_a_tampered_snapshot(tmp_path):
    trace = HackathonTrace(run_id="tamper", log_dir=tmp_path)
    session = open_snapshot("org/app", "abc", directory=tmp_path / "snaps")
    session.put("github_http", "https://api.github.com/repos/org/app", {"stargazers_count": 10})
    session.seal(trace)
    trace.seal()
    ok, _detail = verify_snapshot(trace.path, session.path)
    assert ok
    doc = json.loads(session.path.read_text(encoding="utf-8"))
    doc["records"][0]["payload"]["stargazers_count"] = 999999
    session.path.write_text(json.dumps(doc), encoding="utf-8")
    bad, message = verify_snapshot(trace.path, session.path)
    assert bad is False
    assert "does not match" in message


def test_a_name_that_is_not_in_the_candidate_list_is_ignored(tmp_path):
    def fake(_prompt, _system):
        return json.dumps({
            "verdict": "ALREADY_EXISTS",
            "reason": "None already does this.",
            "problem_quote": "lists objects in a bucket",
            "alternative_repo": "None",
            "alternative_quote": "not in any source",
        })

    decision = judge_discovery(
        {
            "repo_id": "bower/bower",
            "description": "A package manager.",
            "readme_excerpt": "This tool lists objects in a bucket from the browser.",
            "stars": 10,
        },
        [],
        [],
        client=RelevanceClient(cache_dir=tmp_path, mock_handler=fake),
    )
    assert decision[0] == "CANDIDATE"
    assert "quote not in the source" not in decision[1]
    assert "not found in candidate prior-art" not in decision[1]
    assert "None" not in decision[1]


def test_request_keeps_a_javascript_library_query():
    queries = keyword_queries({
        "repo_id": "request/request",
        "description": "Simplified HTTP request client",
        "primary_language": "JavaScript",
        "_category": "http",
    })
    assert any(query.casefold() == "javascript http library" for query in queries)


def test_problem_extraction_retries_with_the_readme_first_section(tmp_path):
    calls = {"n": 0}
    readme = "Look up Japanese words from the command line.\n\nMore text."

    def fake(prompt, _system):
        calls["n"] += 1
        if "README first section" in prompt:
            assert "Look up Japanese words" in prompt
            assert "A dictionary." in prompt
            return json.dumps({
                "problem": "Finding a word is slow.",
                "target_user": "readers",
                "category": "dictionary",
                "search_queries": ["japanese dictionary"],
                "quotes": ["Look up Japanese words from the command line."],
            })
        return json.dumps({
            "problem": "Invented.",
            "target_user": "nobody",
            "category": "fiction",
            "search_queries": ["nope"],
            "quotes": ["this sentence is not in the readme"],
        })

    from app.agents.relevance import extract_problem_statement

    data, meta = extract_problem_statement(
        {"repo_id": "melissaboiko/myougiden", "description": "A dictionary.", "readme_excerpt": readme},
        client=RelevanceClient(cache_dir=tmp_path, mock_handler=fake),
    )
    assert calls["n"] == 3
    assert data is not None
    assert meta["fallback"] is False
    assert data["quotes"] == ["Look up Japanese words from the command line."]


def test_python_and_node_hash_the_same_snapshot_bytes(tmp_path):
    import hashlib
    import subprocess

    raw = b'{"commit":"abc","records":[{"kind":"tool","key":"q"}],"repo_id":"acme/widget"}'
    path = tmp_path / "search-snapshot.json"
    path.write_bytes(raw)
    python_hash = hashlib.sha256(raw).hexdigest()
    script = (
        "const fs=require('fs'); const crypto=require('crypto');"
        "const bytes=fs.readFileSync(process.argv[1]);"
        "process.stdout.write(crypto.createHash('sha256').update(bytes).digest('hex'));"
    )
    node_hash = subprocess.check_output(["node", "-e", script, str(path)], text=True)
    assert node_hash == python_hash
    page = __import__("pathlib").Path("pro/ui/index.html").read_text(encoding="utf-8")
    assert "arrayBuffer" in page
    assert "canonicalJson(body)" not in page


def test_full_pipeline_replay_does_not_call_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_PRO_DIR", str(tmp_path / "runtime"))
    calls = {"github": 0, "tool": 0, "model": 0}
    readme = "Lists objects in a bucket for developers.\n\n- Browse a bucket from the browser today.\n"

    def getter(url, token="", accept=""):
        calls["github"] += 1
        if calls.get("replay"):
            raise AssertionError("live github " + url)
        if "sindresorhus/got" in str(url):
            return {
                "full_name": "sindresorhus/got",
                "stargazers_count": 14000,
                "pushed_at": RECENT,
                "archived": False,
                "description": "HTTP request library for developers.",
                "license": {"spdx_id": "MIT"},
            }
        if str(url).endswith("/readme"):
            return readme
        if "/git/trees/" in str(url):
            return {"tree": [
                {"path": "src/main.js", "type": "blob"},
                {"path": "src/list.js", "type": "blob"},
            ]}
        return {
            "full_name": "acme/widget",
            "name": "widget",
            "description": "Lists objects in a bucket for developers.",
            "html_url": "https://github.com/acme/widget",
            "default_branch": "main",
            "pushed_at": "2026-11-01T00:00:00Z",
            "created_at": "2020-01-01T00:00:00Z",
            "stargazers_count": 12,
            "forks_count": 1,
            "open_issues_count": 0,
            "license": {"spdx_id": "MIT"},
            "language": "JavaScript",
        }

    class _Box:
        def __init__(self):
            self.budget = _Budget()
            self.tools = {"web_search": object(), "github": object()}
            self.trace = None

        def has(self, name):
            return name in self.tools

        def call(self, name, _args):
            calls["tool"] += 1
            if calls.get("replay"):
                raise AssertionError("live search " + name)
            if name == "github":
                return ToolResult(
                    ok=True,
                    data={"items": [{
                        "full_name": "sindresorhus/got",
                        "html_url": "https://github.com/sindresorhus/got",
                        "description": "HTTP request library for developers.",
                        "stargazers_count": 14000,
                        "pushed_at": RECENT,
                        "language": "JavaScript",
                        "license": {"spdx_id": "MIT"},
                    }]},
                    error="",
                    source_url="https://api.github.com/search",
                    fetched_at=RECENT,
                )
            return ToolResult(
                ok=True,
                data={"items": [{
                    "url": "https://github.com/sindresorhus/got",
                    "title": "got",
                    "snippet": "HTTP request library for developers.",
                }]},
                error="",
                source_url="https://example.test",
                fetched_at=RECENT,
            )

    def model(prompt, _system):
        calls["model"] += 1
        if calls.get("replay"):
            raise AssertionError("live model")
        if "CANDIDATE, ALREADY_EXISTS, or NO_GO" in prompt:
            return json.dumps({
                "verdict": "NO_GO",
                "reason": "Nothing else.",
                "problem_quote": "Lists objects in a bucket for developers.",
                "alternative_repo": None,
            })
        if "same_core_job" in prompt or "Alternative:" in prompt:
            return json.dumps({
                "same_core_job": True,
                "same_input_output": True,
                "same_target_user": True,
                "covers_main_features": True,
                "same_problem": True,
                "quotes": ["HTTP request library for developers."],
                "reason": "same job",
            })
        return json.dumps({
            "problem": "Listing a bucket is awkward.",
            "target_user": "developers",
            "category": "http",
            "search_queries": ["bucket browser"],
            "quotes": ["Lists objects in a bucket for developers."],
        })

    from scripts.judge_repo import judge

    first = judge(
        "acme/widget",
        out_dir=tmp_path / "out-a",
        log_dir=tmp_path / "logs-a",
        run_id="pipe-a",
        getter=getter,
        relevance_client=RelevanceClient(cache_dir=tmp_path / "model-a", mock_handler=model),
        plan_rescue=True,
        commit="abc123",
        toolbox=_Box(),
    )
    calls["replay"] = True
    before = dict(calls)
    second = judge(
        "acme/widget",
        out_dir=tmp_path / "out-b",
        log_dir=tmp_path / "logs-b",
        run_id="pipe-b",
        getter=getter,
        relevance_client=RelevanceClient(cache_dir=tmp_path / "model-b", mock_handler=model),
        plan_rescue=True,
        commit="abc123",
        toolbox=_Box(),
    )
    assert calls["github"] == before["github"]
    assert calls["tool"] == before["tool"]
    assert calls["model"] == before["model"]
    assert second["verdict"] == first["verdict"]
    assert second["reason"].split("Method:")[0] == first["reason"].split("Method:")[0]
    assert second["rescue_plans"] == first["rescue_plans"]
    from app.agents.search_snapshot import search_events_hash

    assert search_events_hash(tmp_path / "logs-a" / "trace-pipe-a.jsonl") == search_events_hash(
        tmp_path / "logs-b" / "trace-pipe-b.jsonl"
    )
    assert "SEARCH_SNAPSHOT" in (tmp_path / "logs-b" / "trace-pipe-b.jsonl").read_text(encoding="utf-8")
    assert first["verdict"] == "ALREADY_EXISTS"
    assert "sindresorhus/got" in first["reason"]
