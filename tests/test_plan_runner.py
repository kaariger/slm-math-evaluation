"""Deterministic fault injection for the §13 runner; no model, server or network.

A fake keyed server, an injected clock and fake sleep-lock, notification and
command children drive the real worker synchronously.
"""

from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from typing import Any
from unittest.mock import patch

import yaml

from slm_math_evaluation import cli, data, plan_runner, runner
from slm_math_evaluation.run_store import load_run


ROOT = Path(__file__).parents[1]
REAL_POPEN = subprocess.Popen
REAL_CHECK_RUNTIME = plan_runner.check_runtime
PROTOCOL = "configs/protocols/fixture-runner.yaml"
SPEC = "configs/models/fixture-runner-spec.yaml"
HARNESS = "a" * 40


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class SimulatedCrash(BaseException):
    """Stands in for a killed worker: no handler records a state."""


class FakeClock:
    def __init__(self) -> None:
        self.awake_s, self.continuous_s, self.tick = 1000.0, 5000.0, 0.001
        self.lock = threading.Lock()
        self.calls = 0

    def awake(self) -> float:
        with self.lock:
            self.awake_s += self.tick
            self.continuous_s += self.tick
            return self.awake_s

    def continuous(self) -> float:
        with self.lock:
            return self.continuous_s

    def utc(self) -> str:
        with self.lock:
            self.calls += 1
            return f"2026-11-03T00:00:{self.calls:06d}+00:00"

    def system_sleep(self, seconds: float) -> None:
        """Advance only continuous time, as a sleeping machine does."""
        with self.lock:
            self.continuous_s += seconds


class FakeSleepLock:
    def __init__(self) -> None:
        self.held = False
        self.history: list[str] = []

    def acquire(self) -> bool:
        self.held = True
        self.history.append("acquire")
        return True

    def release(self) -> None:
        if self.held:
            self.history.append("release")
        self.held = False


class FakeServer:
    count = 0

    def __init__(self, harness: "Harness", arguments, environment, stdout) -> None:
        FakeServer.count += 1
        self.pid = 4_000_000 + FakeServer.count
        self.environment = environment
        self.alive = True
        self.stopped = threading.Event()
        harness.servers.append(self)
        if harness.leak_key and hasattr(stdout, "write"):
            stdout.write(b"log line " + environment["LLAMA_API_KEY"].encode() + b"\n")
            stdout.flush()

    def poll(self):
        return None if self.alive else -15

    def terminate(self) -> None:
        self.alive = False
        self.stopped.set()

    kill = terminate

    def wait(self, timeout=None):
        return -15


