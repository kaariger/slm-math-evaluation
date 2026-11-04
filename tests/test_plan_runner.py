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

    def command_popen(self, command, **_kwargs):
        arguments = command[3:]
        self.commands.append(arguments)
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
        # A validation tier before this model's smoke tier; a subset run.
        plan, authorization = h.plan(h.steps("verify", "validation", "smoke"))
        self.assert_exit(3, start, plan, authorization)
        subset = h.steps("smoke")
        subset[0]["subset_sha256"] = "f" * 64
        plan, authorization = h.plan(subset, subsets=["f" * 64])
        self.assert_exit(3, start, plan, authorization)
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
        # A protocol changed after start stops resume with an integrity exit.
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
