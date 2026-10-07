"""Checks for the pinned keyed-server boundary without loading a model."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from slm_math_evaluation import runtime


class RuntimeBoundaryTests(unittest.TestCase):
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
            model = models / "fixture.gguf"
            model.write_bytes(b"model")
            protocol = root / "protocol.yaml"
            protocol.write_text(json.dumps({
                "runtime": {"build": "b10412", "model_alias": "qwen3-8b"},
                "model": {"artifact": {"file": "fixture.gguf", "sha256": "0" * 64}},
            }), encoding="utf-8")
            with patch.dict(os.environ, {"SLM_EVAL_CACHE": str(root)}, clear=False):
                with self.assertRaises(runtime.RuntimeErrorWithCode) as caught:
                    runtime.verify_artifact(protocol)
            self.assertEqual(caught.exception.code, 2)
            self.assertNotEqual(hashlib.sha256(model.read_bytes()).hexdigest(), "0" * 64)


if __name__ == "__main__":
    unittest.main()