class FakeCommand:
    """A command child; ``hold`` polls stay running, then it exits with ``code``."""

    def __init__(self, harness: "Harness", arguments: list[str]) -> None:
        self.harness, self.arguments = harness, arguments
        self.pid = 4_500_000 + len(harness.commands)
        name = arguments[0] if arguments[0] != "model" else arguments[1]
        self.code, self.hold = harness.command_codes.get(name, (0, 0))
        self.returncode = None
        self.polls = 0

    def poll(self):
        if self.returncode is None:
            self.polls += 1
            hook = self.harness.command_hooks.get(self.polls)
            if hook:
                hook()
            if self.polls > self.hold:
                self.returncode = self.code
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class Harness:
    def __init__(self, root: Path, items: int = 3) -> None:
        self.root = root
        self.repo = root / "repo"
        self.clock = FakeClock()
        self.sleep_lock = FakeSleepLock()
        self.notifications: list[str] = []
        self.servers: list[FakeServer] = []
        self.commands: list[list[str]] = []
        self.command_envs: list[dict[str, str] | None] = []
        self.command_codes: dict[str, tuple[int, int]] = {}
        self.command_hooks: dict[int, Any] = {}
        self.killed: list[tuple[int, int]] = []
        self.behaviors: dict[tuple[str, int], str] = {}
        self.generation_calls: list[tuple[str, int]] = []
        self.slots_keys: list[str | None] = []
        self.memory: list[float] = []
        self.leak_key = False
        self.generating = False
        self.decoded = 0
        self.ids = [f"train/item-{n}" for n in range(items)]
        self._write_repository()

    # Repository fixtures
    def _write_repository(self) -> None:
        protocol = json.loads((ROOT / "configs/protocols/protocol.yaml").read_text())
        protocol.update(protocol_version="fixture-runner", role="fixture")
        protocol["model"]["adapter"] = {"id": "qwen3", "version": "1.0.0"}
        protocol["model"]["artifact"]["conversion_record_sha256"] = "d" * 64
        protocol["model"]["chat_template"] = {"source": "embedded", "sha256": "c" * 64}
        protocol["runtime"]["model_alias"] = "fixture-1"
        protocol["sampling"]["context_length"] = {"value": 16384, "provenance": "local-choice"}
        protocol["sampling"]["stop"] = {"value": [], "provenance": "local-choice"}
        protocol["resources"] = {
            "max_memory_gb": {"value": 1, "provenance": "local-choice"},
            "min_free_memory_gb": {"value": 2, "provenance": "local-choice"},
            "stuck_after_awake_s": {"value": 5, "provenance": "local-choice"},
        }
        self.protocol = protocol
        self.protocol_bytes = json.dumps(protocol).encode()
        self.write(PROTOCOL, self.protocol_bytes)
        self.spec_bytes = b"spec_version: '1'\n"
        self.write(SPEC, self.spec_bytes)
        manifest = {"source": protocol["dataset"]["source"],
                    "items": [{"id": item_id, "role": "dev", "subject": "Algebra", "level": 1,
                               "content_sha256": data.content_hash(self.problem(item_id))}
                              for item_id in self.ids],
                    "tiers": {"seed": 0, "smoke": list(self.ids), "validation": []},
                    "exclusions": []}
        self.write(plan_runner.MANIFEST, json.dumps(manifest).encode())

    def write(self, relative: str, content: bytes) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    @staticmethod
    def problem(item_id: str) -> str:
        return f"Problem {item_id}: what is 1 + 1?"

    @property
    def protocol_sha(self) -> str:
        return _sha((self.repo / PROTOCOL).read_bytes())

    @property
    def artifact_sha(self) -> str:
        return self.protocol["model"]["artifact"]["sha256"]

    @property
    def manifest_sha(self) -> str:
        return _sha((self.repo / plan_runner.MANIFEST).read_bytes())

    def steps(self, *kinds: str) -> list[dict]:
        built = []
        for kind in kinds:
            if kind == "verify":
                built.append({"type": "verify", "protocol": PROTOCOL,
                              "protocol_sha256": self.protocol_sha})
            elif kind in ("smoke", "validation"):
                built.append({"type": "run", "protocol": PROTOCOL,
                              "protocol_sha256": self.protocol_sha,
                              "manifest_sha256": self.manifest_sha, "tier": kind,
                              "subset_sha256": None, "k": 1, "seed_base": 0})
            elif kind in ("convert", "sanity"):
                built.append({"type": kind, "spec": SPEC, "spec_sha256": _sha(self.spec_bytes)})
            elif kind == "approval":
                built.append({"type": "approval", "id": "fixture-review"})
            elif kind.startswith("report:"):
                built.append({"type": "report", "of_step": int(kind.split(":")[1])})
        return built

    def plan(self, steps: list[dict], attempts: int = 1, **binding_changes) -> tuple[Path, Path]:
        plan_bytes = yaml.safe_dump({"plan_version": "1", "steps": steps}, sort_keys=False).encode()
        bindings = {"protocols": [self.protocol_sha], "artifacts": [self.artifact_sha],
                    "specs": [_sha(self.spec_bytes)], "manifest": self.manifest_sha,
                    "subsets": [], "runtime_build": "b10412", "harness_revision": HARNESS}
        bindings.update(binding_changes)
        authorization = {"authorization_version": "1", "plan_sha256": _sha(plan_bytes),
                         "scope": "synthetic fixture test; not authority for target evaluation",
                         "bindings": bindings, "attempts_allowed": attempts,
                         "stop_conditions": ["synthetic"], "approved_by": "synthetic-fixture",
                         "approved_utc": "2026-11-03T00:00:00Z"}
        inputs = self.root / "inputs"
        inputs.mkdir(exist_ok=True)
        (inputs / "plan.yaml").write_bytes(plan_bytes)
        (inputs / "authorization.json").write_text(json.dumps(authorization))
        return inputs / "plan.yaml", inputs / "authorization.json"

    # Effects
    def deps(self) -> plan_runner.Deps:
        return plan_runner.Deps(clock=self.clock, sleep=lambda _seconds: None,
                                free_memory_gb=self.free_memory, sleep_lock=self.sleep_lock,
                                notify=self.notifications.append, popen=self.command_popen,
                                killpg=self.killpg, poll_interval=0.001, memory_poll_s=0,
                                child_grace_s=0.05)

    def free_memory(self) -> float:
        return self.memory.pop(0) if self.memory else 64.0

    def command_popen(self, command, **kwargs):
        arguments = command[3:]
        self.commands.append(arguments)
        self.command_envs.append(kwargs.get("env"))
        return FakeCommand(self, arguments)

    def killpg(self, pid: int, number: int) -> None:
        self.killed.append((pid, number))
        for command in (self.last_command,):
            if command is not None and command.pid == pid:
                command.returncode = -number
    last_command: FakeCommand | None = None

    def server_popen(self, arguments, *args, **kwargs):
        if arguments and arguments[0] == "fake-server":
            return FakeServer(self, arguments, kwargs["env"], kwargs.get("stdout"))
        return REAL_POPEN(arguments, *args, **kwargs)

    def control(self, mode: str) -> None:
        state = self.state()
        data.write_json(state.control_path, {"pause": mode, "requested_utc": "fixture"})

    def request(self, _port, method, path, key, payload=None, **_kwargs):
        server = self.servers[-1]
        if not server.alive:
            raise ConnectionRefusedError("fake server stopped")
        if method == "GET" and path == "/health":
            return 200, b"{}"
        if method == "GET" and path == "/props":
            return 200, json.dumps({"default_generation_settings": {"n_ctx": 16384},
                                    "total_slots": 1, "model_alias": "fixture-1",
                                    "ui": False}).encode()
        if method == "GET" and path == "/slots":
            self.slots_keys.append(key)
            if not self.generating:
                return 200, json.dumps([{"id": 0, "is_processing": False}]).encode()
            return 200, json.dumps([{"id": 0, "is_processing": True,
                                     "next_token": [{"n_decoded": self.decoded}]}]).encode()
        assert method == "POST" and path == "/v1/chat/completions", path
        item_id = re.search(r"Problem (\S+):", payload["messages"][0]["content"]).group(1)
        attempt = sum(1 for call in self.generation_calls if call[0] == item_id)
        self.generation_calls.append((item_id, payload["seed"]))
        behavior = self.behaviors.get((item_id, attempt), "ok")
        self.generating = True
        try:
            return self.perform(behavior, server)
        finally:
            self.generating = False

    def perform(self, behavior: str, server: FakeServer):
        ok = (200, json.dumps({"choices": [{"message": {"content": "work \\boxed{2}"},
                                            "finish_reason": "stop"}],
                               "usage": {"completion_tokens": 3}}).encode())
        if behavior == "ok":
            return ok
        if behavior == "progress":
            for _ in range(20):
                self.decoded += 1
                time.sleep(0.002)
            return ok
        if behavior == "pause_after":
            self.control("after_item")
            return ok
        if behavior in ("pause_now", "stuck"):
            if behavior == "pause_now":
                self.control("now")
            server.stopped.wait(10)
            raise ConnectionResetError("server stopped mid-request")
        if behavior == "crash":
            server.alive = False
            raise ConnectionResetError("server crashed")
        if behavior == "error500":
            return 500, b"{}"
        if behavior == "sleep_fail":
            self.clock.system_sleep(30)
            time.sleep(0.01)
            raise ConnectionResetError("lost across sleep")
        if behavior == "sleep_ok":
            self.clock.system_sleep(30)
            time.sleep(0.01)
            return ok
        if behavior == "crash_worker":
            raise SimulatedCrash()
        if behavior == "keyboard_interrupt":  # SIGINT, or SIGTERM/SIGHUP via the cleanup handler.
            raise KeyboardInterrupt
        raise AssertionError(behavior)

    # Inspection
    def state(self) -> plan_runner.State:
        states = plan_runner._plans()
        assert len(states) == 1, states
        return states[0]

    def status(self) -> dict:
        return self.state().status()

    def log(self) -> list[dict]:
        lines, _ = self.state().lines()
        return [json.loads(line) for line in lines]

    def events(self, name: str) -> list[dict]:
        return [entry for entry in self.log() if entry["event"] == name]

    def run(self):
        return load_run(self.status()["run_id"])

    def verification(self, protocol_sha: str) -> bytes:
        return json.dumps({"protocol_sha256": protocol_sha,
                           "model_artifact_sha256": self.artifact_sha,
                           "runtime_build": "b10412", "contract_version": "v0.8.1",
                           "status": "passed"}).encode()


def _sync_launch(state: plan_runner.State, deps: plan_runner.Deps) -> str:
    return plan_runner.Worker(state, deps).run()


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def bind(self, _address):
        pass

    def getsockname(self):
        return ("127.0.0.1", 55555)


