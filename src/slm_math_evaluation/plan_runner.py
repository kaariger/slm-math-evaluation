"""The fixed-sequence runner for one authorized plan (contract §13).

The runner executes the commands a plan names, in order and once each. It has
no branching, looping, expansion or scheduling. ``status.json`` in the runner
state directory is authoritative; ``attempts.log.jsonl`` is an append-only,
hash-chained record of what happened.

A plan's ``run`` steps execute in this worker process so that pauses, memory
waits, stuck items and infrastructure retries apply per item. The other steps
run the named ``slm-eval`` command in a child process and map its exit code.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import fcntl
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator

import yaml

from . import data, runner, runtime
from .data import DataError, sha256_bytes, sha256_file, write_json


REPO = Path(__file__).resolve().parents[2]
# A plan pins the manifest only by hash. The repository holds one split
# manifest (§2), so run steps read it from this fixed path; nothing is searched.
MANIFEST = "configs/datasets/math500-manifest-v1.json"
# Likewise a run step pins a subset only by hash. The repository holds one committed
# subset (§5.2); a non-null subset_sha256 must match its bytes. Nothing is searched.
SUBSET = "configs/datasets/math500-validation-subset-50-seed-20261027-v1.json"
ZERO = "0" * 64
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
STATES = ("running", "paused", "waiting_approval", "interrupted", "stopped", "completed")
TERMINAL = ("stopped", "completed")
STEP_FIELDS = {
    "convert": {"type", "spec", "spec_sha256"},
    "sanity": {"type", "spec", "spec_sha256"},
    "verify": {"type", "protocol", "protocol_sha256"},
    "run": {"type", "protocol", "protocol_sha256", "manifest_sha256", "tier",
            "subset_sha256", "k", "seed_base"},
    "report": {"type", "of_step"},
    "approval": {"type", "id"},
}
AUTHORIZATION_FIELDS = {"authorization_version", "plan_sha256", "scope", "bindings",
                        "attempts_allowed", "stop_conditions", "approved_by", "approved_utc"}
BINDING_FIELDS = {"protocols", "artifacts", "specs", "manifest", "subsets",
                  "runtime_build", "harness_revision"}
INFRA_RETRIES = 2            # §13.5: an infrastructure failure may be retried twice.
STOP_DENOMINATOR = 20        # §13.5: stop when exhausted items reach 1/20 of the run.
SLEEP_GAP_S = 5.0            # Continuous minus awake time beyond this is a sleep.
RECENT_EVENTS = 10


# Clock, sleep lock, notification and memory probe -------------------------------------

def _helper_environment() -> dict[str, str]:
    """Local helper children (caffeinate, osascript, vm_stat, git, ps) need no credential."""
    return runtime.credential_free_environment()


class Clock:
    """Awake time stops while the system sleeps; continuous time does not (§13.5)."""

    def __init__(self) -> None:
        darwin = sys.platform == "darwin"
        self._awake = time.CLOCK_UPTIME_RAW if darwin else time.CLOCK_MONOTONIC
        self._continuous = (time.CLOCK_MONOTONIC if darwin
                            else getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))

    def awake(self) -> float:
        return time.clock_gettime(self._awake)

    def continuous(self) -> float:
        return time.clock_gettime(self._continuous)

    def utc(self) -> str:
        return datetime.now(timezone.utc).isoformat()


class SleepLock:
    """An idle-sleep assertion held only while a step actively works (§13.6).

    ``caffeinate -w`` ends with this process, so a crash also releases it. A
    closed lid or low power still sleeps the machine; that is an interruption.
    """

    def __init__(self) -> None:
        self.process: Any = None

    def acquire(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        if sys.platform != "darwin":
            return False
        try:
            self.process = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, env=_helper_environment())
        except OSError:
            self.process = None
            return False
        return True

    def release(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None


def notify(message: str) -> None:
    """Send a best-effort local notification; ``status.json`` stays authoritative."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(["osascript", "-e", "on run argv",
                        "-e", 'display notification (item 1 of argv) with title "slm-eval runner"',
                        "-e", "end run", message],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False,
                       env=_helper_environment())
    except (OSError, subprocess.SubprocessError):
        pass


def free_memory_gb() -> float | None:
    """Free, inactive and speculative pages on macOS; ``MemAvailable`` on Linux."""
    try:
        if sys.platform == "darwin":
            output = subprocess.run(["vm_stat"], capture_output=True, text=True,
                                    timeout=10, check=False, env=_helper_environment()).stdout
            page = re.search(r"page size of (\d+) bytes", output)
            counts = re.findall(r"^Pages (?:free|inactive|speculative):\s+(\d+)\.", output, re.M)
            if page is None or len(counts) != 3:
                return None
            return sum(int(count) for count in counts) * int(page.group(1)) / 2 ** 30
        with open("/proc/meminfo", encoding="ascii") as stream:
            for line in stream:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / 2 ** 30
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


@dataclass
class Deps:
    """Injectable effects. Tests replace these; there is no public sleep hook."""

    clock: Any = field(default_factory=Clock)
    sleep: Callable[[float], None] = time.sleep
    free_memory_gb: Callable[[], float | None] = free_memory_gb
    sleep_lock: Any = field(default_factory=SleepLock)
    notify: Callable[[str], None] = notify
    popen: Callable[..., Any] = subprocess.Popen
    killpg: Callable[[int, int], None] = os.killpg
    poll_interval: float = 1.0
    memory_poll_s: float = 15.0
    child_grace_s: float = 60.0


# Plan and authorization -----------------------------------------------------------------

