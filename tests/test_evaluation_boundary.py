"""Distinctive extraction and scorer cases beyond the integer visible fixtures."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from slm_math_evaluation import analysis
from slm_math_evaluation.evaluation import extract_raw, score_primary, score_secondary
from slm_math_evaluation.evaluation import split_reasoning


class EvaluationBoundaryTests(unittest.TestCase):
    def test_thinking_requires_non_whitespace_block_text(self):
        cases = (
            (r"<think></think>\boxed{2}", "", False),
            ("<think> \n\t </think>" + r"\boxed{2}", " \n\t ", False),
            (r"\boxed{2}", None, False),
            (r"<think>working</think>\boxed{2}", "working", True),
        )
        expected_extraction = {"answer": "2", "rule": "boxed_last", "status": "ok"}
        for raw, thinking_text, present in cases:
            with self.subTest(raw=raw):
                self.assertEqual(split_reasoning(raw)[0], thinking_text)
                self.assertEqual(extract_raw(raw, False), (present, expected_extraction))

    def test_report_thinking_rate_uses_nonempty_flags(self):
        raws = (r"<think></think>\boxed{2}",
                "<think> \n\t </think>" + r"\boxed{2}",
                r"\boxed{2}", r"<think>working</think>\boxed{2}")
        items = []
        for index, raw in enumerate(raws):
            present, extraction = extract_raw(raw, False)
            items.append({"item_id": f"item-{index}", "sample_index": 0,
                          "extraction": extraction, "truncated": False,
                          "thinking_present": present,
                          "verdicts": {"primary": True, "secondary": True}})
        self.assertEqual([item["thinking_present"] for item in items],
                         [False, False, False, True])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = SimpleNamespace(
                items=items, directory=root, protocol={"deviations": []},
                manifest={"tiers": {"smoke": [item["item_id"] for item in items]}},
                metadata={"tier": "smoke", "k": 1, "protocol_version": "v1",
                          "status": "completed", "protocol_sha256": "protocol",
                          "manifest_sha256": "manifest"})
            with patch.object(analysis, "load_run", return_value=run), \
                 patch.object(analysis, "_method_identities", return_value=(
                     {"id": "boxed-last-v1", "version": "1.0.0"},
                     [{"id": "prm800k-grader", "pin": "fixture"},
                      {"id": "math-verify", "pin": "fixture"}])):
                analysis.report("fixture", out_dir=root)
            self.assertEqual(json.loads((root / "report.json").read_text())
                             ["counts"]["thinking_rate"], 0.25)
            self.assertIn("thinking rate: 0.250000", (root / "report.md").read_text())

    def test_last_balanced_nested_box_in_final_text_wins(self):
        raw = r"<think>discard \boxed{1}</think> first \boxed{2}, then \boxed{\frac{1}{2}}"
        thinking, result = extract_raw(raw, False)
        self.assertTrue(thinking)
        self.assertEqual(result, {"answer": r"\frac{1}{2}", "rule": "boxed_last", "status": "ok"})

    def test_unclosed_second_thinking_block_hides_earlier_final(self):
        raw = r"<think>one</think>\boxed{4}<think>check \boxed{5}"
        _, result = extract_raw(raw, True)
        self.assertEqual(result, {"answer": None, "rule": "none", "status": "truncated"})

    def test_pinned_scorers_handle_symbolic_self_equivalence(self):
        self.assertTrue(score_primary("x^2", "x^2")["correct"])
        self.assertTrue(score_secondary("x^2", "x^2")["correct"])
        self.assertFalse(score_primary(None, "17")["correct"])
        self.assertFalse(score_secondary(None, "17")["correct"])


if __name__ == "__main__":
    unittest.main()