class RunnerHarnessCase(unittest.TestCase):
    items = 3

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.h = Harness(self.root, self.items)
        h = self.h
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.addCleanup(self.temporary.cleanup)
        rows = {item_id: {"unique_id": item_id, "problem": h.problem(item_id), "answer": "2"}
                for item_id in h.ids}

        def verification_copies(protocol_sha, _protocol):
            return {name: h.verification(protocol_sha)
                    for name in ("runtime-verification.json", "artifact-verification.json")}

        stack.enter_context(patch.dict(os.environ, {"SLM_EVAL_RUNS": str(self.root / "runs"),
                                                    "SLM_EVAL_CACHE": str(self.root / "cache")}))
        for target, name, value in (
                (plan_runner, "REPO", h.repo),
                (plan_runner, "harness_revision", lambda: HARNESS),
                (plan_runner, "check_runtime", lambda: None),
                (runner, "_verification_copies", verification_copies),
                (runner, "_source_rows", lambda _manifest, ids: {i: rows[i] for i in ids}),
                (runner, "_host", lambda: {"os": "fixture", "arch": "fixture", "memory_gb": 64}),
                (runner, "_check_runtime_version", lambda _server: None),
                (runner.runtime, "pinned_paths", lambda _p: (Path("fake-server"), Path("model"))),
                (runner.runtime, "sha256_file", lambda _path: h.artifact_sha),
                (runner.runtime, "verify_chat_template", lambda _p, _m: "c" * 64),
                (runner.runtime, "server_arguments", lambda *_a: [
                    "fake-server", "--threads", "8", "--threads-batch", "8",
                    "--threads-http", "7", "--n-gpu-layers", "99", "--log-verbosity", "3"]),
                (runner.runtime, "render_prompt", lambda _port, _key, messages, _p:
                    "<rendered>" + messages[0]["content"]),
                (runner.runtime, "confirm_sampling", lambda *_a: {}),
                (runner.runtime, "request", h.request),
                (runner.socket, "socket", _FakeSocket),
                (runner.subprocess, "Popen", h.server_popen)):
            stack.enter_context(patch.object(target, name, value))
        original_command_popen = h.command_popen

        def tracking_popen(command, **kwargs):
            process = original_command_popen(command, **kwargs)
            h.last_command = process
            return process

        h.command_popen = tracking_popen
        self.output = io.StringIO()
        stack.enter_context(redirect_stdout(self.output))

    def start(self, steps: list[dict], **plan_options) -> list[str]:
        plan, authorization = self.h.plan(steps, **plan_options)
        return plan_runner.start(plan, authorization, self.h.deps(), _sync_launch)

    def resume(self) -> list[str]:
        return plan_runner.resume(self.h.deps(), _sync_launch)

    def assert_exit(self, code: int, function, *args, **kwargs):
        with self.assertRaises(data.DataError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        return caught.exception

    def assert_items(self, expected_ids: list[str] | None = None) -> list[dict]:
        run = self.h.run()
        pairs = [(row["item_id"], row["sample_index"]) for row in run.items]
        self.assertEqual(sorted(pairs), sorted((i, 0) for i in (expected_ids or self.h.ids)))
        return run.items


class PlanValidationTests(RunnerHarnessCase):
    def test_start_rejects_malformed_unbound_and_mismatched_inputs(self) -> None:
        h = self.h
        steps = h.steps("verify", "smoke")
        plan, authorization = h.plan(steps)
        start = lambda p=plan, a=authorization: plan_runner.start(p, a, h.deps(), _sync_launch)
        bad = self.root / "bad.yaml"
        for content in (b"plan_version: '1'\nsteps: []\n", b"[unclosed",
                        yaml.safe_dump({"plan_version": "1", "steps": [{**steps[0], "retries": 2}]}).encode(),
                        yaml.safe_dump({"plan_version": "1", "steps": [{**steps[1], "k": 0}]}).encode(),
                        yaml.safe_dump({"plan_version": "1", "steps": [
                            {**steps[0], "protocol": "/abs/protocol.yaml"}]}).encode(),
                        yaml.safe_dump({"plan_version": "1", "steps": [
                            {**steps[0], "protocol": "../outside.yaml"}]}).encode(),
                        yaml.safe_dump({"plan_version": "1", "steps": [
                            steps[0], {"type": "report", "of_step": 1}]}).encode()):
            with self.subTest(content=content[:60]):
                bad.write_bytes(content)
                self.assert_exit(3, start, bad, authorization)
        # Plan SHA-256 differs from the authorization.
        plan.write_bytes(plan.read_bytes() + b"# edited\n")
        self.assert_exit(2, start)
        for change, code in (({"protocols": []}, 2), ({"artifacts": []}, 2),
                             ({"manifest": "e" * 64}, 2), ({"runtime_build": "b1"}, 2),
                             ({"harness_revision": "b" * 40}, 2)):
            with self.subTest(change=change):
                plan, authorization = h.plan(steps, **change)
                self.assert_exit(code, start, plan, authorization)
        authorization_document = json.loads(authorization.read_text())
        authorization_document["extra"] = True
        authorization.write_text(json.dumps(authorization_document))
        self.assert_exit(3, start, plan, authorization)
        # A validation tier before this model's smoke tier; a subset run whose
        # committed subset file is missing (SubsetRunnerTests covers subsets).
        plan, authorization = h.plan(h.steps("verify", "validation", "smoke"))
        self.assert_exit(3, start, plan, authorization)
        subset = h.steps("smoke")
        subset[0]["subset_sha256"] = "f" * 64
        plan, authorization = h.plan(subset, subsets=["f" * 64])
        self.assert_exit(3, start, plan, authorization)
        # Recovery ruling 3: the plan's manifest hash is checked against the bytes of
        # the one committed manifest; a changed or missing file is refused.
        plan, authorization = h.plan(steps)
        manifest = (h.repo / plan_runner.MANIFEST).read_bytes()
        h.write(plan_runner.MANIFEST, manifest + b"\n")
        self.assert_exit(2, start, plan, authorization)
        (h.repo / plan_runner.MANIFEST).unlink()
        self.assert_exit(3, start, plan, authorization)
        h.write(plan_runner.MANIFEST, manifest)
        # A pinned repository file that no longer matches, or is missing.
        plan, authorization = h.plan(steps)
        h.write(PROTOCOL, h.protocol_bytes + b" ")
        self.assert_exit(2, start, plan, authorization)
        (h.repo / PROTOCOL).unlink()
        self.assert_exit(3, start, plan, authorization)
        self.assertFalse((self.root / "runs" / "_runner").exists())

    def test_cli_usage_errors_and_missing_state_exit_three(self) -> None:
        for argv in (["runner"], ["runner", "start", "--plan", "p"], ["runner", "pause", "--later"],
                     ["runner", "status"], ["runner", "resume"], ["runner", "pause"]):
            with self.subTest(argv=argv), redirect_stdout(io.StringIO()), \
                    patch.object(sys, "stderr", io.StringIO()):
                self.assertEqual(cli.main(argv), 3)


class RunnerLifecycleTests(RunnerHarnessCase):
    def test_stored_copies_hash_chain_approval_and_completion(self) -> None:
        h = self.h
        plan, authorization = h.plan(h.steps("verify", "smoke", "approval"))
        printed = plan_runner.start(plan, authorization, h.deps(), _sync_launch)
        plan_sha = _sha(plan.read_bytes())
        self.assertEqual(printed[0], plan_sha)
        state = h.state()
        self.assertEqual(state.directory, self.root / "runs" / "_runner" / plan_sha)
        self.assertEqual(state.plan_path.read_bytes(), plan.read_bytes())
        self.assertEqual(state.authorization_path.read_bytes(), authorization.read_bytes())
        status = h.status()
        self.assertEqual((status["state"], status["completed_steps"], status["step"]),
                         ("waiting_approval", 2, 3))
        self.assertEqual(status["authorization_sha256"], _sha(authorization.read_bytes()))
        self.assertEqual(h.commands, [["verify-runtime", "--protocol", PROTOCOL],
                                      ["verify-artifact", "--protocol", PROTOCOL]])
        self.assertEqual(h.sleep_lock.history, ["acquire", "release"])
        self.assertEqual(len(h.notifications), 1)
        self.assertIn("approval", h.notifications[0])
        items = self.assert_items()
        self.assertTrue(all(row["infra_retries"] == 0 for row in items))
        self.assertEqual(h.run().metadata["status"], "completed")
        self.assertTrue(all(key for key in h.slots_keys))  # Progress polls are keyed.
        # The originals may now move or vanish: later commands use the copies.
        plan.unlink()
        authorization.unlink()
        self.resume()
        status = h.status()
        self.assertEqual((status["state"], status["completed_steps"]), ("completed", 3))
        self.assertEqual(h.events("approved")[0]["detail"], {"id": "fixture-review"})
        self.assertEqual(len(h.notifications), 2)
        # Hash chain: first prev is zeros; each prev hashes the previous line's bytes.
        lines, torn = state.lines()
        self.assertEqual(torn, b"")
        self.assertEqual(json.loads(lines[0])["prev_sha256"], "0" * 64)
        for previous, line in zip(lines, lines[1:]):
            self.assertEqual(json.loads(line)["prev_sha256"], _sha(previous))
        report = plan_runner.status_report()
        self.assertEqual(report["attempt_log_head_sha256"], _sha(lines[-1]))
        self.assertTrue(report["attempt_log_chain_valid"])
        self.assertEqual(report["recent_events"][-1]["event"], "completed")
        # status writes nothing.
        snapshot = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                          for p in state.directory.iterdir())
        with redirect_stdout(io.StringIO()) as printed_status:
            self.assertEqual(cli.main(["runner", "status"]), 0)
        self.assertEqual(json.loads(printed_status.getvalue())["state"], "completed")
        self.assertEqual(snapshot, sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                                          for p in state.directory.iterdir()))
        # A completed plan is not resumed or restarted.
        self.assert_exit(3, self.resume)
        plan, authorization = h.plan(h.steps("verify", "smoke", "approval"))
        self.assert_exit(3, plan_runner.start, plan, authorization, h.deps(), _sync_launch)

    def test_empty_log_head_is_zeros_and_tampering_is_detected(self) -> None:
        state = plan_runner.State(self.root / "state")
        state.directory.mkdir()
        self.assertEqual(state.head(), "0" * 64)
        self.assertEqual(state.verify_chain(), "0" * 64)
        for event in ("one", "two", "three"):
            state.append(self.h.clock, 1, event, {"n": event})
        lines, _ = state.lines()
        self.assertEqual(state.head(), _sha(lines[-1]))
        raw = state.log_path.read_bytes()
        state.log_path.write_bytes(raw.replace(b'"n":"two"', b'"n":"TWO"'))
        self.assert_exit(2, state.verify_chain)
        state.log_path.write_bytes(raw.replace(lines[1] + b"\n", b""))
        self.assert_exit(2, state.verify_chain)
        # A torn, unterminated tail is dropped on the next append and recorded.
        state.log_path.write_bytes(raw + b'{"seq":4,"prev')
        self.assertEqual(state.verify_chain(), _sha(lines[-1]))
        state.append(self.h.clock, 1, "four")
        entries = [json.loads(line) for line in state.lines()[0]]
        self.assertEqual([entry["event"] for entry in entries[3:]],
                         ["torn_log_tail_discarded", "four"])
        self.assertEqual(entries[3]["detail"]["sha256"], _sha(b'{"seq":4,"prev'))
        state.verify_chain()

    def test_stored_copy_tampering_and_step_input_change(self) -> None:
        h = self.h
        h.behaviors[(h.ids[1], 0)] = "pause_after"
        self.start(h.steps("verify", "smoke"))
        self.assertEqual(h.status()["state"], "paused")
        state = h.state()
        original = state.plan_path.read_bytes()
        state.plan_path.write_bytes(original + b"# tampered\n")
        self.assert_exit(2, self.resume)
        state.plan_path.write_bytes(original)
        authorization = state.authorization_path.read_bytes()
        state.authorization_path.write_bytes(authorization.replace(b"synthetic-fixture", b"someone"))
        self.assert_exit(2, self.resume)
        state.authorization_path.write_bytes(authorization)
        # A protocol or committed manifest changed after start stops resume.
        manifest = (h.repo / plan_runner.MANIFEST).read_bytes()
        h.write(plan_runner.MANIFEST, manifest + b" ")
        self.assert_exit(2, self.resume)
        h.write(plan_runner.MANIFEST, manifest)
        h.write(PROTOCOL, h.protocol_bytes + b"\n")
        self.assert_exit(2, self.resume)
        self.assertEqual(h.status()["state"], "paused")

    def test_protocol_change_before_its_step_stops_the_plan(self) -> None:
        h = self.h
        h.command_hooks[1] = lambda: h.write(PROTOCOL, h.protocol_bytes + b"\n")
        self.start(h.steps("verify", "smoke"))
        status = h.status()
        self.assertEqual((status["state"], status["step"]), ("stopped", 2))
        self.assertEqual(status["last_event"]["detail"]["exit_code"], 2)
        self.assertEqual(h.generation_calls, [])
        self.assertIn("stopped", h.notifications[-1])
        self.assertFalse(h.sleep_lock.held)