def _hex64(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _whole(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _repository_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


def parse_plan(raw: bytes) -> list[dict[str, Any]]:
    try:
        plan = yaml.safe_load(raw)
    except (yaml.YAMLError, UnicodeError) as error:
        raise DataError(f"invalid plan YAML: {error}", 3) from error
    if (not isinstance(plan, dict) or set(plan) != {"plan_version", "steps"}
            or plan["plan_version"] != "1" or not isinstance(plan["steps"], list)
            or not plan["steps"]):
        raise DataError("plan does not match contract §13.1", 3)
    steps = plan["steps"]
    for number, step in enumerate(steps, 1):
        kind = step.get("type") if isinstance(step, dict) else None
        if kind not in STEP_FIELDS or set(step) != STEP_FIELDS[kind]:
            raise DataError(f"plan step {number} does not match contract §13.1", 3)
        valid = True
        if kind in ("convert", "sanity"):
            valid = _repository_path(step["spec"]) and _hex64(step["spec_sha256"])
        elif kind in ("verify", "run"):
            valid = _repository_path(step["protocol"]) and _hex64(step["protocol_sha256"])
        if kind == "run":
            valid = (valid and _hex64(step["manifest_sha256"])
                     and step["tier"] in ("smoke", "validation", "test")
                     and (step["subset_sha256"] is None or _hex64(step["subset_sha256"]))
                     and _whole(step["k"], 1) and _whole(step["seed_base"], 0))
        elif kind == "report":
            target = step["of_step"]
            valid = (_whole(target, 1) and target < number
                     and steps[target - 1].get("type") == "run")
        elif kind == "approval":
            valid = isinstance(step["id"], str) and bool(step["id"].strip())
        if not valid:
            raise DataError(f"plan step {number} has an invalid value", 3)
    return steps


def parse_authorization(raw: bytes) -> dict[str, Any]:
    try:
        document = json.loads(raw)
    except (UnicodeError, ValueError) as error:
        raise DataError(f"invalid authorization JSON: {error}", 3) from error
    valid = isinstance(document, dict) and set(document) == AUTHORIZATION_FIELDS
    if valid:
        bindings = document["bindings"]
        valid = (document["authorization_version"] == "1" and _hex64(document["plan_sha256"])
                 and isinstance(document["scope"], str)
                 and isinstance(bindings, dict) and set(bindings) == BINDING_FIELDS
                 and all(isinstance(bindings[name], list) and all(map(_hex64, bindings[name]))
                         for name in ("protocols", "artifacts", "specs", "subsets"))
                 and (bindings["manifest"] is None or _hex64(bindings["manifest"]))
                 and isinstance(bindings["runtime_build"], str)
                 and isinstance(bindings["harness_revision"], str)
                 and HEX40.fullmatch(bindings["harness_revision"]) is not None
                 and _whole(document["attempts_allowed"], 1)
                 and isinstance(document["stop_conditions"], list)
                 and all(isinstance(item, str) for item in document["stop_conditions"])
                 and isinstance(document["approved_by"], str) and bool(document["approved_by"])
                 and isinstance(document["approved_utc"], str) and bool(document["approved_utc"]))
    if not valid:
        raise DataError("authorization does not match contract §13.2", 3)
    return document


def harness_revision() -> str:
    """The checked-out commit; uncommitted changes make it differ from any binding."""
    try:
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=30, check=True,
                              env=_helper_environment()).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=30, check=True,
                               env=_helper_environment()).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise DataError("installed harness revision is unavailable", 3) from error
    return head + ("+uncommitted" if dirty.strip() else "")


def check_bindings(steps: list[dict[str, Any]], authorization: dict[str, Any],
                   plan_sha: str) -> None:
    """§13.2: the plan hash, every pinned hash and the harness revision are bound."""
    if authorization["plan_sha256"] != plan_sha:
        raise DataError("plan SHA-256 differs from the authorization", 2)
    bindings = authorization["bindings"]
    for number, step in enumerate(steps, 1):
        pinned = [("specs", step.get("spec_sha256")), ("protocols", step.get("protocol_sha256")),
                  ("subsets", step.get("subset_sha256"))]
        for name, value in pinned:
            if value is not None and value not in bindings[name]:
                raise DataError(f"plan step {number} pins an unbound {name[:-1]} hash", 2)
        if step["type"] == "run" and step["manifest_sha256"] != bindings["manifest"]:
            raise DataError(f"plan step {number} pins an unbound manifest hash", 2)
    if bindings["runtime_build"] != runtime.BUILD:
        raise DataError("authorized runtime build differs from the pinned build", 2)
    if harness_revision() != bindings["harness_revision"]:
        raise DataError("installed harness revision differs from the authorization", 2)


def _read_protocol(path: Path, number: int) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_bytes())
        artifact = document["model"]["artifact"]["sha256"]
        build = document["runtime"]["build"]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as error:
        raise DataError(f"plan step {number}: protocol is unreadable: {error}", 3) from error
    if not _hex64(artifact) or not isinstance(build, str):
        raise DataError(f"plan step {number}: protocol artifact or build is malformed", 3)
    return document


def check_inputs(steps: list[dict[str, Any]], bindings: dict[str, Any],
                 numbers: set[int] | None = None) -> dict[int, dict[str, Any]]:
    """Check each step's pinned repository inputs; return the protocols read."""
    protocols = {}
    for number, step in enumerate(steps, 1):
        if numbers is not None and number not in numbers:
            continue
        for name, digest in (("spec", "spec_sha256"), ("protocol", "protocol_sha256")):
            if name not in step:
                continue
            path = REPO / step[name]
            if not path.is_file():
                raise DataError(f"plan step {number}: {name} is missing: {step[name]}", 3)
            if sha256_file(path) != step[digest]:
                raise DataError(f"plan step {number}: {name} SHA-256 differs from the plan", 2)
        if "protocol" in step:
            protocol = _read_protocol(REPO / step["protocol"], number)
            if protocol["model"]["artifact"]["sha256"] not in bindings["artifacts"]:
                raise DataError(f"plan step {number}: protocol artifact is not bound", 2)
            if protocol["runtime"]["build"] != bindings["runtime_build"]:
                raise DataError(f"plan step {number}: protocol runtime build is not bound", 2)
            protocols[number] = protocol
        if step["type"] == "run":
            manifest = REPO / MANIFEST
            if not manifest.is_file():
                raise DataError("committed manifest is missing", 3)
            if sha256_file(manifest) != step["manifest_sha256"]:
                raise DataError(f"plan step {number}: manifest SHA-256 differs from the plan", 2)
            protocol = protocols[number]
            if protocol["model"].get("adapter") is None or not isinstance(protocol.get("resources"), dict):
                raise DataError(f"plan step {number}: run steps need a protocol with an "
                                "adapter and resources (§4)", 3)
            if step["subset_sha256"] is not None:
                _plan_subset_ids(number, step, protocol)
    return protocols


def _plan_subset_ids(number: int, step: dict[str, Any], protocol: dict[str, Any]) -> list[str]:
    """The committed subset's IDs, after its hash and its §5.2 binding to the step."""
    path = REPO / SUBSET
    if not path.is_file():
        raise DataError("committed subset is missing", 3)
    content = path.read_bytes()
    if sha256_bytes(content) != step["subset_sha256"]:
        raise DataError(f"plan step {number}: subset SHA-256 differs from the plan", 2)
    try:
        subset = json.loads(content)
    except ValueError as error:
        raise DataError(f"plan step {number}: subset is not JSON: {error}", 3) from error
    tier_ids = runner._identity(protocol, data.read_json(REPO / MANIFEST), step["tier"])
    return data.subset_ids(subset, step["manifest_sha256"], step["tier"], tier_ids)


