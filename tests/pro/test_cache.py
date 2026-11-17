from pro.cache import ResultCache, cache_key, permalink_for


def test_cache_is_keyed_by_repo_and_commit_and_a_repeat_is_a_read(tmp_path):
    cache = ResultCache(tmp_path)
    first = cache.put("Owner/Name", "abc123", {"verdict": "CANDIDATE", "reason": "kept"})
    assert first["id"] == permalink_for("Owner/Name", "abc123")
    assert first["cache_key"] == cache_key("owner/name", "abc123")
    again = cache.get("owner/name", "abc123")
    assert again["verdict"] == "CANDIDATE"
    assert cache.get("owner/name", "def456") is None
    assert cache_key("Owner/Name", "abc123") == cache_key("owner/name", "abc123")
    assert cache_key("owner/name", "abc123") != cache_key("owner/name", "def456")
