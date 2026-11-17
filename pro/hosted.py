"""Hosted model calls for PhoenixRepo Pro.

Primary: the existing NVIDIA OpenAI-compatible client (NvidiaDirect).
Optional fallback: Gemini's OpenAI-compatible endpoint when GEMINI_API_KEY is set.
Neither path reads METERLESS_RUNTIME_TOKEN. Demo mode is the absence of both keys
and is decided by provider_mode(), not by this client.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx

from app.adapters.meterless import message_text
from app.adapters.nvidia import DEFAULT_BASE_URL, NvidiaDirect, NvidiaError
from app.agents.relevance import RelevanceClient, RelevanceLLMError
from config.settings import Settings

DEFAULT_NEMOTRON = "nvidia/nemotron-3-ultra-550b-a55b"
DEFAULT_GEMINI = "gemini-2.5-flash"
GEMINI_CHAT_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def provider_mode(settings: Settings) -> str:
    """nvidia when an NVIDIA key is set, else gemini, else demo."""

    if settings.nvidia_api_key:
        return "nvidia"
    if settings.gemini_api_key:
        return "gemini"
    return "demo"


def nemotron_model_id(settings: Settings) -> str:
    chosen = (settings.nvidia_model or "").strip()
    return chosen or DEFAULT_NEMOTRON


class HostedRelevanceClient(RelevanceClient):
    """RelevanceClient that posts to NVIDIA, then Gemini, and never to Nightshift."""

    def __init__(
        self,
        *,
        nvidia_api_key: str = "",
        nvidia_model: str = DEFAULT_NEMOTRON,
        nvidia_base_url: str = DEFAULT_BASE_URL,
        gemini_api_key: str = "",
        gemini_model: str = DEFAULT_GEMINI,
        max_calls: int = 24,
        cache_dir=None,
        nvidia_transport: httpx.BaseTransport | None = None,
        gemini_transport: httpx.BaseTransport | None = None,
        on_step=None,
        timeout_seconds: float = 90,
    ) -> None:
        super().__init__(
            token="hosted-provider",
            model=nvidia_model or DEFAULT_NEMOTRON,
            cache_dir=cache_dir,
            nemotron_policy="label",
            nvidia_api_key=nvidia_api_key or None,
            nvidia_base_url=nvidia_base_url,
            nvidia_model=nvidia_model or DEFAULT_NEMOTRON,
            wall_clock_timeout=timeout_seconds,
        )
        self._nvidia_key = nvidia_api_key or ""
        self._gemini_key = gemini_api_key or ""
        self._gemini_model = gemini_model or DEFAULT_GEMINI
        self.max_calls = max_calls
        self._http_calls = 0
        self._nvidia_transport = nvidia_transport
        self._gemini_transport = gemini_transport
        self._on_step = on_step
        self.providers_used: list[str] = []

    def _complete(
        self,
        prompt: str,
        system_prompt: str = "",
        repo_id: str = "",
        step: str = "",
        call_budget_s: float | None = None,
    ) -> tuple[str, str, str]:
        if self.mock_handler is not None:
            return super()._complete(
                prompt, system_prompt, repo_id=repo_id, step=step, call_budget_s=call_budget_s,
            )
        model = self.nvidia_model or self.model
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        cached = self._read_hosted_cache(model, system_prompt, prompt)
        if cached is not None:
            self._observe(
                model, cached["served_model"], repo_id, step,
                provider=cached["provider"], cache_hit=True,
            )
            self.last_cache_hit = True
            return cached["response_text"], model, cached["served_model"]

        errors: list[str] = []
        if self._nvidia_key:
            self._charge(step, "nvidia")
            try:
                text, served = self._call_nvidia(messages, model)
            except (NvidiaError, httpx.HTTPError, RelevanceLLMError) as exc:
                errors.append(f"nvidia: {exc}")
                self._observe(model, "", repo_id, step, status="error", provider="nvidia-direct")
            else:
                self._write_hosted_cache(model, system_prompt, prompt, text, served, "nvidia-direct")
                self._observe(model, served, repo_id, step, provider="nvidia-direct")
                self.providers_used.append("nvidia-direct")
                return text, model, served
        if self._gemini_key:
            self._charge(step, "gemini")
            try:
                text, served = self._call_gemini(messages)
            except (httpx.HTTPError, RelevanceLLMError, ValueError) as exc:
                errors.append(f"gemini: {exc}")
                self._observe(self._gemini_model, "", repo_id, step, status="error", provider="gemini")
            else:
                self._write_hosted_cache(model, system_prompt, prompt, text, served, "gemini")
                self._observe(self._gemini_model, served, repo_id, step, provider="gemini")
                self.providers_used.append("gemini")
                return text, self._gemini_model, served
        detail = "; ".join(errors) or "no hosted model key is configured"
        raise RelevanceLLMError(f"hosted model call failed: {detail}")

    def _charge(self, step: str, provider: str) -> None:
        if self._http_calls >= self.max_calls:
            raise RelevanceLLMError(
                f"max model calls per run ({self.max_calls}) reached"
            )
        self._http_calls += 1
        if self._on_step is not None:
            self._on_step(step, provider)

    def _call_nvidia(self, messages: list[dict[str, str]], model: str) -> tuple[str, str]:
        client = NvidiaDirect(
            api_key=self._nvidia_key,
            base_url=self.nvidia_base_url or DEFAULT_BASE_URL,
            model=model,
            timeout_seconds=float(self.wall_clock_timeout or 90),
            max_tokens=int(self.max_tokens),
            transport=self._nvidia_transport,
        )
        text = client.complete(messages, model=model)
        served = client.last_served_model or ""
        return text, served

    def _call_gemini(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        body = {
            "model": self._gemini_model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
            "max_tokens": int(self.max_tokens),
        }
        headers = {
            "Authorization": f"Bearer {self._gemini_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=float(self.wall_clock_timeout or 90), transport=self._gemini_transport) as client:
                response = client.post(GEMINI_CHAT_URL, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise RelevanceLLMError("Gemini chat timed out.") from exc
        if response.status_code in (401, 403):
            raise RelevanceLLMError("Gemini rejected the API key.")
        if response.status_code >= 400:
            raise RelevanceLLMError(f"Gemini chat failed with HTTP {response.status_code}.")
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise RelevanceLLMError("Gemini chat returned non-JSON.") from exc
        served = payload.get("model") if isinstance(payload, dict) else None
        served_text = served.strip() if isinstance(served, str) else ""
        try:
            return message_text(payload), served_text or self._gemini_model
        except Exception as exc:  # noqa: BLE001 — the adapter raises its own error type
            raise RelevanceLLMError("Gemini chat returned no text.") from exc

    def _cache_file(self, model: str, system_prompt: str, prompt: str):
        digest = hashlib.sha256(f"{model}\n{system_prompt}\n{prompt}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"hosted-{digest}.json"

    def _read_hosted_cache(self, model: str, system_prompt: str, prompt: str) -> dict | None:
        path = self._cache_file(model, system_prompt, prompt)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or "response_text" not in data:
            return None
        return data

    def _write_hosted_cache(self, model, system_prompt, prompt, text, served, provider) -> None:
        path = self._cache_file(model, system_prompt, prompt)
        path.write_text(
            json.dumps(
                {"response_text": text, "served_model": served, "provider": provider, "model": model},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def build_hosted_client(settings: Settings, **kwargs) -> HostedRelevanceClient:
    if provider_mode(settings) == "demo":
        raise RelevanceLLMError("hosted judging needs NVIDIA_API_KEY or GEMINI_API_KEY")
    return HostedRelevanceClient(
        nvidia_api_key=settings.nvidia_api_key or "",
        nvidia_model=nemotron_model_id(settings),
        nvidia_base_url=settings.nvidia_base_url,
        gemini_api_key=settings.gemini_api_key or "",
        gemini_model=(settings.gemini_model or DEFAULT_GEMINI).strip() or DEFAULT_GEMINI,
        timeout_seconds=float(settings.nvidia_timeout_seconds or 90),
        **kwargs,
    )
