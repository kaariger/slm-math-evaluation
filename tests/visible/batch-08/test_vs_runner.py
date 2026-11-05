"""Visible specification tests — runner control plane on approval-only plans (contract v0.8 §3 runner commands, §13).

Contract-only: written before any v0.8 implementation commit was supplied,
with no implementation code seen. Tests use only the `slm-eval` CLI and the
contract's file schemas.

Resources (stated in each docstring):
  hermetic  the `slm-eval` CLI only. Plans hold only `approval` steps, so no
            model, runtime, dataset or network is needed. Each test has its
            own temporary SLM_EVAL_CACHE, SLM_EVAL_RUNS, SLM_EVAL_SCRATCH and
            working directory.
  harness   the installed harness revision, for `bindings.harness_revision`
            (§13.2): SLM_EVAL_TEST_HARNESS_REVISION, or else `git rev-parse
            HEAD` of the repository that contains the pytest working
            directory. Skipped if neither is available.
  repo      a committed protocol file (VS-RN-07 only), named in a plan by its
            path relative to the repository root (§13.1).

`runner start` leaves a runner working in the background (§3). Every CLI
process gets a unique VISIBLE_SUITE_TAG environment variable; after each test,
every process carrying the tag is terminated. Runner commands write stdout and
stderr to files rather than pipes, so a background process holding inherited
handles cannot stall a test. State changes are awaited by polling
`status.json` for up to WAIT seconds.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable is passed to the implementation.
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
import time
import uuid
from pathlib import Path

import pytest

TIMEOUT = 300
WAIT = 120.0
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

# ---------------------------------------------------------------------------
# CLI, per-test environment and process cleanup
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


def tagged_pids(tag: str) -> list[int]:
    """Live processes whose environment carries VISIBLE_SUITE_TAG=<tag>."""
    needle = f"VISIBLE_SUITE_TAG={tag}"
    found = []
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if entry.name.isdigit():
                try:
                    if needle.encode() in (entry / "environ").read_bytes():
                        found.append(int(entry.name))
                except OSError:
                    continue
    else:
        out = subprocess.run(["ps", "-A", "-E", "-ww", "-o", "pid=,command="], capture_output=True,
                             encoding="utf-8", errors="replace").stdout
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit() and needle in parts[1]:
                found.append(int(parts[0]))
    return [pid for pid in found if pid != os.getpid()]


class Env:
    """A temporary cache, run store, scratch and working directory for one test; inputs live apart."""

    def __init__(self, root: Path):
        self.root = root
        self.cache, self.runs, self.scratch, self.work = (root / n for n in ("cache", "runs", "scratch", "work"))
        self.inputs, self.logs = root / "inputs", root / "logs"
        for directory in (self.cache, self.runs, self.scratch, self.work, self.inputs, self.logs):
            directory.mkdir(parents=True)
        self.tag = uuid.uuid4().hex
        self.vars = {k: v for k, v in os.environ.items() if _passed_on(k)}
        self.vars.update(SLM_EVAL_CACHE=str(self.cache), SLM_EVAL_RUNS=str(self.runs),
                         SLM_EVAL_SCRATCH=str(self.scratch), VISIBLE_SUITE_TAG=self.tag)

    def run(self, *args: object) -> subprocess.CompletedProcess:
        """Run a command to its exit; stdout and stderr go through files, never pipes."""
        name = uuid.uuid4().hex[:8]
        out_path, err_path = self.logs / f"{name}.out", self.logs / f"{name}.err"
        argv = _command() + [str(a) for a in args]
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            popen = subprocess.Popen(argv, cwd=self.work, env=self.vars, stdin=subprocess.DEVNULL, stdout=out,
                                     stderr=err, start_new_session=True)
            try:
                code = popen.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                popen.kill()
                raise
        return subprocess.CompletedProcess(argv, code, out_path.read_text("utf-8", "replace"),
                                           err_path.read_text("utf-8", "replace"))

    def snapshot(self) -> dict[str, bytes]:
        """Every file under the cache, run store, scratch and working directory, with its bytes."""
        return {str(p.relative_to(self.root)): p.read_bytes()
                for base in (self.cache, self.runs, self.scratch, self.work)
                for p in sorted(base.rglob("*")) if p.is_file()}

    def cleanup(self) -> None:
        pids = tagged_pids(self.tag)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in pids:
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            deadline = time.time() + 5
            while time.time() < deadline and (pids := tagged_pids(self.tag)):
                time.sleep(0.2)
            if not pids:
                return


@pytest.fixture
def env(tmp_path: Path):
    environment = Env(tmp_path)
    yield environment
    environment.cleanup()


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
    configured = os.environ.get("SLM_EVAL_TEST_HARNESS_REVISION", "").strip()
    if configured:
        return configured
    root = _repo_root()
    if root is not None and shutil.which("git"):
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              encoding="utf-8").stdout.strip()
        if HEX40.fullmatch(head):
            return head
    pytest.skip("needs the installed harness revision (§13.2): set SLM_EVAL_TEST_HARNESS_REVISION or run "
                "from a git checkout of the harness")


def approval_plan(*gates: str, extra_steps: tuple[dict, ...] = ()) -> bytes:
    return dump({"plan_version": "1",
                 "steps": [*extra_steps, *({"type": "approval", "id": gate} for gate in gates)]})


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


def start(env: Env, plan: bytes, auth: bytes) -> tuple[subprocess.CompletedProcess, Path, Path]:
    plan_path, auth_path = env.inputs / "plan.yaml", env.inputs / "authorization.json"
    plan_path.write_bytes(plan)
    auth_path.write_bytes(auth)
    return env.run("runner", "start", "--plan", plan_path, "--authorization", auth_path), plan_path, auth_path


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
    """§13.3 "the SHA-256 of the previous line": with or without its line terminator (not fixed by §13.3)."""
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


def test_vs_rn_01_start_prints_the_plan_hash_and_reaches_the_approval_wait(env):
    """VS-RN-01 · hermetic + harness · §3 runner start "Prints the plan's SHA-256"; §13.3 status.json;
    §13.4 "An approval step sets state waiting_approval".

    A plan of two approval steps with a matching authorization → exit 0; the
    plan's SHA-256 on stdout; `status.json` reaches `waiting_approval` and
    holds every §13.3 key, with the plan's and the authorization's SHA-256.
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