class PauseAndRecoveryTests(RunnerHarnessCase):
    def test_pause_after_item_completes_it_then_resumes(self) -> None:
        h = self.h
        h.behaviors[(h.ids[1], 0)] = "pause_after"
        self.start(h.steps("smoke"))
        status = h.status()
        self.assertEqual(status["state"], "paused")
        self.assertEqual(status["progress"], {"done": 2, "total": 3})
        self.assert_items(h.ids[:2])
        run = h.run()
        self.assertEqual(run.metadata["status"], "paused")
        self.assertEqual(run.metadata["attempts"][-1]["status"], "paused")
        self.assertEqual([e["detail"] for e in run.metadata["events"] if e["type"] == "pause"],
                         [{"mode": "after_item"}])
        self.assertFalse(h.servers[-1].alive)
        self.assertEqual(h.sleep_lock.history, ["acquire", "release"])
        self.assertFalse(h.state().control_path.exists())
        self.assert_exit(3, plan_runner.pause, False, h.deps())  # Already paused.
        self.resume()
        self.assertEqual(h.status()["state"], "completed")
        self.assert_items()
        self.assertEqual([item for item, _ in h.generation_calls], h.ids)
        attempts = h.run().metadata["attempts"]
        self.assertEqual([(a["status"], a["resumed_from"]) for a in attempts],
                         [("paused", None), ("completed", 1)])

    def test_pause_now_discards_only_the_item_in_progress(self) -> None:
        h = self.h
        h.behaviors[(h.ids[1], 0)] = "pause_now"
        self.start(h.steps("smoke"))
        self.assertEqual(h.status()["state"], "paused")
        self.assert_items(h.ids[:1])
        pause = [e for e in h.run().metadata["events"] if e["type"] == "pause"]
        self.assertEqual(pause[0]["detail"], {"mode": "now", "discarded": {
            "item_id": h.ids[1], "sample_index": 0}})
        self.assertFalse(h.servers[-1].alive)
        self.resume()
        self.assertEqual(h.status()["state"], "completed")
        items = self.assert_items()
        self.assertEqual([item for item, _ in h.generation_calls],
                         [h.ids[0], h.ids[1], h.ids[1], h.ids[2]])
        self.assertTrue(all(row["infra_retries"] == 0 for row in items))

    def test_pause_now_interrupts_a_command_step(self) -> None:
        h = self.h
        h.command_codes["convert"] = (0, 10_000)
        h.command_hooks[3] = lambda: h.control("now")
        self.start(h.steps("convert", "smoke"))
        status = h.status()
        self.assertEqual((status["state"], status["completed_steps"]), ("paused", 0))
        self.assertEqual(h.killed[0][1], 2)  # SIGINT lets the converter clean up.
        h.command_codes["convert"] = (0, 0)
        h.command_hooks.clear()
        self.resume()
        self.assertEqual(h.status()["state"], "completed")
        self.assertEqual([c[:2] for c in h.commands], [["model", "convert"], ["model", "convert"]])

    def test_pause_command_requires_a_live_worker(self) -> None:
        h = self.h
        h.behaviors[(h.ids[0], 0)] = "crash_worker"
        with self.assertRaises(SimulatedCrash):
            self.start(h.steps("smoke"))
        self.assertEqual(h.status()["state"], "running")
        self.assert_exit(3, plan_runner.pause, True, h.deps())
        with patch.object(plan_runner.State, "worker_alive", return_value=True):
            printed = plan_runner.pause(True, h.deps())
        self.assertEqual(h.state().requested_pause(), "now")
        self.assertEqual(printed, [str(h.state().control_path), str(h.state().log_path)])
        self.assertEqual(h.events("pause_requested")[-1]["detail"], {"mode": "now"})

    def test_kill_resume_regenerates_only_the_item_in_progress(self) -> None:
        h = self.h
        h.behaviors[(h.ids[1], 0)] = "crash_worker"
        with self.assertRaises(SimulatedCrash):
            self.start(h.steps("verify", "smoke"))
        self.assertEqual(h.status()["state"], "running")  # Nothing recorded the crash.
        self.assertFalse(h.state().worker_alive())
        report = plan_runner.status_report()
        self.assertFalse(report["worker_running"])
        # A crash mid-write leaves an unterminated item fragment and log tail.
        directory = h.run().directory
        with (directory / "items.jsonl").open("ab") as stream:
            stream.write(b'{"item_id":"' + h.ids[1].encode() + b'","sample_')
        with h.state().log_path.open("ab") as stream:
            stream.write(b'{"seq":99')
        self.resume()
        status = h.status()
        self.assertEqual(status["state"], "completed")
        items = self.assert_items()
        self.assertEqual([item for item, _ in h.generation_calls],
                         [h.ids[0], h.ids[1], h.ids[1], h.ids[2]])
        self.assertTrue(all(row["infra_retries"] == 0 for row in items))
        events = [entry["event"] for entry in h.log()]
        self.assertIn("torn_log_tail_discarded", events)
        self.assertLess(events.index("interrupted"), events.index("resumed"))
        run = h.run()
        partial = [e["detail"] for e in run.metadata["events"]
                   if e["type"] == "interrupted" and e["detail"].get("reason") ==
                   "partial_item_write_discarded"]
        self.assertEqual(len(partial), 1)
        ended = [e["detail"] for e in run.metadata["events"] if e["type"] == "interrupted"
                 and e["detail"].get("reason") == "previous_attempt_ended"]
        self.assertEqual(ended, [{"reason": "previous_attempt_ended", "attempt": 1}])
        self.assertEqual([(a["status"], a["resumed_from"]) for a in run.metadata["attempts"]],
                         [("interrupted", None), ("completed", 1)])
        self.assertEqual(h.commands.count(["verify-runtime", "--protocol", PROTOCOL]), 1)
        h.state().verify_chain()

    def test_attempts_allowed_bounds_restarts_after_a_stop(self) -> None:
        h = self.h
        h.command_codes["sanity"] = (1, 0)
        steps = h.steps("sanity", "smoke")
        plan, authorization = h.plan(steps, attempts=2)
        plan_runner.start(plan, authorization, h.deps(), _sync_launch)
        self.assertEqual(h.status()["state"], "stopped")
        self.assert_exit(3, self.resume)  # A stopped plan is not resumable.
        h.command_codes["sanity"] = (1, 0)
        plan_runner.start(plan, authorization, h.deps(), _sync_launch)
        self.assertEqual((h.status()["state"], h.status()["starts"]), ("stopped", 2))
        self.assert_exit(3, plan_runner.start, plan, authorization, h.deps(), _sync_launch)


