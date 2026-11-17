"""Problem-extraction quotes stay verbatim, with a looser match and one retry."""

import json

from app.agents.relevance import RelevanceClient, extract_problem_statement, quote_in_text


def test_quote_match_normalises_markdown_case_and_straight_quotes():
    readme = 'Use the **Web GUI** to [print](https://example.com/print) a "label" today.'
    assert quote_in_text("use the Web GUI to print a label today.", readme)
    assert quote_in_text("https://example.com/print", readme)
    assert not quote_in_text("use the Web GUI to scan a label today.", readme)


def test_problem_extraction_retries_once_with_the_readme_and_keeps_a_real_quote(tmp_path):
    readme = "Compress files quickly using multithreaded gzip."
    calls = {"n": 0}

    def fake(prompt, _system):
        calls["n"] += 1
        if calls["n"] == 1:
            quote = "petabyte-scale streaming compression"
        else:
            assert "README Excerpt" in prompt
            assert readme in prompt
            quote = "Compress files quickly using multithreaded gzip."
        return json.dumps({
            "problem": "Files are large.",
            "target_user": "people with files",
            "category": "compression",
            "search_queries": ["gzip tool"],
            "quotes": [quote],
        })

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake)
    data, meta = extract_problem_statement(
        {"repo_id": "a/b", "description": "A compressor.", "readme_excerpt": readme},
        client=client,
    )
    assert calls["n"] == 2
    assert meta["fallback"] is False
    assert data["quotes"] == ["Compress files quickly using multithreaded gzip."]


def test_problem_extraction_still_rejects_a_quote_that_is_not_in_the_source(tmp_path):
    calls = {"n": 0}

    def fake(_prompt, _system):
        calls["n"] += 1
        return json.dumps({
            "problem": "Invented.",
            "target_user": "nobody",
            "category": "fiction",
            "search_queries": ["nope"],
            "quotes": ["this sentence is not in the readme"],
        })

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake)
    data, meta = extract_problem_statement(
        {"repo_id": "a/b", "description": "A compressor.", "readme_excerpt": "Compress files quickly."},
        client=client,
    )
    assert data is None
    assert calls["n"] == 2
    assert meta["fallback"] is False
    assert "this sentence is not in the readme" in meta["error"]


def test_a_quote_that_already_matches_is_not_retried(tmp_path):
    calls = {"n": 0}
    readme = "Compress files quickly using multithreaded gzip."

    def fake(_prompt, _system):
        calls["n"] += 1
        return json.dumps({
            "problem": "Files are large.",
            "target_user": "people with files",
            "category": "compression",
            "search_queries": ["gzip tool"],
            "quotes": ["multithreaded gzip"],
        })

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake)
    data, _meta = extract_problem_statement(
        {"repo_id": "a/b", "readme_excerpt": readme},
        client=client,
    )
    assert calls["n"] == 1
    assert data["quotes"] == ["multithreaded gzip"]
