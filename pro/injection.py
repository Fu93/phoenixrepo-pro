"""Detect and neutralise prompt-injection text before a model sees a README.

The matchers are deterministic. They do not ask a model whether a paragraph is
an attack. A hit is replaced in the text the judge stores; the original excerpt
is kept only for the responsible-AI panel.
"""

from __future__ import annotations

import re

# Each pattern is a named, case-insensitive search. The names are part of the
# public report, so tests and the UI can refer to them.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore-previous", re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|rules)", re.I)),
    ("disregard-above", re.compile(r"disregard\s+(?:the\s+)?(?:above|previous|prior|system)", re.I)),
    ("role-override", re.compile(r"you\s+are\s+now\b", re.I)),
    ("new-instructions", re.compile(r"(?:new|updated)\s+(?:system\s+)?instructions?\s*:", re.I)),
    ("forced-verdict", re.compile(r"verdict\s+must\s+be\s+[A-Za-z_]+", re.I)),
    ("output-only-verdict", re.compile(r"output\s+only\s+(?:CANDIDATE|ALREADY_EXISTS|NEEDS_REVIEW|NO_GO)", re.I)),
    ("html-comment-instruction", re.compile(r"<!--[\s\S]{0,800}?(?:ignore|instruction|verdict|system\s+prompt)[\s\S]{0,800}?-->", re.I)),
)

NEUTRAL_TOKEN = "[neutralised instruction removed]"


def scan(text: str) -> list[dict[str, str]]:
    """Return non-overlapping findings in document order."""

    raw = text or ""
    found: list[tuple[int, int, str, str]] = []
    for name, pattern in PATTERNS:
        for match in pattern.finditer(raw):
            found.append((match.start(), match.end(), name, match.group(0)))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, str, str]] = []
    cursor = -1
    for start, end, name, excerpt in found:
        if start < cursor:
            continue
        kept.append((start, end, name, excerpt))
        cursor = end
    return [
        {"pattern": name, "excerpt": _excerpt(excerpt), "start": str(start), "end": str(end)}
        for start, end, name, excerpt in kept
    ]


def neutralise(text: str) -> tuple[str, dict[str, object]]:
    """Replace injection spans. The returned text is what a model may read."""

    raw = text or ""
    findings = scan(raw)
    if not findings:
        return raw, {"detected": False, "neutralised": False, "count": 0, "findings": []}
    cleaned = raw
    for finding in reversed(findings):
        start = int(finding["start"])
        end = int(finding["end"])
        cleaned = cleaned[:start] + NEUTRAL_TOKEN + cleaned[end:]
    report = {
        "detected": True,
        "neutralised": True,
        "count": len(findings),
        "findings": [{"pattern": item["pattern"], "excerpt": item["excerpt"]} for item in findings],
    }
    return cleaned, report


def _excerpt(text: str, limit: int = 180) -> str:
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    return one[: limit - 1] + "…"