class InfrastructureTests(RunnerHarnessCase):
    def test_retries_are_recorded_and_the_first_completion_is_scored(self) -> None:
        h = self.h
        h.behaviors[(h.ids[1], 0)] = "crash"
        h.behaviors[(h.ids[1], 1)] = "error500"
        self.start(h.steps("smoke"))
        self.assertEqual(h.status()["state"], "completed")
        items = {row["item_id"]: row for row in self.assert_items()}
        self.assertEqual(items[h.ids[1]]["infra_retries"], 2)
        self.assertEqual(items[h.ids[0]]["infra_retries"], 0)
        events = h.run().metadata["events"]
        retries = [e["detail"] for e in events if e["type"] == "infra_retry"]
        self.assertEqual([(r["reason"], r["retry"]) for r in retries],
                         [("server_crash", 1), ("server_error", 2)])
        self.assertEqual(sum(e["type"] == "server_restart" for e in events), 2)
        self.assertEqual(len(h.servers), 3)
        self.assertEqual(h.run().metadata["infra_failures"], [])

    def test_stuck_item_detected_on_awake_clock_then_five_percent_stop(self) -> None:
        h = self.h
        h.clock.tick = 0.5
        for attempt in range(3):
            h.behaviors[(h.ids[0], attempt)] = "stuck"
        self.start(h.steps("smoke", "approval"))
        status = h.status()
        self.assertEqual((status["state"], status["step"]), ("stopped", 1))
        self.assertIsNone(status["last_event"]["detail"]["exit_code"])
        self.assertIn("5%", status["last_event"]["detail"]["reason"])
        run = h.run()
        self.assertEqual(run.metadata["status"], "stopped")
        self.assertEqual(run.metadata["infra_failures"],
                         [{"item_id": h.ids[0], "sample_index": 0, "reason": "stuck", "retries": 2}])
        self.assertEqual(len(h.generation_calls), 3)
        self.assertTrue(h.slots_keys and all(h.slots_keys))
        self.assertIn("stopped", h.notifications[-1])
        self.assertFalse(h.sleep_lock.held)
        self.assertFalse(h.servers[-1].alive)

    def test_progress_resets_the_stuck_clock(self) -> None:
        h = self.h
        h.clock.tick = 0.05
        h.behaviors[(h.ids[0], 0)] = "progress"
        self.start(h.steps("smoke"))
        self.assertEqual(h.status()["state"], "completed")
        self.assertEqual(h.run().metadata["infra_failures"], [])


class FivePercentRuleTests(RunnerHarnessCase):
    items = 21

    def test_one_exhausted_item_continues_and_the_second_stops(self) -> None:
        h = self.h
        for item in (h.ids[2], h.ids[5]):
            for attempt in range(3):
                h.behaviors[(item, attempt)] = "error500"
        self.start(h.steps("smoke"))
        status = h.status()
        self.assertEqual(status["state"], "stopped")
        run = h.run()
        self.assertEqual([f["item_id"] for f in run.metadata["infra_failures"]], [h.ids[2], h.ids[5]])
        self.assertEqual(len(run.items), 4)  # Items 0, 1, 3 and 4 completed; 2 failed earlier.
        self.assertEqual(status["progress"], {"done": 6, "total": 21})


