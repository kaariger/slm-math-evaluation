"""No Hugging Face credential binding reaches a model-server or converter-tool child.

Only synthetic sentinel values are used; no real credential is read or needed.
Each check launches a real child process through the production launch code. The
child records variable names and booleans only, never values.
"""

from __future__ import annotations

import email.message
import importlib.util
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
import urllib.request
import urllib.response

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


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_public_repo", Path(__file__).parents[1] / "scripts/validate_public_repo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_local_helper_children_drop_credentials(self) -> None:
        """F4: caffeinate, osascript, vm_stat, git and ps run with no credential.

        Each real helper command is replaced by the stand-in, which records names
        and booleans only. The production code still chooses the environment.
        """
        from slm_math_evaluation import plan_runner
        validator = _load_validator()

        def controlled(real):
            def call(arguments, *args, **kwargs):
                return real([str(self.tool)], *args, **kwargs)
            return call

        with patch.object(plan_runner.sys, "platform", "darwin"), \
                patch.object(plan_runner.subprocess, "Popen", controlled(subprocess.Popen)), \
                patch.object(plan_runner.subprocess, "run", controlled(subprocess.run)), \
                patch.object(validator.subprocess, "run", controlled(subprocess.run)):
            lock = plan_runner.SleepLock()
            self.assertTrue(lock.acquire())                     # caffeinate
            lock.process.wait(timeout=30)
            lock.release()
            plan_runner.notify("synthetic notification")        # osascript
            plan_runner.free_memory_gb()                        # vm_stat
            plan_runner.harness_revision()                      # git rev-parse, git status
            plan_runner._process_command(os.getpid())          # ps
            list(validator.candidate_paths(self.root))          # git ls-files
        entries = _reports(self.tool, "child")
        self.assertEqual(len(entries), 7)
        for entry in entries:
            self.assert_clean(entry, key=False)

    def test_downloader_keeps_its_own_hugging_face_authentication(self) -> None:
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            return _Response(b"content")

        digest = converter.data.sha256_bytes(b"content")
        with patch.object(converter, "_open", side_effect=urlopen), \
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


BEARER = "Bearer " + SENTINELS["HF_TOKEN"]
SIGNED = "?X-Amz-Signature=synthetic-signed-query"


class _Transport:
    """Canned responses by URL, through urllib's real redirect handling; no network."""

    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes]]) -> None:
        self.routes, self.seen = routes, []

    def respond(self, request: urllib.request.Request):
        self.seen.append({"url": request.full_url,
                          "bearer": request.get_header("Authorization") == BEARER,
                          "authorization": request.get_header("Authorization") is not None,
                          "range": request.get_header("Range")})
        code, extra, body = self.routes[request.full_url]
        headers = email.message.Message()
        for name, value in extra.items():
            headers[name] = value
        response = urllib.response.addinfourl(io.BytesIO(body), headers, request.full_url, code)
        response.msg = "fixture"
        return response


def _fake_handlers(transport: _Transport) -> tuple:
    class FakeHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return transport.respond(request)

    class FakeHTTP(urllib.request.HTTPHandler):
        def http_open(self, request):
            return transport.respond(request)

    return FakeHTTPS(), FakeHTTP()