def check_smoke_first(steps: list[dict[str, Any]], protocols: dict[int, dict[str, Any]]) -> None:
    """§12.4: for every model, the plan runs the smoke tier before any other tier."""
    smoked = set()
    for number, step in enumerate(steps, 1):
        if step["type"] != "run":
            continue
        artifact = protocols[number]["model"]["artifact"]["sha256"]
        if step["tier"] == "smoke":
            smoked.add(artifact)
        elif artifact not in smoked:
            raise DataError(f"plan step {number}: a {step['tier']} run precedes this "
                            "model's smoke run", 3)


def check_artifacts(steps: list[dict[str, Any]], protocols: dict[int, dict[str, Any]],
                    after: int) -> None:
    """Re-verify present model files of pending steps (§13.4 resume)."""
    checked = set()
    for number, protocol in protocols.items():
        artifact = protocol["model"]["artifact"]
        path = data.cache_root() / "models" / str(artifact.get("file"))
        if number <= after or (path, artifact["sha256"]) in checked:
            continue
        checked.add((path, artifact["sha256"]))
        if path.is_file() and sha256_file(path) != artifact["sha256"]:
            raise DataError(f"plan step {number}: model artifact SHA-256 mismatch", 2)


def check_runtime() -> None:
    try:
        runtime.verify_runtime_files(data.cache_root() / "models" / f"llama-{runtime.BUILD}")
    except runtime.RuntimeErrorWithCode as error:
        raise DataError(str(error), error.code) from error


RUNTIME_STEPS = ("verify", "run")


def check_runtime_if_needed(steps: list[dict[str, Any]]) -> None:
    """Verify the installed runtime only for plans whose remaining steps start a server.

    The authorization's runtime build is checked for every plan (check_bindings),
    and each verify or run step re-verifies the runtime files when it starts.
    """
    if any(step["type"] in RUNTIME_STEPS for step in steps):
        check_runtime()


# State directory ------------------------------------------------------------------------

def runner_root() -> Path:
    return data.runs_root() / "_runner"


def _write_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.partial")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _line_sha(line: bytes) -> str:
    return sha256_bytes(line)


