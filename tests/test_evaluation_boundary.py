"""Distinctive extraction and scorer cases beyond the integer visible fixtures."""

from __future__ import annotations

import unittest

from slm_math_evaluation.evaluation import extract_raw, score_primary, score_secondary


class EvaluationBoundaryTests(unittest.TestCase):
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
