"""Visible specification tests — adapter modes, effective values and fault injection with a real fixture model, acting only on provably owned processes (contract v0.8.1 §3, §4, §5.2, §6, §7, §13).

Contract-only: written from the approved interface and the maintainer's
readings, with no implementation code seen. Tests use only the `slm-eval`
CLI, the processes it starts and the contract's files.

Every test needs a REAL FIXTURE MODEL AND THE PINNED RUNTIME and is skipped as
NOT RUN unless all of these are present:

  SLM_EVAL_TEST_RUNTIME=1        the pinned llama.cpp runtime is available to
                                 the implementation
  SLM_EVAL_TEST_FIXTURE_PROTOCOL a `role: fixture` protocol (§4) for a small
                                 model, with a neutral `fixture-<n>` alias when
                                 it names one; for runner tests it must be a
                                 committed file inside the repository (§13.1)
  SLM_EVAL_TEST_SEED_CACHE       a dataset cache holding the pinned dataset and
                                 models/<model.artifact.file>; copied into each
                                 test's temporary SLM_EVAL_CACHE (files over
                                 256 MiB are symlinked)
  split manifest                 SLM_EVAL_TEST_MANIFEST, or the committed
                                 manifest with the pinned SHA-256
  tools                          `pgrep`, `ps` and `lsof`

Runner tests also need the installed harness revision
(SLM_EVAL_TEST_HARNESS_REVISION, or `git rev-parse HEAD` of the repository)
and a clean checkout at that revision. A fixture stands in for the adapter
families only in mode dispatch; it is never evidence of target compatibility.
Adapter versions are those of the maintainer's clarification (1.0.0).

Process ownership. A test inspects or signals only processes it can prove it
owns:
  * each CLI process it launches, in a new session and process group. Launch
    handles are not reaped until the test's cleanup, so their PIDs and group
    IDs cannot be reused while the test runs; exit statuses are read without
    reaping;
  * members of those process groups (`pgrep -g <owned group>`);
  * children of processes already proven owned (`pgrep -P <owned pid>`).
Each owned process is recorded with its start time (`ps -o stat=,lstart= -p
<pid>`), re-read before every signal and after every child listing; a changed
or missing start time means the PID no longer names that process, and nothing
is sent. `lsof -p` is called only with owned PIDs. No process table is scanned
for environments or command lines, and no other process is ever signalled.

NOT RUN. A test that would have to act on a process it cannot prove it owns
(for example a background runner that left the launching process group and
lineage before it was observed) is skipped as NOT RUN, naming that need. A
test whose fixture run finishes before its fault is injected is NOT RUN. A
NOT RUN skip is never a pass and never credits fault coverage.

Timing: model start-up is awaited for up to STARTUP seconds and a run for up
to RUN_WAIT seconds; no speed is required. VS-FX-15 watches for QUIET_WATCH
seconds to confirm nothing restarts on its own.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable, and no variable whose name contains TOKEN, SECRET,
PASSWORD or API_KEY, is passed to the implementation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
from collections import Counter
from fractions import Fraction
from pathlib import Path

import pytest

EXIT_OK, EXIT_INTEGRITY, EXIT_PRECONDITION = 0, 2, 3
STARTUP = 1800.0
RUN_WAIT = 3600.0
CONTROL_TIMEOUT = 300.0
QUIET_WATCH = 10.0
POLL, FAST_POLL = 0.5, 0.05
LARGE_FILE = 256 * 1024 * 1024
APPROVED_ADAPTER_VERSION = "1.0.0"
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
PINNED_MANIFEST_SHA256 = "a10c02e7ac60b563a4d112cad92c2540ce58d6c0a49fa73f844091df66e48c71"
SUBSET_METHOD = "largest-remainder allocation by subject, then a seeded draw within each subject"
FIXTURE_ALIAS = re.compile(r"fixture-\d+")
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
THINKING_STATES = {"absent", "empty", "present", "unterminated"}

SERVER_GAP = ("the run's model server is not provably owned: no process in the launched process group or its "
              "lineage was listening on TCP. Observing it needs a process-identity interface this suite does not "
              "have, so no fault was injected")
RUNNER_GAP = ("the background runner is not provably owned: no live process remained in the process group or "
              "lineage of `runner start` (for example, it detached into a new session before it was observed). "
              "Acting on it needs a runner that stays in the launching process group, or an approved "
              "process-identity interface; no process was signalled")


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

    def listening(self, exclude=()) -> set[int]:
        pids = sorted(self.live() - set(exclude))
        if not pids:
            return set()
        out = _query(["lsof", "-nP", "-a", "-p", ",".join(map(str, pids)), "-iTCP", "-sTCP:LISTEN", "-t"])
        found = {int(t) for t in out.split() if t.isdigit()} & set(pids)
        return {pid for pid in found if start_token(pid) == self.known.get(pid)}

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


class Env:
    """Temporary locations for one test, a seeded cache, and the processes it owns."""

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
        self.runner_state: Path | None = None

    def start(self, *args: object) -> tuple[subprocess.Popen, Path, Path]:
        name = uuid.uuid4().hex[:8]
        out_path, err_path = self.logs / f"{name}.out", self.logs / f"{name}.err"
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            popen = self.owned.launch(_command() + [str(a) for a in args], cwd=self.work, env=self.vars, out=out,
                                      err=err)
        return popen, out_path, err_path

    def wait_exit(self, popen: subprocess.Popen, timeout: float, poll: float = POLL) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.owned.refresh()
            code = self.owned.exit_code(popen)
            if code is not None:
                self.owned.refresh()
                return code
            time.sleep(poll)
        try:
            os.killpg(popen.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        pytest.fail(f"{popen.args!r} did not exit within {timeout:.0f} s", pytrace=False)

    def run(self, *args: object, timeout: float = RUN_WAIT, poll: float = POLL) -> subprocess.CompletedProcess:
        popen, out_path, err_path = self.start(*args)
        code = self.wait_exit(popen, timeout, poll)
        return subprocess.CompletedProcess(popen.args, code, out_path.read_text("utf-8", "replace"),
                                           err_path.read_text("utf-8", "replace"))

    def cleanup(self) -> None:
        if self.runner_state is not None and runner_status(self.runner_state).get("state") == "running":
            # Contract-level stop for a runner this test may not be able to identify (§13.4).
            try:
                popen, _, _ = self.start("runner", "pause", "--now")
                deadline = time.monotonic() + CONTROL_TIMEOUT
                while time.monotonic() < deadline and self.owned.exit_code(popen) is None:
                    time.sleep(POLL)
            except Exception:
                pass
        self.owned.cleanup()


@pytest.fixture
def env(tmp_path: Path):
    require_live()
    environment = Env(tmp_path)
    try:
        seed_cache(environment)
        yield environment
    finally:
        environment.cleanup()


def _explain(proc: subprocess.CompletedProcess) -> str:
    return (f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump(data) -> bytes:
    """JSON text, which every YAML parser also reads."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