class State:
    """Files of one plan's runner state under ``_runner/<plan_sha256>/`` (§13.3)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.status_path = directory / "status.json"
        self.plan_path = directory / "plan.yaml"
        self.authorization_path = directory / "authorization.json"
        self.log_path = directory / "attempts.log.jsonl"
        self.server_log = directory / "server.log"
        self.runner_log = directory / "runner.log"
        self.control_path = directory / "control.json"
        self.processes_path = directory / "processes.json"
        self.worker_lock_path = directory / "worker.lock"
        self.state_lock_path = directory / "state.lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self.state_lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def status(self) -> dict[str, Any]:
        try:
            status = json.loads(self.status_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise DataError("runner status is missing", 3) from error
        except (OSError, UnicodeError, ValueError) as error:
            raise DataError(f"runner status is unreadable: {error}", 2) from error
        if not isinstance(status, dict) or status.get("state") not in STATES:
            raise DataError("runner status is malformed", 2)
        return status

    def write_status(self, status: dict[str, Any], clock: Any) -> None:
        status["updated_utc"] = clock.utc()
        write_json(self.status_path, status)

    def lines(self) -> tuple[list[bytes], bytes]:
        """Complete log lines without terminators, and any unterminated torn tail."""
        try:
            raw = self.log_path.read_bytes()
        except FileNotFoundError:
            return [], b""
        complete, _, torn = raw.rpartition(b"\n")
        return (complete.split(b"\n") if complete or raw.startswith(b"\n") else []), torn

    def head(self) -> str:
        lines, _ = self.lines()
        return _line_sha(lines[-1]) if lines else ZERO

    def verify_chain(self) -> str:
        lines, _ = self.lines()
        previous = ZERO
        for number, line in enumerate(lines, 1):
            try:
                entry = json.loads(line)
            except ValueError as error:
                raise DataError(f"attempt log line {number} is malformed", 2) from error
            if (not isinstance(entry, dict)
                    or set(entry) != {"seq", "prev_sha256", "at_utc", "step", "event", "detail"}
                    or entry["seq"] != number or entry["prev_sha256"] != previous):
                raise DataError(f"attempt log hash chain breaks at line {number}", 2)
            previous = _line_sha(line)
        return previous

    def append(self, clock: Any, step: int | None, event: str,
               detail: dict[str, Any] | None = None) -> dict[str, Any]:
        """Append one chained line; callers hold ``locked()``."""
        lines, torn = self.lines()
        if torn:
            self._discard_torn(torn)
            self.append(clock, step, "torn_log_tail_discarded",
                        {"bytes": len(torn), "sha256": sha256_bytes(torn)})
            lines, _ = self.lines()
        entry = {"seq": len(lines) + 1, "prev_sha256": _line_sha(lines[-1]) if lines else ZERO,
                 "at_utc": clock.utc(), "step": step, "event": event, "detail": detail or {}}
        line = json.dumps(entry, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        descriptor = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(descriptor, line)  # One write keeps a crash from splitting a line.
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return entry

    def _discard_torn(self, torn: bytes) -> None:
        # An unterminated tail was never a complete record. Removing it keeps the
        # chain of complete lines intact; its hash is logged in the next line.
        size = self.log_path.stat().st_size
        with self.log_path.open("r+b") as stream:
            stream.truncate(size - len(torn))
            os.fsync(stream.fileno())

    def worker_alive(self) -> bool:
        """Whether a worker holds the lock. Opens read-only; writes nothing."""
        try:
            descriptor = os.open(self.worker_lock_path, os.O_RDONLY)
        except FileNotFoundError:
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            os.close(descriptor)
        return False

    @contextmanager
    def worker_lock(self, wait_s: float = 10) -> Iterator[bool]:
        descriptor = os.open(self.worker_lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            deadline = time.monotonic() + wait_s
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        acquired = False
                        break
                    time.sleep(0.05)
            yield acquired
        finally:
            os.close(descriptor)

    def requested_pause(self) -> str | None:
        try:
            document = json.loads(self.control_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, UnicodeError):
            return None
        mode = document.get("pause") if isinstance(document, dict) else None
        return mode if mode in ("after_item", "now") else None


def verify_copies(state: State, status: dict[str, Any]) -> tuple[bytes, bytes]:
    """§13.3: the stored copies match ``status.json`` and each other."""
    try:
        plan_bytes = state.plan_path.read_bytes()
        authorization_bytes = state.authorization_path.read_bytes()
    except OSError as error:
        raise DataError("stored plan or authorization copy is missing", 2) from error
    if (sha256_bytes(plan_bytes) != status.get("plan_sha256")
            or sha256_bytes(authorization_bytes) != status.get("authorization_sha256")
            or state.directory.name != status.get("plan_sha256")):
        raise DataError("stored plan or authorization copy SHA-256 mismatch", 2)
    if parse_authorization(authorization_bytes)["plan_sha256"] != status["plan_sha256"]:
        raise DataError("stored authorization does not bind the stored plan", 2)
    return plan_bytes, authorization_bytes


def _plans() -> list[State]:
    root = runner_root()
    if not root.is_dir():
        return []
    return [State(path) for path in sorted(root.iterdir())
            if HEX64.fullmatch(path.name) and (path / "status.json").is_file()]


def _current(command: str) -> State:
    """The one non-terminal plan; ``status`` falls back to the latest finished plan."""
    plans = _plans()
    active = [state for state in plans if state.status()["state"] not in TERMINAL]
    if len(active) > 1:
        raise DataError("more than one runner plan is active in this run store", 3)
    if active:
        return active[0]
    if command == "status" and plans:
        return max(plans, key=lambda state: state.status().get("updated_utc", ""))
    raise DataError("no active runner plan", 3)


def _process_command(pid: int) -> str | None:
    try:
        result = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                                text=True, timeout=10, check=False, env=_helper_environment())
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _signal_process(pid: int, group: bool, number: int) -> None:
    try:
        (os.killpg if group else os.kill)(pid, number)
    except (ProcessLookupError, PermissionError):
        pass


def _recover_processes(state: State, clock: Any) -> None:
    """Stop a server or step child left behind by a killed worker."""
    try:
        records = json.loads(state.processes_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return
    for kind, record in sorted(records.items()):
        command = _process_command(record["pid"])
        if command is None or any(marker not in command for marker in record["markers"]):
            continue
        group = kind == "step"
        _signal_process(record["pid"], group, signal.SIGTERM)
        for _ in range(100):
            if _process_command(record["pid"]) is None:
                break
            time.sleep(0.1)
        else:
            _signal_process(record["pid"], group, signal.SIGKILL)
        with state.locked():
            state.append(clock, None, "orphan_process_stopped", {"kind": kind})
    state.processes_path.unlink(missing_ok=True)


# Worker ---------------------------------------------------------------------------------

class _Paused(Exception):
    def __init__(self, mode: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(mode)
        self.mode, self.detail = mode, detail or {}


class _Stopped(Exception):
    def __init__(self, code: int | None, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


class _Interrupted(Exception):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


class _Waiting(Exception):
    def __init__(self, approval: str) -> None:
        super().__init__(approval)
        self.approval = approval


class Worker:
    """Executes the stored plan from its checkpoint until a pause, wait or end."""

    def __init__(self, state: State, deps: Deps | None = None) -> None:
        self.state = state
        self.deps = deps or Deps()
        self.clock = self.deps.clock
        self.steps: list[dict[str, Any]] = []
        self.authorization: dict[str, Any] = {}
        self._mark = (0.0, 0.0)
        self._processes: dict[str, Any] = {}

    # Status and log
    def log(self, at_step: int | None, event: str, detail: dict[str, Any] | None = None,
            **updates: Any) -> None:
        with self.state.locked():
            entry = self.state.append(self.clock, at_step, event, detail)
            status = self.state.status()
            status["last_event"] = {"event": event, "step": at_step, "at_utc": entry["at_utc"],
                                    "detail": entry["detail"]}
            status.update(updates)
            self.state.write_status(status, self.clock)

    def update(self, **updates: Any) -> None:
        with self.state.locked():
            status = self.state.status()
            for name, value in updates.items():
                if name == "step_runs":
                    status.setdefault("step_runs", {}).update(value)
                else:
                    status[name] = value
            self.state.write_status(status, self.clock)

    def requested_pause(self) -> str | None:
        return self.state.requested_pause()

    def observe_clock(self, step: int | None) -> float:
        """Record a sleep and wake when continuous time outran awake time."""
        awake, continuous = self.clock.awake(), self.clock.continuous()
        gap = (continuous - self._mark[1]) - (awake - self._mark[0])
        self._mark = (awake, continuous)
        if gap <= SLEEP_GAP_S:
            return 0.0
        self.log(step, "sleep", {"slept_s": round(gap, 3)})
        self.log(step, "wake", {"slept_s": round(gap, 3)})
        return gap

    def record_process(self, kind: str, pid: int | None, markers: list[str] | None = None) -> None:
        if pid is None:
            self._processes.pop(kind, None)
        else:
            self._processes[kind] = {"pid": pid, "markers": markers or []}
        if self._processes:
            write_json(self.state.processes_path, self._processes)
        else:
            self.state.processes_path.unlink(missing_ok=True)

    # Lifecycle
    def run(self) -> str:
        with self.state.worker_lock() as acquired:
            if not acquired:
                return "busy"
            status = self.state.status()
            if status["state"] != "running":
                return status["state"]
            previous = runtime.install_cleanup_signals()
            try:
                return self._execute(status)
            finally:
                runtime.restore_cleanup_signals(previous)
                self.deps.sleep_lock.release()
                self.record_process("server", None)
                self.record_process("step", None)

    def _finish(self, step: int | None, event: str, state: str,
                detail: dict[str, Any] | None = None, message: str | None = None) -> str:
        # Checkpoint and server stop already happened; release the lock, then set state.
        self.deps.sleep_lock.release()
        self.log(step, event, detail, state=state)
        if message is not None:
            self.deps.notify(message)
        return state

    def _execute(self, status: dict[str, Any]) -> str:
        sha = status["plan_sha256"][:12]
        number: int | None = None
        try:
            plan_bytes, authorization_bytes = verify_copies(self.state, status)
            self.steps = parse_plan(plan_bytes)
            self.authorization = parse_authorization(authorization_bytes)
            if not self.deps.sleep_lock.acquire():
                self.log(None, "sleep_lock_unavailable")
            self._mark = (self.clock.awake(), self.clock.continuous())
            while True:
                completed = self.state.status()["completed_steps"]
                if completed >= len(self.steps):
                    break
                mode = self.requested_pause()
                if mode is not None:
                    raise _Paused(mode)
                number = completed + 1
                step = self.steps[number - 1]
                self.log(number, "step_started", {"type": step["type"]}, step=number,
                         progress={"done": 0, "total": 1})
                self._check_step(number, step)
                getattr(self, "_step_" + step["type"])(number, step)
                self.log(number, "step_completed", {"type": step["type"]},
                         completed_steps=number)
            return self._finish(number, "completed", "completed",
                                message=f"Runner plan {sha} completed")
        except _Paused as pause:
            self.state.control_path.unlink(missing_ok=True)
            return self._finish(number, "paused", "paused", {"mode": pause.mode, **pause.detail})
        except _Waiting as wait:
            return self._finish(number, "waiting_approval", "waiting_approval",
                                {"id": wait.approval},
                                message=f"Runner plan {sha} is waiting for approval")
        except _Stopped as stop:
            return self._finish(number, "stopped", "stopped",
                                {"exit_code": stop.code, "reason": stop.reason},
                                message=f"Runner plan {sha} stopped")
        except DataError as error:
            return self._finish(number, "stopped", "stopped",
                                {"exit_code": error.code, "reason": str(error)},
                                message=f"Runner plan {sha} stopped")
        except _Interrupted as interruption:
            return self._finish(number, "interrupted", "interrupted", interruption.detail)
        except KeyboardInterrupt:
            return self._finish(number, "interrupted", "interrupted", {"reason": "signal"})
        except Exception as error:  # Unexpected faults are recoverable interruptions.
            return self._finish(number, "interrupted", "interrupted",
                                {"reason": "error", "error": type(error).__name__,
                                 "message": str(error)[:500]})

    def _check_step(self, number: int, step: dict[str, Any]) -> None:
        """§13.1: inputs that no longer match their pins stop the plan."""
        try:
            verify_copies(self.state, self.state.status())
            check_inputs(self.steps, self.authorization["bindings"], {number})
            if harness_revision() != self.authorization["bindings"]["harness_revision"]:
                raise DataError("installed harness revision differs from the authorization", 2)
            if step["type"] in ("verify", "run"):
                check_runtime()
        except DataError as error:
            raise _Stopped(error.code, str(error)) from error

    # Steps
    def _command(self, number: int, arguments: list[str], downloads: bool = False) -> None:
        """Run one ``slm-eval`` command child; only downloading steps keep HF credentials.

        Exits 2, 3 and 4 stop the plan. Exit 1 or a signal stops it too, unless this
        worker observed a sleep gap while the child ran: that is §13.5 evidence of an
        interruption, which spends no budget and is resumed by hand. An exit code
        alone, such as a convert exit 1, is no evidence of a lost network.
        """
        command = [sys.executable, "-m", "slm_math_evaluation.cli", *arguments]
        environment = None if downloads else runtime.credential_free_environment()
        with self.state.runner_log.open("ab") as log:
            process = self.deps.popen(command, cwd=REPO, stdin=subprocess.DEVNULL, stdout=log,
                                      stderr=log, start_new_session=True, env=environment)
        self.record_process("step", process.pid, ["slm_math_evaluation.cli", arguments[0]])
        slept = False
        try:
            while process.poll() is None:
                self.deps.sleep(self.deps.poll_interval)
                slept = self.observe_clock(number) > 0 or slept
                if self.requested_pause() == "now":
                    self._stop_child(process)
                    raise _Paused("now", {"discarded": {"command": arguments[0]}})
        finally:
            if process.poll() is None:
                self._stop_child(process)
            self.record_process("step", None)
        slept = self.observe_clock(number) > 0 or slept  # A gap just before the exit.
        code = process.returncode
        if code == 0:
            return
        if code not in (2, 3, 4) and slept:
            raise _Interrupted({"reason": "sleep", "command": arguments[0], "exit_code": code})
        raise _Stopped(code if code in (1, 2, 3, 4) else 1,
                       f"{' '.join(arguments[:2])} exited {code}")

    def _stop_child(self, process: Any) -> None:
        """SIGINT lets the command clean up its own children; SIGKILL after a grace."""
        for number in (signal.SIGINT, signal.SIGKILL):
            try:
                self.deps.killpg(process.pid, number)
            except (ProcessLookupError, PermissionError):
                pass
            deadline = time.monotonic() + self.deps.child_grace_s
            while process.poll() is None and time.monotonic() < deadline:
                self.deps.sleep(0.1)
            if process.poll() is not None:
                return

    def _step_convert(self, number: int, step: dict[str, Any]) -> None:
        self._command(number, ["model", "convert", "--spec", step["spec"]], downloads=True)

    def _step_sanity(self, number: int, step: dict[str, Any]) -> None:
        # A sanity reference file is downloaded from its pinned source (§12.4).
        self._command(number, ["model", "sanity", "--spec", step["spec"]], downloads=True)

    def _step_verify(self, number: int, step: dict[str, Any]) -> None:
        self._command(number, ["verify-runtime", "--protocol", step["protocol"]])
        self._command(number, ["verify-artifact", "--protocol", step["protocol"]])

    def _step_report(self, number: int, step: dict[str, Any]) -> None:
        run_id = self.state.status().get("step_runs", {}).get(str(step["of_step"]))
        if run_id is None:
            raise _Stopped(3, f"step {step['of_step']} has no run to report")
        self._command(number, ["report", "--run", run_id])

    def _step_approval(self, number: int, step: dict[str, Any]) -> None:
        raise _Waiting(step["id"])

    def _step_run(self, number: int, step: dict[str, Any]) -> None:
        protocol_path, manifest_path = REPO / step["protocol"], REPO / MANIFEST
        protocol = yaml.safe_load(protocol_path.read_bytes())
        manifest = data.read_json(manifest_path)
        if step["subset_sha256"] is None:
            subset_path, ids = None, runner._identity(protocol, manifest, step["tier"])
        else:
            subset_path, ids = REPO / SUBSET, _plan_subset_ids(number, step, protocol)
        total = len(ids) * step["k"]
        control = PlanControl(self, number, protocol, total)
        run_id = self.state.status().get("step_runs", {}).get(str(number))
        try:
            if run_id is None:
                self.update(progress={"done": 0, "total": total})
                runner.start(protocol_path, manifest_path, step["tier"], step["k"],
                             step["seed_base"], control=control, subset_path=subset_path)
                return
            directory = data.runs_root() / run_id
            metadata = data.read_json(directory / "run.json")
            expected = {"protocol_sha256": step["protocol_sha256"],
                        "manifest_sha256": step["manifest_sha256"], "tier": step["tier"],
                        "subset_sha256": step["subset_sha256"],
                        "k": step["k"], "seeds": [step["seed_base"] + n for n in range(step["k"])]}
            if any(metadata.get(name) != value for name, value in expected.items()):
                raise _Stopped(2, "stored run identity differs from the plan step")
            if metadata.get("status") == "completed":
                return
            attempts = metadata.get("attempts") or [{"n": None}]
            if (metadata.get("status") in ("in_progress", "interrupted")
                    and not runner.interruption_recorded(metadata, attempts[-1]["n"])):
                # The previous attempt ended with no recorded interruption (§6.1, §6.3):
                # a crash or kill. Nothing observed its item, so the event names none.
                control.pending.append(("interrupted", {"reason": "previous_attempt_ended",
                                                        "attempt": attempts[-1]["n"]}))
            torn = _discard_partial_item(directory)
            if torn is not None:
                control.pending.append(("interrupted", {"reason": "partial_item_write_discarded",
                                                        **torn}))
            runner.resume(run_id, control=control)
        except runner.RunPaused as pause:
            raise _Paused("now" if pause.now else "after_item", control.pause_detail) from pause
        except runner.RunStopped as stop:
            raise _Stopped(None, str(stop)) from stop
        except runtime.RuntimeErrorWithCode as error:
            raise _Stopped(error.code, str(error)) from error
        except DataError as error:
            raise _Stopped(error.code, str(error)) from error


def _discard_partial_item(directory: Path) -> dict[str, Any] | None:
    """Drop an unterminated last ``items.jsonl`` line: a partial generation (§6.1)."""
    path = directory / "items.jsonl"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    if not raw or raw.endswith(b"\n"):
        return None
    keep = raw.rfind(b"\n") + 1
    fragment = raw[keep:]
    with path.open("r+b") as stream:
        stream.truncate(keep)
        os.fsync(stream.fileno())
    return {"discarded_bytes": len(fragment), "discarded_sha256": sha256_bytes(fragment)}


class PlanControl:
    """Per-item controls the runner applies inside ``runner._generate`` (§13.4–§13.5)."""

    def __init__(self, worker: Worker, step: int, protocol: dict[str, Any], total: int) -> None:
        self.worker, self.step, self.total = worker, step, total
        self.clock = worker.clock
        self.deps = worker.deps
        resources = protocol["resources"]
        self.floor_gb = resources["min_free_memory_gb"]["value"]
        self.stuck_after = resources["stuck_after_awake_s"]["value"]
        self.server_log = worker.state.server_log
        self.metadata: dict[str, Any] = {}
        self.directory: Path | None = None
        self.pending: list[tuple[str, dict[str, Any]]] = []
        self.pause_detail: dict[str, Any] = {}
        self.current: dict[str, Any] | None = None
        self.item_gap = False
        self.done = 0
        self._awake_start = self.clock.awake()

    # Run lifecycle hooks
    def run_created(self, run_id: str) -> None:
        self.worker.update(step_runs={str(self.step): run_id}, run_id=run_id)
        self.worker.log(self.step, "run_created", {"run_id": run_id})

    def bind(self, directory: Path, metadata: dict[str, Any], attempt: dict[str, Any]) -> None:
        self.directory, self.metadata = directory, metadata
        metadata.setdefault("events", [])
        metadata.setdefault("infra_failures", [])
        metadata["status"] = "in_progress"
        self._awake_start = self.clock.awake()
        if attempt["resumed_from"] is not None:
            self.event("resume", {"attempt": attempt["n"]}, write=False)
        for kind, detail in self.pending:
            self.event(kind, detail, write=False)
        self.pending.clear()
        with (directory / "items.jsonl").open("rb") as stream:
            completed = sum(1 for _ in stream)
        self.done = completed + len(metadata["infra_failures"])
        self.worker.update(progress={"done": self.done, "total": self.total},
                           run_id=metadata["run_id"])

    def server_started(self, session: Any) -> None:
        self.worker.record_process("server", session.process.pid,
                                   [str(session.server), f"--port {session.port}"])

    def server_stopped(self, session: Any) -> None:
        self.worker.record_process("server", None)
        if not session.key or not self.server_log.is_file():
            return
        content = self.server_log.read_bytes()
        if session.key.encode("utf-8") in content:
            _write_bytes(self.server_log, content.replace(session.key.encode("utf-8"), b"[redacted]"))
            raise runtime.RuntimeErrorWithCode("runtime key appeared in server.log; redacted", 4)

    def event(self, kind: str, detail: dict[str, Any], write: bool = True) -> None:
        self.metadata["events"].append({"type": kind, "at_utc": self.clock.utc(),
                                        "awake_s": round(self.clock.awake() - self._awake_start, 3),
                                        "detail": detail})
        if write and self.directory is not None:
            write_json(self.directory / "run.json", self.metadata)
        self.worker.log(self.step, kind, detail)

    # Item accounting
    def exhausted(self, item_id: str, sample: int, metadata: dict[str, Any] | None = None) -> bool:
        failures = (metadata if metadata is not None else self.metadata).get("infra_failures", [])
        return any(entry["item_id"] == item_id and entry["sample_index"] == sample
                   for entry in failures)

    def retries_used(self, item_id: str, sample: int) -> int:
        return sum(1 for entry in self.metadata["events"]
                   if entry["type"] == "infra_retry"
                   and entry["detail"].get("item_id") == item_id
                   and entry["detail"].get("sample_index") == sample
                   and entry["detail"].get("retry") is not None)

    def item_done(self, item_id: str, sample: int) -> None:
        self.done += 1
        self.current = None
        self.worker.update(progress={"done": self.done, "total": self.total})

    # Before each item: pause after the previous item, then the memory floor.
    def before_item(self, item_id: str, sample: int) -> None:
        self.current = {"item_id": item_id, "sample_index": sample}
        self._pause_if_requested(in_flight=False)
        self._wait_for_memory()
        self.item_gap = False
        self._observe()

    def _pause_if_requested(self, in_flight: bool) -> None:
        mode = self.worker.requested_pause()
        if mode is None or (in_flight and mode != "now"):
            return
        detail = {"mode": mode}
        if in_flight and self.current is not None:
            detail["discarded"] = dict(self.current)
        self.pause_detail = detail
        self.event("pause", detail)
        raise runner.RunPaused(now=mode == "now")

    def _wait_for_memory(self) -> None:
        free = self.deps.free_memory_gb()
        if free is None:
            raise DataError("free memory is unavailable to check the protocol floor", 3)
        if free >= self.floor_gb:
            return
        started = self.clock.awake()
        self.event("memory_wait", {"free_gb": round(free, 3), "floor_gb": self.floor_gb})
        while True:
            self.deps.sleep(self.deps.memory_poll_s)
            self._observe()
            self._pause_if_requested(in_flight=False)
            free = self.deps.free_memory_gb()
            if free is None:
                raise DataError("free memory is unavailable to check the protocol floor", 3)
            if free >= self.floor_gb:
                break
        self.event("memory_wait", {"cleared": True, "free_gb": round(free, 3),
                                   "floor_gb": self.floor_gb,
                                   "waited_awake_s": round(self.clock.awake() - started, 3)})

    def _observe(self) -> None:
        gap = self.worker.observe_clock(self.step)
        if gap:
            self.item_gap = True
            slept = round(gap, 3)
            asleep = datetime.now(timezone.utc) - timedelta(seconds=gap)
            for kind, at in (("sleep", asleep.isoformat()), ("wake", self.clock.utc())):
                self.metadata["events"].append({"type": kind, "at_utc": at,
                                                "awake_s": round(self.clock.awake() - self._awake_start, 3),
                                                "detail": {"slept_s": slept}})
            if self.directory is not None:
                write_json(self.directory / "run.json", self.metadata)

    # Generation with keyed progress polling
    def generate(self, session: Any, send: Callable[[], tuple[int, bytes]]) -> tuple[int, bytes]:
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["result"] = send()
            except BaseException as error:  # Re-raised in the worker thread below.
                box["error"] = error

        thread = threading.Thread(target=target, name="slm-eval-generation", daemon=True)
        thread.start()
        last_tokens, last_progress = -1, self.clock.awake()
        while True:
            thread.join(self.deps.poll_interval)
            self._observe()
            if not thread.is_alive():
                break
            if self.worker.requested_pause() == "now":
                session.stop()
                thread.join(30)
                self._pause_if_requested(in_flight=True)
            if not session.alive():
                thread.join(30)
                raise self._lost("server_crash")
            tokens = self._decoded(session)
            now = self.clock.awake()
            if tokens is not None and tokens > last_tokens:
                last_tokens, last_progress = tokens, now
            elif now - last_progress >= self.stuck_after:
                session.stop()
                thread.join(30)
                raise runner.InfraFailure("stuck")
        if "error" in box:
            error = box["error"]
            if isinstance(error, (OSError, http.client.HTTPException)):
                raise self._lost("server_crash" if not session.alive() else "server_error")
            raise error
        status, body = box["result"]
        if status >= 500:
            raise self._lost("server_error")
        return status, body

    def _lost(self, reason: str) -> Exception:
        # A failure across a sleep gap is an interruption, never a retry (§13.5).
        return runner.Interruption("sleep") if self.item_gap else runner.InfraFailure(reason)

    @staticmethod
    def _decoded(session: Any) -> int | None:
        """Tokens decoded so far, from the keyed slot; the body is never logged."""
        try:
            status, body = runtime.request(session.port, "GET", "/slots", session.key, timeout=5)
            if status != 200:
                return None
            slot = json.loads(body)[0]
            if not slot.get("is_processing"):
                return None
            tokens = slot["next_token"]
            return sum(int(entry["n_decoded"]) for entry in
                       (tokens if isinstance(tokens, list) else [tokens]))
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError, IndexError):
            return None

    def classify(self, failure: Exception, session: Any) -> Exception:
        if isinstance(failure, (runner.InfraFailure, runner.Interruption)):
            return failure
        self._observe()
        return self._lost("server_crash" if not session.alive() else "server_error")

    def interrupted(self, item_id: str, sample: int, reason: str,
                    thinking_state: str | None = None) -> None:
        """Record an item's interruption; its state only if that attempt returned output."""
        detail = {"item_id": item_id, "sample_index": sample, "reason": reason}
        if thinking_state is not None:
            detail["thinking_state"] = thinking_state
        self.event("interrupted", detail)
        self.item_gap = False

    def infra_failure(self, item_id: str, sample: int, reason: str, retries: int,
                      total: int) -> bool:
        """Record one failure; return whether to retry. Stops at the 5% rule."""
        detail = {"item_id": item_id, "sample_index": sample, "reason": reason}
        self.item_gap = False
        if retries < INFRA_RETRIES:
            self.event("infra_retry", {**detail, "retry": retries + 1})
            return True
        self.metadata["infra_failures"].append({**detail, "retries": retries})
        self.event("infra_retry", {**detail, "retry": None, "exhausted": True})
        self.done += 1
        self.worker.update(progress={"done": self.done, "total": self.total})
        if len(self.metadata["infra_failures"]) * STOP_DENOMINATOR >= total:
            raise runner.RunStopped("items that exhausted their retries reached 5% of the run")
        return False


