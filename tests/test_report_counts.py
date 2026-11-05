"""§7 report counts on synthetic run directories; no model, dataset or network.

v0.8 runs gain counts of missing final answers, infrastructure failures and
interruptions. v0.7 runs keep their unchanged report (§4 compatibility).
"""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from slm_math_evaluation import cli, data


ROOT = Path(__file__).parents[1]
LEGACY_COUNTS = {"format_failures", "truncations", "fallback_extractions", "thinking_rate",
                 "grader_disagreements"}


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _row(item_id: str, status: str, truncated: bool, primary: bool) -> dict:
    raw, reference = f"synthetic output for {item_id}", "2"
    answer = None if status in ("no_answer", "truncated") else "2"
    return {"item_id": item_id, "sample_index": 0, "seed": 0, "prompt_sha256": "0" * 64,
            "raw_output": raw, "raw_output_sha256": _sha(raw.encode()),
            "thinking_present": False, "thinking_state": "absent", "truncated": truncated,
            "finish_reason": "length" if truncated else "stop", "tokens_generated": 1,
            "infra_retries": 0, "reference_answer": reference,
            "reference_sha256": _sha(reference.encode()),
            "extraction": {"answer": answer, "rule": "boxed_last" if answer else "none",
                           "status": status},
            "verdicts": {"primary": primary, "secondary": primary},
            "timing_ms": 1, "awake_ms": 1}


class ReportCountTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runs = Path(temporary.name)
        environment = patch.dict(os.environ, {"SLM_EVAL_RUNS": str(self.runs)})
        environment.start()
        self.addCleanup(environment.stop)
        self.legacy = json.loads((ROOT / "configs/protocols/protocol.yaml").read_text())
        self.current = json.loads(json.dumps(self.legacy))
        self.current["model"]["adapter"] = {"id": "qwen3", "version": "1.0.0"}
        self.ids = [f"train/item-{n}" for n in range(1, 7)]
        self.manifest = {"source": self.legacy["dataset"]["source"],
                         "items": [{"id": i, "role": "dev"} for i in self.ids],
                         "tiers": {"smoke": self.ids, "validation": self.ids}, "exclusions": []}

    def write_run(self, run_id: str, protocol: dict, rows: list[dict], tier: str = "smoke",
                  events: list[dict] | None = None, subset: dict | None = None) -> Path:
        directory = self.runs / run_id
        directory.mkdir()
        protocol_bytes = json.dumps(protocol).encode()
        manifest_bytes = json.dumps(self.manifest).encode()
        (directory / "protocol.yaml").write_bytes(protocol_bytes)
        (directory / "manifest.json").write_bytes(manifest_bytes)
        metadata = {"run_id": run_id, "protocol_version": protocol["protocol_version"],
                    "protocol_sha256": _sha(protocol_bytes), "manifest_sha256": _sha(manifest_bytes),
                    "tier": tier, "k": 1, "seeds": [0],
                    "model_artifact_sha256": protocol["model"]["artifact"]["sha256"],
                    "runtime_build": "b10412", "extractor": protocol["extraction"],
                    "scorers": [protocol["scorers"]["primary"], protocol["scorers"]["secondary"]],
                    "executor": "synthetic", "host": {"os": "x", "arch": "x", "memory_gb": 1},
                    "attempts": [], "status": "completed"}
        if protocol["model"].get("adapter") is not None:
            subset_bytes = None
            if subset is not None:
                subset_bytes = data.canonical_json({**subset, "manifest_sha256": _sha(manifest_bytes)})
                (directory / "subset.json").write_bytes(subset_bytes)
            metadata.update(role=protocol.get("role", "target"), events=events or [],
                            subset_sha256=_sha(subset_bytes) if subset_bytes else None)
        data.write_json(directory / "run.json", metadata)
        (directory / "items.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return directory

    def report(self, run_id: str) -> int:
        with redirect_stdout(io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
            return cli.main(["report", "--run", run_id])

    def test_v08_counts_keep_infrastructure_failures_out_of_scoring(self) -> None:
        rows = [_row(self.ids[0], "ok", False, True),
                _row(self.ids[1], "no_answer", False, False),   # format failure, missing
                _row(self.ids[2], "truncated", True, False),    # truncation, missing
                _row(self.ids[3], "ok", True, False),           # truncation with an answer
                _row(self.ids[4], "fallback", False, False)]
        exhausted = {"item_id": self.ids[5], "sample_index": 0, "reason": "stuck"}
        events = [
            {"type": "infra_retry", "detail": {**exhausted, "retry": 1}},
            {"type": "infra_retry", "detail": {**exhausted, "retry": 2}},
            {"type": "infra_retry", "detail": {**exhausted, "retry": None, "exhausted": True}},
            {"type": "infra_retry", "detail": {"item_id": self.ids[0], "sample_index": 0,
                                               "reason": "server_error", "retry": 1}},
            # An older event bound to a pair, without state metadata: unknown.
            {"type": "interrupted", "detail": {"item_id": self.ids[1], "sample_index": 0,
                                               "reason": "sleep"}},
            # An event that names no pair: no_item.
            {"type": "interrupted", "detail": {"reason": "previous_attempt_ended", "attempt": 1}},
            *({"type": "interrupted", "detail": {"item_id": self.ids[n], "sample_index": 0,
                                                 "reason": "sleep", "thinking_state": state}}
              for n, state in enumerate(("absent", "empty", "present", "unterminated", "bogus"))),
            {"type": "sleep", "detail": {"slept_s": 30}}, {"type": "wake", "detail": {"slept_s": 30}},
            {"type": "pause", "detail": {"mode": "now"}}]
        for event in events:
            event.update(at_utc="2026-11-04T00:00:00+00:00", awake_s=0)
        directory = self.write_run("current", self.current, rows, events=events)
        self.assertEqual(self.report("current"), 0)
        report = json.loads((directory / "report.json").read_text())
        counts = report["counts"]
        self.assertEqual({name: counts[name] for name in
                          ("missing_final_answers", "infrastructure_failures", "interruptions",
                           "format_failures", "truncations", "fallback_extractions")},
                         {"missing_final_answers": 2, "infrastructure_failures": 1,
                          "interruptions": 7, "format_failures": 1, "truncations": 2,
                          "fallback_extractions": 1})
        # P2: four states plus the unknown and no_item bins; an invalid state is unknown.
        self.assertEqual(counts["interruption_thinking_states"],
                         {"absent": 1, "empty": 1, "present": 1, "unterminated": 1,
                          "unknown": 2, "no_item": 1})
        self.assertEqual(sum(counts["interruption_thinking_states"].values()), counts["interruptions"])
        # P9: the exhausted pair is not a wrong answer; accuracy stays over scored pairs,
        # and planned, scored and exhausted pairs are explicit on an incomplete result.
        self.assertEqual(report["scorers"]["primary"]["accuracy"], 0.2)
        self.assertEqual(report["completeness"],
                         {"observed_items": 5, "expected_items": 6, "complete": False,
                          "planned_pairs": 6, "scored_pairs": 5, "exhausted_pairs": 1})
        self.assertIsNone(report["subset_sha256"])
        markdown = (directory / "report.md").read_text()
        self.assertIn("Missing final answers: 2; infrastructure failures (not scored): 1; "
                      "interruptions: 7", markdown)
        self.assertIn("INCOMPLETE: 5 of 6 planned pairs scored (1 exhausted", markdown)

    def test_subset_run_reports_its_own_item_set_and_binding(self) -> None:
        subset = {"subset_version": "1", "tier": "validation", "size": 2, "stratify": "subject",
                  "method": "largest-remainder allocation by subject, then a seeded draw within each subject",
                  "seed": 1, "ids": [self.ids[1], self.ids[4]]}
        rows = [_row(self.ids[1], "ok", False, True), _row(self.ids[4], "ok", False, False)]
        directory = self.write_run("subset", self.current, rows, tier="validation", subset=subset)
        self.assertEqual(self.report("subset"), 0)
        report = json.loads((directory / "report.json").read_text())
        self.assertEqual(report["completeness"],
                         {"observed_items": 2, "expected_items": 2, "complete": True,
                          "planned_pairs": 2, "scored_pairs": 2, "exhausted_pairs": 0})
        self.assertNotIn("INCOMPLETE", (directory / "report.md").read_text())
        self.assertEqual(report["subset_sha256"], _sha((directory / "subset.json").read_bytes()))
        self.assertIn("Subset SHA-256: " + report["subset_sha256"], (directory / "report.md").read_text())
        (directory / "report.json").unlink()
        original = (directory / "subset.json").read_bytes()
        (directory / "subset.json").write_bytes(original + b" ")
        self.assertEqual(self.report("subset"), 2)
        (directory / "subset.json").unlink()
        self.assertEqual(self.report("subset"), 3)
        self.assertFalse((directory / "report.json").exists())

    def test_published_value_that_is_not_a_whole_count_is_flagged_and_still_read(self) -> None:
        for name, items, value, flagged in (("odd", 3, "0.5", True), ("even", 4, "0.5", False),
                                            ("float-ten", 10, "0.7", False), ("third", 9, "0.25", True)):
            with self.subTest(name=name):
                rows = [_row(self.ids[n % len(self.ids)] + f"-{n}", "ok", False, n % 2 == 0)
                        for n in range(items)]
                self.manifest["tiers"]["smoke"] = [row["item_id"] for row in rows]
                self.manifest["items"] = [{"id": row["item_id"], "role": "dev"} for row in rows]
                directory = self.write_run(f"published-{name}", self.current, rows)
                with redirect_stdout(io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
                    code = cli.main(["report", "--run", f"published-{name}", "--compare-published", value])
                self.assertEqual(code, 0)
                comparison = json.loads((directory / "report.json").read_text())["comparison"]
                self.assertIs(comparison["not_whole_count"], flagged)
                self.assertEqual(comparison["published"], float(value))
                self.assertIn(comparison["reading"], {"CONSISTENT", "PROTOCOL-SENSITIVE", "UNEXPLAINED"})
                self.assertEqual("not_whole_count" in (directory / "report.md").read_text(), flagged)
        # A v0.7 run keeps its unchanged comparison, with no flag.
        rows = [_row(self.ids[n], "ok", False, True) for n in range(3)]
        directory = self.write_run("published-legacy", self.legacy, rows)
        with redirect_stdout(io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(cli.main(["report", "--run", "published-legacy", "--compare-published", "0.5"]), 0)
        self.assertNotIn("not_whole_count", (directory / "report.json").read_text())

    def test_not_whole_count_uses_planned_not_scored_pairs(self) -> None:
        """P9: planned pairs decide the flag even when infrastructure failures cut scored pairs."""
        for name, planned, flagged in (("planned-4-scored-3", 4, False),   # 2.0 planned; 1.5 scored
                                       ("planned-3-scored-2", 3, True)):   # 1.5 planned; 1.0 scored
            with self.subTest(name=name):
                ids = [f"train/{name}-{n}" for n in range(planned)]
                self.manifest["tiers"]["smoke"] = ids
                self.manifest["items"] = [{"id": i, "role": "dev"} for i in ids]
                rows = [_row(i, "ok", False, True) for i in ids[:-1]]
                exhausted = {"item_id": ids[-1], "sample_index": 0, "reason": "stuck",
                             "retry": None, "exhausted": True}
                events = [{"type": "infra_retry", "at_utc": "2026-11-04T00:00:00+00:00",
                           "awake_s": 0, "detail": exhausted}]
                directory = self.write_run(name, self.current, rows, events=events)
                with redirect_stdout(io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
                    self.assertEqual(cli.main(["report", "--run", name, "--compare-published", "0.5"]), 0)
                report = json.loads((directory / "report.json").read_text())
                self.assertEqual((report["completeness"]["planned_pairs"],
                                  report["completeness"]["scored_pairs"]), (planned, planned - 1))
                self.assertIs(report["comparison"]["not_whole_count"], flagged)
                self.assertEqual(report["scorers"]["primary"]["accuracy"], 1.0)  # Over scored pairs.

    def test_v07_report_and_fixture_refusal_are_unchanged(self) -> None:
        rows = [_row(self.ids[0], "ok", False, True), _row(self.ids[1], "no_answer", False, False)]
        directory = self.write_run("legacy", self.legacy, rows)
        self.assertEqual(self.report("legacy"), 0)
        report = json.loads((directory / "report.json").read_text())
        self.assertEqual(set(report["counts"]), LEGACY_COUNTS)
        self.assertNotIn("subset_sha256", report)
        self.assertNotIn("Missing final answers", (directory / "report.md").read_text())
        fixture = {**self.current, "role": "fixture"}
        directory = self.write_run("fixture", fixture, rows)
        self.assertEqual(self.report("fixture"), 3)
        self.assertFalse((directory / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
