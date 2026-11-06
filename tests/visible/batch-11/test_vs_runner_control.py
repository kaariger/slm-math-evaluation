"""Visible specification tests — runner control plane on approval-only plans, acting only on provably owned processes (contract v0.8.1 §3 runner commands, §13).

Contract-only: written from the approved interface and the maintainer's
readings, with no implementation code seen. Tests use only the `slm-eval`
CLI, the processes it starts and the contract's file schemas.

Resources (stated in each docstring):
  hermetic  the `slm-eval` CLI only. Plans hold only `approval` steps, so no
            model, runtime, dataset or network is needed. Each test has its
            own temporary SLM_EVAL_CACHE, SLM_EVAL_RUNS, SLM_EVAL_SCRATCH and
            working directory.
  harness   the installed harness revision, for `bindings.harness_revision`
            (§13.2): SLM_EVAL_TEST_HARNESS_REVISION, or else `git rev-parse
            HEAD` of the repository that contains the pytest working
            directory, which must then be a clean checkout (P7). NOT RUN
            otherwise.
  repo      a committed protocol file (VS-RN-18 only), named in a plan by its
            path relative to the repository root (§13.1).
  tools     `pgrep` and `ps`, for owned-process observation.

Process ownership. A test inspects or signals only processes it can prove it
owns:
  * each CLI process it launches, in a new session and process group. Launch
    handles are not reaped until the test's teardown, so their PIDs and group
    IDs cannot be reused while the test runs; exit statuses are read without
    reaping;
  * members of those process groups (`pgrep -g <owned group>`);
  * children of processes already proven owned (`pgrep -P <owned pid>`).
Each owned process is recorded with its start time (`ps -o stat=,lstart= -p
<pid>`), re-read before every signal and after every child listing; a changed
or missing start time means the PID no longer names that process, and nothing
is sent. No process table is scanned for environments or command lines, and
no other process is ever signalled.

A refused `runner start` must leave no child. While it runs, its group and
lineage are polled every FAST_POLL seconds; after it exits, no owned process
may be alive, and no process may still hold its stdout or stderr. Those two
are pipes that the test drains, so end of file arrives only once every
process holding them has closed them. A child that left the launching group
and lineage before it was observed, and also closed the inherited stdout and
stderr, is beyond these observations.

Teardown acts only on plans the test itself started, through the contract's
own control commands in the test's own run store: `runner pause --now` for a
plan still `running`, and `runner resume` past the remaining approvals of an
approval-only plan that is `waiting_approval`. So a runner the test cannot
observe still reaches a final state. Every owned process is then ended.
Commands that may leave a runner in the background write stdout and stderr
to files, never pipes. State changes are awaited by polling `status.json`
for up to WAIT seconds.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable, and no variable whose name contains TOKEN, SECRET,
PASSWORD or API_KEY, is passed to the implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

TIMEOUT = 300
WAIT = 120.0
POLL, FAST_POLL = 0.3, 0.05
STDIO_GRACE = 5.0
SETTLE_WAIT = 15.0
TEARDOWN_BUDGET = 120.0
EXIT_OK, EXIT_INTEGRITY, EXIT_PRECONDITION = 0, 2, 3
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
STATES = {"running", "paused", "waiting_approval", "interrupted", "stopped", "completed"}
STATUS_KEYS = {"plan_sha256", "authorization_sha256", "state", "step", "run_id", "progress", "last_event",
               "updated_utc"}
LOG_KEYS = {"seq", "prev_sha256", "at_utc", "step", "event", "detail"}
RUNTIME_BUILD = "b10412"
PINNED_MANIFEST_SHA256 = "a10c02e7ac60b563a4d112cad92c2540ce58d6c0a49fa73f844091df66e48c71"
HEX40 = re.compile(r"[0-9a-f]{40}")


def not_run(reason: str):
    pytest.skip(f"NOT RUN: {reason}")


# ---------------------------------------------------------------------------
# provably owned processes
# ---------------------------------------------------------------------------


def _query(argv: list[str]) -> str:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                          errors="replace").stdout


def start_token(pid: int) -> str | None:
    """The start time of a live process with this PID; None if it is gone or a zombie."""
    parts = _query(["ps", "-o", "stat=,lstart=", "-p", str(pid)]).strip().split(None, 1)
    if len(parts) != 2 or parts[0].startswith("Z"):
        return None
    return parts[1].strip()


def _pgrep(flag: str, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    return {int(t) for t in _query(["pgrep", flag, ",".join(map(str, ids))]).split() if t.isdigit()}


class Owned:
    """Launch handles and the processes proven to descend from them, each pinned by its start time."""

    def __init__(self):
        self.leaders: list[subprocess.Popen] = []
        self.known: dict[int, str] = {}

    def launch(self, argv: list[str], *, cwd: Path, env: dict, out, err) -> subprocess.Popen:
        popen = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                 start_new_session=True)
        self.leaders.append(popen)
        self._register(popen.pid)
        return popen

    def _groups(self) -> list[int]:
        """Group IDs of unreaped leaders: reserved, so membership in them proves ownership."""
        return [p.pid for p in self.leaders if p.returncode is None]

    def _register(self, pid: int) -> bool:
        if pid <= 1 or pid in (os.getpid(), os.getppid()):
            return False
        token = start_token(pid)
        if token is None:
            return False
        self.known[pid] = token
        return True

    def _still(self, pid: int, groups: list[int]) -> bool:
        return pid in groups or (self.known.get(pid) is not None and start_token(pid) == self.known[pid])

    def exit_code(self, popen: subprocess.Popen) -> int | None:
        """A leader's exit status, read without reaping it (None while it runs)."""
        if popen.returncode is not None:
            return popen.returncode
        try:
            info = os.waitid(os.P_PID, popen.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return None
        if info is None:
            return None
        return info.si_status if info.si_code == os.CLD_EXITED else -info.si_status

    def refresh(self) -> None:
        groups = self._groups()
        for pid in _pgrep("-g", groups):
            self._register(pid)
        frontier, done = set(groups) | set(self.known), set()
        while frontier - done:
            parent = min(frontier - done)
            done.add(parent)
            if not self._still(parent, groups):
                continue
            children = _pgrep("-P", [parent])
            if not self._still(parent, groups):
                continue
            frontier |= {child for child in children if self._register(child)}

    def live(self) -> set[int]:
        return {pid for pid, token in list(self.known.items()) if start_token(pid) == token}

    def send(self, pid: int, sig: int) -> bool:
        if pid <= 1 or pid in (os.getpid(), os.getppid()):
            return False
        if pid not in self._groups() and (self.known.get(pid) is None or start_token(pid) != self.known[pid]):
            return False
        try:
            os.kill(pid, sig)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def kill_all(self, sig: int) -> None:
        self.refresh()
        for group in self._groups():
            try:
                os.killpg(group, sig)
            except (ProcessLookupError, PermissionError):
                pass
        for pid in self.live():
            self.send(pid, sig)

    def wait_gone(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.refresh()
            if not self.live() and all(self.exit_code(p) is not None for p in self.leaders):
                return True
            time.sleep(0.2)
        return False

    def cleanup(self) -> None:
        for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 10.0)):
            self.kill_all(sig)
            if self.wait_gone(grace):
                break
        for popen in self.leaders:
            if popen.returncode is None:
                try:
                    popen.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass


