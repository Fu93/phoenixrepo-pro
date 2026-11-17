from datetime import datetime, timezone

import pytest

from pro.limits import LimitConfig, LimitError, RunLedger


def test_reserve_counts_ip_and_global_and_resets_on_the_next_utc_day(tmp_path):
    clock = {"now": datetime(2026, 11, 16, 12, tzinfo=timezone.utc)}
    ledger = RunLedger(
        tmp_path / "limits.json",
        LimitConfig(max_model_calls=3, per_ip_daily=2, global_daily=3),
        now=lambda: clock["now"],
    )
    assert ledger.reserve("10.0.0.1") == {"ip": 1, "global": 1}
    assert ledger.reserve("10.0.0.2")["global"] == 2
    assert ledger.reserve("10.0.0.1") == {"ip": 2, "global": 3}
    with pytest.raises(LimitError) as capped:
        ledger.reserve("10.0.0.1")
    assert capped.value.kind == "per_ip"
    with pytest.raises(LimitError) as global_cap:
        ledger.reserve("10.0.0.9")
    assert global_cap.value.kind == "global"
    clock["now"] = datetime(2026, 11, 17, 1, tzinfo=timezone.utc)
    assert ledger.reserve("10.0.0.1") == {"ip": 1, "global": 1}


def test_config_reads_positive_env_overrides(monkeypatch):
    monkeypatch.setenv("PHOENIX_MAX_MODEL_CALLS", "5")
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_PER_IP", "0")
    monkeypatch.setenv("PHOENIX_DAILY_RUNS_GLOBAL", "nope")
    config = LimitConfig.from_env()
    assert config.max_model_calls == 5
    assert config.per_ip_daily == 8
    assert config.global_daily == 40
