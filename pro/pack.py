"""Build the three committed examples from recorded engine output.

This does not call a model. It reads replay traces, the human labels, and the
fichero rescue plan that gate 2 already wrote. Invoice has no committed rescue
file; the packer asks the existing deterministic planner to draft one from the
recorded evidence and labels that draft as such.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.rescue_plan import deterministic_plan, evidence_index
from pro.examples import EXAMPLES_DIR
from pro.present import (
    alternatives_from_discovery,
    discovery_from_events,
    primary_quote,
    problem_from_events,
    public_result,
    trace_events,
)
from scripts.verify_trace import verify

ROOT = Path(__file__).resolve().parents[1]

SPECS = (
    {
        "id": "ex-selfdb",
        "replay": "selfdb-io-selfdb",
        "title": "SelfDB — recorded ALREADY_EXISTS",
        "rescue_file": None,
    },
    {
        "id": "ex-invoice",
        "replay": "tharani-2006-invoice-generator-application",
        "title": "Invoice generator — recorded CANDIDATE, a known false pass",
        "rescue_file": None,
    },
    {
        "id": "ex-fichero",
        "replay": "0xmh-fichero-printer",
        "title": "fichero-printer — recorded CANDIDATE with a rescue plan",
        "rescue_file": "docs/rescue/0xMH__fichero-printer.rescue.json",
    },
)


def _labels() -> dict[str, dict]:
    path = ROOT / "data" / "eval" / "gate1_labels.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(case["repo_id"]).lower(): case for case in data.get("cases") or []}


def _human(case: dict | None, verdict: str) -> dict:
    if not case:
        return {}
    acceptable = [str(item) for item in (case.get("acceptable") or [])]
    evidence = []
    for item in case.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        evidence.append(
            {
                "role": str(item.get("role") or ""),
                "url": str(item.get("source_url") or ""),
                "quote": str(item.get("quote") or ""),
            }
        )
    return {
        "acceptable": acceptable,
        "why": str(case.get("why") or ""),
        "audit_class": str((case.get("audit") or {}).get("class") or ""),
        "false_pass": bool(acceptable) and verdict not in acceptable,
        "evidence": evidence,
    }


def _plan_from_discovery(repo_id: str, problem: str, discovery: dict) -> dict:
    text = problem.strip() or repo_id
    row = {
        "repo_id": repo_id,
        "verdict": "CANDIDATE",
        "evidence": [
            {
                "id": f"candidate:{repo_id}",
                "text": text,
                "url": f"https://github.com/{repo_id}",
                "content_hash": "",
            }
        ],
        "must_beat": [
            {
                "alternative": item.get("alternative"),
                "reason": item.get("reason") or "",
                "quote": item.get("quote") or "",
                "overlap": item.get("overlap") or "",
            }
            for item in (discovery.get("must_beat") or [])
            if isinstance(item, dict)
        ],
    }
    plan = deterministic_plan(row, evidence_index(row))
    plan["repo_id"] = repo_id
    plan["drafted_by"] = "deterministic"
    plan["source"] = "app.rescue_plan.deterministic_plan, from the recorded DISCOVERY_FILTER evidence"
    plan["source_note"] = (
        "The original run did not commit a rescue-plan file. This plan was built later, "
        "without a model, from the quotes that run already stored."
    )
    return plan


def build_example(spec: dict, labels: dict[str, dict]) -> dict:
    replay = ROOT / "data" / "replays" / spec["replay"]
    record = json.loads((replay / "record.json").read_text(encoding="utf-8"))
    trace_path = replay / "trace.jsonl"
    ok, run_hash = verify(trace_path, expected_run_hash=(record.get("recorded_run") or {}).get("run_hash"))
    if not ok:
        raise RuntimeError(f"{spec['id']} trace failed verification: {run_hash}")
    events = trace_events(trace_path)
    discovery = discovery_from_events(events)
    verdict = str(discovery.get("verdict") or (record.get("verdict") or {}).get("verdict") or "")
    reason = str(discovery.get("reason") or (record.get("verdict") or {}).get("reason") or "")
    alternatives = alternatives_from_discovery(discovery)
    repo_id = str(record.get("repo_id") or "")
    problem = problem_from_events(events)
    if spec["rescue_file"]:
        rescue = json.loads((ROOT / spec["rescue_file"]).read_text(encoding="utf-8"))
        rescue["source"] = spec["rescue_file"]
        rescue_note = "Committed gate-2 rescue plan. Items were checked against evidence when that file was written."
    elif verdict == "CANDIDATE":
        rescue = _plan_from_discovery(repo_id, problem, discovery)
        rescue_note = rescue.get("source_note") or ""
    else:
        rescue = None
        rescue_note = "Gate 2 writes a rescue plan only after CANDIDATE. This verdict has no plan."
    human = _human(labels.get(repo_id.lower()), verdict)
    return public_result(
        {
            "id": spec["id"],
            "title": spec["title"],
            "repo_id": repo_id,
            "repo_url": f"https://github.com/{repo_id}",
            "commit": "",
            "verdict": verdict,
            "reason": reason,
            "quote": primary_quote(verdict, reason, alternatives),
            "alternatives": alternatives,
            "problem": problem,
            "run_id": str((record.get("recorded_run") or {}).get("run_id") or ""),
            "run_hash": run_hash,
            "trace_file": str(trace_path.relative_to(ROOT)),
            "trace": events,
            "rescue": rescue,
            "rescue_note": rescue_note,
            "human_label": human,
            "injection": {
                "detected": False,
                "neutralised": False,
                "count": 0,
                "findings": [],
                "note": (
                    "This README was judged before the neutraliser existed. "
                    "The injection demo on the home page runs the same detector on a fixture README."
                ),
            },
            "provider": "recorded",
            "source": "recorded-engine-trace",
            "source_note": (
                f"Packed from data/replays/{spec['replay']} without a new model call. "
                "The judgement itself is from the pre-SLAY HACKS engine."
            ),
            "demo": True,
            "cached": True,
            "model_calls": 0,
        }
    )


def build_all() -> list[dict]:
    labels = _labels()
    return [build_example(spec, labels) for spec in SPECS]


def write_all(directory: Path | None = None) -> list[Path]:
    target = directory or EXAMPLES_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for example in build_all():
        path = target / f"{example['id']}.json"
        path.write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return written
