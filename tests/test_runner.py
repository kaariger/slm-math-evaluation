"""Fixture-scale evidence for the complete run capture path, without a model run."""

from __future__ import annotations

import json
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from slm_math_evaluation import data, runner
from slm_math_evaluation.run_store import load_run


class _FakeProcess:
    def __init__(self, *args, **kwargs):
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def bind(self, _address):
        pass

    def getsockname(self):
        return ("127.0.0.1", 55555)


class RunnerFixtureTests(unittest.TestCase):
    def test_captures_raw_generation_with_replayable_hashes_and_verdicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protocol = json.loads((Path(__file__).parents[1] / "configs/protocols/math500-v1-draft.yaml").read_text())
            protocol.pop("draft_status")
            protocol_path = root / "protocol.yaml"
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            problem = "What is 8 + 9?"
            manifest = {"source": protocol["dataset"]["source"],
                        "items": [{"id": "train/fixture", "role": "dev",
                                   "content_sha256": data.content_hash(problem)}],
                        "tiers": {"smoke": ["train/fixture"], "validation": []}, "exclusions": []}
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            raw = "<think>Compute the sum.</think>\\boxed{17}"
            response = {"choices": [{"message": {"content": raw}, "finish_reason": "stop"}],
                        "usage": {"completion_tokens": 12}}
            submitted_seeds = []

            def request(_port, method, _path, _key, payload=None):
                if method == "GET":
                    return 200, b"{}"
                submitted_seeds.append(payload["seed"])
                return 200, json.dumps(response).encode()

            with patch.dict(os.environ, {"SLM_EVAL_RUNS": str(root / "runs")}), \
                 patch.object(runner, "_verification_copies", return_value={
                     "runtime-verification.json": b"{}", "artifact-verification.json": b"{}"}), \
                 patch.object(runner, "_source_rows", return_value={
                     "train/fixture": {"unique_id": "train/fixture", "problem": problem, "answer": "17"}}), \
                 patch.object(runner.runtime, "pinned_paths", return_value=(root / "server", root / "model")), \
                 patch.object(runner.runtime, "sha256_file", side_effect=lambda path: (
                     data.sha256_file(path) if path == protocol_path else protocol["model"]["artifact"]["sha256"])), \
                 patch.object(runner.runtime, "server_arguments", return_value=["server"]), \
                 patch.object(runner, "_check_runtime_version"), \
                 patch.object(runner.runtime, "request", side_effect=request), \
                 patch.object(runner, "_host", return_value={"os": "fixture", "arch": "fixture", "memory_gb": 1}), \
                 patch.object(runner.socket, "socket", _FakeSocket), \
                 patch.object(runner.subprocess, "Popen", _FakeProcess):
                output = io.StringIO()
                with redirect_stdout(output):
                    run_id = runner.start(protocol_path, manifest_path, "smoke", 1, 3100)
                stored = load_run(run_id)
                self.assertEqual(submitted_seeds, [3100])
                first_line = (stored.directory / "items.jsonl").read_bytes()
                # Emulate interruption after the first of two requested samples.
                metadata = stored.metadata
                metadata["status"] = "interrupted"
                metadata["k"] = 2
                metadata["seeds"] = [3100, 3101]
                metadata["attempts"][0]["status"] = "interrupted"
                data.write_json(stored.directory / "run.json", metadata)
                self.assertEqual(runner.resume(run_id), run_id)
                resumed = load_run(run_id)
            self.assertEqual(output.getvalue().strip(), run_id)
            self.assertEqual(submitted_seeds, [3100, 3101])
            self.assertTrue((resumed.directory / "items.jsonl").read_bytes().startswith(first_line))
            self.assertEqual(resumed.metadata["status"], "completed")
            self.assertEqual(len(resumed.metadata["attempts"]), 2)
            self.assertEqual(len(resumed.items), 2)
            row = resumed.items[0]
            self.assertEqual(row["raw_output"], raw)
            self.assertEqual(row["extraction"], {"answer": "17", "rule": "boxed_last", "status": "ok"})
            self.assertEqual(row["verdicts"], {"primary": True, "secondary": True})
            self.assertEqual(row["reference_answer"], "17")


if __name__ == "__main__":
    unittest.main()
