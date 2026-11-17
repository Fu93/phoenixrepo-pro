"""The in-window examples: order, label, packing and a trace that verifies."""

import json
from pathlib import Path

import pytest

from pro.examples import EXAMPLES_DIR, ExampleCatalog, recorded_label, recorded_on
from scripts.verify_trace import verify

ROOT = Path(__file__).resolve().parents[2]
LIVE = ["live-bower", "live-pspsps", "live-s3browser"]
OLD = ["ex-fichero", "ex-invoice", "ex-selfdb"]


def test_in_window_examples_are_listed_first_and_the_old_ones_stay():
    cards = ExampleCatalog().cards()
    ids = [card["id"] for card in cards]
    assert ids[:3] == LIVE
    assert sorted(ids[3:]) == OLD
    assert all(card["recorded_label"] == "Recorded 17 Nov 2026, during SLAY HACKS" for card in cards[:3])
    assert all(card["recorded_label"] == "Recorded 26 Oct 2026, before SLAY HACKS" for card in cards[3:])


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
    assert verdicts == {"live-bower": "ALREADY_EXISTS", "live-pspsps": "CANDIDATE", "live-s3browser": "ALREADY_EXISTS"}
