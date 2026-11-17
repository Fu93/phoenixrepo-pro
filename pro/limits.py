"""Hard caps for hosted PhoenixRepo Pro runs.

A cache hit, a pre-judged example, and a run that never calls a model do not
call reserve(). A run that did call a model spends one daily slot, including
when the verdict is NEEDS_REVIEW or the run errors after that call. Each HTTP
call still counts against the per-run model-call cap.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class LimitError(RuntimeError):
    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class LimitConfig:
    max_model_calls: int = 24
    per_ip_daily: int = 8
    global_daily: int = 40

    @classmethod
    def from_env(cls) -> "LimitConfig":
        return cls(
            max_model_calls=_positive("PHOENIX_MAX_MODEL_CALLS", 24),
            per_ip_daily=_positive("PHOENIX_DAILY_RUNS_PER_IP", 8),
            global_daily=_positive("PHOENIX_DAILY_RUNS_GLOBAL", 40),
        )


def _positive(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class RunLedger:
    """UTC-day counters stored in one JSON file."""

    def __init__(self, path: Path, config: LimitConfig | None = None, *, now=None) -> None:
        self.path = path
        self.config = config or LimitConfig()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()

    def block_reason(self, ip: str) -> str:
        """``per_ip``, ``global``, or ``ok`` when another run that calls a model may start."""

        who = (ip or "unknown").strip() or "unknown"
        with self._lock:
            state = self._state_for_today()
            used_ip = int((state.get("ips") or {}).get(who) or 0)
            used_global = int(state.get("global") or 0)
            if used_ip >= self.config.per_ip_daily:
                return "per_ip"
            if used_global >= self.config.global_daily:
                return "global"
            return "ok"

    def can_reserve(self, ip: str) -> bool:
        """False when this IP or the process is already at the daily cap."""

        return self.block_reason(ip) == "ok"

    def reserve(self, ip: str) -> dict[str, int]:
        """Take one live-run slot for this IP and for the process. Raises LimitError."""

        who = (ip or "unknown").strip() or "unknown"
        with self._lock:
            state = self._state_for_today()
            ips = state.setdefault("ips", {})
            used_ip = int(ips.get(who) or 0)
            used_global = int(state.get("global") or 0)
            if used_ip >= self.config.per_ip_daily:
                raise LimitError(
                    f"Daily run limit reached for this address ({self.config.per_ip_daily}).",
                    kind="per_ip",
                )
            if used_global >= self.config.global_daily:
                raise LimitError(
                    f"Global daily run limit reached ({self.config.global_daily}).",
                    kind="global",
                )
            ips[who] = used_ip + 1
            state["global"] = used_global + 1
            state["day"] = self._today()
            self._save(state)
            return {"ip": ips[who], "global": state["global"]}

    def release(self, ip: str) -> None:
        """Give back a slot when the run never called a model."""

        who = (ip or "unknown").strip() or "unknown"
        with self._lock:
            state = self._state_for_today()
            ips = state.setdefault("ips", {})
            if int(ips.get(who) or 0) > 0:
                ips[who] = int(ips[who]) - 1
            if int(state.get("global") or 0) > 0:
                state["global"] = int(state["global"]) - 1
            state["day"] = self._today()
            self._save(state)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            state = self._state_for_today()
            return {
                "day": state.get("day"),
                "global_used": int(state.get("global") or 0),
                "global_limit": self.config.global_daily,
                "per_ip_limit": self.config.per_ip_daily,
                "max_model_calls": self.config.max_model_calls,
            }

    def _today(self) -> str:
        return self._now().astimezone(timezone.utc).date().isoformat()

    def _state_for_today(self) -> dict:
        state = self._load()
        if state.get("day") != self._today():
            return {"day": self._today(), "global": 0, "ips": {}}
        return state

    def _load(self) -> dict:
        if not self.path.exists():
            return {"day": self._today(), "global": 0, "ips": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"day": self._today(), "global": 0, "ips": {}}
        if not isinstance(data, dict):
            return {"day": self._today(), "global": 0, "ips": {}}
        data.setdefault("ips", {})
        return data

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