def test_vs_rn_02_stored_copies_and_hash_chained_log(env):
    """VS-RN-02 · hermetic + harness · §13.2 "stores byte-identical copies of the plan and the
    authorization"; §13.3 copies' SHA-256 equal status.json; attempts.log.jsonl hash chain.

    In `_runner/<plan_sha256>/`: `plan.yaml` and `authorization.json` are
    byte-identical to the inputs; `attempts.log.jsonl` has at least one line,
    each JSON with the six §13.3 keys, `seq` increasing, and `prev_sha256` of
    every line after the first equal to the SHA-256 of the line before it.
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


def test_vs_rn_03_resume_continues_past_each_approval_to_completion(env):
    """VS-RN-03 · hermetic + harness · §13.4 "resume continues past" an approval step; §3 runner resume;
    §13.3 attempts.log.jsonl is append-only.

    Two approval steps: waiting at the first → `runner resume` exit 0 →
    waiting at the second (a different `step`) → `runner resume` exit 0 →
    state `completed`. The log only grows: each earlier version is a prefix
    of the later one, and the chain holds throughout.
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


def test_vs_rn_04_status_prints_state_and_head_hash_and_writes_nothing(env):
    """VS-RN-04 · hermetic + harness · §3 runner status "Prints the plan's state … and the attempt-log head
    hash … Writes nothing"; §13.3 "runner status prints the head hash".

    While waiting for approval, after the state has settled: `runner status`
    → exit 0; stdout holds `waiting_approval` and the SHA-256 of the log's
    last line; no file under the cache, run store, scratch or working
    directory is added, removed or changed.
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
    assert tagged_pids(env.tag) == [], "a refused start left a process running"


@pytest.mark.parametrize("variant", ["wrong-plan-sha256", "plan-edited-after-authorization"])
def test_vs_rn_05_plan_hash_mismatch_exits_2_and_starts_nothing(env, variant):
    """VS-RN-05 · hermetic + harness · §13.2 runner start exits 2 unless "the plan's SHA-256 equals
    plan_sha256"; "An authorized plan is never edited"; §3 a plan hash mismatch exits 2.

    An authorization naming another plan's hash, or a plan changed after it
    was authorized → exit 2; no `status.json` for the plan; no process left.
    """
    plan = approval_plan("visible-gate-1")
    if variant == "wrong-plan-sha256":
        auth = authorization(plan, plan_sha256=sha256_bytes(approval_plan("visible-gate-other")))
    else:
        auth, plan = authorization(plan), approval_plan("visible-gate-1", "visible-gate-added")
    proc, _, _ = start(env, plan, auth)
    _assert_refused(env, plan, proc, EXIT_INTEGRITY)


def test_vs_rn_06_harness_revision_mismatch_exits_2(env):
    """VS-RN-06 · hermetic + harness · §13.2 runner start exits 2 unless "the installed harness revision
    equals harness_revision".

    `bindings.harness_revision` is a different 40-hex revision → exit 2; no
    `status.json`; no process left.
    """
    installed = harness_revision()
    other = "0" * 40 if installed != "0" * 40 else "1" * 40
    plan = approval_plan("visible-gate-1")
    proc, _, _ = start(env, plan, authorization(plan, harness=other))
    _assert_refused(env, plan, proc, EXIT_INTEGRITY)


def test_vs_rn_07_a_pinned_hash_missing_from_bindings_exits_2(env):
    """VS-RN-07 · hermetic + harness + repo · §13.2 runner start exits 2 unless "every hash the plan pins is
    listed in bindings"; §13.1 paths relative to the repository root.

    A plan whose `verify` step pins a committed protocol (its real path and
    SHA-256) while `bindings.protocols` is empty → exit 2; no `status.json`;
    no process left. The path and hash are real, so the missing binding is
    the only fault.
    """
    root = _repo_root()
    if root is None:
        pytest.skip("needs the repository that contains the pytest working directory")
    protocols = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS | TEST_DIRS)
        protocols += [Path(dirpath) / n for n in sorted(filenames)
                      if n.endswith((".yaml", ".yml")) and b"protocol_version" in (Path(dirpath) / n).read_bytes()]
    if not protocols:
        pytest.skip("no committed protocol file found")
    protocol = protocols[0]
    step = {"type": "verify", "protocol": str(protocol.relative_to(root)),
            "protocol_sha256": sha256_bytes(protocol.read_bytes())}
    plan = approval_plan("visible-gate-1", extra_steps=(step,))
    proc, _, _ = start(env, plan, authorization(plan, protocols=()))
    _assert_refused(env, plan, proc, EXIT_INTEGRITY)


@pytest.mark.parametrize("copy_name", ["plan.yaml", "authorization.json"])
def test_vs_rn_08_resume_refuses_a_tampered_stored_copy(env, copy_name):
    """VS-RN-08 · hermetic + harness · §13.4 resume "re-verifies the hashes of the stored plan and
    authorization … and exits 2 on any mismatch"; §13.5 integrity failures always stop the plan.

    While waiting at the first of two approvals, the stored copy gains one
    trailing byte → `runner resume` exits 2, and the plan never moves to its
    second step or completes.
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