# Commands -------------------------------------------------------------------------------

def _launch(state: State, deps: Deps) -> None:
    """Start the background worker and wait until it holds the worker lock."""
    with state.runner_log.open("ab") as log:
        process = deps.popen([sys.executable, "-m", "slm_math_evaluation.plan_runner",
                              str(state.directory)], cwd=REPO, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if state.worker_alive() or process.poll() is not None:
            return
        time.sleep(0.05)
    raise DataError("runner worker did not start", 1)


def _validated(state: State, status: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resume-time integrity: copies, log chain, bindings, inputs, artifacts and runtime."""
    plan_bytes, authorization_bytes = verify_copies(state, status)
    steps, authorization = parse_plan(plan_bytes), parse_authorization(authorization_bytes)
    state.verify_chain()
    check_bindings(steps, authorization, status["plan_sha256"])
    protocols = check_inputs(steps, authorization["bindings"])
    check_artifacts(steps, protocols, status["completed_steps"])
    check_runtime_if_needed(steps[status["completed_steps"]:])
    return steps, authorization


def start(plan_path: Path, authorization_path: Path, deps: Deps | None = None,
          launch: Callable[[State, Deps], Any] = _launch) -> list[str]:
    deps = deps or Deps()
    try:
        plan_bytes, authorization_bytes = plan_path.read_bytes(), authorization_path.read_bytes()
    except OSError as error:
        raise DataError(f"plan or authorization is missing: {error}", 3) from error
    steps = parse_plan(plan_bytes)
    authorization = parse_authorization(authorization_bytes)
    plan_sha, authorization_sha = sha256_bytes(plan_bytes), sha256_bytes(authorization_bytes)
    check_bindings(steps, authorization, plan_sha)
    protocols = check_inputs(steps, authorization["bindings"])
    check_smoke_first(steps, protocols)
    check_runtime_if_needed(steps)
    state = State(runner_root() / plan_sha)
    if any(other.status()["state"] not in TERMINAL
           for other in _plans() if other.directory != state.directory):
        raise DataError("another runner plan is active in this run store", 3)
    if state.directory.exists():
        status = state.status()
        if status["state"] != "stopped":
            raise DataError(f"plan already has runner state {status['state']}; "
                            "use `slm-eval runner resume`", 3)
        verify_copies(state, status)
        if (state.plan_path.read_bytes() != plan_bytes
                or state.authorization_path.read_bytes() != authorization_bytes):
            raise DataError("authorization differs from the stored copy for this plan", 2)
        state.verify_chain()
        if status["starts"] >= authorization["attempts_allowed"]:
            raise DataError("authorized attempts for this plan are used", 3)
        _recover_processes(state, deps.clock)
        with state.locked():
            state.append(deps.clock, status["step"], "start", {"attempt": status["starts"] + 1})
            status.update(state="running", starts=status["starts"] + 1)
            state.write_status(status, deps.clock)
    else:
        state.directory.mkdir(parents=True)
        _write_bytes(state.plan_path, plan_bytes)
        _write_bytes(state.authorization_path, authorization_bytes)
        status = {"plan_sha256": plan_sha, "authorization_sha256": authorization_sha,
                  "state": "running", "step": 1, "steps_total": len(steps), "completed_steps": 0,
                  "run_id": None, "step_runs": {}, "progress": {"done": 0, "total": 1},
                  "last_event": None, "starts": 1,
                  "attempts_allowed": authorization["attempts_allowed"],
                  "harness_revision": authorization["bindings"]["harness_revision"]}
        verify_copies(state, status)
        with state.locked():
            entry = state.append(deps.clock, None, "start", {"attempt": 1})
            status["last_event"] = {"event": "start", "step": None, "at_utc": entry["at_utc"],
                                    "detail": entry["detail"]}
            state.write_status(status, deps.clock)
    launch(state, deps)
    return [plan_sha, str(state.status_path), str(state.plan_path),
            str(state.authorization_path), str(state.log_path)]


def pause(now: bool, deps: Deps | None = None) -> list[str]:
    deps = deps or Deps()
    state = _current("pause")
    status = state.status()
    if status["state"] != "running":
        raise DataError(f"plan is {status['state']}, not running", 3)
    if not state.worker_alive():
        raise DataError("plan worker is not running; use `slm-eval runner resume` to recover", 3)
    mode = "now" if now else "after_item"
    write_json(state.control_path, {"pause": mode, "requested_utc": deps.clock.utc()})
    with state.locked():
        entry = state.append(deps.clock, status["step"], "pause_requested", {"mode": mode})
        status = state.status()
        status["last_event"] = {"event": "pause_requested", "step": status["step"],
                                "at_utc": entry["at_utc"], "detail": entry["detail"]}
        state.write_status(status, deps.clock)
    return [str(state.control_path), str(state.log_path)]


def resume(deps: Deps | None = None, launch: Callable[[State, Deps], Any] = _launch) -> list[str]:
    deps = deps or Deps()
    state = _current("resume")
    deadline = time.monotonic() + 15
    while state.worker_alive() and time.monotonic() < deadline:
        time.sleep(0.1)  # A pausing worker releases its lock as it exits.
    if state.worker_alive():
        raise DataError("plan worker is still running", 3)
    status = state.status()
    previous = status["state"]
    if previous not in ("paused", "interrupted", "waiting_approval", "running"):
        raise DataError(f"plan is {previous}; nothing to resume", 3)
    steps, _ = _validated(state, status)
    _recover_processes(state, deps.clock)
    state.control_path.unlink(missing_ok=True)
    with state.locked():
        # ``append`` first discards and records any torn, unterminated log tail.
        if previous == "running":
            # The worker ended without recording a state: a crash, reboot or power loss.
            state.append(deps.clock, status["step"], "interrupted", {"reason": "worker_ended"})
        status = state.status()
        if previous == "waiting_approval":
            number = status["completed_steps"] + 1
            if steps[number - 1]["type"] != "approval":
                raise DataError("waiting state does not match an approval step", 2)
            state.append(deps.clock, number, "approved", {"id": steps[number - 1]["id"]})
            status["completed_steps"] = number
        entry = state.append(deps.clock, status["step"], "resumed",
                             {"from": "interrupted" if previous == "running" else previous})
        status.update(state="running", last_event={"event": "resumed", "step": status["step"],
                                                   "at_utc": entry["at_utc"],
                                                   "detail": entry["detail"]})
        state.write_status(status, deps.clock)
    launch(state, deps)
    return [status["plan_sha256"], str(state.status_path), str(state.log_path)]


def status_report() -> dict[str, Any]:
    """Read-only view: ``status.json`` plus the attempt-log head and recent events."""
    state = _current("status")
    status = state.status()
    lines, torn = state.lines()
    try:
        state.verify_chain()
        chain_valid = True
    except DataError:
        chain_valid = False
    recent = []
    for line in lines[-RECENT_EVENTS:]:
        try:
            entry = json.loads(line)
            recent.append({name: entry.get(name) for name in ("seq", "at_utc", "step", "event", "detail")})
        except ValueError:
            recent.append({"malformed": True})
    return {**status, "attempt_log_head_sha256": _line_sha(lines[-1]) if lines else ZERO,
            "attempt_log_lines": len(lines), "attempt_log_chain_valid": chain_valid,
            "attempt_log_torn_tail_bytes": len(torn), "worker_running": state.worker_alive(),
            "recent_events": recent}


def _worker_main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        return 3
    directory = Path(arguments[0])
    if directory.resolve().parent != runner_root().resolve() or not HEX64.fullmatch(directory.name):
        return 3
    Worker(State(directory)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main(sys.argv[1:]))