class InterruptionAndResourceTests(RunnerHarnessCase):
    def test_sleep_gap_is_an_interruption_not_a_retry(self) -> None:
        h = self.h
        h.behaviors[(h.ids[0], 0)] = "sleep_ok"
        h.behaviors[(h.ids[1], 0)] = "sleep_fail"
        self.start(h.steps("smoke"))
        self.assertEqual(h.status()["state"], "completed")
        items = {row["item_id"]: row for row in self.assert_items()}
        self.assertEqual(items[h.ids[1]]["infra_retries"], 0)
        events = h.run().metadata["events"]
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds.count("sleep"), 2)
        self.assertEqual(kinds.count("wake"), 2)
        self.assertEqual([e["detail"]["reason"] for e in events if e["type"] == "interrupted"],
                         ["sleep"])
        self.assertNotIn("infra_retry", kinds)
        sleep = next(e for e in events if e["type"] == "sleep")
        self.assertGreaterEqual(sleep["detail"]["slept_s"], 30)
        # Thirty seconds asleep exceed the five-second stuck limit on a wall clock,
        # but not on the awake clock: the slept item still completed.
        self.assertTrue(items[h.ids[0]]["awake_ms"] < items[h.ids[0]]["timing_ms"])

    def test_awake_clock_source(self) -> None:
        clock = plan_runner.Clock()
        if sys.platform == "darwin":
            self.assertEqual(clock._awake, plan_runner.time.CLOCK_UPTIME_RAW)
        self.assertLessEqual(clock.awake(), clock.awake())

    def test_memory_floor_waits_and_never_fails(self) -> None:
        h = self.h
        h.memory = [64, 1.0, 1.5, 0.5, 8.0]
        self.start(h.steps("smoke"))
        self.assertEqual(h.status()["state"], "completed")
        waits = [e["detail"] for e in h.run().metadata["events"] if e["type"] == "memory_wait"]
        self.assertEqual(waits[0], {"free_gb": 1.0, "floor_gb": 2})
        self.assertTrue(waits[1]["cleared"])
        self.assertEqual(len(waits), 2)
        self.assert_items()

    def test_pause_now_during_memory_wait(self) -> None:
        h = self.h
        h.memory = [64, 1.0, 1.0]

        def free():
            if not h.memory:
                h.control("now")
                return 1.0
            return h.memory.pop(0)

        h.free_memory = free
        self.start(h.steps("smoke"))
        self.assertEqual(h.status()["state"], "paused")
        self.assert_items(h.ids[:1])

    def test_disk_floor_breach_and_command_exit_codes(self) -> None:
        for name, kind, code, state in (("convert", "convert", 3, "stopped"),
                                        ("convert", "convert", 1, "interrupted"),
                                        ("verify-runtime", "verify", 4, "stopped"),
                                        ("sanity", "sanity", 1, "stopped")):
            with self.subTest(name=name, code=code):
                self.tearDown_state()
                self.h.command_codes = {name: (code, 2)}
                self.start(self.h.steps(kind, "smoke"))
                status = self.h.status()
                self.assertEqual(status["state"], state)
                self.assertEqual(status["completed_steps"], 0)
                self.assertEqual(status["last_event"]["detail"]["exit_code"], code)
                self.assertFalse(self.h.sleep_lock.held)
                self.assertEqual(self.h.generation_calls, [])

    def tearDown_state(self) -> None:
        import shutil
        shutil.rmtree(self.root / "runs", ignore_errors=True)
        self.h.commands.clear()

    def test_report_step_runs_report_for_its_run(self) -> None:
        h = self.h
        self.start(h.steps("smoke", "report:1"))
        self.assertEqual(h.status()["state"], "completed")
        self.assertEqual(h.commands, [["report", "--run", h.status()["step_runs"]["1"]]])

    def test_server_log_key_is_redacted_and_stops_the_plan(self) -> None:
        h = self.h
        h.leak_key = True
        self.start(h.steps("smoke"))
        status = h.status()
        self.assertEqual((status["state"], status["last_event"]["detail"]["exit_code"]),
                         ("stopped", 4))
        self.assertIn(b"[redacted]", h.state().server_log.read_bytes())


class SubsetRunnerTests(RunnerHarnessCase):
    """§3 ``run --subset`` and §13.1 subset_sha256, on synthetic tiers only."""

    items = 7

    def setUp(self) -> None:
        super().setUp()
        h = self.h
        manifest = json.loads((h.repo / plan_runner.MANIFEST).read_text())
        for index, item in enumerate(manifest["items"]):
            item["subject"] = "Algebra" if index % 2 else "Geometry"
        manifest["tiers"].update(smoke=h.ids[:2], validation=h.ids[2:])
        h.write(plan_runner.MANIFEST, json.dumps(manifest).encode())
        self.subset_path = h.repo / plan_runner.SUBSET
        data.build_subset(h.repo / plan_runner.MANIFEST, "validation", 3, "subject", 5,
                          self.subset_path)
        self.subset_bytes = self.subset_path.read_bytes()
        self.subset = json.loads(self.subset_bytes)
        self.subset_sha = _sha(self.subset_bytes)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir(exist_ok=True)

    def subset_steps(self, digest: str | None = None) -> list[dict]:
        steps = self.h.steps("smoke", "validation")
        steps[1]["subset_sha256"] = digest or self.subset_sha
        return steps

    def run_directories(self) -> list[Path]:
        runs = self.root / "runs"
        return sorted(p for p in runs.iterdir() if not p.name.startswith("_")) if runs.is_dir() else []

    def variant(self, name: str, **changes: Any) -> Path:
        path = self.inputs / name
        path.write_bytes(data.canonical_json({**self.subset, **changes}))
        return path

    def cli_run(self, *extra: str, protocol: Path | None = None, tier: str = "validation") -> int:
        argv = ["run", "--protocol", str(protocol or self.h.repo / PROTOCOL),
                "--manifest", str(self.h.repo / plan_runner.MANIFEST), "--tier", tier, *extra]
        with patch.object(sys, "stderr", io.StringIO()):
            return cli.main(argv)

    def test_subset_ids_are_an_ordered_strict_subset(self) -> None:
        validation = self.h.ids[2:]
        self.assertEqual(len(self.subset["ids"]), 3)
        self.assertEqual(self.subset["ids"], [i for i in validation if i in self.subset["ids"]])

    def test_plan_subset_run_generates_only_subset_ids(self) -> None:
        h = self.h
        self.start(self.subset_steps(), subsets=[self.subset_sha])
        status = h.status()
        self.assertEqual((status["state"], status["progress"]), ("completed", {"done": 3, "total": 3}))
        run = load_run(status["step_runs"]["2"])
        self.assertEqual(run.metadata["subset_sha256"], self.subset_sha)
        self.assertEqual((run.directory / "subset.json").read_bytes(), self.subset_bytes)
        self.assertEqual([row["item_id"] for row in run.items], self.subset["ids"])
        self.assertEqual([item for item, _ in h.generation_calls], h.ids[:2] + self.subset["ids"])
        smoke = load_run(status["step_runs"]["1"])
        self.assertIsNone(smoke.metadata["subset_sha256"])
        self.assertFalse((smoke.directory / "subset.json").exists())

    def test_paused_subset_run_resumes_from_its_stored_binding(self) -> None:
        h = self.h
        h.behaviors[(self.subset["ids"][1], 0)] = "pause_after"
        self.start(self.subset_steps(), subsets=[self.subset_sha])
        self.assertEqual(h.status()["state"], "paused")
        # The committed subset is re-verified on resume (§13.4).
        h.write(plan_runner.SUBSET, self.subset_bytes + b" ")
        self.assert_exit(2, self.resume)
        h.write(plan_runner.SUBSET, self.subset_bytes)
        self.resume()
        self.assertEqual(h.status()["state"], "completed")
        run = load_run(h.status()["step_runs"]["2"])
        self.assertEqual(sorted(row["item_id"] for row in run.items), sorted(self.subset["ids"]))
        self.assertEqual([item for item, _ in h.generation_calls], h.ids[:2] + self.subset["ids"])
        self.assertEqual([(a["status"], a["resumed_from"]) for a in run.metadata["attempts"]],
                         [("paused", None), ("completed", 1)])

    def test_tampered_stored_subset_binding_stops_the_plan(self) -> None:
        h = self.h
        for target in ("copy", "metadata"):
            with self.subTest(target=target):
                import shutil
                shutil.rmtree(self.root / "runs", ignore_errors=True)
                h.generation_calls.clear()  # Behaviors are keyed by attempt number.
                h.behaviors = {(self.subset["ids"][0], 0): "pause_after"}
                self.start(self.subset_steps(), subsets=[self.subset_sha])
                run = load_run(h.status()["step_runs"]["2"])
                if target == "copy":
                    (run.directory / "subset.json").write_bytes(self.subset_bytes + b" ")
                else:
                    data.write_json(run.directory / "run.json", {**run.metadata, "subset_sha256": None})
                self.resume()
                status = h.status()
                self.assertEqual((status["state"], status["last_event"]["detail"]["exit_code"]),
                                 ("stopped", 2))

    def test_plan_start_refuses_unbound_mismatched_or_misbound_subsets(self) -> None:
        h = self.h
        bound = {"subsets": [self.subset_sha]}
        self.assert_exit(2, self.start, self.subset_steps(), subsets=[])          # unbound hash
        self.assert_exit(2, self.start, self.subset_steps("e" * 64), subsets=["e" * 64])
        smoke = h.steps("smoke")
        smoke[0]["subset_sha256"] = self.subset_sha                              # tier differs
        self.assert_exit(2, self.start, smoke, **bound)
        for changes, code in (({"manifest_sha256": "e" * 64}, 2), ({"ids": self.subset["ids"][::-1]}, 2),
                              ({"extra": True}, 3), ({"size": 2}, 3)):
            with self.subTest(changes=changes):
                content = data.canonical_json({**self.subset, **changes})
                h.write(plan_runner.SUBSET, content)
                self.assert_exit(code, self.start, self.subset_steps(_sha(content)),
                                 subsets=[_sha(content)])
        h.write(plan_runner.SUBSET, b"{not json")
        self.assert_exit(3, self.start, self.subset_steps(_sha(b"{not json")),
                         subsets=[_sha(b"{not json")])
        self.assertFalse((self.root / "runs" / "_runner").exists())
        self.assertEqual(h.generation_calls, [])

    def test_direct_run_subset_generates_binds_and_resumes(self) -> None:
        h = self.h
        self.assertEqual(self.cli_run("--subset", str(self.subset_path)), 0)
        run_id = self.output.getvalue().split()[-1]
        run = load_run(run_id)
        self.assertEqual(run.metadata["subset_sha256"], self.subset_sha)
        self.assertEqual((run.directory / "subset.json").read_bytes(), self.subset_bytes)
        self.assertEqual([row["item_id"] for row in run.items], self.subset["ids"])
        # An interrupted subset run resumes only its missing subset pair.
        lines = (run.directory / "items.jsonl").read_bytes().splitlines(keepends=True)
        (run.directory / "items.jsonl").write_bytes(b"".join(lines[:-1]))
        data.write_json(run.directory / "run.json", {**run.metadata, "status": "interrupted"})
        self.assertEqual(runner.resume(run_id), run_id)
        self.assertEqual([item for item, _ in h.generation_calls],
                         self.subset["ids"] + self.subset["ids"][-1:])
        self.assertEqual(load_run(run_id).metadata["status"], "completed")
        # The stored copy is bound by hash: a changed copy exits 2, a missing one 3.
        data.write_json(run.directory / "run.json", {**load_run(run_id).metadata, "status": "interrupted"})
        (run.directory / "subset.json").write_bytes(self.subset_bytes + b" ")
        self.assert_exit(2, runner.resume, run_id)
        (run.directory / "subset.json").unlink()
        self.assert_exit(3, runner.resume, run_id)

    def test_direct_run_refuses_mismatched_and_malformed_subsets(self) -> None:
        h = self.h
        legacy = self.inputs / "v07-protocol.yaml"
        legacy.write_bytes((ROOT / "configs/protocols/protocol.yaml").read_bytes())
        cases = [
            (2, [], self.variant("other-manifest.json", manifest_sha256="e" * 64), "validation"),
            (2, [], self.subset_path, "smoke"),
            (2, [], self.variant("outside.json", ids=[h.ids[0], *self.subset["ids"][1:]]), "validation"),
            (2, [], self.variant("order.json", ids=self.subset["ids"][::-1]), "validation"),
            (3, [], self.variant("extra.json", extra=True), "validation"),
            (3, [], self.variant("size.json", size=2), "validation"),
            (3, [], self.variant("empty.json", ids=[], size=0), "validation"),
            (3, [], self.inputs / "missing.json", "validation"),
            (3, ["--protocol", str(legacy)], self.subset_path, "validation"),
        ]
        (self.inputs / "not-json.json").write_bytes(b"{")
        cases.append((3, [], self.inputs / "not-json.json", "validation"))
        for code, extra, path, tier in cases:
            with self.subTest(path=path.name, tier=tier, extra=extra):
                protocol = Path(extra[1]) if extra else None
                self.assertEqual(self.cli_run("--subset", str(path), protocol=protocol, tier=tier), code)
        self.assertEqual(self.run_directories(), [])
        self.assertEqual(h.generation_calls, [])
        with patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(cli.main(["run", "--resume", "x", "--subset", str(self.subset_path)]), 3)


