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
    def test_nonzero_few_shot_is_rejected(self):
        protocol = json.loads((Path(__file__).parents[1] /
                               "configs/protocols/protocol.yaml").read_text())
        protocol["prompt"]["few_shot"] = 1
        with self.assertRaises(data.DataError) as caught:
            runner._protocol_check(protocol)
        self.assertEqual(caught.exception.code, 3)

    def test_approved_protocols_render_exact_user_message(self):
        root = Path(__file__).parents[1] / "configs/protocols"
        q4 = json.loads((root / "protocol.yaml").read_text())
        q8 = json.loads((root / "math500-v1-q8.yaml").read_text())
        suffix = "Please reason step by step, and put your final answer within " + chr(92) + "boxed{}."
        expected_template = "{problem}\n\n" + suffix
        problem = "PROMPT_SENTINEL_α\nsecond line"
        expected_message = problem + "\n\n" + suffix
        for protocol, version in ((q4, "v1"), (q8, "v1-q8")):
            self.assertEqual(protocol["protocol_version"], version)
            self.assertEqual(protocol["prompt"]["template"].encode(), expected_template.encode())
            self.assertIsNone(protocol["prompt"]["system_prompt"])
            self.assertEqual(protocol["prompt"]["few_shot"], 0)
            self.assertEqual(runner._prompt(protocol, problem),
                             (expected_message, [{"role": "user", "content": expected_message}]))
        for protocol in (q4, q8):
            protocol.pop("protocol_version")
            protocol["model"].pop("artifact")
        self.assertEqual(q4, q8)

    def test_captures_raw_generation_with_replayable_hashes_and_verdicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protocol = json.loads((Path(__file__).parents[1] / "configs/protocols/protocol.yaml").read_text())
            protocol_path = root / "protocol.yaml"
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            verification = json.dumps({"protocol_sha256": data.sha256_file(protocol_path),
                                       "model_artifact_sha256": protocol["model"]["artifact"]["sha256"],
                                       "runtime_build": protocol["runtime"]["build"],
                                       "contract_version": "v0.6", "status": "passed"}).encode()
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
            last_params = {}

            def request(_port, method, _path, _key, payload=None, **_kwargs):
                if method == "GET":
                    if _path == "/props":
                        return 200, json.dumps({"default_generation_settings": {"n_ctx": 16384},
                                                "total_slots": 1, "model_alias": "qwen3-8b",
                                                "ui": False}).encode()
                    if _path == "/slots":
                        return 200, json.dumps([{"params": {**last_params,
                                                               "n_predict": last_params["max_tokens"]}}]).encode()
                    return 200, b"{}"
                submitted_seeds.append(payload["seed"])
                last_params.update({name: payload[name] for name in runner.runtime.SAMPLING_FIELDS})
                return 200, json.dumps(response).encode()

            with patch.dict(os.environ, {"SLM_EVAL_RUNS": str(root / "runs")}), \
                 patch.object(runner, "_verification_copies", return_value={
                     "runtime-verification.json": verification,
                     "artifact-verification.json": verification}) as verification_mock, \
                 patch.object(runner, "_source_rows", return_value={
                     "train/fixture": {"unique_id": "train/fixture", "problem": problem, "answer": "17"}}), \
                 patch.object(runner.runtime, "pinned_paths", return_value=(root / "server", root / "model")), \
                 patch.object(runner.runtime, "sha256_file", side_effect=lambda path: (
                     data.sha256_file(path) if path == protocol_path else protocol["model"]["artifact"]["sha256"])), \
                 patch.object(runner.runtime, "server_arguments", return_value=[
                     "server", "--threads", "8", "--threads-batch", "8", "--threads-http", "7",
                     "--n-gpu-layers", "99", "--log-verbosity", "3"]), \
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
                # A later verification uses a new ephemeral port and changes
                # the current record; resume relies on the retained copy.
                verification_mock.side_effect = AssertionError("current verification store read on resume")
                self.assertEqual(runner.resume(run_id), run_id)
                resumed = load_run(run_id)
            self.assertEqual(output.getvalue().strip(), run_id)
            self.assertEqual(submitted_seeds, [3100, 3101])
            self.assertTrue((resumed.directory / "items.jsonl").read_bytes().startswith(first_line))
            self.assertEqual(resumed.metadata["status"], "completed")
            self.assertEqual(len(resumed.metadata["attempts"]), 2)
            for attempt in resumed.metadata["attempts"]:
                effective = attempt["effective_runtime_settings"]
                self.assertEqual(effective["threads"], 8)
                self.assertEqual(effective["threads_batch"], 8)
                self.assertEqual(effective["http_threads"], 7)
                self.assertEqual(effective["requested_gpu_layers"], 99)
                self.assertNotIn("gpu_layers", effective)
                self.assertEqual(effective["log_verbosity"], 3)
                self.assertEqual(effective["context_size"], 16384)
                self.assertFalse(effective["trace_logging"])
                self.assertEqual(attempt["confirmed_request_sampling"]["max_tokens"], 14000)
                self.assertGreater(attempt["generation_timeout_seconds"], 600)
            self.assertEqual(len(resumed.items), 2)
            row = resumed.items[0]
            self.assertEqual(row["raw_output"], raw)
            self.assertEqual(row["extraction"], {"answer": "17", "rule": "boxed_last", "status": "ok"})
            self.assertEqual(row["verdicts"], {"primary": True, "secondary": True})
            self.assertEqual(row["reference_answer"], "17")


if __name__ == "__main__":
    unittest.main()
