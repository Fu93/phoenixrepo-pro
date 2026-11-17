"""Shape a judge trace into the public result page.

The page shows the verdict, the quotes, the alternatives, and the hash chain.
It does not add a new opinion about the repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.trace_log import canonical_body

MODEL_DECIDES = [
    "What the repository's problem and user are, in the problem step.",
    "Whether a found alternative is the same job, and at what overlap.",
    "A draft rescue plan, when gate 1 has already returned CANDIDATE and a model call is still allowed.",
]

CODE_DECIDES = [
    "A quote is kept only when quote_in_text finds it in the cited text.",
    "A full-overlap alternative blocks only when the maturity and licence checks also pass. A failed check is recorded under must-beat and does not block.",
    "BUDGET_EXHAUSTED cannot produce CANDIDATE. A search that stops early is NEEDS_REVIEW or UNJUDGED.",
    "Prompt-injection spans in a README are removed before the text is stored for the model.",
    "The trace hash. Each line's line_hash is SHA-256 of the canonical JSON body, and the next line's prev_hash must equal it.",
    "The per-run model-call cap and the daily run caps. A cache hit spends neither.",
]

STATIC_LIMITS = [
    "A verified quote shows those words were in the cited text. It does not show that the alternative is a good product, or that the verdict is the one a human would write.",
    "The scored set had four false passes: CANDIDATE where the human label's only acceptable verdict was ALREADY_EXISTS (Invoice generator, ScrapeServ, raindrop-wordpress-linkblog-sync, go-file-sync). They are listed in docs/JUDGES.md.",
    "The hash chain shows the recorded lines were not edited after sealing. It does not show the lines are true.",
    "A Gemini fallback is not a Nemotron answer. The trace flags MODEL_MISMATCH when the served id is outside nvidia/nemotron-*.",
    "On a free Render service the result cache lives on an ephemeral disk and is empty after a restart or spin-down.",
]


def parse_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"text": value}
        return parsed if isinstance(parsed, dict) else {"text": value}
    return {}


def trace_events(path: Path) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            continue
        summary = parse_summary(record.get("output_summary"))
        events.append(
            {
                "action": str(record.get("action") or record.get("event") or ""),
                "status": str(record.get("status") or ""),
                "timestamp": str(record.get("timestamp") or ""),
                "prev_hash": str(record.get("prev_hash") or ""),
                "line_hash": str(record.get("line_hash") or ""),
                "canonical": canonical_body(record),
                "detail": detail_for(str(record.get("action") or ""), summary),
            }
        )
    return events


def detail_for(action: str, summary: dict[str, Any]) -> str:
    if action == "DISCOVERY_FILTER":
        return f"{summary.get('verdict') or ''} {summary.get('reason') or ''}".strip()[:240]
    if action == "PROBLEM_STATEMENT":
        return str(summary.get("problem") or "")[:240]
    if action == "LLM_CALL":
        served = summary.get("served_model") or ""
        provider = summary.get("provider") or ""
        step = ""
        return " ".join(part for part in (str(served), str(provider), step) if part)[:240]
    if action == "RUN_SEAL":
        return "Run seal. The line hash is the run hash."
    text = summary.get("text")
    if isinstance(text, str) and text:
        return text[:240]
    if summary:
        return json.dumps(summary, ensure_ascii=False)[:240]
    return ""


def discovery_from_events(events: list[dict[str, str]]) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("action") == "DISCOVERY_FILTER" and event.get("canonical"):
            record = json.loads(event["canonical"])
            return parse_summary(record.get("output_summary"))
    return {}


def problem_from_events(events: list[dict[str, str]]) -> str:
    for event in events:
        if event.get("action") != "PROBLEM_STATEMENT":
            continue
        record = json.loads(event["canonical"])
        summary = parse_summary(record.get("output_summary"))
        return str(summary.get("problem") or "")
    return ""


def _plain_name(name: str) -> str:
    text = (name or "").strip()
    if text.startswith("web:"):
        return text[4:]
    return text


def _url_for(name: str) -> str:
    text = _plain_name(name)
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.count("/") == 1 and " " not in text:
        return f"https://github.com/{text}"
    return ""


def _row_state(reason: str, blocked_flag: bool) -> str:
    text = (reason or "").casefold()
    if any(phrase in text for phrase in ("couldn't verify", "could not verify", "not found", "unverified")):
        return "could_not_verify"
    if "did not block" in text or not blocked_flag:
        return "did_not_block"
    return "blocked"


def annotate_alternative(row: dict[str, Any], *, blocked_flag: bool) -> dict[str, Any]:
    """Badges follow the check, not the mere presence of a quote string."""

    reason = str(row.get("reason") or "")
    state = _row_state(reason, blocked_flag)
    quote = str(row.get("quote") or "")
    verified = bool(quote) and state != "could_not_verify"
    noted = dict(row)
    noted["quote"] = quote
    noted["quote_verified"] = verified
    noted["blocked"] = state == "blocked"
    noted["state"] = state
    return noted


def plain_verdict_reason(verdict: str, reason: str) -> str:
    """One sentence a reader can use without the engine's method string."""

    text = (reason or "").split("Method:")[0].strip().rstrip(".")
    low = text.casefold()
    if verdict == "NOT_ENOUGH":
        return text or "There is not enough public text to judge this repository."
    if "couldn't verify" in low or "could not verify" in low:
        return "A named alternative was dropped because its quote was not in that source. The verdict does not rest on it."
    if "not found" in low or "unverified" in low:
        return "A quote the model offered was not in the source, so that claim was not used."
    if verdict == "ALREADY_EXISTS":
        return "A maintained alternative already does this job. The quote was found in that alternative's own text."
    if verdict == "CANDIDATE":
        return "Nothing that was checked already does this job. Listed quotes were found in the sources named."
    if verdict == "NEEDS_REVIEW":
        return text or "The check did not finish cleanly, so this is not a pass and not a refusal."
    if verdict == "NEEDS_PRIOR_ART":
        return "Not enough alternatives were checked to decide."
    if verdict == "UNJUDGED":
        return "The model call did not finish, so there is no verdict."
    if verdict == "NO_GO":
        return text or "The check says not to revive this repository."
    return text or "No reason was stored."