class RuntimeRequirementTests(RunnerHarnessCase):
    """S-008: runtime files are required only where a step starts a server (empty cache here)."""

    def test_approval_only_plans_run_without_runtime_files(self) -> None:
        h = self.h
        self.assertFalse((self.root / "cache").exists())
        with patch.object(plan_runner, "check_runtime", REAL_CHECK_RUNTIME):
            steps = h.steps("approval") + [{"type": "approval", "id": "second-review"}]
            self.start(steps)
            self.assertEqual((h.status()["state"], h.status()["step"]), ("waiting_approval", 1))
            self.resume()
            self.assertEqual((h.status()["state"], h.status()["step"]), ("waiting_approval", 2))
            self.resume()
            self.assertEqual(h.status()["state"], "completed")
            # A plan that would start a server still needs the files, at start.
            plan, authorization = h.plan(h.steps("verify", "smoke"))
            self.assert_exit(3, plan_runner.start, plan, authorization, h.deps(), _sync_launch)
        self.assertEqual((h.servers, h.commands, h.generation_calls), ([], [], []))

    def test_resume_checks_runtime_files_only_for_pending_server_steps(self) -> None:
        h = self.h
        self.start(h.steps("approval", "verify", "smoke"))       # Runtime present at start.
        self.assertEqual(h.status()["state"], "waiting_approval")
        with patch.object(plan_runner, "check_runtime", REAL_CHECK_RUNTIME):
            self.assert_exit(3, self.resume)                       # Server steps still pending.
        self.assertEqual(h.status()["state"], "waiting_approval")
        self.resume()
        self.assertEqual(h.status()["state"], "completed")

    def test_completed_server_steps_need_no_runtime_files_to_pass_an_approval(self) -> None:
        h = self.h
        self.start(h.steps("verify", "smoke", "approval"))
        self.assertEqual(h.status()["state"], "waiting_approval")
        with patch.object(plan_runner, "check_runtime", REAL_CHECK_RUNTIME):
            self.resume()
        self.assertEqual(h.status()["state"], "completed")


