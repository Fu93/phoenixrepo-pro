import json

import httpx
import pytest

from app.adapters.nvidia import NvidiaDirect
from app.agents.relevance import RelevanceLLMError
from config.settings import Settings
from pro.hosted import GEMINI_CHAT_URL, HostedRelevanceClient, provider_mode


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_provider_mode_is_demo_without_keys(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert provider_mode(_settings()) == "demo"
    assert provider_mode(_settings(nvidia_api_key="nv-test")) == "nvidia"
    assert provider_mode(_settings(gemini_api_key="gm-test")) == "gemini"
    assert provider_mode(_settings(nvidia_api_key="nv-test", gemini_api_key="gm-test")) == "nvidia"


def test_nvidia_client_is_used_and_gemini_is_not_called(tmp_path):
    seen = {"nvidia": 0, "gemini": 0}

    def nvidia(request: httpx.Request) -> httpx.Response:
        seen["nvidia"] += 1
        assert request.url.host == "integrate.api.nvidia.com"
        assert request.headers["authorization"] == "Bearer nv-test"
        body = json.loads(request.content)
        assert body["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": "from-nvidia"}}]})

    def gemini(request: httpx.Request) -> httpx.Response:
        seen["gemini"] += 1
        return httpx.Response(200, json={"model": "gemini-2.5-flash", "choices": [{"message": {"content": "from-gemini"}}]})

    client = HostedRelevanceClient(
        nvidia_api_key="nv-test",
        gemini_api_key="gm-test",
        cache_dir=tmp_path,
        max_calls=4,
        nvidia_transport=httpx.MockTransport(nvidia),
        gemini_transport=httpx.MockTransport(gemini),
    )
    text, _requested, served = client.complete("judge this", "system", repo_id="a/b", step="problem")
    assert text == "from-nvidia"
    assert served == "nvidia/nemotron-3-ultra-550b-a55b"
    assert seen == {"nvidia": 1, "gemini": 0}
    assert isinstance(NvidiaDirect(api_key="nv-test", model="nvidia/nemotron-3-ultra-550b-a55b", transport=httpx.MockTransport(nvidia)), NvidiaDirect)


def test_gemini_fallback_runs_when_nvidia_fails(tmp_path):
    seen = {"gemini": 0}

    def nvidia(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "down"})

    def gemini(request: httpx.Request) -> httpx.Response:
        seen["gemini"] += 1
        assert str(request.url) == GEMINI_CHAT_URL
        assert request.headers["authorization"] == "Bearer gm-test"
        return httpx.Response(200, json={"model": "gemini-2.5-flash", "choices": [{"message": {"content": "from-gemini"}}]})

    client = HostedRelevanceClient(
        nvidia_api_key="nv-test",
        gemini_api_key="gm-test",
        cache_dir=tmp_path,
        max_calls=4,
        nvidia_transport=httpx.MockTransport(nvidia),
        gemini_transport=httpx.MockTransport(gemini),
    )
    text, requested, served = client.complete("judge this", "", repo_id="a/b", step="discovery")
    assert text == "from-gemini"
    assert served == "gemini-2.5-flash"
    assert requested == "gemini-2.5-flash"
    assert seen["gemini"] == 1
    assert "gemini" in client.providers_used


def test_per_run_call_cap_blocks_a_second_http_call(tmp_path):
    calls = {"n": 0}

    def nvidia(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "down"})

    def gemini(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"model": "gemini-2.5-flash", "choices": [{"message": {"content": "no"}}]})

    client = HostedRelevanceClient(
        nvidia_api_key="nv-test",
        gemini_api_key="gm-test",
        cache_dir=tmp_path,
        max_calls=1,
        nvidia_transport=httpx.MockTransport(nvidia),
        gemini_transport=httpx.MockTransport(gemini),
    )
    with pytest.raises(RelevanceLLMError, match="max model calls"):
        client.complete("once", "", repo_id="a/b", step="problem")
    assert calls["n"] == 1


def test_hosted_response_cache_does_not_call_again(tmp_path):
    calls = {"n": 0}

    def nvidia(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": "cached-body"}}]})

    client = HostedRelevanceClient(
        nvidia_api_key="nv-test",
        cache_dir=tmp_path,
        max_calls=2,
        nvidia_transport=httpx.MockTransport(nvidia),
    )
    client.complete("same", "sys", repo_id="a/b", step="problem")
    text, _requested, served = client.complete("same", "sys", repo_id="a/b", step="problem")
    assert text == "cached-body"
    assert served.startswith("nvidia/nemotron-")
    assert calls["n"] == 1
    assert client.last_cache_hit is True
