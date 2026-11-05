"""Hermetic converter fixtures: no model, network, or external tool is needed."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from slm_math_evaluation import converter, data


class Response(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, content_range: str = "") -> None:
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Range": content_range}


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fixture_spec(root: Path) -> Path:
    source = b'{"architectures":["LlamaForCausalLM"]}'
    text = b"A fixed non-math fixture paragraph."
    spec = {
        "spec_version": "1", "name": "fixture-1", "role": "fixture",
        "source": {"repo": "HuggingFaceTB/SmolLM2-135M-Instruct", "commit": "a" * 40,
                   "files": [{"path": "config.json", "sha256": _sha(source)}]},
        "architecture": "LlamaForCausalLM", "text_only": True,
        "template": {"inserts_system_message": False, "thinking_control": "none"},
        "conversion": {"tool": {"release": "b10412", "script_sha256": converter.SCRIPT_SHA256,
                                "python_lock_sha256": data.sha256_file(converter.LOCK)},
                       "intermediate": "bf16", "quantization": "Q4_K_M", "calibration": "none"},
        "expected_sizes_gb": {"source": 0.01, "intermediate": 0.01, "output": 0.01},
        "sanity": {"reference": None, "perplexity_text_sha256": _sha(text),
                   "max_perplexity_ratio": 1.1},
    }
    path = root / "spec.json"
    path.write_text(json.dumps(spec))
    (root / "sanity.txt").write_bytes(text)
    return path


class ConverterTests(unittest.TestCase):
    def test_selected_python_must_match_separate_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            with patch.object(converter.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 0)) as run:
                converter._verify_python(Path("python"), scratch)
            self.assertIn("-I", run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][-1], str(converter.LOCK))
            with patch.object(converter.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaises(data.DataError) as caught:
                    converter._verify_python(Path("python"), scratch)
            self.assertEqual(caught.exception.code, 2)

    def test_monitored_write_stops_on_disk_floor_breach(self) -> None:
        class Process:
            def __init__(self):
                self.terminated = False
            def poll(self):
                return 1 if self.terminated else None
            def terminate(self):
                self.terminated = True
            def wait(self, timeout=None):
                return 1
        process = Process()
        with patch.object(converter.subprocess, "Popen", return_value=process), \
             patch.object(converter, "_space_check", side_effect=data.DataError("floor", 3)):
            with self.assertRaises(data.DataError) as caught:
                converter._run_monitored(["fixture-tool"], Path("scratch"), Path("models"), {})
        self.assertEqual(caught.exception.code, 3)
        self.assertTrue(process.terminated)

    def test_perplexity_uses_explicit_cpu_and_parses_pinned_result(self) -> None:
        output = subprocess.CompletedProcess([], 0, "", "Final estimate: PPL = 3.1250 +/- 0.01")
        with patch.object(converter.subprocess, "run", return_value=output) as run:
            self.assertEqual(converter._perplexity(Path("ppl"), Path("model"), Path("text")), 3.125)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--device") + 1], "none")
        self.assertEqual(command[command.index("--fit") + 1], "off")

    def test_download_hash_mismatch_keeps_only_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(converter, "_open", return_value=Response(b"wrong")):
                with self.assertRaises(data.DataError) as caught:
                    converter._download("https://example.test/file", root / "file", _sha(b"right"),
                                        root / "scratch", root / "models")
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse((root / "file").exists())
            self.assertEqual((root / "file.partial").read_bytes(), b"wrong")

    def test_partial_download_resumes_with_range_and_atomic_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "model.safetensors"
            final.with_name(final.name + ".partial").write_bytes(b"abc")
            seen = []
            def response(request, timeout):
                seen.append(request.get_header("Range"))
                self.assertFalse(final.exists())
                return Response(b"def", 206, "bytes 3-5/6")
            with patch.object(converter, "_open", side_effect=response):
                converter._download("https://example.test/file", final, _sha(b"abcdef"),
                                    root / "scratch", root / "models")
            self.assertEqual(seen, ["bytes=3-"])
            self.assertEqual(final.read_bytes(), b"abcdef")
            self.assertFalse(final.with_name(final.name + ".partial").exists())

    def test_disk_floor_refuses_step_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"SLM_EVAL_FLOOR_GB": "0",
                                      "SLM_EVAL_SCRATCH_FLOOR_GB": "0"}), \
                 patch.object(converter.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
                with self.assertRaises(data.DataError) as caught:
                    converter._space_check(root / "scratch", root / "models", scratch_peak=0.01)
            self.assertEqual(caught.exception.code, 3)

    def test_step_resume_and_record_linkage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = _fixture_spec(root)
            calls = []

            def download(_url, path, expected, _scratch, _models):
                path.parent.mkdir(parents=True, exist_ok=True)
                content = b'{"architectures":["LlamaForCausalLM"]}'
                self.assertEqual(_sha(content), expected)
                path.write_bytes(content)
                return path

            @contextmanager
            def source_tree(_archive, _scratch):
                yield root

            def run_step(command, _scratch, _models, _environment):
                calls.append(command)
                partial = Path(command[-1] if "--outfile" in command else command[-2])
                self.assertTrue(partial.name.endswith(".partial"))
                self.assertFalse(partial.with_name(partial.name[:-8]).exists())
                partial.write_bytes(b"intermediate" if "--outfile" in command else b"output")

            environment = {"SLM_EVAL_CACHE": str(root / "cache"),
                           "SLM_EVAL_SCRATCH": str(root / "scratch"),
                           "SLM_EVAL_FLOOR_GB": "0", "SLM_EVAL_SCRATCH_FLOOR_GB": "0",
                           "SLM_EVAL_SANITY_TEXT": str(root / "sanity.txt")}
            with patch.dict(os.environ, environment), \
                 patch.object(converter, "_tools", return_value=(Path(sys.executable),
                                                               Path("quantize"), Path("perplexity"))), \
                 patch.object(converter, "_archive", return_value=root / "archive"), \
                 patch.object(converter, "_source_tree", side_effect=source_tree), \
                 patch.object(converter, "_download", side_effect=download), \
                 patch.object(converter, "_run_monitored", side_effect=run_step), \
                 patch.object(converter, "chat_template", return_value="template"), \
                 patch.object(converter.runner, "_host", return_value={
                     "os": "fixture", "arch": "fixture", "memory_gb": 1}), \
                 patch.object(converter, "_perplexity", return_value=5.0):
                output, record_path = converter.convert(spec_path)
                record_bytes = record_path.read_bytes()
                self.assertEqual(len(calls), 2)
                self.assertEqual(converter.convert(spec_path), [output, record_path])
                self.assertEqual(len(calls), 2)
                self.assertEqual(record_path.read_bytes(), record_bytes)
                sanity_path = converter.sanity(spec_path)
            record = json.loads(record_bytes)
            sanity = json.loads(sanity_path.read_bytes())
            self.assertEqual(record["source"]["files"][0]["sha256_expected"],
                             record["source"]["files"][0]["sha256_observed"])
            self.assertEqual(record["intermediate"]["sha256"], _sha(b"intermediate"))
            self.assertEqual(record["output"]["sha256"], _sha(b"output"))
            self.assertEqual(sanity["conversion_record_sha256"], _sha(record_bytes))
            self.assertEqual(sanity["output_sha256"], record["output"]["sha256"])
            self.assertEqual(sanity["status"], "passed")

    def test_reference_ratio_failure_writes_linked_sanity_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = _fixture_spec(root)
            spec = json.loads(spec_path.read_text())
            reference_bytes = b"reference gguf"
            spec["sanity"]["reference"] = {"repo": "publisher/reference", "commit": "b" * 40,
                                            "file": "reference.gguf", "sha256": _sha(reference_bytes)}
            spec_path.write_text(json.dumps(spec))
            spec_sha = data.sha256_file(spec_path)
            cache = root / "cache" / "models"
            cache.mkdir(parents=True)
            output = cache / "fixture-1-Q4_K_M.gguf"
            output.write_bytes(b"converted gguf")
            conversion = cache / "fixture-1-conversion-record.json"
            data.write_json(conversion, {"spec_sha256": spec_sha,
                                         "output": {"sha256": data.sha256_file(output)}})
            environment = {"SLM_EVAL_CACHE": str(root / "cache"),
                           "SLM_EVAL_SCRATCH": str(root / "scratch"),
                           "SLM_EVAL_FLOOR_GB": "0", "SLM_EVAL_SCRATCH_FLOOR_GB": "0",
                           "SLM_EVAL_SANITY_TEXT": str(root / "sanity.txt")}
            with patch.dict(os.environ, environment), \
                 patch.object(converter, "_tools", return_value=(Path("python"),
                                                               Path("quantize"), Path("ppl"))), \
                 patch.object(converter, "_perplexity", side_effect=[2.0, 1.0]), \
                 patch.object(converter, "_open",
                              return_value=Response(reference_bytes)):
                with self.assertRaises(data.DataError) as caught:
                    converter.sanity(spec_path)
            self.assertEqual(caught.exception.code, 1)
            recorded = json.loads((cache / "fixture-1-sanity-record.json").read_bytes())
            self.assertEqual(recorded["ratio"], 2.0)
            self.assertEqual(recorded["status"], "failed")
            self.assertEqual(recorded["conversion_record_sha256"], data.sha256_file(conversion))


if __name__ == "__main__":
    unittest.main()
