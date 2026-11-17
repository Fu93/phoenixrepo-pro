"""The in-window examples: order, label, packing and a trace that verifies."""

import json
from pathlib import Path

import pytest

from pro.examples import EXAMPLES_DIR, ExampleCatalog, recorded_label, recorded_on
from scripts.verify_trace import verify

ROOT = Path(__file__).resolve().parents[2]
LIVE = ["live-bower", "live-pspsps", "live-request", "live-s3browser"]
OLD = ["ex-fichero", "ex-invoice", "ex-selfdb"]


def test_in_window_examples_are_listed_first_and_the_old_ones_stay():
    cards = ExampleCatalog().cards()
    ids = [card["id"] for card in cards]
    assert ids[:4] == LIVE
    assert sorted(ids[4:]) == OLD
    assert all(card["recorded_label"] == "Recorded 17 Nov 2026, during SLAY HACKS" for card in cards[:4])
    assert all(card["recorded_label"] == "Recorded 26 Oct 2026, before SLAY HACKS" for card in cards[4:])


def test_recorded_label_handles_a_missing_or_bad_date():
    assert recorded_label("") == ""
    assert recorded_label("not a date") == ""
    assert recorded_on({"source": "recorded-engine-trace"}) == "2026-10-26"
    assert recorded_label("2026-11-07").endswith("during SLAY HACKS")
    assert recorded_label("2026-11-06").endswith("before SLAY HACKS")


def test_get_carries_the_label():
    assert ExampleCatalog().get("live-pspsps")["recorded_label"] == "Recorded 17 Nov 2026, during SLAY HACKS"
    assert ExampleCatalog().get("ex-selfdb")["recorded_label"] == "Recorded 26 Oct 2026, before SLAY HACKS"


@pytest.mark.parametrize("example_id", LIVE)
def test_live_example_trace_verifies_and_the_json_is_clean(example_id):
    row = json.loads((EXAMPLES_DIR / f"{example_id}.json").read_text(encoding="utf-8"))
    trace = ROOT / row["trace_file"]
    ok, run_hash = verify(trace, expected_run_hash=row["run_hash"])
    assert ok, run_hash
    assert row["quote_verified"] is True
    assert row["recorded_on"] == "2026-11-17"
    blob = json.dumps(row)
    for needle in ("/workspace", "/home/", "/tmp/", "nvapi-", "AIza", "Bearer ", "data/replays", "data/pro-runtime", "Meterless", "Nightshift"):
        assert needle not in blob
    assert [t["action"] for t in row["trace"]][-1] == "RUN_SEAL"


def test_the_three_verdicts_are_the_ones_the_traces_show():
    verdicts = {
        example_id: json.loads((EXAMPLES_DIR / f"{example_id}.json").read_text(encoding="utf-8"))["verdict"]
        for example_id in LIVE
    }
    assert verdicts == {"live-bower": "ALREADY_EXISTS", "live-pspsps": "CANDIDATE", "live-request": "ALREADY_EXISTS", "live-s3browser": "ALREADY_EXISTS"}


SNAPSHOT_EXAMPLES = ["live-bower", "live-request", "live-s3browser"]


@pytest.mark.parametrize("example_id", SNAPSHOT_EXAMPLES)
def test_a_packed_example_serves_a_snapshot_that_verifies(example_id, tmp_path):
    from fastapi.testclient import TestClient

    from app.agents.search_snapshot import file_sha256
    import pro.api as api
    from api.run import app
    from pro.service import ProService

    row = json.loads((EXAMPLES_DIR / f"{example_id}.json").read_text(encoding="utf-8"))
    snap = ROOT / row["snapshot_file"]
    assert file_sha256(snap) == row["snapshot_sha256"]
    from app.agents.search_snapshot import verify_snapshot

    ok, detail = verify_snapshot(ROOT / row["trace_file"], snap)
    assert ok, detail
    api.SERVICE = ProService(tmp_path / "state", settings_fn=lambda: None, examples=ExampleCatalog())
    served = TestClient(app).get(f"/pro/api/results/{example_id}/snapshot")
    assert served.status_code == 200
    assert served.content == snap.read_bytes()
    tampered = bytearray(snap.read_bytes())
    tampered[len(tampered) // 2] ^= 0x01
    bad = ROOT / "pro" / "examples" / "_tamper_check.json"
    try:
        bad.write_bytes(bytes(tampered))
        assert not verify_snapshot(ROOT / row["trace_file"], bad)[0]
    finally:
        bad.unlink(missing_ok=True)