def test_vs_rn_09_resume_uses_the_stored_copies_when_the_originals_are_gone(env):
    """VS-RN-09 · hermetic + harness · §13.2 "Every later runner command uses those copies, so moving or
    deleting the originals never blocks resume".

    After start, the original plan and authorization files are deleted →
    `runner resume` exits 0 and the plan reaches its second approval.
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
def test_vs_rn_10_runner_commands_without_a_plan_exit_3(env, args):
    """VS-RN-10 · hermetic · §3 exit 3 "A precondition is unmet (for example a missing file, tool, or run)";
    §3 written paths only on stdout.

    An empty run store (no plan was ever started) → exit 3; empty stdout;
    nothing written.
    """
    before = env.snapshot()
    proc = env.run(*args)
    assert proc.returncode == EXIT_PRECONDITION, "expected exit 3\n" + _explain(proc)
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)
    assert env.snapshot() == before, "a failing runner command wrote files"


def test_vs_rn_11_a_step_type_outside_the_plan_schema_never_runs(env):
    """VS-RN-11 · hermetic + harness · §13.1 "There is no branching, looping, expansion or scheduling";
    prohibited: "an experiment or matrix engine".

    A correctly authorized plan whose only step is a `matrix` step (with an
    expansion over models and seeds) → either `runner start` refuses it (non-
    zero exit, no state, no process), or the plan ends `stopped`. It never
    reaches `completed` or `waiting_approval`.
    """
    matrix = {"type": "matrix", "id": "visible-expansion", "over": {"model": ["a", "b"], "seed": [0, 1]}}
    plan = dump({"plan_version": "1", "steps": [matrix]})
    proc, _, _ = start(env, plan, authorization(plan))
    if proc.returncode != EXIT_OK:
        time.sleep(2)
        status = read_status(env, plan)
        assert status is None or status.get("state") == "stopped", f"refused plan left state {status!r}"
        return
    status = wait_state(env, plan, "stopped", "completed", "waiting_approval")
    assert status.get("state") == "stopped", f"a matrix step was run: {status!r}"
