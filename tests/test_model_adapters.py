"""Fixture-scale model boundaries; no target model or server is loaded."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from slm_math_evaluation import model_adapters, runner, runtime
from slm_math_evaluation.data import DataError
from slm_math_evaluation.evaluation import extract_raw_with_state
from slm_math_evaluation.gguf_metadata import chat_template
from slm_math_evaluation.run_store import load_run


def _protocol(name: str, mode: str) -> dict:
    root = Path(__file__).parents[1]
    document = json.loads((root / "configs/protocols/protocol.yaml").read_text())
    adapter_id = "gemma4" if name == "gemma-4-12b" else "phi4-reasoning"
    template_hash = (model_adapters.GEMMA_TEMPLATE_SHA256 if adapter_id == "gemma4"
                     else model_adapters.PHI_TEMPLATE_SHA256)
    document["role"] = "target"
    document["model"]["adapter"] = {"id": adapter_id, "version": "1.0.0"}
    document["model"]["chat_template"] = {"source": "embedded", "sha256": template_hash}
    document["model"]["artifact"]["conversion_record_sha256"] = None
    document["runtime"]["model_alias"] = name
    document["reasoning_mode"] = {"value": mode, "provenance": "vendor-template"}
    document["sampling"]["context_length"] = {"value": 16384, "provenance": "local-choice"}
    document["sampling"]["stop"] = {"value": [], "provenance": "local-choice"}
    document["resources"] = {
        "max_memory_gb": {"value": 24, "provenance": "local-choice"},
        "min_free_memory_gb": {"value": 2, "provenance": "local-choice"},
        "stuck_after_awake_s": {"value": 180, "provenance": "local-choice"},
    }
    return document


def _tiny_gguf(path: Path, template: str) -> None:
    def string(value: str) -> bytes:
        encoded = value.encode("utf-8")
        return struct.pack("<Q", len(encoded)) + encoded
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 2)
                     + string("general.name") + struct.pack("<I", 8) + string("fixture")
                     + string("tokenizer.chat_template") + struct.pack("<I", 8)
                     + string(template))


class ModelAdapterTests(unittest.TestCase):
    def test_approved_modes_and_phi_thinking_off_rejection(self) -> None:
        for name in ("phi-4-reasoning", "phi-4-reasoning-plus"):
            protocol = _protocol(name, "always_on")
            runner._protocol_check(protocol)
            adapter = model_adapters.from_protocol(protocol)
            self.assertEqual(adapter.alias, name)
            self.assertEqual(adapter.settings("always_on"), ("auto", {}))
            protocol["reasoning_mode"]["value"] = "thinking_off"
            with self.assertRaises(DataError) as caught:
                runner._protocol_check(protocol)
            self.assertEqual(caught.exception.code, 3)
            with self.assertRaises(runtime.RuntimeErrorWithCode) as caught:
                runtime.pinned_paths(protocol)
            self.assertEqual(caught.exception.code, 3)
        for mode, value in (("thinking_on", True), ("thinking_off", False)):
            protocol = _protocol("gemma-4-12b", mode)
            runner._protocol_check(protocol)
            self.assertEqual(model_adapters.from_protocol(protocol).settings(mode),
                             ("auto", {"enable_thinking": value}))
        fixture = _protocol("gemma-4-12b", "thinking_on")
        fixture["role"] = "fixture"
        fixture["runtime"]["model_alias"] = "fixture-2"
        runner._protocol_check(fixture)
        self.assertEqual(model_adapters.from_protocol(fixture).alias, "fixture-2")
        fixture["runtime"]["model_alias"] = "gemma-4-12b"
        with self.assertRaises(DataError):
            runner._protocol_check(fixture)
        with self.assertRaises(DataError):
            runner._resource_host_check(protocol, {"memory_gb": 16})

    def test_reasoning_states_and_missing_final(self) -> None:
        for adapter, opening, closing in (
            (model_adapters.Phi4ReasoningAdapter("phi-4-reasoning"), "<think>", "</think>"),
            (model_adapters.Gemma4Adapter(), "<|channel>thought\n", "<channel|>"),
        ):
            cases = (
                ("", False, "absent", None, "no_answer"),
                ("answer \\boxed{2}", False, "absent", "2", "ok"),
                (closing + "answer \\boxed{2}", False, "absent", "2", "ok"),
                (opening[:-1] + " broken " + closing + "answer \\boxed{2}",
                 False, "absent", "2", "ok"),
                (opening + closing + "answer \\boxed{2}", False, "empty", "2", "ok"),
                (opening + "  \n" + closing + "answer \\boxed{2}", False, "empty", "2", "ok"),
                (opening + "work" + closing + "answer \\boxed{2}", False, "present", "2", "ok"),
                (opening + "work" + closing, False, "present", None, "no_answer"),
                (opening + "work", False, "unterminated", None, "no_answer"),
                (opening + "work", True, "unterminated", None, "truncated"),
                (opening + "old" + closing + "answer \\boxed{2}" + opening,
                 True, "unterminated", None, "truncated"),
                (opening + "work" + closing + "no final box", True,
                 "present", None, "truncated"),
            )
            for raw, truncated, state, answer, status in cases:
                with self.subTest(adapter=adapter.id, raw=raw, truncated=truncated):
                    present, actual_state, extraction = extract_raw_with_state(raw, truncated, adapter)
                    self.assertEqual(actual_state, state)
                    self.assertEqual(extraction["answer"], answer)
                    self.assertEqual(extraction["status"], status)
                    self.assertEqual(present, state in ("present", "unterminated"))

    def test_embedded_template_hash_and_malformed_gguf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.gguf"
            _tiny_gguf(path, "fixture template")
            self.assertEqual(chat_template(path), "fixture template")
            protocol = _protocol("gemma-4-12b", "thinking_on")
            pinned = hashlib.sha256(b"fixture template").hexdigest()
            protocol["model"]["chat_template"]["sha256"] = pinned
            with patch.object(model_adapters.Gemma4Adapter, "template_sha256", pinned):
                self.assertEqual(runtime.verify_chat_template(protocol, path), pinned)
                path.write_bytes(path.read_bytes()[:-3])
                with self.assertRaises(runtime.RuntimeErrorWithCode) as caught:
                    runtime.verify_chat_template(protocol, path)
                self.assertEqual(caught.exception.code, 2)

    def test_keyed_template_arguments_and_new_protocol_values(self) -> None:
        protocol = _protocol("gemma-4-12b", "thinking_off")
        protocol["sampling"]["top_k"]["value"] = 0
        protocol["sampling"]["min_p"]["value"] = 0
        protocol["sampling"]["stop"]["value"] = ["<turn|>"]
        runner._protocol_check(protocol)
        observed = []
        def request(_port, _method, path, key, payload, **_kwargs):
            observed.append((path, key, payload))
            return 200, json.dumps({"prompt": "<bos>...<|channel>thought\n<channel|>"}).encode()
        with patch.object(runtime, "request", side_effect=request):
            rendered = runtime.render_prompt(12345, "fixture-key", [{"role": "user", "content": "2+2"}], protocol)
        self.assertTrue(rendered.endswith("<channel|>"))
        self.assertEqual(observed[0][1], "fixture-key")
        self.assertEqual(observed[0][2]["chat_template_kwargs"], {"enable_thinking": False})
        args = runtime.server_arguments(Path("server"), Path("model"), 12345, protocol)
        self.assertEqual(args[args.index("--alias") + 1], "gemma-4-12b")
        self.assertEqual(args[args.index("--ctx-size") + 1], "16384")
        self.assertEqual(args[args.index("--reasoning") + 1], "auto")
        self.assertNotIn("--chat-template-kwargs", args)
        protocol["sampling"]["context_length"]["value"] = 8192
        with self.assertRaises(DataError):
            runner._protocol_check(protocol)

    def test_gemma_fixture_run_records_rendered_prompt_and_effective_args(self) -> None:
        class FakeProcess:
            def __init__(self, *_args, **_kwargs):
                self.stopped = False
            def poll(self):
                return 0 if self.stopped else None
            def terminate(self):
                self.stopped = True
            def wait(self, timeout=None):
                return 0

        class FakeSocket:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def bind(self, _address):
                pass
            def getsockname(self):
                return ("127.0.0.1", 55555)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protocol = _protocol("gemma-4-12b", "thinking_on")
            protocol["role"] = "fixture"
            protocol["runtime"]["model_alias"] = "fixture-1"
            protocol["sampling"]["top_k"]["value"] = 0
            protocol["sampling"]["min_p"]["value"] = 0
            protocol_path = root / "fixture-protocol.json"
            protocol_path.write_text(json.dumps(protocol))
            problem = "What is 1 + 1?"
            manifest = {"source": protocol["dataset"]["source"],
                        "items": [{"id": "train/fixture", "role": "dev",
                                   "content_sha256": hashlib.sha256(problem.encode()).hexdigest()}],
                        "tiers": {"smoke": ["train/fixture"]}, "exclusions": []}
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            rendered = "<bos><|turn>user\n" + problem + "<turn|>\n<|turn>model\n"
            raw = "<|channel>thought\ncompute\n<channel|>\\boxed{2}"
            response = {"choices": [{"message": {"content": raw}, "finish_reason": "stop"}],
                        "usage": {"completion_tokens": 9}}
            sent = []

            def request(_port, method, path, _key, payload=None, **_kwargs):
                if method == "GET":
                    return 200, json.dumps({"default_generation_settings": {"n_ctx": 16384},
                                            "total_slots": 1, "model_alias": "fixture-1",
                                            "ui": False}).encode()
                sent.append((path, payload))
                return 200, json.dumps(response).encode()

            with patch.dict(os.environ, {"SLM_EVAL_RUNS": str(root / "runs")}), \
                 patch.object(runner, "_verification_copies", return_value={}), \
                 patch.object(runner, "_source_rows", return_value={
                     "train/fixture": {"problem": problem, "answer": "2"}}), \
                 patch.object(runner, "_host", return_value={
                     "os": "fixture", "arch": "fixture", "memory_gb": 32}), \
                 patch.object(runner.runtime, "pinned_paths", return_value=(root / "server", root / "model")), \
                 patch.object(runner.runtime, "sha256_file", return_value=protocol["model"]["artifact"]["sha256"]), \
                 patch.object(runner.runtime, "verify_chat_template", return_value=model_adapters.GEMMA_TEMPLATE_SHA256), \
                 patch.object(runner.runtime, "server_arguments", return_value=[
                     "server", "--threads", "8", "--threads-batch", "8", "--threads-http", "7",
                     "--n-gpu-layers", "99", "--log-verbosity", "3"]), \
                 patch.object(runner.runtime, "render_prompt", return_value=rendered), \
                 patch.object(runner.runtime, "confirm_sampling", return_value={}), \
                 patch.object(runner.runtime, "request", side_effect=request), \
                 patch.object(runner, "_check_runtime_version"), \
                 patch.object(runner.socket, "socket", FakeSocket), \
                 patch.object(runner.subprocess, "Popen", FakeProcess):
                with redirect_stdout(io.StringIO()):
                    run_id = runner.start(protocol_path, manifest_path, "smoke", 1, 1)
                stored = load_run(run_id)
            self.assertEqual(stored.metadata["adapter"]["id"], "gemma4")
            self.assertEqual(stored.metadata["role"], "fixture")
            self.assertEqual(stored.metadata["model_source_revision"], None)
            self.assertEqual(stored.metadata["generation_timeout_seconds"], None)
            self.assertEqual(stored.metadata["effective"]["template_args"], {"enable_thinking": True})
            self.assertEqual(stored.items[0]["prompt_sha256"], hashlib.sha256(rendered.encode()).hexdigest())
            self.assertEqual(stored.items[0]["thinking_state"], "present")
            self.assertEqual(stored.items[0]["extraction"]["answer"], "2")
            self.assertEqual(sent[0][1]["chat_template_kwargs"], {"enable_thinking": True})
            self.assertEqual(sent[0][1]["stop"], [])
            self.assertEqual(sent[0][1]["top_k"], 0)
            self.assertEqual(sent[0][1]["min_p"], 0)


if __name__ == "__main__":
    unittest.main()