# ---------------------------------------------------------------------------
# capabilities and inputs
# ---------------------------------------------------------------------------


def require_live() -> None:
    if os.environ.get("SLM_EVAL_TEST_RUNTIME", "").strip() != "1":
        not_run("needs the pinned runtime and a fixture model: set SLM_EVAL_TEST_RUNTIME=1")
    for variable in ("SLM_EVAL_TEST_FIXTURE_PROTOCOL", "SLM_EVAL_TEST_SEED_CACHE"):
        if not os.environ.get(variable, "").strip():
            not_run(f"needs {variable}")
    missing = [tool for tool in ("pgrep", "ps", "lsof") if shutil.which(tool) is None]
    if missing:
        not_run(f"needs {', '.join(missing)} on PATH for owned-process observation")
    if not (hasattr(os, "waitid") and hasattr(os, "WNOWAIT")):
        not_run("needs os.waitid with WNOWAIT to read exit statuses without releasing owned PIDs")


def seed_cache(env: Env) -> None:
    source = Path(os.environ["SLM_EVAL_TEST_SEED_CACHE"]).expanduser()
    for dirpath, _, filenames in os.walk(source, followlinks=True):
        target = env.cache / Path(dirpath).relative_to(source)
        target.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            real = (Path(dirpath) / name).resolve()
            if real.stat().st_size > LARGE_FILE:
                os.symlink(real, target / name)
            else:
                shutil.copy2(real, target / name)


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError:
        not_run("PyYAML is needed to read protocol files")
    return yaml


def fixture_protocol() -> tuple[Path, bytes, dict]:
    """The fixture protocol, checked to be a clearly labelled fixture with its own template binding."""
    path = Path(os.environ["SLM_EVAL_TEST_FIXTURE_PROTOCOL"]).expanduser().resolve()
    raw = path.read_bytes()
    data = _yaml().safe_load(raw.decode("utf-8"))
    if not (isinstance(data, dict) and data.get("role") == "fixture"):
        pytest.fail("SLM_EVAL_TEST_FIXTURE_PROTOCOL must name a protocol with role: fixture (§4)", pytrace=False)
    alias = dig(data, "runtime", "model_alias")
    if alias is not None and not FIXTURE_ALIAS.fullmatch(str(alias)):
        pytest.fail(f"the fixture protocol's alias {alias!r} is not a fixture-<n> alias (§10)", pytrace=False)
    if not HEX64.fullmatch(str(dig(data, "model", "chat_template", "sha256"))):
        pytest.fail("the fixture protocol has no 64-hex chat_template.sha256 (§4)", pytrace=False)
    return path, raw, data


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