class RedirectScopeTests(unittest.TestCase):
    """F1: a download credential never leaves its HTTPS origin across redirects."""

    HF = "https://huggingface.co/owner/name/resolve/" + "c" * 40 + "/model.safetensors"
    CDN = "https://cdn-lfs.hf.co/repos/x/y" + SIGNED

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"HF_TOKEN": SENTINELS["HF_TOKEN"]})
        environment.start()
        self.addCleanup(environment.stop)

    def download(self, routes: dict, url: str | None = None, body: bytes = b"weights",
                 name: str = "model.safetensors"):
        transport = _Transport(routes)
        real = urllib.request.build_opener
        with patch.object(converter.urllib.request, "build_opener",
                          side_effect=lambda *handlers: real(*_fake_handlers(transport), *handlers)), \
                patch.object(converter, "_space_check"):
            try:
                converter._download(url or self.HF, self.root / name, converter.data.sha256_bytes(body),
                                    self.root / "scratch", self.root / "models")
                error = None
            except converter.data.DataError as caught:
                error = caught
        return transport.seen, error

    @staticmethod
    def found(location: str) -> tuple[int, dict[str, str], bytes]:
        return 302, {"Location": location}, b""

    def test_same_origin_redirect_keeps_the_bearer(self) -> None:
        for index, same in enumerate(("https://huggingface.co/api/resolve-cache/x",
                                      "https://huggingface.co:443/api/resolve-cache/x",
                                      "https://HuggingFace.co/api/resolve-cache/x")):
            with self.subTest(location=same):
                seen, error = self.download({self.HF: self.found(same), same: (200, {}, b"weights")},
                                            name=f"same-{index}")
                self.assertIsNone(error)
                self.assertEqual([hop["bearer"] for hop in seen], [True, True])

    def test_changed_origin_or_port_strips_the_bearer(self) -> None:
        for index, other in enumerate((self.CDN, "https://huggingface.co:8443/x",
                                       "https://huggingface.co.example.test/x", "https://example.test/x")):
            with self.subTest(location=other):
                seen, error = self.download({self.HF: self.found(other), other: (200, {}, b"weights")},
                                            name=f"other-{index}")
                self.assertIsNone(error)
                self.assertEqual([(hop["bearer"], hop["authorization"]) for hop in seen],
                                 [(True, True), (False, False)])

    def test_https_to_http_redirect_is_refused_before_any_request(self) -> None:
        for downgrade in ("http://huggingface.co/x", "http://cdn-lfs.hf.co/x"):
            with self.subTest(location=downgrade):
                seen, error = self.download({self.HF: self.found(downgrade),
                                             downgrade: (200, {}, b"weights")}, name="downgrade")
                self.assertEqual(error.code, 1)
                self.assertEqual([hop["url"] for hop in seen], [self.HF])
                self.assertFalse((self.root / "downgrade").exists())

    def test_a_stripped_bearer_is_never_reattached_along_the_chain(self) -> None:
        back = "https://huggingface.co/api/again"
        seen, error = self.download({self.HF: self.found(self.CDN), self.CDN: self.found(back),
                                     back: (200, {}, b"weights")})
        self.assertIsNone(error)
        self.assertEqual([hop["authorization"] for hop in seen], [True, False, False])
        # A download that starts off the origin never gains the bearer, even on it.
        other = "https://codeload.github.com/owner/name/tar.gz/" + "c" * 40
        seen, error = self.download({other: self.found(self.HF), self.HF: (200, {}, b"archive")},
                                    url=other, body=b"archive", name="archive.tar.gz")
        self.assertIsNone(error)
        self.assertEqual([hop["authorization"] for hop in seen], [False, False])

    def test_resumed_range_follows_a_cdn_redirect_and_the_hash_is_still_checked(self) -> None:
        (self.root / "model.safetensors.partial").write_bytes(b"wei")
        seen, error = self.download({self.HF: self.found(self.CDN),
                                     self.CDN: (206, {"Content-Range": "bytes 3-6/7"}, b"ghts")})
        self.assertIsNone(error)
        self.assertEqual([(hop["range"], hop["bearer"]) for hop in seen],
                         [("bytes=3-", True), ("bytes=3-", False)])
        self.assertEqual((self.root / "model.safetensors").read_bytes(), b"weights")
        (self.root / "model.safetensors").unlink()
        seen, error = self.download({self.HF: self.found(self.CDN), self.CDN: (200, {}, b"tampered")})
        self.assertEqual(error.code, 2)
        self.assertFalse((self.root / "model.safetensors").exists())

    def test_failures_after_a_redirect_name_no_signed_url_or_credential(self) -> None:
        seen, error = self.download({self.HF: self.found(self.CDN), self.CDN: (500, {}, b"")})
        self.assertEqual(error.code, 1)
        message = str(error)
        for secret in (SIGNED, "X-Amz-Signature", SENTINELS["HF_TOKEN"], "cdn-lfs"):
            self.assertNotIn(secret, message)


if __name__ == "__main__":
    unittest.main()
