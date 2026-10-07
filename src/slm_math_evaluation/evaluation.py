"""Pure final-answer extraction and the two pinned scoring boundaries."""

from __future__ import annotations

import re
from typing import Any


EXTRACTOR_ID = "boxed-last-v1"
EXTRACTOR_VERSION = "1.0.0"
FALLBACK_RULES = ("fallback-final-number-v1",)
PRIMARY_PIN = "7ecc794703b2877f63226f2477a49b34f9b25163"
SECONDARY_PIN = "0.8.0"


def split_reasoning(raw_output: str) -> tuple[str | None, str]:
    closing = raw_output.rfind("</think>")
    opening = raw_output.rfind("<think>")
    if opening < 0:
        return None, raw_output
    if opening > closing:
        return raw_output[opening:], ""
    return raw_output[:closing], raw_output[closing + len("</think>"):]


def _balanced_boxes(final: str) -> list[str]:
    answers: list[str] = []
    start = 0
    while (marker := final.find(r"\boxed{", start)) >= 0:
        begin = marker + len(r"\boxed{")
        depth = 1
        cursor = begin
        while cursor < len(final) and depth:
            if final[cursor] == "{":
                depth += 1
            elif final[cursor] == "}":
                depth -= 1
            cursor += 1
        if depth == 0:
            answers.append(final[begin:cursor - 1].strip())
        start = begin
    return answers


def extract(final: str, truncated: bool) -> dict[str, Any]:
    boxes = _balanced_boxes(final)
    if boxes:
        return {"answer": boxes[-1], "rule": "boxed_last", "status": "ok"}
    # The fallback order is part of EXTRACTOR_VERSION.
    match = re.search(r"(?:final\s+(?:answer|number)\s*(?:is|:)\s*)([-+]?\d+(?:\.\d+)?)\b", final, re.I)
    if match:
        return {"answer": match.group(1), "rule": FALLBACK_RULES[0], "status": "fallback"}
    return {"answer": None, "rule": "none", "status": "truncated" if truncated else "no_answer"}


def extract_raw(raw_output: str, truncated: bool) -> tuple[bool, dict[str, Any]]:
    thinking, final = split_reasoning(raw_output)
    return thinking is not None, extract(final, truncated)


def score_primary(answer: str | None, reference: str) -> dict[str, Any]:
    if answer is None:
        correct = False
    else:
        from ._prm800k_grader.grader import grade_answer
        correct = bool(grade_answer(answer, reference))
    return {"correct": correct, "scorer_id": "prm800k-grader", "scorer_pin": PRIMARY_PIN}


def score_secondary(answer: str | None, reference: str) -> dict[str, Any]:
    if answer is None:
        correct = False
    else:
        from math_verify import parse, verify
        # MATH answer fields are expressions, not model prose. Delimit them as
        # math so LatexExtractionConfig can parse symbolic answers such as x^2.
        gold = parse(f"${reference.strip().strip('$')}$")
        prediction = parse(f"${answer.strip().strip('$')}$")
        correct = bool(verify(gold, prediction))
    return {"correct": correct, "scorer_id": "math-verify", "scorer_pin": SECONDARY_PIN}
