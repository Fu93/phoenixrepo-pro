"""Hosted model calls for PhoenixRepo Pro.

Primary: the existing NVIDIA OpenAI-compatible client (NvidiaDirect).
Optional fallback: Gemini's OpenAI-compatible endpoint when GEMINI_API_KEY is set.
Neither path reads METERLESS_RUNTIME_TOKEN. Demo mode is the absence of both keys
and is decided by provider_mode(), not by this client.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any

import httpx

from app.adapters.meterless import message_text
from app.adapters.nvidia import DEFAULT_BASE_URL, NvidiaDirect, NvidiaError
from app.agents.relevance import RelevanceClient, RelevanceLLMError
from pro.pace import Pace
from config.settings import Settings

DEFAULT_NEMOTRON = "nvidia/nemotron-3-ultra-550b-a55b"
DEFAULT_GEMINI = "gemini-2.5-flash"
GEMINI_CHAT_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
PROMPT_LIMIT = 12000
_SHARED_PACE: Pace | None = None


def shared_pace() -> Pace:
    global _SHARED_PACE
    if _SHARED_PACE is None:
        workers = int(os.environ.get("PHOENIX_CALL_CONCURRENCY") or "4")
        per_minute = int(os.environ.get("PHOENIX_CALLS_PER_MINUTE") or "40")
        _SHARED_PACE = Pace(concurrency=max(1, workers), per_minute=max(1, per_minute))
    return _SHARED_PACE


def trim_prompt(text: str, limit: int = PROMPT_LIMIT) -> str:
    """Keep the start and the instructions at the end. The middle is the part that grows."""

    if len(text) <= limit:
        return text
    tail = 2400
    head = max(0, limit - tail - 20)
    return text[:head] + "\n...[trimmed]...\n" + text[-tail:]


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
        self.parallel_batches = True
        self.alternative_batch_size = 4
        self.retry_backoff = 1.0
        self._trace_lock = threading.Lock()
        self._pace = shared_pace()

    def _complete(
        self,
        prompt: str,
        system_prompt: str = "",
        repo_id: str = "",
        step: str = "",
        call_budget_s: float | None = None,
    ) -> tuple[str, str, str]:
        from app.agents.search_snapshot import remember_model, replay_model

        model = self.nvidia_model or self.model
        sent = trim_prompt(prompt)
        stored = replay_model(system_prompt, sent, model, 0)
        if stored is not None:
            if stored.get("incomplete"):
                raise RelevanceLLMError("search snapshot is incomplete: no stored model reply for this prompt")
            served = str(stored.get("served_model") or model)
            requested = str(stored.get("requested_model") or model)
            self._observe(requested, served, repo_id, step, provider="nvidia-direct", cache_hit=True)
            self.last_cache_hit = True
            return str(stored.get("response_text") or ""), requested, served
        if self.mock_handler is not None:
            return super()._complete(
                prompt, system_prompt, repo_id=repo_id, step=step, call_budget_s=call_budget_s,
            )
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": trim_prompt(prompt)})
        cached = self._read_hosted_cache(model, system_prompt, prompt)
        if cached is not None:
            remember_model(system_prompt, sent, model, 0, cached["response_text"], model, cached["served_model"])
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
                with self._pace.slot():
                    text, served = self._call_nvidia(messages, model, step)
            except (NvidiaError, httpx.HTTPError, RelevanceLLMError) as exc:
                errors.append(f"nvidia: {exc}")
                self._observe(model, "", repo_id, step, status="error", provider="nvidia-direct")
            else:
                self._write_hosted_cache(model, system_prompt, prompt, text, served, "nvidia-direct")
                remember_model(system_prompt, sent, model, 0, text, model, served)
                self._observe(model, served, repo_id, step, provider="nvidia-direct")
                self.providers_used.append("nvidia-direct")
                return text, model, served
        if self._gemini_key:
            self._charge(step, "gemini")
            try:
                with self._pace.slot():
                    text, served = self._call_gemini(messages)
            except (httpx.HTTPError, RelevanceLLMError, ValueError) as exc:
                errors.append(f"gemini: {exc}")
                self._observe(self._gemini_model, "", repo_id, step, status="error", provider="gemini")
            else:
                self._write_hosted_cache(model, system_prompt, prompt, text, served, "gemini")
                remember_model(system_prompt, sent, self._gemini_model, 0, text, self._gemini_model, served)
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

    def _observe(self, *args, **kwargs):
        with self._trace_lock:
            return super()._observe(*args, **kwargs)

    def _call_nvidia(self, messages: list[dict[str, str]], model: str, step: str = "") -> tuple[str, str]:
        last: Exception | None = None
        timeout = call_timeout(step, float(self.wall_clock_timeout or 45))
        for attempt in range(2):
            client = NvidiaDirect(
                api_key=self._nvidia_key,
                base_url=self.nvidia_base_url or DEFAULT_BASE_URL,
                model=model,
                timeout_seconds=timeout,
                max_tokens=int(self.max_tokens),
                transport=self._nvidia_transport,
            )
            try:
                text = client.complete(messages, model=model)
            except NvidiaError as exc:
                last = exc
                if attempt == 0 and _retryable(str(exc)):
                    time.sleep(self.retry_backoff)
                    continue
                raise
            served = client.last_served_model or ""
            return text, served
        raise RelevanceLLMError(f"hosted model call failed: {last}")

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


RESCUE_TIMEOUT_S = 45.0


def call_timeout(step: str, default: float) -> float:
    """The rescue draft has its own cap, under a minute, so a stuck call cannot dominate the run."""

    if step == "rescue_plan":
        return min(float(default or RESCUE_TIMEOUT_S), RESCUE_TIMEOUT_S)
    return float(default or 90)


def _retryable(message: str) -> bool:
    text = message.casefold()
    return "429" in text or "timed out" in text


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