def manifest_path() -> Path:
    configured = os.environ.get("SLM_EVAL_TEST_MANIFEST", "").strip()
    if configured:
        return Path(configured).expanduser()
    root = _repo_root()
    if root is not None:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS | TEST_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if name.endswith(".json") and sha256_file(path) == PINNED_MANIFEST_SHA256:
                    return path
    not_run("needs the split manifest: set SLM_EVAL_TEST_MANIFEST")


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
    not_run("needs the installed harness revision: set SLM_EVAL_TEST_HARNESS_REVISION")


def largest_remainder(counts: dict[str, int], size: int) -> dict[str, int]:
    """§5.2 allocation in exact arithmetic: proportional shares, largest remainders first, ties by name."""
    total = sum(counts.values())
    quotas = {s: Fraction(size * n, total) for s, n in counts.items()}
    allocation = {s: q.numerator // q.denominator for s, q in quotas.items()}
    for subject in sorted(counts, key=lambda s: (-(quotas[s] - allocation[s]), s))[:size - sum(allocation.values())]:
        allocation[subject] += 1
    return allocation


def rebuild_ids(manifest: dict, size: int, seed: int) -> list[str]:
    """v0.8.1 §5.2: per subject a fresh random.Random(seed).sample in tiers.validation order; listed in that order."""
    tier = list(manifest["tiers"]["validation"])
    subject = {item["id"]: item.get("subject") for item in manifest["items"]}
    drawn = set()
    for name, count in largest_remainder(Counter(subject[i] for i in tier), size).items():
        drawn |= set(random.Random(seed).sample([i for i in tier if subject[i] == name], count))
    return [i for i in tier if i in drawn]


# ---------------------------------------------------------------------------
# runs and their records
# ---------------------------------------------------------------------------


def verify(env: Env, protocol: Path) -> None:
    for command in ("verify-artifact", "verify-runtime"):
        proc = env.run(command, "--protocol", protocol, timeout=STARTUP)
        assert proc.returncode == EXIT_OK, f"{command} failed on the fixture protocol\n" + _explain(proc)


def run_dirs(env: Env) -> list[Path]:
    return sorted(p for p in env.runs.iterdir() if p.is_dir() and not p.name.startswith("_"))


def complete_lines(run_dir: Path) -> list[bytes]:
    path = run_dir / "items.jsonl"
    if not path.is_file():
        return []
    data = path.read_bytes()
    return data[:data.rfind(b"\n") + 1].splitlines()


def wait_until(env: Env, predicate, timeout: float, what: str, poll: float = POLL):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        env.owned.refresh()
        value = predicate()
        if value:
            return value
        time.sleep(poll)
    pytest.fail(f"timed out after {timeout:.0f} s waiting for {what}", pytrace=False)


def run_json(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def planned_pairs(run_dir: Path) -> set[tuple]:
    """The tier, or the stored subset copy, × k (§6.1; stored subset.json bound by run.json.subset_sha256)."""
    doc = run_json(run_dir)
    k = int(doc.get("k") or 1)
    if doc.get("subset_sha256") is None:
        ids = dig(json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")), "tiers", doc.get("tier"))
    else:
        copy_path = run_dir / "subset.json"
        assert copy_path.is_file() and sha256_file(copy_path) == doc["subset_sha256"], \
            "a subset run has no stored subset.json with run.json subset_sha256"
        ids = json.loads(copy_path.read_text(encoding="utf-8"))["ids"]
    return {(i, s) for i in ids or [] for s in range(k)}


def items_problems(run_dir: Path) -> list[str]:
    """§6.1 and §6.3 after a finished run: whole JSON lines, valid hashes, each planned pair exactly once."""
    problems, pairs = [], []
    raw = (run_dir / "items.jsonl").read_bytes() if (run_dir / "items.jsonl").is_file() else b""
    if raw and not raw.endswith(b"\n"):
        problems.append("items.jsonl ends in a partial line")
    for n, line in enumerate(raw.splitlines(), start=1):
        try:
            record = json.loads(line)
        except ValueError:
            problems.append(f"items.jsonl line {n} is not JSON (partial write)")
            continue
        pairs.append((record.get("item_id"), record.get("sample_index")))
        if sha256_bytes(str(record.get("raw_output")).encode("utf-8")) != record.get("raw_output_sha256"):
            problems.append(f"line {n}: raw_output_sha256 mismatch")
        if not HEX64.fullmatch(str(record.get("prompt_sha256"))):
            problems.append(f"line {n}: prompt_sha256 is not 64-hex")
        if record.get("thinking_state") not in THINKING_STATES:
            problems.append(f"line {n}: thinking_state {record.get('thinking_state')!r}")
    if len(pairs) != len(set(pairs)):
        problems.append("an item × sample pair was generated twice")
    planned = planned_pairs(run_dir)
    if set(pairs) != planned:
        problems.append(f"{len(set(pairs) & planned)} of {len(planned)} planned pairs recorded, "
                        f"{len(set(pairs) - planned)} unplanned")
    return problems


def wait_first_item(env: Env, popen: subprocess.Popen) -> Path:
    def ready():
        dirs = run_dirs(env)
        if dirs and complete_lines(dirs[0]):
            return dirs[0]
        code = env.owned.exit_code(popen)
        if code == EXIT_OK:
            not_run("the fixture run finished before its first item was observed")
        if code is not None:
            pytest.fail(f"the run exited with {code} before its first item", pytrace=False)
        return None
    return wait_until(env, ready, STARTUP, "the first item")


def start_smoke_run(env: Env) -> tuple[subprocess.Popen, Path]:
    protocol, _, _ = fixture_protocol()
    verify(env, protocol)
    popen, _, _ = env.start("run", "--protocol", protocol, "--manifest", manifest_path(), "--tier", "smoke",
                            "--k", "1")
    return popen, wait_first_item(env, popen)


def crashed_run(env: Env) -> tuple[Path, bytes]:
    """A smoke run crashed after its first item: SIGKILL to the CLI and its server alike; (run_dir, items)."""
    popen, run_dir = start_smoke_run(env)
    env.owned.refresh()
    if env.owned.exit_code(popen) is not None:
        not_run("the fixture run finished before it could be crashed")
    if len(complete_lines(run_dir)) >= len(planned_pairs(run_dir)):
        not_run("every planned pair was written before the crash could be injected")
    if not env.owned.listening(exclude={popen.pid}):
        not_run(SERVER_GAP)
    env.owned.kill_all(signal.SIGKILL)
    if not env.owned.wait_gone(30):
        pytest.fail("owned processes survived SIGKILL", pytrace=False)
    if env.owned.exit_code(popen) != -signal.SIGKILL:
        not_run(f"the run exited with {env.owned.exit_code(popen)} on its own before the crash took effect")
    path = run_dir / "items.jsonl"
    return run_dir, path.read_bytes() if path.is_file() else b""


# ---------------------------------------------------------------------------
# tests: adapter modes and effective values
# ---------------------------------------------------------------------------

MODE_CASES = [("phi4-reasoning", "always_on", "thinking_off"), ("phi4-reasoning", "always_on", "forced_non_think"),
              ("gemma4", "thinking_on", "forced_think"), ("gemma4", "thinking_off", "hybrid")]


@pytest.mark.parametrize("adapter_id,allowed,disallowed", MODE_CASES, ids=[f"{a}-{d}" for a, _, d in MODE_CASES])
def test_vs_fx_09_mode_rejection_against_an_allowed_control(env, adapter_id, allowed, disallowed):
    """VS-FX-09 · live · §4 "A reasoning_mode value the adapter doesn't allow makes verify-artifact … exit 3";
    reasoning modes by adapter (Phi thinking-off rejection; Gemma thinking on and off).

    Proposed replacement for VS-FX-01. The fixture protocol, still `role: fixture` with its own alias and
    template, with the adapter set at version 1.0.0: with an allowed mode `verify-artifact` exits 0 and writes
    its record under the protocol's SHA-256; with a disallowed mode it exits 3 and writes no record. Only the
    mode differs, so the exit 3 isolates it.
    """
    _, _, data = fixture_protocol()
    outcomes = {}
    for mode in (allowed, disallowed):
        variant = copy.deepcopy(data)
        variant["model"]["adapter"] = {"id": adapter_id, "version": APPROVED_ADAPTER_VERSION}
        variant["reasoning_mode"] = {"value": mode,
                                     "provenance": dig(data, "reasoning_mode", "provenance") or "local-choice"}
        raw = dump(variant)
        path = env.inputs / f"protocol-{adapter_id}-{mode}.yaml"
        path.write_bytes(raw)
        proc = env.run("verify-artifact", "--protocol", path, timeout=STARTUP)
        record = env.runs / "_verification" / sha256_bytes(raw) / "artifact-verification.json"
        outcomes[mode] = (proc, record.is_file())
    proc, written = outcomes[allowed]
    assert proc.returncode == EXIT_OK and written, f"allowed mode {allowed} was not verified\n" + _explain(proc)
    proc, written = outcomes[disallowed]
    assert proc.returncode == EXIT_PRECONDITION, f"disallowed mode {disallowed}: expected exit 3\n" + _explain(proc)
    assert not written, f"disallowed mode {disallowed} wrote an artifact-verification record"


# Distinctive per-request values, none a llama.cpp default, so a value can only appear in effective.sampling if
# it was taken from the protocol.
DISTINCTIVE_SAMPLING = {"temperature": 0.61, "top_p": 0.93, "top_k": 17, "min_p": 0.02, "presence_penalty": 0.01,
                        "repeat_penalty": 1.03, "max_output_tokens": 383}


def _numbers(obj) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, str):
        try:
            value = float(obj)
        except ValueError:
            return []
        return [value] if math.isfinite(value) else []
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _numbers(v)]
    if isinstance(obj, list):
        return [n for v in obj for n in _numbers(v)]
    return []


def _same(a, b) -> bool:
    numbers_a, numbers_b = _numbers(a), _numbers(b)
    if len(numbers_a) == 1 and len(numbers_b) == 1:
        return abs(numbers_a[0] - numbers_b[0]) <= 1e-9 * max(1.0, abs(numbers_b[0]))
    return a == b


def test_vs_fx_10_a_fixture_run_records_the_values_it_sent_and_cannot_be_reported(env):
    """VS-FX-10 · live · §4 "The values actually sent are recorded in run.json"; §6.1 role, adapter,
    chat_template_sha256, effective, items; §7 a fixture run can't be reported.

    Proposed replacement for VS-FX-02, with a meaningful declared-versus-effective check that invents no
    key names. The fixture protocol with distinctive per-request sampling values (only fields it already
    declares) → a completed smoke run: `run.json` has role fixture, the protocol's adapter and chat-template
    hash, and `effective` with `reasoning`, a `template_args` mapping and a `sampling` mapping in which every
    distinctive value appears (under any name) and every name shared with the protocol has the protocol's
    value; every item is whole and hashed, no item generated more than max_output_tokens, and the tier is
    covered. `report` exits 3 and writes no report.
    """
    _, _, data = fixture_protocol()
    variant = copy.deepcopy(data)
    perturbed = {}
    for name, value in DISTINCTIVE_SAMPLING.items():
        entry = dig(variant, "sampling", name)
        if isinstance(entry, dict) and "value" in entry:
            entry["value"] = value
            perturbed[name] = value
    if len(perturbed) < 3:
        pytest.fail("the fixture protocol declares fewer than three per-request sampling fields", pytrace=False)
    protocol = env.inputs / "protocol-distinctive-sampling.yaml"
    protocol.write_bytes(dump(variant))
    verify(env, protocol)
    proc = env.run("run", "--protocol", protocol, "--manifest", manifest_path(), "--tier", "smoke", "--k", "1")
    assert proc.returncode == EXIT_OK, "the fixture smoke run failed\n" + _explain(proc)
    dirs = run_dirs(env)
    assert len(dirs) == 1, f"expected one run directory, found {[d.name for d in dirs]}\n" + _explain(proc)
    run_dir = dirs[0]
    doc = run_json(run_dir)
    assert doc.get("role") == "fixture", f"role {doc.get('role')!r}"
    assert dig(doc, "adapter", "id") == (dig(variant, "model", "adapter", "id") or "qwen3"), f"adapter {doc.get('adapter')!r}"
    assert doc.get("chat_template_sha256") == dig(variant, "model", "chat_template", "sha256")
    effective = doc.get("effective") or {}
    assert "reasoning" in effective and isinstance(effective.get("template_args"), dict), f"effective {effective!r}"
    sent = effective.get("sampling")
    assert isinstance(sent, dict) and sent, f"effective.sampling {sent!r}"
    values = _numbers(sent)
    absent = {k: v for k, v in perturbed.items() if not any(_same(n, v) for n in values)}
    assert not absent, f"declared values never sent (effective.sampling {sent!r}): {absent}"
    declared = {k: v.get("value") for k, v in (variant.get("sampling") or {}).items() if isinstance(v, dict)}
    differing = {k: (declared[k], sent[k]) for k in set(declared) & set(sent) if not _same(sent[k], declared[k])}
    assert not differing, f"values sent differ from the protocol: {differing}"
    problems = items_problems(run_dir)
    cap = declared.get("max_output_tokens")
    over = [n for n, line in enumerate(complete_lines(run_dir), start=1)
            if isinstance(cap, int) and isinstance(json.loads(line).get("tokens_generated"), int)
            and json.loads(line)["tokens_generated"] > cap]
    if over:
        problems.append(f"items {over[:10]} generated more than max_output_tokens {cap}")
    assert not problems, "\n".join(problems)
    report = env.run("report", "--run", run_dir.name, timeout=CONTROL_TIMEOUT)
    assert report.returncode == EXIT_PRECONDITION, "a fixture run was reported\n" + _explain(report)
    written = [n for n in ("report.json", "report.md") if (run_dir / n).exists()]
    assert not written, f"a refused report wrote {written}"


# ---------------------------------------------------------------------------
# tests: crash, resume and integrity (§3, §6.3)
# ---------------------------------------------------------------------------


def test_vs_fx_11_a_crashed_run_resumes_without_regenerating(env):
    """VS-FX-11 · live · §6.3 resume: only missing pairs are generated, completed pairs never are, each resume
    appends an attempt; §6.1 "A partial generation is never written … It is recorded as an interrupted
    event"; §10 cleanup.

    Proposed replacement for VS-FX-03, signalling only owned processes. A smoke run crashed after its first
    item, while pairs remain and its owned server is listening (SIGKILL to the owned process group and every
    owned descendant): items.jsonl holds only whole lines. `run --resume` → exit 0; the pre-crash bytes are
    kept; every planned pair appears once; ≥ 2 attempts, the later ones with `resumed_from`; an `interrupted`
    event (or attempt) is recorded; no owned process is left, or listening, afterwards.
    """
    run_dir, before = crashed_run(env)
    assert not before or before.endswith(b"\n"), "a crash left a partial line in items.jsonl"
    proc = env.run("run", "--resume", run_dir.name)
    assert proc.returncode == EXIT_OK, "resume failed\n" + _explain(proc)
    assert (run_dir / "items.jsonl").read_bytes().startswith(before), "pre-crash items were rewritten"
    problems = items_problems(run_dir)
    assert not problems, "\n".join(problems)
    doc = run_json(run_dir)
    attempts = doc.get("attempts") or []
    assert len(attempts) >= 2 and all(a.get("resumed_from") is not None for a in attempts[1:]), \
        f"attempts {attempts!r}"
    interrupted = any(e.get("type") == "interrupted" for e in doc.get("events") or []) or \
        any(a.get("status") == "interrupted" for a in attempts)
    assert interrupted, "the crash is recorded neither as an interrupted event nor as an interrupted attempt"
    env.owned.refresh()
    assert not env.owned.listening(), "an owned server is still listening after the resumed run"
    assert not env.owned.live(), f"owned processes remain after the resumed run exited: {sorted(env.owned.live())}"


def test_vs_fx_12_resume_refuses_a_different_model_file(env):
    """VS-FX-12 · live · §6.3 "Resume is allowed only when the protocol, manifest, model, and runtime build are
    all identical to the original run. Any difference exits 2 and generates nothing".

    Proposed replacement for VS-FX-04, signalling only owned processes. A crashed smoke run (as in
    VS-FX-11); the model file in the test's cache is replaced by different bytes → `run --resume` exits 2 and
    items.jsonl is byte-identical.
    """
    run_dir, before = crashed_run(env)
    _, _, data = fixture_protocol()
    model = env.cache / "models" / str(dig(data, "model", "artifact", "file"))
    model.unlink()
    model.write_bytes(b"visible-suite: not the recorded model\n")
    proc = env.run("run", "--resume", run_dir.name)
    assert proc.returncode == EXIT_INTEGRITY, "expected exit 2\n" + _explain(proc)
    after = (run_dir / "items.jsonl").read_bytes() if (run_dir / "items.jsonl").is_file() else b""
    assert after == before, "a refused resume changed items.jsonl"


@pytest.mark.parametrize("case", ["wrong-manifest-hash", "wrong-tier", "control-valid-subset"])
def test_vs_fx_13_subset_mismatch_exits_2_against_a_valid_control(env, case):
    """VS-FX-13 · live · §3 run "Exits 2 … if the subset's manifest_sha256 or tier doesn't match"; §5.2; stored
    subset copy bound by run.json.subset_sha256.

    Proposed replacement for VS-FX-05. With both verification records in place, a size-2 subset of the
    validation tier drawn by the independent §5.2 rebuild (seed 0), so that only the injected fault differs:
    another manifest's hash with `--tier validation`, or the valid subset with `--tier smoke` → exit 2 and no
    run directory. Control: the valid subset with `--tier validation` → exit 0; one run directory whose
    run.json records the subset file's SHA-256, a stored subset.json with those exact bytes, and items for
    exactly the subset's ids.
    """
    protocol, _, _ = fixture_protocol()
    manifest = manifest_path()
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    verify(env, protocol)
    subset = {"subset_version": "1", "manifest_sha256": sha256_file(manifest), "tier": "validation", "size": 2,
              "stratify": "subject", "method": SUBSET_METHOD, "seed": 0, "ids": rebuild_ids(doc, 2, 0)}
    run_tier = "validation"
    if case == "wrong-manifest-hash":
        subset["manifest_sha256"] = "0" * 64
    elif case == "wrong-tier":
        run_tier = "smoke"
    raw = dump(subset)
    path = env.inputs / "subset.json"
    path.write_bytes(raw)
    proc = env.run("run", "--protocol", protocol, "--manifest", manifest, "--tier", run_tier, "--subset", path)
    if case != "control-valid-subset":
        assert proc.returncode == EXIT_INTEGRITY, "expected exit 2\n" + _explain(proc)
        assert run_dirs(env) == [], "a refused run created a run directory"
        return
    assert proc.returncode == EXIT_OK, "the valid subset run failed\n" + _explain(proc)
    dirs = run_dirs(env)
    assert len(dirs) == 1, f"expected one run directory, found {[d.name for d in dirs]}"
    record = run_json(dirs[0])
    assert record.get("subset_sha256") == sha256_bytes(raw), f"subset_sha256 {record.get('subset_sha256')!r}"
    stored = dirs[0] / "subset.json"
    assert stored.is_file() and stored.read_bytes() == raw, "the run stores no exact copy of subset.json"
    problems = items_problems(dirs[0])
    assert not problems, "\n".join(problems)
    assert {i for i, _ in planned_pairs(dirs[0])} == set(subset["ids"])


# ---------------------------------------------------------------------------
# tests: runner pause, crash and retries (§13.4, §13.5)
# ---------------------------------------------------------------------------


def runner_status(state: Path) -> dict:
    try:
        doc = json.loads((state / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def runner_plan(env: Env) -> tuple[bytes, Path]:
    """A verify step and a smoke run step pinned to the committed fixture protocol, started and authorized."""
    protocol, raw, data = fixture_protocol()
    root = _repo_root()
    if root is None or root not in protocol.parents:
        not_run("runner tests need SLM_EVAL_TEST_FIXTURE_PROTOCOL committed inside the repository (§13.1)")
    relative, digest, manifest = str(protocol.relative_to(root)), sha256_bytes(raw), sha256_file(manifest_path())
    plan = dump({"plan_version": "1", "steps": [
        {"type": "verify", "protocol": relative, "protocol_sha256": digest},
        {"type": "run", "protocol": relative, "protocol_sha256": digest, "manifest_sha256": manifest,
         "tier": "smoke", "subset_sha256": None, "k": 1, "seed_base": 0}]})
    auth = dump({
        "authorization_version": "1", "plan_sha256": sha256_bytes(plan),
        "scope": "visible-suite fixture runner check",
        "bindings": {"protocols": [digest], "artifacts": [dig(data, "model", "artifact", "sha256")], "specs": [],
                     "manifest": manifest, "subsets": [], "runtime_build": dig(data, "runtime", "build"),
                     "harness_revision": harness_revision()},
        "attempts_allowed": 1, "stop_conditions": ["any integrity failure"],
        "approved_by": "maintainer", "approved_utc": "2026-11-05T00:00:00Z"})
    plan_path, auth_path = env.inputs / "plan.yaml", env.inputs / "authorization.json"
    plan_path.write_bytes(plan)
    auth_path.write_bytes(auth)
    env.runner_state = env.runs / "_runner" / sha256_bytes(plan)
    proc = env.run("runner", "start", "--plan", plan_path, "--authorization", auth_path, timeout=CONTROL_TIMEOUT,
                   poll=FAST_POLL)
    assert proc.returncode == EXIT_OK, "runner start failed\n" + _explain(proc)
    return plan, env.runner_state


def runner_run_dir(env: Env, state: Path) -> Path:
    def ready():
        status = runner_status(state)
        if status.get("state") == "completed":
            not_run("the fixture plan completed before its first item was observed")
        run_id = status.get("run_id")
        run_dir = env.runs / str(run_id) if run_id else None
        return run_dir if run_dir is not None and complete_lines(run_dir) else None
    return wait_until(env, ready, STARTUP, "the runner's first generated item", poll=FAST_POLL)


def owned_runner_server(env: Env) -> set[int]:
    """The listening server of a running plan, provided the runner and server are provably owned."""
    env.owned.refresh()
    leaders = {p.pid for p in env.owned.leaders}
    if not env.owned.live() - leaders:
        not_run(RUNNER_GAP)
    servers = env.owned.listening()
    if not servers:
        not_run(SERVER_GAP)
    return servers


SETTLED = {"completed", "stopped", "interrupted", "paused", "waiting_approval"}


def resume_to_completion(env: Env, state: Path) -> None:
    """`runner resume`, then wait for the plan to settle (§13.3 states); it must settle as completed."""
    before = runner_status(state)
    proc = env.run("runner", "resume", timeout=CONTROL_TIMEOUT, poll=FAST_POLL)
    assert proc.returncode == EXIT_OK, "runner resume failed\n" + _explain(proc)
    wait_until(env, lambda: runner_status(state) != before, STARTUP, "status.json to change after resume")
    status = wait_until(env, lambda: s if (s := runner_status(state)).get("state") in SETTLED else None, RUN_WAIT,
                        "the plan to settle")
    assert status.get("state") == "completed", f"the resumed plan settled as {status.get('state')!r}, not completed"


def snapshot(*roots: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for root in roots for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("now", [False, True], ids=["pause", "pause-now"])
def test_vs_fx_14_runner_pause_stops_the_server_and_resume_completes(env, now):
    """VS-FX-14 · live + harness · §13.4 pause takes effect after the item in progress (`--now`: at once, the
    item discarded and regenerated); "stops the model server … sets state paused"; resume continues from the
    last checkpoint.

    Proposed replacement for VS-FX-06, observing only owned processes. During the smoke step, with the
    runner's server provably owned: `runner pause` (or `--now`) exits 0; the state becomes `paused`; the
    observed server process is gone and no owned process listens. `runner resume` exits 0 and the plan
    completes; every planned pair appears once; `run.json` records the pause and the resume.
    """
    _, state = runner_plan(env)
    run_dir = runner_run_dir(env, state)
    servers = owned_runner_server(env)
    proc = env.run("runner", "pause", *(["--now"] if now else []), timeout=CONTROL_TIMEOUT, poll=FAST_POLL)
    assert proc.returncode == EXIT_OK, "runner pause failed\n" + _explain(proc)
    status = wait_until(env, lambda: s if (s := runner_status(state)).get("state") in ("paused", "completed") else None,
                        STARTUP, "state paused")
    if status.get("state") == "completed":
        not_run("the fixture plan completed before the pause took effect")
    time.sleep(3)
    env.owned.refresh()
    assert not servers & env.owned.live(), "the model server is still running while paused"
    assert not env.owned.listening(), "an owned process is listening while paused"
    resume_to_completion(env, state)
    problems = items_problems(run_dir)
    assert not problems, "\n".join(problems)
    doc = run_json(run_dir)
    types = [e.get("type") for e in doc.get("events") or []]
    statuses = [a.get("status") for a in doc.get("attempts") or []]
    assert ("pause" in types and "resume" in types) or "paused" in statuses, \
        f"run.json records no pause and resume: events {types}, attempts {statuses}"


def test_vs_fx_15_runner_recovers_from_a_crash(env):
    """VS-FX-15 · live + harness · §13.4 "The same command recovers after a crash, a reboot or a power loss
    (state interrupted). Nothing restarts automatically"; §6.3.

    Proposed replacement for VS-FX-07, signalling only owned processes. During the smoke step, with the runner
    and its server provably owned and pairs remaining, every owned process is killed (SIGKILL). For 10 s no
    owned process exists and no file in the runner state or the run directory changes. `runner resume` exits
    0, the plan completes, every planned pair appears once, and the run has at least two attempts.
    """
    _, state = runner_plan(env)
    run_dir = runner_run_dir(env, state)
    owned_runner_server(env)
    if len(complete_lines(run_dir)) >= len(planned_pairs(run_dir)):
        not_run("every planned pair was written before the crash could be injected")
    env.owned.kill_all(signal.SIGKILL)
    if not env.owned.wait_gone(30):
        pytest.fail("owned processes survived SIGKILL", pytrace=False)
    if runner_status(state).get("state") == "completed":
        not_run("the fixture plan completed before the crash took effect")
    before = snapshot(state, run_dir)
    time.sleep(QUIET_WATCH)
    env.owned.refresh()
    assert not env.owned.live(), "an owned process exists after the crash"
    assert snapshot(state, run_dir) == before, "work restarted on its own: runner state or run files changed"
    resume_to_completion(env, state)
    problems = items_problems(run_dir)
    assert not problems, "\n".join(problems)
    assert len(run_json(run_dir).get("attempts") or []) >= 2, "the resumed run has a single attempt"


def test_vs_fx_16_a_server_crash_is_retried_and_accounted(env):
    """VS-FX-16 · live · §13.5 infrastructure reasons include "a server crash while the machine is awake"; up
    to 2 retries; "Every retry is recorded, and the first completed generation is the one scored"; §6.1
    `infra_retries`, `server_restart` and `infra_retry` events.

    Proposed replacement for VS-FX-08, signalling only owned processes. During a smoke run, while pairs
    remain, only the owned listening server is killed (SIGKILL), once. The run exits 0 with status
    `completed`; every planned pair appears once; `run.json` has a `server_restart` or `infra_retry` event;
    every `infra_retries` is in 0..2, and their total does not exceed the number of `infra_retry` events.
    """
    popen, run_dir = start_smoke_run(env)
    env.owned.refresh()
    servers = env.owned.listening(exclude={popen.pid})
    planned = len(planned_pairs(run_dir))
    if env.owned.exit_code(popen) is not None or len(complete_lines(run_dir)) >= planned:
        not_run("the fixture run finished before the server could be crashed")
    if not servers:
        not_run(SERVER_GAP)
    killed = {pid for pid in servers if env.owned.send(pid, signal.SIGKILL)}
    if not killed:
        not_run("the observed server exited before the fault")
    if env.owned.exit_code(popen) is not None or len(complete_lines(run_dir)) >= planned:
        not_run("the fixture run finished while the server crash was injected")
    code = env.wait_exit(popen, RUN_WAIT)
    assert not killed & env.owned.live(), "the killed server is still running"
    assert code == EXIT_OK, f"the run exited {code} after one server crash"
    doc = run_json(run_dir)
    assert doc.get("status") == "completed", f"status {doc.get('status')!r}"
    problems = items_problems(run_dir)
    assert not problems, "\n".join(problems)
    types = [e.get("type") for e in doc.get("events") or []]
    assert "server_restart" in types or "infra_retry" in types, f"no restart or retry recorded: {types}"
    retries = [json.loads(line).get("infra_retries", 0) for line in complete_lines(run_dir)]
    assert all(isinstance(r, int) and 0 <= r <= 2 for r in retries), f"infra_retries {retries}"
    assert sum(retries) <= types.count("infra_retry"), \
        f"items count {sum(retries)} retries, but only {types.count('infra_retry')} infra_retry events"
