"""Turn yes/no overlap answers into full, partial, or none.

The model does not choose the word. A second read happens only when the first
read is one answer away from full, and the two reads have to agree.
"""

from __future__ import annotations

from typing import Any

CRITERIA = ("same_core_job", "same_input_output", "same_target_user", "covers_main_features")
_YES = {"yes", "true", "y"}
_NO = {"no", "false", "n"}


def _bit(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in _YES:
            return True
        if text in _NO:
            return False
    return None


def rubric_answers(entry: dict[str, Any]) -> dict[str, bool] | None:
    """The four answers, or None when the reply did not include them."""

    if not isinstance(entry, dict):
        return None
    if "same_core_job" not in entry:
        return None
    answers: dict[str, bool] = {}
    for key in CRITERIA:
        bit = _bit(entry.get(key))
        if bit is None:
            return None
        answers[key] = bit
    return answers


def derive_overlap(answers: dict[str, bool]) -> str:
    if not answers["same_core_job"]:
        return "none"
    rest = ("same_input_output", "same_target_user", "covers_main_features")
    if all(answers[key] for key in rest):
        return "full"
    return "partial"


def is_borderline(answers: dict[str, bool]) -> bool:
    """One criterion short of full. A clear full, a clear partial, or a miss is not re-asked."""

    if not answers["same_core_job"]:
        return False
    missing = sum(
        1
        for key in ("same_input_output", "same_target_user", "covers_main_features")
        if not answers[key]
    )
    return missing == 1


def settle_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Copy the entry with overlap set from the rubric when the four answers are present."""

    if entry.get("overlap_reads") in {"agree", "disagree"}:
        return dict(entry)
    settled = dict(entry)
    answers = rubric_answers(entry)
    if answers is None:
        settled["overlap"] = str(entry.get("overlap") or "none").strip().lower() or "none"
        return settled
    settled["rubric"] = answers
    settled["overlap"] = derive_overlap(answers)
    settled["same_problem"] = answers["same_core_job"]
    settled["borderline"] = is_borderline(answers)
    return settled


RUBRIC_FIELDS = (
    "Answer these four questions true or false, about the alternative versus the candidate: "
    "same_core_job (the same core job), same_input_output (the same kind of input and output), "
    "same_target_user (the same kind of user), covers_main_features (it covers the candidate README's main feature list). "
    "Do not decide the words full or partial. Code derives that label from these four answers."
)