def alternatives_from_discovery(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in summary.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        name = _plain_name(str(item.get("alternative") or item.get("name") or ""))
        rows.append(annotate_alternative(
            {
                "name": name,
                "url": _url_for(str(item.get("url") or name)),
                "overlap": str(item.get("overlap") or ""),
                "quote": str(item.get("quote") or ""),
                "reason": str(item.get("reason") or ""),
            },
            blocked_flag=True,
        ))
    for item in summary.get("must_beat") or []:
        if not isinstance(item, dict):
            continue
        name = _plain_name(str(item.get("alternative") or ""))
        rows.append(annotate_alternative(
            {
                "name": name,
                "url": _url_for(name),
                "overlap": str(item.get("overlap") or ""),
                "quote": str(item.get("quote") or ""),
                "reason": str(item.get("reason") or ""),
            },
            blocked_flag=False,
        ))
    return rows


def primary_quote(verdict: str, reason: str, alternatives: list[dict[str, Any]]) -> str:
    for row in alternatives:
        if row.get("blocked") and row.get("quote_verified") and row.get("quote"):
            return str(row["quote"])
    for row in alternatives:
        if row.get("quote_verified") and row.get("quote"):
            return str(row["quote"])
    low = (reason or "").casefold()
    if any(phrase in low for phrase in ("not found", "unverified", "couldn't verify", "could not verify")):
        return ""
    marker = "quote: '"
    if marker in reason:
        return reason.split(marker, 1)[1].split("'", 1)[0]
    return ""


def responsibility(result: dict[str, Any]) -> dict[str, Any]:
    limitations = list(STATIC_LIMITS)
    human = result.get("human_label") or {}
    if human.get("false_pass"):
        limitations.insert(
            0,
            "This recorded verdict is a known false pass: the engine said "
            f"{result.get('verdict')} and the human label only accepts "
            f"{', '.join(human.get('acceptable') or [])}.",
        )
    return {
        "model_decides": list(MODEL_DECIDES),
        "code_decides": list(CODE_DECIDES),
        "injection": result.get("injection") or {
            "detected": False,
            "neutralised": False,
            "count": 0,
            "findings": [],
        },
        "limitations": limitations,
        "verify": [
            "On the trace page, Verify hashes each canonical line with SHA-256 in the browser and checks that each prev_hash equals the previous line_hash.",
            "Tamper edits a copy of one line in the browser. The committed file is not written.",
            "From a checkout: python scripts/verify_trace.py TRACE --expected-run-hash HASH",
        ],
        "run_hash": result.get("run_hash") or "",
        "trace_file": result.get("trace_file") or "",
    }


def public_trace_ref(value: object) -> str:
    """A trace name the browser can show. Never an absolute server path."""

    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    path = Path(text)
    drive = len(text) > 2 and text[1] == ":"
    if drive or path.is_absolute() or text.startswith("~") or ".." in path.parts:
        return path.name
    return text


def public_result(fields: dict[str, Any]) -> dict[str, Any]:
    """Attach the responsible-AI block. Does not drop caller fields."""

    result = dict(fields)
    result["trace_file"] = public_trace_ref(result.get("trace_file"))
    rows = []
    for row in result.get("alternatives") or []:
        if isinstance(row, dict) and "state" not in row:
            rows.append(annotate_alternative(row, blocked_flag=bool(row.get("blocked"))))
        elif isinstance(row, dict):
            rows.append(row)
    if rows:
        result["alternatives"] = rows
    if not result.get("plain_reason"):
        result["plain_reason"] = plain_verdict_reason(str(result.get("verdict") or ""), str(result.get("reason") or ""))
    verified_rows = [row for row in result.get("alternatives") or [] if row.get("quote_verified")]
    result["quote_verified"] = bool(result.get("quote")) and bool(verified_rows or (
        result.get("quote") and not any(
            phrase in str(result.get("reason") or "").casefold()
            for phrase in ("not found", "unverified", "couldn't verify", "could not verify")
        )
    ))
    if result.get("quote") and not result["quote_verified"]:
        result["quote"] = ""
    result["responsibility"] = responsibility(result)
    return result