def _drain(stream, path: Path, closed: threading.Event) -> None:
    """Copy a pipe into a file until every holder of its write end has closed it."""
    try:
        with open(path, "wb") as fh:
            while chunk := os.read(stream.fileno(), 65536):
                fh.write(chunk)
                fh.flush()
    except OSError:
        pass
    finally:
        stream.close()
        closed.set()


# ---------------------------------------------------------------------------
# CLI and per-test environment
# ---------------------------------------------------------------------------


def _command() -> list[str]:
    override = os.environ.get("SLM_EVAL_TEST_CMD", "").strip()
    if override:
        return shlex.split(override)
    found = shutil.which("slm-eval")
    if found is None:
        pytest.fail("`slm-eval` is not on PATH; run inside the project environment or set SLM_EVAL_TEST_CMD",
                    pytrace=False)
    return [found]


_LOCATIONS = ("SLM_EVAL_CACHE", "SLM_EVAL_RUNS", "SLM_EVAL_SCRATCH", "SLM_EVAL_FLOOR_GB", "SLM_EVAL_SCRATCH_FLOOR_GB")


def _passed_on(name: str) -> bool:
    upper = name.upper()
    return (name not in _LOCATIONS and not name.startswith("SLM_EVAL_TEST_")
            and not any(word in upper for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")))


def _text(path: Path) -> str:
    return path.read_text("utf-8", "replace") if path.is_file() else ""


def require_tools() -> None:
    missing = [tool for tool in ("pgrep", "ps") if shutil.which(tool) is None]
    if missing:
        not_run(f"needs {', '.join(missing)} on PATH for owned-process observation")
    if not (hasattr(os, "waitid") and hasattr(os, "WNOWAIT")):
        not_run("needs os.waitid with WNOWAIT to read exit statuses without releasing owned PIDs")


class Env:
    """A temporary cache, run store, scratch and working directory for one test, and the processes it owns."""

    def __init__(self, root: Path):
        self.root = root
        self.cache, self.runs, self.scratch, self.work = (root / n for n in ("cache", "runs", "scratch", "work"))
        self.inputs, self.logs = root / "inputs", root / "logs"
        for directory in (self.cache, self.runs, self.scratch, self.work, self.inputs, self.logs):
            directory.mkdir(parents=True)
        self.vars = {k: v for k, v in os.environ.items() if _passed_on(k)}
        self.vars.update(SLM_EVAL_CACHE=str(self.cache), SLM_EVAL_RUNS=str(self.runs),
                         SLM_EVAL_SCRATCH=str(self.scratch))
        self.owned = Owned()
        self.started: list[bytes] = []

    def run(self, *args: object, timeout: float = TIMEOUT, poll: float = POLL,
            watch_stdio: bool = False) -> subprocess.CompletedProcess:
        """Run a command to its exit as an owned process. With watch_stdio, stdout and stderr are drained pipes
        and `stdio_held` names any still held STDIO_GRACE seconds after the exit; otherwise they are files."""
        name = uuid.uuid4().hex[:8]
        out_path, err_path = self.logs / f"{name}.out", self.logs / f"{name}.err"
        argv = _command() + [str(a) for a in args]
        closed: dict[str, threading.Event] = {}
        if watch_stdio:
            popen = self.owned.launch(argv, cwd=self.work, env=self.vars, out=subprocess.PIPE, err=subprocess.PIPE)
            for label, stream, path in (("stdout", popen.stdout, out_path), ("stderr", popen.stderr, err_path)):
                closed[label] = threading.Event()
                threading.Thread(target=_drain, args=(stream, path, closed[label]), daemon=True).start()
        else:
            with open(out_path, "wb") as out, open(err_path, "wb") as err:
                popen = self.owned.launch(argv, cwd=self.work, env=self.vars, out=out, err=err)
        code = self._wait_exit(popen, timeout, poll)
        held = [label for label, event in closed.items() if not event.wait(STDIO_GRACE)]
        proc = subprocess.CompletedProcess(argv, code, _text(out_path), _text(err_path))
        proc.stdio_held = held
        return proc

    def _wait_exit(self, popen: subprocess.Popen, timeout: float, poll: float) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.owned.refresh()
            code = self.owned.exit_code(popen)
            if code is not None:
                self.owned.refresh()
                return code
            time.sleep(poll)
        try:
            os.killpg(popen.pid, signal.SIGKILL)   # the unreaped leader's group cannot have been reused
        except (ProcessLookupError, PermissionError):
            pass
        pytest.fail(f"{popen.args!r} did not exit within {timeout:.0f} s", pytrace=False)

    def leftovers(self, proc: subprocess.CompletedProcess) -> list[str]:
        """What a command left behind: live owned processes, or a still-held stdout or stderr."""
        self.owned.refresh()
        found = []
        alive = sorted(self.owned.live())
        if alive:
            found.append(f"owned processes {alive} are alive")
        held = getattr(proc, "stdio_held", [])
        if held:
            found.append("a process still holds the command's " + " and ".join(held))
        return found

    def snapshot(self) -> dict[str, bytes]:
        """Every file under the cache, run store, scratch and working directory, with its bytes."""
        return {str(p.relative_to(self.root)): p.read_bytes()
                for base in (self.cache, self.runs, self.scratch, self.work)
                for p in sorted(base.rglob("*")) if p.is_file()}

    # -- teardown: contract control commands for the test's own plans, then owned processes --------------

    def _note(self, text: str) -> None:
        with open(self.logs / "teardown.txt", "a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    def _control(self, deadline: float, *args: str) -> int | None:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            self._note(f"budget spent before {' '.join(args)}")
            return None
        proc = self.run(*args, timeout=min(TIMEOUT, remaining))
        self._note(f"{' '.join(args)} exited {proc.returncode}")
        return proc.returncode

    def _await(self, plan: bytes, accept, deadline: float, limit: float) -> dict:
        until = min(deadline, time.monotonic() + limit)
        status = read_status(self, plan) or {}
        while time.monotonic() < until and not accept(status):
            time.sleep(POLL)
            status = read_status(self, plan) or {}
        return status

    def _settle(self, plan: bytes, deadline: float) -> None:
        own_approvals = _approval_only(plan) and (state_dir(self, plan) / "plan.yaml").is_file() and \
            (state_dir(self, plan) / "plan.yaml").read_bytes() == plan
        for _ in range(_step_count(plan) + 2):
            status = self._await(plan, lambda s: s.get("state") != "running", deadline, SETTLE_WAIT)
            state = status.get("state")
            if state == "running":
                self._control(deadline, "runner", "pause", "--now")
                self._await(plan, lambda s: s.get("state") != "running", deadline, WAIT)
                return
            if state != "waiting_approval" or not own_approvals:
                return
            if self._control(deadline, "runner", "resume") != EXIT_OK:
                return
            self._await(plan, lambda s, before=status: s != before, deadline, WAIT)

    def teardown(self) -> None:
        deadline = time.monotonic() + TEARDOWN_BUDGET
        try:
            for plan in self.started:
                self._settle(plan, deadline)
        except (Exception, pytest.fail.Exception) as exc:   # teardown never replaces the test's own result
            self._note(f"settling raised {type(exc).__name__}")
        finally:
            self.owned.cleanup()


@pytest.fixture
def env(tmp_path: Path):
    require_tools()
    environment = Env(tmp_path)
    try:
        yield environment
    finally:
        environment.teardown()


def _explain(proc: subprocess.CompletedProcess) -> str:
    return (f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump(data) -> bytes:
    """JSON text, which every YAML parser also reads."""
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# plans (§13.1), authorizations (§13.2) and state (§13.3)
# ---------------------------------------------------------------------------


def _repo_root() -> Path | None:
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        pyproject = candidate / "pyproject.toml"
        try:
            if pyproject.is_file() and "slm-eval" in pyproject.read_text(encoding="utf-8", errors="replace"):
                return candidate
        except OSError:
            continue
    return None


def harness_revision() -> str:
    """The installed revision (§13.2): configured, or the clean checked-out commit (P7)."""
    configured = os.environ.get("SLM_EVAL_TEST_HARNESS_REVISION", "").strip()
    if configured:
        return configured
    root = _repo_root()
    if root is not None and shutil.which("git"):
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              encoding="utf-8").stdout.strip()
        if HEX40.fullmatch(head):
            state = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "status", "--porcelain",
                                    "--untracked-files=normal"], capture_output=True, encoding="utf-8")
            if state.returncode != 0:
                not_run("P7: cannot establish whether the checkout is clean; set SLM_EVAL_TEST_HARNESS_REVISION")
            if state.stdout.strip():
                not_run("P7: the checkout has uncommitted changes or non-ignored untracked files, so the "
                        "installed revision matches no authorization; commit or ignore them, or set "
                        "SLM_EVAL_TEST_HARNESS_REVISION")
            return head
    not_run("needs the installed harness revision (§13.2): set SLM_EVAL_TEST_HARNESS_REVISION or run from a "
            "clean git checkout of the harness")


def approval_plan(*gates: str, extra_steps: tuple[dict, ...] = ()) -> bytes:
    return dump({"plan_version": "1",
                 "steps": [*extra_steps, *({"type": "approval", "id": gate} for gate in gates)]})


def _steps(plan: bytes) -> list:
    try:
        steps = json.loads(plan).get("steps")
    except (ValueError, AttributeError):
        return []
    return steps if isinstance(steps, list) else []


def _step_count(plan: bytes) -> int:
    return len(_steps(plan))


def _approval_only(plan: bytes) -> bool:
    steps = _steps(plan)
    return bool(steps) and all(isinstance(s, dict) and s.get("type") == "approval" for s in steps)


def authorization(plan: bytes, *, plan_sha256: str | None = None, harness: str | None = None,
                  protocols: tuple[str, ...] = ()) -> bytes:
    return dump({
        "authorization_version": "1",
        "plan_sha256": plan_sha256 or sha256_bytes(plan),
        "scope": "visible-suite hermetic runner check: approval steps only",
        "bindings": {"protocols": list(protocols), "artifacts": [], "specs": [],
                     "manifest": PINNED_MANIFEST_SHA256, "subsets": [], "runtime_build": RUNTIME_BUILD,
                     "harness_revision": harness or harness_revision()},
        "attempts_allowed": 1,
        "stop_conditions": ["any integrity failure"],
        "approved_by": "maintainer",
        "approved_utc": "2026-10-27T00:00:00Z",
    })


def start(env: Env, plan: bytes, auth: bytes, *, watch_stdio: bool = False):
    """`runner start` on the test's own plan; the plan is registered for teardown before the start."""
    plan_path, auth_path = env.inputs / "plan.yaml", env.inputs / "authorization.json"
    plan_path.write_bytes(plan)
    auth_path.write_bytes(auth)
    env.started.append(plan)
    proc = env.run("runner", "start", "--plan", plan_path, "--authorization", auth_path, poll=FAST_POLL,
                   watch_stdio=watch_stdio)
    return proc, plan_path, auth_path


def state_dir(env: Env, plan: bytes) -> Path:
    return env.runs / "_runner" / sha256_bytes(plan)


def read_status(env: Env, plan: bytes) -> dict | None:
    try:
        doc = json.loads((state_dir(env, plan) / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def wait_for(env: Env, plan: bytes, accept, what: str) -> dict:
    deadline, last = time.time() + WAIT, None
    while time.time() < deadline:
        doc = read_status(env, plan)
        if doc is not None:
            last = doc
            if accept(doc):
                return doc
        time.sleep(0.3)
    pytest.fail(f"status.json never showed {what} within {WAIT:.0f} s; last seen: {last!r}", pytrace=False)


def wait_state(env: Env, plan: bytes, *states: str) -> dict:
    return wait_for(env, plan, lambda d: d.get("state") in states, f"state in {states}")


def wait_stable(env: Env, quiet: float = 3.0) -> None:
    """Wait until no file under the test's locations changes for `quiet` seconds."""
    deadline, before, since = time.time() + WAIT, env.snapshot(), time.time()
    while time.time() < deadline:
        time.sleep(0.5)
        now = env.snapshot()
        if now != before:
            before, since = now, time.time()
        elif time.time() - since >= quiet:
            return
    pytest.fail("the runner state never settled", pytrace=False)


def log_lines(env: Env, plan: bytes) -> list[bytes]:
    raw = (state_dir(env, plan) / "attempts.log.jsonl").read_bytes()
    lines = raw.split(b"\n")
    return lines[:-1] if lines and lines[-1] == b"" else lines


def _line_hashes(line: bytes) -> set[str]:
    """§13.3 "the SHA-256 of the previous line": with or without its line terminator, as in VS-RN-02 to 04.
    The exact v0.8.1 rule is checked by VS-CL-08 and VS-CL-09."""
    return {sha256_bytes(line), sha256_bytes(line + b"\n")}


def chain_problems(lines: list[bytes]) -> list[str]:
    problems, previous_seq = [], None
    for n, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except ValueError:
            problems.append(f"line {n} is not JSON")
            continue
        if not isinstance(record, dict) or not LOG_KEYS <= set(record):
            problems.append(f"line {n} lacks {sorted(LOG_KEYS)}")
            continue
        seq = record["seq"]
        if not isinstance(seq, int) or (previous_seq is not None and seq <= previous_seq):
            problems.append(f"line {n}: seq {seq!r} does not increase")
        previous_seq = seq if isinstance(seq, int) else previous_seq
        if n > 1 and record["prev_sha256"] not in _line_hashes(lines[n - 2]):
            problems.append(f"line {n}: prev_sha256 is not the SHA-256 of line {n - 1}")
    return problems


# ---------------------------------------------------------------------------
# tests: start, stored copies, status and resume
# ---------------------------------------------------------------------------


def _started(env: Env, plan: bytes, auth: bytes) -> dict:
    proc, _, _ = start(env, plan, auth)
    assert proc.returncode == EXIT_OK, "runner start failed\n" + _explain(proc)
    return wait_state(env, plan, "waiting_approval")


def test_vs_rn_12_start_prints_the_plan_hash_and_reaches_the_approval_wait(env):
    """VS-RN-12 · hermetic + harness · §3 runner start "Prints the plan's SHA-256"; §13.3 status.json;
    §13.4 "An approval step sets state waiting_approval".

    Proposed replacement for VS-RN-01: the same assertions, with owned processes only. A plan of two approval
    steps with a matching authorization → exit 0; the plan's SHA-256 on stdout; `status.json` reaches
    `waiting_approval` and holds every §13.3 key, with the plan's and the authorization's SHA-256.
    """
    plan = approval_plan("visible-gate-1", "visible-gate-2")
    auth = authorization(plan)
    proc, _, _ = start(env, plan, auth)
    assert proc.returncode == EXIT_OK, "runner start failed\n" + _explain(proc)
    assert sha256_bytes(plan) in proc.stdout, "the plan's SHA-256 is not on stdout\n" + _explain(proc)
    status = wait_state(env, plan, "waiting_approval")
    missing = sorted(STATUS_KEYS - set(status))
    assert not missing, f"status.json lacks {missing}: {status!r}"
    assert status["plan_sha256"] == sha256_bytes(plan), f"plan_sha256 {status['plan_sha256']!r}"
    assert status["authorization_sha256"] == sha256_bytes(auth), \
        f"authorization_sha256 {status['authorization_sha256']!r}"
    progress = status["progress"]
    assert isinstance(progress, dict) and {"done", "total"} <= set(progress), f"progress {progress!r}"


def test_vs_rn_13_stored_copies_and_hash_chained_log(env):
    """VS-RN-13 · hermetic + harness · §13.2 "stores byte-identical copies of the plan and the
    authorization"; §13.3 copies' SHA-256 equal status.json; attempts.log.jsonl hash chain.

    Proposed replacement for VS-RN-02: the same assertions, with owned processes only. In
    `_runner/<plan_sha256>/`: `plan.yaml` and `authorization.json` are byte-identical to the inputs;
    `attempts.log.jsonl` has at least one line, each JSON with the six §13.3 keys, `seq` increasing, and
    `prev_sha256` of every line after the first equal to the SHA-256 of the line before it.
    """
    plan = approval_plan("visible-gate-1")
    auth = authorization(plan)
    _started(env, plan, auth)
    directory = state_dir(env, plan)
    assert (directory / "plan.yaml").read_bytes() == plan, "stored plan.yaml differs from the input"
    assert (directory / "authorization.json").read_bytes() == auth, "stored authorization.json differs"
    lines = log_lines(env, plan)
    assert lines, "attempts.log.jsonl is empty"
    problems = chain_problems(lines)
    assert not problems, "attempts.log.jsonl departs from §13.3:\n" + "\n".join(problems)


def test_vs_rn_14_resume_continues_past_each_approval_to_completion(env):
    """VS-RN-14 · hermetic + harness · §13.4 "resume continues past" an approval step; §3 runner resume;
    §13.3 attempts.log.jsonl is append-only.

    Proposed replacement for VS-RN-03: the same assertions, with owned processes only. Two approval steps:
    waiting at the first → `runner resume` exit 0 → waiting at the second (a different `step`) → `runner
    resume` exit 0 → state `completed`. The log only grows: each earlier version is a prefix of the later
    one, and the chain holds throughout.
    """
    plan = approval_plan("visible-gate-1", "visible-gate-2")
    first = _started(env, plan, authorization(plan))
    log_first = log_lines(env, plan)
    proc = env.run("runner", "resume")
    assert proc.returncode == EXIT_OK, "runner resume failed at the first approval\n" + _explain(proc)
    wait_for(env, plan, lambda d: d.get("state") == "waiting_approval" and d.get("step") != first["step"],
             "waiting_approval at the second step")
    log_second = log_lines(env, plan)
    proc = env.run("runner", "resume")
    assert proc.returncode == EXIT_OK, "runner resume failed at the second approval\n" + _explain(proc)
    wait_state(env, plan, "completed")
    log_last = log_lines(env, plan)
    assert log_second[:len(log_first)] == log_first, "the log was rewritten between the two approvals"
    assert log_last[:len(log_second)] == log_second, "the log was rewritten after the second approval"
    assert len(log_last) > len(log_first), "resuming twice added nothing to attempts.log.jsonl"
    problems = chain_problems(log_last)
    assert not problems, "attempts.log.jsonl departs from §13.3:\n" + "\n".join(problems)


def test_vs_rn_15_status_prints_state_and_head_hash_and_writes_nothing(env):
    """VS-RN-15 · hermetic + harness · §3 runner status "Prints the plan's state … and the attempt-log head
    hash … Writes nothing"; §13.3 "runner status prints the head hash".

    Proposed replacement for VS-RN-04: the same assertions, with owned processes only. While waiting for
    approval, after the state has settled: `runner status` → exit 0; stdout holds `waiting_approval` and the
    SHA-256 of the log's last line; no file under the cache, run store, scratch or working directory is
    added, removed or changed.
    """
    plan = approval_plan("visible-gate-1")
    _started(env, plan, authorization(plan))
    wait_stable(env)
    before = env.snapshot()
    proc = env.run("runner", "status")
    after = env.snapshot()
    assert proc.returncode == EXIT_OK, "runner status failed\n" + _explain(proc)
    assert "waiting_approval" in proc.stdout, "the state is not on stdout\n" + _explain(proc)
    head = _line_hashes(log_lines(env, plan)[-1])
    assert any(h in proc.stdout for h in head), "the attempt-log head hash is not on stdout\n" + _explain(proc)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not changed, f"runner status changed files: {changed}"


# ---------------------------------------------------------------------------
# tests: integrity refusals (§13.2, §13.4)
# ---------------------------------------------------------------------------


def _assert_refused(env: Env, plan: bytes, proc: subprocess.CompletedProcess, code: int | None) -> None:
    if code is not None:
        assert proc.returncode == code, f"expected exit {code}\n" + _explain(proc)
    else:
        assert proc.returncode != EXIT_OK, "the plan was accepted\n" + _explain(proc)
    time.sleep(2)
    assert read_status(env, plan) is None, f"a refused plan has runner state: {read_status(env, plan)!r}"
    left = env.leftovers(proc)
    assert not left, "a refused start left a process running: " + "; ".join(left)


@pytest.mark.parametrize("variant", ["wrong-plan-sha256", "plan-edited-after-authorization"])
def test_vs_rn_16_plan_hash_mismatch_exits_2_and_starts_nothing(env, variant):
    """VS-RN-16 · hermetic + harness · §13.2 runner start exits 2 unless "the plan's SHA-256 equals
    plan_sha256"; "An authorized plan is never edited"; §3 a plan hash mismatch exits 2.

    Proposed replacement for VS-RN-05, with the no-child assertion made through owned observations only. An
    authorization naming another plan's hash, or a plan changed after it was authorized → exit 2; no
    `status.json` for the plan; no owned process alive and no process holding the command's stdout or stderr.
    """
    plan = approval_plan("visible-gate-1")
    if variant == "wrong-plan-sha256":
        auth = authorization(plan, plan_sha256=sha256_bytes(approval_plan("visible-gate-other")))
    else:
        auth, plan = authorization(plan), approval_plan("visible-gate-1", "visible-gate-added")
    proc, _, _ = start(env, plan, auth, watch_stdio=True)
    _assert_refused(env, plan, proc, EXIT_INTEGRITY)


def test_vs_rn_17_harness_revision_mismatch_exits_2(env):
    """VS-RN-17 · hermetic + harness · §13.2 runner start exits 2 unless "the installed harness revision
    equals harness_revision".

    Proposed replacement for VS-RN-06, with the no-child assertion made through owned observations only.
    `bindings.harness_revision` is a different 40-hex revision → exit 2; no `status.json`; no owned process
    alive and no process holding the command's stdout or stderr.
    """
    installed = harness_revision()
    other = "0" * 40 if installed != "0" * 40 else "1" * 40
    plan = approval_plan("visible-gate-1")
    proc, _, _ = start(env, plan, authorization(plan, harness=other), watch_stdio=True)
    _assert_refused(env, plan, proc, EXIT_INTEGRITY)


def test_vs_rn_18_a_pinned_hash_missing_from_bindings_exits_2(env):
    """VS-RN-18 · hermetic + harness + repo · §13.2 runner start exits 2 unless "every hash the plan pins is
    listed in bindings"; §13.1 paths relative to the repository root.

    Proposed replacement for VS-RN-07, with the no-child assertion made through owned observations only. A
    plan whose `verify` step pins a committed protocol (its real path and SHA-256) while `bindings.protocols`
    is empty → exit 2; no `status.json`; no owned process alive and no process holding the command's stdout
    or stderr. The path and hash are real, so the missing binding is the only fault.
    """
    root = _repo_root()
    if root is None:
        not_run("needs the repository that contains the pytest working directory")
    protocols = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS | TEST_DIRS)
        protocols += [Path(dirpath) / n for n in sorted(filenames)
                      if n.endswith((".yaml", ".yml")) and b"protocol_version" in (Path(dirpath) / n).read_bytes()]
    if not protocols:
        not_run("no committed protocol file found")
    protocol = protocols[0]
    step = {"type": "verify", "protocol": str(protocol.relative_to(root)),
            "protocol_sha256": sha256_bytes(protocol.read_bytes())}
    plan = approval_plan("visible-gate-1", extra_steps=(step,))
    proc, _, _ = start(env, plan, authorization(plan, protocols=()), watch_stdio=True)
    _assert_refused(env, plan, proc, EXIT_INTEGRITY)


@pytest.mark.parametrize("copy_name", ["plan.yaml", "authorization.json"])
def test_vs_rn_19_resume_refuses_a_tampered_stored_copy(env, copy_name):
    """VS-RN-19 · hermetic + harness · §13.4 resume "re-verifies the hashes of the stored plan and
    authorization … and exits 2 on any mismatch"; §13.5 integrity failures always stop the plan.

    Proposed replacement for VS-RN-08: the same assertions, with owned processes only. While waiting at the
    first of two approvals, the stored copy gains one trailing byte → `runner resume` exits 2, and the plan
    never moves to its second step or completes.
    """
    plan = approval_plan("visible-gate-1", "visible-gate-2")
    first = _started(env, plan, authorization(plan))
    stored = state_dir(env, plan) / copy_name
    stored.write_bytes(stored.read_bytes() + b" ")
    proc = env.run("runner", "resume")
    assert proc.returncode == EXIT_INTEGRITY, "expected exit 2\n" + _explain(proc)
    time.sleep(3)
    status = read_status(env, plan) or {}
    assert status.get("state") != "completed" and status.get("step") == first["step"], \
        f"the plan advanced after a refused resume: {status!r}"


def test_vs_rn_20_resume_uses_the_stored_copies_when_the_originals_are_gone(env):
    """VS-RN-20 · hermetic + harness · §13.2 "Every later runner command uses those copies, so moving or
    deleting the originals never blocks resume".

    Proposed replacement for VS-RN-09: the same assertions, with owned processes only. After start, the
    original plan and authorization files are deleted → `runner resume` exits 0 and the plan reaches its
    second approval.
    """
    plan = approval_plan("visible-gate-1", "visible-gate-2")
    proc, plan_path, auth_path = start(env, plan, authorization(plan))
    assert proc.returncode == EXIT_OK, "runner start failed\n" + _explain(proc)
    first = wait_state(env, plan, "waiting_approval")
    plan_path.unlink()
    auth_path.unlink()
    proc = env.run("runner", "resume")
    assert proc.returncode == EXIT_OK, "runner resume failed without the originals\n" + _explain(proc)
    wait_for(env, plan, lambda d: d.get("state") == "waiting_approval" and d.get("step") != first["step"],
             "waiting_approval at the second step")


@pytest.mark.parametrize("args", [("runner", "status"), ("runner", "pause"), ("runner", "pause", "--now"),
                                  ("runner", "resume")],
                         ids=["status", "pause", "pause-now", "resume"])
def test_vs_rn_21_runner_commands_without_a_plan_exit_3(env, args):
    """VS-RN-21 · hermetic · §3 exit 3 "A precondition is unmet (for example a missing file, tool, or run)";
    §3 written paths only on stdout.

    Proposed replacement for VS-RN-10: the same assertions, with owned processes only. An empty run store
    (no plan was ever started) → exit 3; empty stdout; nothing written.
    """
    before = env.snapshot()
    proc = env.run(*args)
    assert proc.returncode == EXIT_PRECONDITION, "expected exit 3\n" + _explain(proc)
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)
    assert env.snapshot() == before, "a failing runner command wrote files"


def test_vs_rn_22_a_step_type_outside_the_plan_schema_never_runs(env):
    """VS-RN-22 · hermetic + harness · §13.1 "There is no branching, looping, expansion or scheduling";
    prohibited: "an experiment or matrix engine".

    Proposed replacement for VS-RN-11, with owned processes only. A correctly authorized plan whose only step
    is a `matrix` step (with an expansion over models and seeds) → either `runner start` refuses it (non-zero
    exit, no state or state `stopped`, no owned process alive and no process holding its stdout or stderr),
    or the plan ends `stopped`. It never reaches `completed` or `waiting_approval`.
    """
    matrix = {"type": "matrix", "id": "visible-expansion", "over": {"model": ["a", "b"], "seed": [0, 1]}}
    plan = dump({"plan_version": "1", "steps": [matrix]})
    proc, _, _ = start(env, plan, authorization(plan), watch_stdio=True)
    if proc.returncode != EXIT_OK:
        time.sleep(2)
        status = read_status(env, plan)
        assert status is None or status.get("state") == "stopped", f"refused plan left state {status!r}"
        left = env.leftovers(proc)
        assert not left, "a refused start left a process running: " + "; ".join(left)
        return
    status = wait_state(env, plan, "stopped", "completed", "waiting_approval")
    assert status.get("state") == "stopped", f"a matrix step was run: {status!r}"
