"""Checks for the pinned keyed-server boundary without loading a model."""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import time
import unittest
from unittest.mock import patch

from slm_math_evaluation import runtime, runner
from slm_math_evaluation.data import DataError


class RuntimeBoundaryTests(unittest.TestCase):
    def test_delayed_local_generation_response_exceeds_thirty_seconds(self) -> None:
        class DelayedHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                time.sleep(31)
                body = b'{"choices":[{"message":{"content":"done"}}]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), DelayedHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            timeout = runtime.generation_timeout(14000)
            self.assertGreater(timeout, 14000 / 23.3)
            started = time.monotonic()
            status, body = runner._generation_response(
                server.server_address[1], "fixture", {"max_tokens": 14000}, timeout)
            duration = time.monotonic() - started
            self.assertGreater(duration, 30)
            self.assertEqual(status, 200)
            self.assertIn(b"done", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_generation_timeout_reports_duration(self) -> None:
        with patch.object(runtime, "request", side_effect=TimeoutError("timed out")):
            with self.assertRaises(DataError) as caught:
                runner._generation_response(12345, "fixture", {}, 61)
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("61 seconds", str(caught.exception))

    def test_sampling_confirmation_detects_mismatch(self) -> None:
        requested = {"temperature": .6, "top_p": .95, "top_k": 20,
                     "min_p": 0, "presence_penalty": 0, "repeat_penalty": 1,
                     "max_tokens": 16, "seed": 42}
        confirmed = {**requested, "top_k": 40, "n_predict": 16}
        with patch.object(runtime, "request", return_value=(200,
                            json.dumps([{"params": confirmed}]).encode())):
            with self.assertRaises(runtime.RuntimeErrorWithCode) as caught:
                runtime.confirm_sampling(12345, "fixture", requested)
        self.assertEqual(caught.exception.code, 4)
        self.assertIn("top_k", str(caught.exception))

    def test_server_arguments_force_loopback_alias_and_raw_reasoning(self) -> None:
        protocol = {
            "runtime": {
                "host": "127.0.0.1", "port": "ephemeral", "ui": False,
                "reasoning_format": "none", "context_size": 4096,
                "threads": -1, "threads_batch": -1,
                "batch_size": 2048, "ubatch_size": 512,
                "gpu_layers": 99, "flash_attention": True, "parallel_slots": 1,
                "cache_type_k": "f16", "cache_type_v": "f16",
                "load_mode": "mmap", "context_shift": False,
                "jinja": True, "reasoning_budget": -1,
            },
            "reasoning_mode": {"value": "hybrid"},
        }
        args = runtime.server_arguments(Path("server"), Path("model"), 12345, protocol)
        self.assertEqual(args[args.index("--host") + 1], "127.0.0.1")
        self.assertEqual(args[args.index("--alias") + 1], "qwen3-8b")
        self.assertEqual(args[args.index("--reasoning-format") + 1], "none")
        self.assertEqual(args[args.index("--log-verbosity") + 1], "3")
        self.assertGreater(int(args[args.index("--threads") + 1]), 0)
        self.assertGreater(int(args[args.index("--threads-http") + 1]), 0)
        self.assertIn("--no-webui", args)
        with self.assertRaises(runtime.RuntimeErrorWithCode) as caught:
            runtime.server_arguments(Path("server"), Path("model"), 12345,
                                     {**protocol, "runtime": {**protocol["runtime"],
                                                              "host": "0.0.0.0"}})
        self.assertEqual(caught.exception.code, 3)

    def test_model_listing_must_contain_only_neutral_alias(self) -> None:
        model = Path("/tmp/private-model.gguf")
        valid = json.dumps({"data": [{"id": "qwen3-8b"}]}).encode()
        exposed = json.dumps({"data": [{"id": str(model)}]}).encode()
        self.assertTrue(runtime._check_exempt_body("/v1/models", valid, model))
        self.assertFalse(runtime._check_exempt_body("/v1/models", exposed, model))

    def test_stream_reassembly_keeps_thinking_tags(self) -> None:
        events = (
            b'data: {"choices":[{"delta":{"content":"<think>"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"391</think>"}}]}\n\n'
            b'data: [DONE]\n\n'
        )
        self.assertEqual(runtime._stream_content(events), "<think>391</think>")

    def test_wrong_artifact_hash_exits_two_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "models"
            server = models / "llama-b10412" / "llama-server"
            server.parent.mkdir(parents=True)
            server.write_text("fixture", encoding="utf-8")
            server_impl = server.parent / "libllama-server-impl.dylib"
            server_impl.write_text("fixture", encoding="utf-8")
            model = models / "fixture.gguf"
            model.write_bytes(b"model")
            protocol = root / "protocol.yaml"
            protocol.write_text(json.dumps({
                "runtime": {"build": "b10412", "model_alias": "qwen3-8b"},
                "model": {"artifact": {"file": "fixture.gguf", "sha256": "0" * 64}},
            }), encoding="utf-8")
            def pinned_sha(path):
                if path == server:
                    return runtime.SERVER_SHA256
                if path == server_impl:
                    return runtime.SERVER_IMPL_SHA256
                return hashlib.sha256(path.read_bytes()).hexdigest()

            with patch.dict(os.environ, {"SLM_EVAL_CACHE": str(root)}, clear=False), \
                 patch.object(runtime, "sha256_file", side_effect=pinned_sha):
                with self.assertRaises(runtime.RuntimeErrorWithCode) as caught:
                    runtime.verify_artifact(protocol)
            self.assertEqual(caught.exception.code, 2)
            self.assertNotEqual(hashlib.sha256(model.read_bytes()).hexdigest(), "0" * 64)


if __name__ == "__main__":
    unittest.main()
