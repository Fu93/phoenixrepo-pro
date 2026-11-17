"""Hard caps for hosted PhoenixRepo Pro runs.

A cache hit and a pre-judged example do not call reserve(). A live run that
passes the cap spends one daily slot even if the model later fails. Each HTTP
call to a model is counted separately against the per-run call cap by the
hosted client.
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

    def reserve(self, ip: str) -> dict[str, int]:
        """Take one live-run slot for this IP and for the process. Raises LimitError."""

        who = (ip or "unknown").strip() or "unknown"
        with self._lock:
            state = self._load()
            day = self._today()
            if state.get("day") != day:
                state = {"day": day, "global": 0, "ips": {}}
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
            state["day"] = day
            self._save(state)
            return {"ip": ips[who], "global": state["global"]}

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            state = self._load()
            if state.get("day") != self._today():
                state = {"day": self._today(), "global": 0, "ips": {}}
            return {
                "day": state.get("day"),
                "global_used": int(state.get("global") or 0),
                "global_limit": self.config.global_daily,
                "per_ip_limit": self.config.per_ip_daily,
                "max_model_calls": self.config.max_model_calls,
            }

    def _today(self) -> str:
        return self._now().astimezone(timezone.utc).date().isoformat()

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
