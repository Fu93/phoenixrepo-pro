"""Parallel events keep the file order equal to the hash chain."""

from concurrent.futures import ThreadPoolExecutor

from app.trace_log import HackathonTrace
from scripts.verify_trace import verify


def test_threaded_events_verify(tmp_path):
    trace = HackathonTrace("run-threads", tmp_path)
    trace.event(agent_name="orchestrator", action="RUN_START", input_summary={}, output_summary="x", status="ok")

    def one(i):
        trace.event(agent_name="toolbox", action="TOOL_CALL", input_summary={"i": i}, output_summary="ok", status="ok")

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(one, range(300)))
    run_hash = trace.seal()
    ok, detail = verify(trace.path, expected_run_hash=run_hash)
    assert ok, detail