class DirectInterruptionTests(RunnerHarnessCase):
    """F2: a direct v0.8 `slm-eval run` records its interruptions in run.json events."""

    def direct_run(self) -> str:
        return runner.start(self.h.repo / PROTOCOL, self.h.repo / plan_runner.MANIFEST,
                            "smoke", 1, 0)

    def interrupted(self, run_id: str) -> list[dict]:
        return [e["detail"] for e in load_run(run_id).metadata["events"] if e["type"] == "interrupted"]

    def test_a_signal_is_an_event_and_resume_regenerates_only_the_lost_item(self) -> None:
        h = self.h
        h.behaviors[(h.ids[1], 0)] = "keyboard_interrupt"
        with self.assertRaises(KeyboardInterrupt):
            self.direct_run()
        run_id = self.output.getvalue().split()[-1]
        run = load_run(run_id)
        self.assertEqual((run.metadata["status"], run.metadata["attempts"][-1]["status"]),
                         ("interrupted", "interrupted"))
        self.assertEqual(self.interrupted(run_id), [{"reason": "signal", "attempt": 1}])
        self.assertEqual([row["item_id"] for row in run.items], h.ids[:1])  # Nothing partial.
        self.assertFalse(h.servers[-1].alive)
        self.assertEqual(runner.resume(run_id), run_id)
        resumed = load_run(run_id)
        self.assertEqual(resumed.metadata["status"], "completed")
        self.assertEqual(sorted(row["item_id"] for row in resumed.items), h.ids)
        self.assertEqual([item for item, _ in h.generation_calls], [h.ids[0], h.ids[1], h.ids[1], h.ids[2]])
        self.assertEqual(self.interrupted(run_id), [{"reason": "signal", "attempt": 1}])  # Not twice.
        self.assertTrue(all(row["infra_retries"] == 0 for row in resumed.items))

    def test_an_attempt_without_a_checkpoint_is_recorded_on_resume(self) -> None:
        h = self.h
        h.behaviors[(h.ids[1], 0)] = "keyboard_interrupt"
        with self.assertRaises(KeyboardInterrupt):
            self.direct_run()
        run_id = self.output.getvalue().split()[-1]
        run = load_run(run_id)
        # As after kill -9 or power loss: no handler ran, so nothing was recorded.
        metadata = dict(run.metadata, events=[])
        metadata["attempts"][-1]["ended"] = None
        data.write_json(run.directory / "run.json", metadata)
        runner.resume(run_id)
        self.assertEqual(self.interrupted(run_id), [{"reason": "previous_attempt_ended", "attempt": 1}])
        self.assertEqual(load_run(run_id).metadata["status"], "completed")

    def test_errors_are_not_interruptions_and_legacy_runs_gain_no_events(self) -> None:
        h = self.h
        h.behaviors[(h.ids[0], 0)] = "error500"
        self.assert_exit(1, self.direct_run)
        run_id = self.output.getvalue().split()[-1]
        self.assertEqual(load_run(run_id).metadata["status"], "interrupted")
        self.assertEqual(self.interrupted(run_id), [])
        legacy = json.loads((ROOT / "configs/protocols/protocol.yaml").read_text())
        h.write("configs/protocols/legacy.yaml", json.dumps(legacy).encode())
        h.behaviors[(h.ids[0], 1)] = "keyboard_interrupt"
        # The fake server reports the fixture alias; the legacy settings check is not under test.
        with patch.object(runner.runtime, "sha256_file", lambda _path: legacy["model"]["artifact"]["sha256"]), \
                patch.object(runner, "_effective_settings", return_value={}), \
                self.assertRaises(KeyboardInterrupt):
            runner.start(h.repo / "configs/protocols/legacy.yaml", h.repo / plan_runner.MANIFEST,
                         "smoke", 1, 0)
        legacy_run = load_run(self.output.getvalue().split()[-1])
        self.assertEqual(legacy_run.metadata["status"], "interrupted")
        self.assertNotIn("events", legacy_run.metadata)


class CredentialChildTests(RunnerHarnessCase):
    """Only the downloading converter steps inherit HF credentials (synthetic sentinels)."""

    def test_command_and_server_children_drop_credentials(self) -> None:
        h = self.h
        sentinels = {name: f"synthetic-sentinel-{name.lower()}"
                     for name in runner.runtime.HF_CREDENTIAL_VARIABLES}
        with patch.dict(os.environ, sentinels):
            self.start(h.steps("convert", "sanity", "verify", "smoke", "report:4"))
        self.assertEqual(h.status()["state"], "completed")
        names = [arguments[1] if arguments[0] == "model" else arguments[0] for arguments in h.commands]
        self.assertEqual(names, ["convert", "sanity", "verify-runtime", "verify-artifact", "report"])
        environments = dict(zip(names, h.command_envs))
        # The converter's own Hugging Face requests keep the parent environment.
        self.assertIsNone(environments.pop("convert"))
        self.assertIsNone(environments.pop("sanity"))
        for name, environment in environments.items():
            with self.subTest(command=name):
                self.assertFalse(set(sentinels) & set(environment))
                self.assertEqual(environment["SLM_EVAL_RUNS"], str(self.root / "runs"))
        self.assertTrue(h.servers)
        for server in h.servers:
            self.assertFalse(set(sentinels) & set(server.environment))
            self.assertIn("LLAMA_API_KEY", server.environment)
        leaked = [path for path in (self.root / "runs").rglob("*") if path.is_file()
                  and any(value.encode() in path.read_bytes() for value in sentinels.values())]
        self.assertEqual(leaked, [])


class RunStoreWriteFailureTests(RunnerHarnessCase):
    def test_short_item_write_is_an_interruption_and_resume_recovers(self) -> None:
        """A run-store write failure (here ENOSPC) leaves only a torn line to discard."""
        import errno
        h = self.h
        real_fsync, failed = os.fsync, []

        def fsync(descriptor: int) -> None:
            items = list((self.root / "runs").glob("*/items.jsonl"))
            if (not failed and items and os.fstat(descriptor).st_ino == items[0].stat().st_ino
                    and items[0].read_bytes().count(b"\n") == 2):
                failed.append(items[0])
                os.truncate(items[0], items[0].stat().st_size - 5)  # A short write.
                raise OSError(errno.ENOSPC, "No space left on device")
            real_fsync(descriptor)

        with patch.object(runner.os, "fsync", side_effect=fsync):
            self.start(h.steps("smoke"))
        status = h.status()
        self.assertEqual((status["state"], status["last_event"]["detail"]["error"]),
                         ("interrupted", "OSError"))
        self.assertFalse(failed[0].read_bytes().endswith(b"\n"))
        self.assertFalse(h.servers[-1].alive)
        self.assertFalse(h.sleep_lock.held)
        self.resume()
        self.assertEqual(h.status()["state"], "completed")
        self.assert_items()
        self.assertEqual([item for item, _ in h.generation_calls],
                         [h.ids[0], h.ids[1], h.ids[1], h.ids[2]])
        reasons = [e["detail"].get("reason") for e in h.run().metadata["events"]
                   if e["type"] == "interrupted"]
        self.assertEqual(reasons, ["previous_attempt_ended", "partial_item_write_discarded"])


class BoundaryTests(unittest.TestCase):
    def test_fixture_pins_its_own_template_and_targets_keep_the_source_pin(self) -> None:
        protocol = json.loads((ROOT / "configs/protocols/fixture-smollm2-135m.yaml").read_text())
        adapter = runner.model_adapters.from_protocol(protocol)
        runner._protocol_check(protocol)
        self.assertTrue(runner.runtime.template_pin_valid(protocol, adapter))
        for digest in ("C" * 64, "c" * 63, None):
            protocol["model"]["chat_template"]["sha256"] = digest
            self.assertFalse(runner.runtime.template_pin_valid(protocol, adapter))
        protocol["model"]["chat_template"]["sha256"] = "c" * 64
        protocol["role"] = "target"
        protocol["runtime"]["model_alias"] = "qwen3-8b"
        adapter = runner.model_adapters.from_protocol(protocol)
        self.assertFalse(runner.runtime.template_pin_valid(protocol, adapter))
        protocol["model"]["chat_template"]["sha256"] = adapter.template_sha256
        self.assertTrue(runner.runtime.template_pin_valid(protocol, adapter))
        self.assertEqual(protocol["role"], "target")

    def test_worker_entry_accepts_only_a_plan_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {"SLM_EVAL_RUNS": temporary}):
            self.assertEqual(plan_runner._worker_main([]), 3)
            self.assertEqual(plan_runner._worker_main([temporary]), 3)
            self.assertEqual(plan_runner._worker_main(
                [str(Path(temporary) / "elsewhere" / ("a" * 64))]), 3)

    def test_free_memory_parses_vm_stat(self) -> None:
        output = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                  "Pages free:        1000.\nPages active:   5.\n"
                  "Pages inactive:    2000.\nPages speculative:  1000.\n")
        with patch.object(plan_runner.sys, "platform", "darwin"), \
                patch.object(plan_runner.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, output, "")):
            self.assertAlmostEqual(plan_runner.free_memory_gb(), 4000 * 16384 / 2 ** 30)
        with patch.object(plan_runner.sys, "platform", "darwin"), \
                patch.object(plan_runner.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, "garbled", "")):
            self.assertIsNone(plan_runner.free_memory_gb())


if __name__ == "__main__":
    unittest.main()
