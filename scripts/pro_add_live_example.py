"""Pack one live PhoenixRepo Pro judgement as a committed example.

    python scripts/pro_add_live_example.py RESULT.json SERVER_TRACE.jsonl --id live-name --title "..." --day 2026-11-17

RESULT.json is the stored result (or a wrapper with a "result" key). SERVER_TRACE.jsonl is the trace
file the server wrote for that run. The trace copy stops at the RUN_SEAL line, which is the line the
run hash names. A model call that is logged after the seal (a rescue plan that timed out) is left out of
the copy and is not part of the sealed chain. The copy has to verify against the run hash, or nothing is written.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_trace import verify  # noqa: E402

EXAMPLES = ROOT / "pro" / "examples"
FORBIDDEN = re.compile(r"/workspace|/home/|/tmp/|nvapi-|AIza|Bearer |ghp_|github_pat_|data/pro-runtime")
DROP = {"cache_key"}


def pack(result_path: Path, trace_path: Path, example_id: str, title: str, day: str, note: str) -> Path:
    raw = json.loads(result_path.read_text(encoding="utf-8"))
    result = raw.get("result") if isinstance(raw.get("result"), dict) else raw
    run_hash = str(result.get("run_hash") or "")
    lines = [line for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    sealed: list[str] = []
    for line in lines:
        sealed.append(line)
        record = json.loads(line)
        if record.get("action") == "RUN_SEAL":
            break
    else:
        raise SystemExit("the trace has no RUN_SEAL line")
    if json.loads(sealed[-1]).get("line_hash") != run_hash:
        raise SystemExit("the RUN_SEAL line is not the run hash in the result")
    dest = EXAMPLES / "traces" / f"{example_id}.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(sealed) + "\n", encoding="utf-8")
    ok, detail = verify(dest, expected_run_hash=run_hash)
    if not ok:
        dest.unlink()
        raise SystemExit(f"trace does not verify: {detail}")
    rel = f"pro/examples/traces/{example_id}.jsonl"
    row = {key: value for key, value in result.items() if key not in DROP}
    row.update(
        {
            "id": example_id,
            "title": title,
            "source": "recorded-live-judge",
            "source_note": note,
            "demo": True,
            "cached": True,
            "recorded_on": day,
            "trace_file": rel,
        }
    )
    if isinstance(row.get("responsibility"), dict):
        row["responsibility"] = {**row["responsibility"], "trace_file": rel}
    text = json.dumps(row, indent=2, ensure_ascii=False) + "\n"
    # An adapter prefix in an error message names an internal route; the page already shows it as "the model route".
    text = re.sub(r"(?i)\b(?:meterless|nightshift) ", "", text)
    hit = FORBIDDEN.search(text) or FORBIDDEN.search(dest.read_text(encoding="utf-8"))
    if hit:
        dest.unlink()
        raise SystemExit(f"refusing to write: found {hit.group(0)!r}")
    out = EXAMPLES / f"{example_id}.json"
    out.write_text(text, encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("result", type=Path)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--day", default="2026-11-17")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    note = args.note or f"Judged live on {args.day}, during SLAY HACKS, with the hosted NVIDIA path. Packed with scripts/pro_add_live_example.py."
    print(pack(args.result, args.trace, args.id, args.title, args.day, note))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
