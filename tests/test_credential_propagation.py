"""No Hugging Face credential binding reaches a model-server or converter-tool child.

Only synthetic sentinel values are used; no real credential is read or needed.
Each check launches a real child process through the production launch code. The
child records variable names and booleans only, never values.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from slm_math_evaluation import converter, runner, runtime


SENTINELS = {name: f"synthetic-sentinel-{name.lower()}"
             for name in runtime.HF_CREDENTIAL_VARIABLES}
CHILD = """
import json, os, sys
from pathlib import Path
names = {names!r}
kind = "version" if "--version" in sys.argv else "child"
report = Path(sys.argv[0]).with_name("report-" + kind + ".json")
entries = json.loads(report.read_text()) if report.exists() else []
entries.append({{"hf_names": sorted(name for name in os.environ if name in names),
                 "sentinel_names": sorted(name for name, value in os.environ.items()
                                          if "synthetic-sentinel-" in value),
                 "key_present": "LLAMA_API_KEY" in os.environ,
                 "keep": os.environ.get("KEEP_THIS_VARIABLE")}})
report.write_text(json.dumps(entries))
print("version: 1 ({commit})" if kind == "version" else "Final estimate: PPL = 2.5000")
"""


def _child(directory: Path) -> Path:
    path = directory / "fake-tool"
    path.write_text(f"#!{sys.executable} -I\n" + CHILD.format(
        names=runtime.HF_CREDENTIAL_VARIABLES, commit=runtime.COMMIT[:9]))
    path.chmod(0o755)
    return path


def _reports(tool: Path, kind: str) -> list[dict]:
    return json.loads(tool.with_name(f"report-{kind}.json").read_text())


def _no_sentinel_in(directory: Path) -> list[str]:
    return [str(path) for path in directory.rglob("*") if path.is_file()
            and any(value.encode() in path.read_bytes() for value in SENTINELS.values())]


class CredentialPropagationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {**SENTINELS, "KEEP_THIS_VARIABLE": "1",
                                              "LLAMA_ARG_CHAT_TEMPLATE": "chatml",
                                              "SLM_EVAL_RUNS": str(self.root / "runs")})
        environment.start()
        self.addCleanup(environment.stop)
        self.tool = _child(self.root)

    def assert_clean(self, entry: dict, key: bool) -> None:
        self.assertEqual(entry["hf_names"], [])
        self.assertEqual(entry["sentinel_names"], [])
        self.assertEqual(entry["keep"], "1")
        self.assertIs(entry["key_present"], key)

    def test_environment_helpers_drop_every_named_binding(self) -> None:
        self.assertEqual(set(runtime.HF_CREDENTIAL_VARIABLES),
                         {"HF_TOKEN", "HF_HUB_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"})
        free = runtime.credential_free_environment()
        self.assertFalse(set(SENTINELS) & set(free))
        self.assertEqual((free["KEEP_THIS_VARIABLE"], free["LLAMA_ARG_CHAT_TEMPLATE"]), ("1", "chatml"))
        server = runtime.llama_environment("run-key")
        self.assertFalse(set(SENTINELS) & set(server))
        self.assertNotIn("LLAMA_ARG_CHAT_TEMPLATE", server)
        self.assertEqual(server["LLAMA_API_KEY"], "run-key")
        self.assertNotIn("LLAMA_API_KEY", runtime.llama_environment())
        # The parent keeps its bindings; only children lose them.
        self.assertEqual(os.environ["HF_TOKEN"], SENTINELS["HF_TOKEN"])

    def test_verification_server_and_version_children(self) -> None:
        protocol = self.root / "protocol.yaml"
        protocol.write_text(json.dumps({
            "runtime": {"build": "b10412", "model_alias": "qwen3-8b"},
            "model": {"artifact": {"file": "fixture.gguf", "sha256": "0" * 64}}}))
        for name, verify in (("runtime", runtime.verify_runtime),
                             ("artifact", runtime.verify_artifact)):
            with self.subTest(verify=name), \
                    patch.object(runtime, "pinned_paths", return_value=(self.tool, self.root / "m")), \
                    patch.object(runtime, "sha256_file", return_value="0" * 64), \
                    patch.object(runtime, "verify_chat_template", return_value=None), \
                    patch.object(runtime, "server_arguments", return_value=[str(self.tool), "serve"]):
                with self.assertRaises(runtime.RuntimeErrorWithCode) as caught:
                    verify(protocol)  # The stand-in exits, so startup fails after launch.
                self.assertEqual(caught.exception.code, 3)
        servers, versions = _reports(self.tool, "child"), _reports(self.tool, "version")
        self.assertEqual((len(servers), len(versions)), (2, 2))
        for entry in servers:
            self.assert_clean(entry, key=True)
        for entry in versions:
            self.assert_clean(entry, key=False)
        self.assertEqual(_no_sentinel_in(self.root / "runs"), [])
        self.assertTrue(list((self.root / "runs" / "_verification").rglob("*.json")))

    def test_run_server_and_version_children(self) -> None:
        log = self.root / "server.log"
        runner._check_runtime_version(self.tool)
        with patch.object(runtime, "server_arguments", return_value=[str(self.tool), "serve"]):
            session = runner._Server(self.tool, self.root / "model", {}, log)
            session.prepare()
        self.assertFalse(set(SENTINELS) & set(session.environment))
        session.launch()
        deadline = time.monotonic() + 30
        while session.alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(session.alive())
        self.assert_clean(_reports(self.tool, "child")[0], key=True)
        self.assert_clean(_reports(self.tool, "version")[0], key=False)
        self.assertEqual(_no_sentinel_in(self.root), [])

    def test_converter_tool_dependency_and_sanity_children(self) -> None:
        scratch, models = self.root / "scratch", self.root / "models"
        environment = converter._environment(scratch)
        self.assertFalse(set(SENTINELS) & set(environment))
        self.assertEqual(environment["HF_HOME"], str(scratch / "huggingface"))
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
        converter._verify_python(self.tool, scratch)                      # dependency check
        with patch.object(converter, "_space_check"):
            converter._run_monitored([str(self.tool), "convert"], scratch, models,
                                     converter._environment(scratch))     # convert / quantize
        self.assertEqual(converter._perplexity(self.tool, self.root / "m", self.root / "t"), 2.5)
        entries = _reports(self.tool, "child")
        self.assertEqual(len(entries), 3)
        for entry in entries:
            self.assert_clean(entry, key=False)
        self.assertEqual(_no_sentinel_in(self.root), [])

    def test_downloader_keeps_its_own_hugging_face_authentication(self) -> None:
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            return _Response(b"content")

        digest = converter.data.sha256_bytes(b"content")
        with patch.object(converter.urllib.request, "urlopen", side_effect=urlopen), \
                patch.object(converter, "_space_check"):
            for index, url in enumerate(("https://huggingface.co/owner/name/resolve/c/file",
                                         "https://codeload.github.com/owner/name/tar.gz/c")):
                converter._download(url, self.root / f"file-{index}", digest,
                                    self.root / "scratch", self.root / "models")
        hosted, other = (request.get_header("Authorization") for request in requests)
        self.assertEqual(hosted, "Bearer " + SENTINELS["HF_TOKEN"])
        self.assertIsNone(other)


class _Response(io.BytesIO):
    status = 200
    headers: dict = {}


if __name__ == "__main__":
    unittest.main()
