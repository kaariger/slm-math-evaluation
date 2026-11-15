"""Visible specification tests — runner pause and crash recovery when the background runner may detach, acting only on processes proven owned through the test's launch handles or through write handles on the plan's own fresh files (contract v0.8.1 §6.1, §6.3, §10, §13.3, §13.4).

Contract-only: written from the approved interface and the maintainer's
readings, with no implementation code seen. Tests use only the `slm-eval`
CLI, the processes it starts and the contract's files. Failures name files,
line numbers, pairs, counts and process IDs; no record content and no CLI
output is printed (the CLI's stdout and stderr stay in named files).

VS-FX-18 and VS-FX-19 succeed VS-FX-14 and VS-FX-15. Those find processes only
through the launching process group and lineage, so a conforming runner that
detaches before it is observed leaves them NOT RUN. Here a detached runner and
its model server can also be proven owned, through read-only handle inspection
scoped to the plan's own fresh, contract-declared files.

Every test needs a REAL FIXTURE MODEL, THE PINNED RUNTIME AND THE HARNESS
REVISION, and is skipped as NOT RUN unless all of these are present:

  SLM_EVAL_TEST_RUNTIME=1        the pinned llama.cpp runtime is available to
                                 the implementation
  SLM_EVAL_TEST_FIXTURE_PROTOCOL a `role: fixture` protocol (§4) for a small
                                 model, with a neutral `fixture-<n>` alias when
                                 it names one, committed inside the repository
                                 (§13.1 paths are repository-relative)
  SLM_EVAL_TEST_SEED_CACHE       a dataset cache holding the pinned dataset and
                                 models/<model.artifact.file>; copied into each
                                 test's temporary SLM_EVAL_CACHE (files over
                                 256 MiB are symlinked)
  split manifest                 SLM_EVAL_TEST_MANIFEST, or the committed
                                 manifest with the pinned SHA-256
  harness revision               SLM_EVAL_TEST_HARNESS_REVISION, or `git
                                 rev-parse HEAD` of a clean checkout (P7)
  tools                          `ps`, `pgrep` and `lsof`

Only the fixture's smoke tier runs, as the verify step and run step of a plan
the test authorizes for itself. A fixture is never evidence of target
compatibility.

Process ownership. Each test uses fresh temporary locations and first waits
for the next whole second, its start second. A process is proven owned, and
recorded with its identity (start time and user ID, from `ps -o
stat=,uid=,ppid=,pgid=,lstart= -p <pid>`), when it is:
  1. a CLI process the test launched, in a new session and process group. Its
     handle is not reaped until teardown, so its PID and group ID cannot be
     reused while the test runs; exit statuses are read without reaping;
  2. a member of such a launch group (`pgrep -g`);
  3. a holder of a WRITE handle on a contract-declared file of this test's own
     plan: its runner state `_runner/<plan_sha256>/` (§13.3) or the run
     directory its status.json names (§6.1), both inside the test's fresh run
     store (`lsof -- <those files>`, read-only);
  4. a child of a proven process (`pgrep -P`), checked before and after the
     listing;
  5. the parent of a proven process (its PPID, never 1), re-read after the
     parent's identity is taken, and started no later than that child;
  6. a member of a proven process's own process group (`pgrep -g`), checked
     before and after the listing.
Each must also be alive, not a zombie, of the test's effective user ID and
started no earlier than the test's start second; the pytest process and its
parent never qualify. A live process met by rules 3 to 6 that fails a guard is
recorded as adjoining but unproven. A process name, a port, a PID or a parent
link alone never proves ownership. No process table is scanned for environments
or command lines: `lsof` is asked only about the plan's own files and, for
listening sockets, only about proven PIDs (`lsof -p`). Every identity is
re-read immediately before a signal; a changed or missing identity means the PID
no longer names that process, and nothing is sent.

Runner proof (VS-FX-19). Before the crash, besides a proven listening model
server, the runner must be proven: a proven process that does not listen and
either descends from a launch (rules 1, 2 and 4 from a launch handle), is an
ancestor of the proven server (the runner starts and stops it, §13.4), or holds
a write handle on the plan's status.json or attempts.log.jsonl. The ancestry of
every proven server and runner must end at PID 1 or at the test itself, and no
adjoining process may be unproven. Otherwise the test is NOT RUN, naming the
missing proof, and nothing is signalled. After the crash, a process proven only
then is ordered against the crash by its start second: one that started before
it shows that the crash did not end the whole plan (NOT RUN); one that started
after it is work restarted on its own (a failure).

NOT RUN. Missing capabilities, a missing proof, and a fixture plan that ends
before its fault are skipped as NOT RUN, naming the need. A NOT RUN skip is
never a pass and never credits fault coverage.

Teardown is not an assertion. It sends the contract's own `runner pause --now`
to the test's plan if it is still `running`, then ends every proven process
(SIGTERM, then SIGKILL). What it found, sent and left is written to
logs/teardown.txt in the test's temporary directory and recorded as the pytest
user property `vs_teardown`; a proven process left alive, or a teardown error,
also raises a warning. Processes outside these proofs (for example a detached
process that holds none of the plan's files) cannot be observed, so their
absence is never claimed.

Timing: model start-up is awaited for up to STARTUP seconds and a run for up
to RUN_WAIT seconds; ownership proof is sought for up to PROOF_WAIT seconds;
VS-FX-19 watches for QUIET_WATCH seconds after the crash; teardown waits up to
TEARDOWN_WAIT seconds for its paused plan to leave `running`. No speed is
required.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable, and no variable whose name contains TOKEN, SECRET,
PASSWORD or API_KEY, is passed to the implementation. The process tools run
with only PATH, TZ and the C locale.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
import warnings
from pathlib import Path
from typing import NamedTuple

import pytest

EXIT_OK = 0
STARTUP = 1800.0
RUN_WAIT = 3600.0
CONTROL_TIMEOUT = 300.0
QUIET_WATCH = 10.0
PROOF_WAIT = 120.0
TEARDOWN_WAIT = 30.0
POLL, FAST_POLL, PROOF_POLL = 0.5, 0.05, 1.0
LARGE_FILE = 256 * 1024 * 1024
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
PINNED_MANIFEST_SHA256 = "a10c02e7ac60b563a4d112cad92c2540ce58d6c0a49fa73f844091df66e48c71"
FIXTURE_ALIAS = re.compile(r"fixture-\d+")
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
THINKING_STATES = {"absent", "empty", "present", "unterminated"}
SETTLED = {"completed", "stopped", "interrupted", "paused", "waiting_approval"}

# Contract-declared files of one plan: its runner state (§13.3) and its run directory (§6.1, §6.4).
STATE_FILES = ("status.json", "attempts.log.jsonl", "server.log", "plan.yaml", "authorization.json")
RUN_FILES = ("items.jsonl", "run.json", "protocol.yaml", "manifest.json", "runtime-verification.json",
             "artifact-verification.json", "rescore.json")
RUNNER_STATE_FILES = frozenset({"status.json", "attempts.log.jsonl"})

SERVER_GAP = ("no model server is provably owned: no process listening on TCP was in the test's launch groups or "
              "lineage, or held a write handle on a contract-declared file of this plan's fresh runner state or run "
              "directory (or descended from or adjoined such a process), with the test's user ID and a start after "
              "the test began")
RUNNER_GAP = ("the runner is not provably owned: no proven process that does not listen descends from the test's "
              "launches, is an ancestor of the proven model server, or holds a write handle on the plan's "
              "status.json or attempts.log.jsonl. Killing only the server would be a server crash (VS-FX-16), not a "
              "runner crash")


def not_run(reason: str):
    pytest.skip(f"NOT RUN: {reason}")


def fail(message: str):
    """Fail without a traceback or assertion introspection, which could show record contents."""
    raise pytest.fail.Exception(message, pytrace=False) from None


# ---------------------------------------------------------------------------
# process identities
# ---------------------------------------------------------------------------

_TOOL_ENV = {"PATH": os.environ.get("PATH") or "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C", "LANG": "C"}
if os.environ.get("TZ"):
    _TOOL_ENV["TZ"] = os.environ["TZ"]


def _query(argv: list[str]) -> str:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                          errors="replace", env=_TOOL_ENV).stdout


class ProcInfo(NamedTuple):
    stat: str
    uid: int
    ppid: int
    pgid: int
    start: str


def probe(pid: int) -> ProcInfo | None:
    """`ps` fields of a live process with this PID; None if it is gone or a zombie."""
    if pid <= 0:
        return None
    parts = _query(["ps", "-o", "stat=,uid=,ppid=,pgid=,lstart=", "-p", str(pid)]).strip().split(None, 4)
    if len(parts) != 5 or parts[0].startswith("Z"):
        return None
    try:
        return ProcInfo(parts[0], int(parts[1]), int(parts[2]), int(parts[3]), " ".join(parts[4].split()))
    except ValueError:
        return None


def start_second(start: str) -> int | None:
    """The epoch second of a `ps` lstart value (local time, 1 s resolution)."""
    try:
        return int(time.mktime(time.strptime(start, "%a %b %d %H:%M:%S %Y")))
    except (ValueError, OverflowError):
        return None


def _pgrep(flag: str, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    return {int(t) for t in _query(["pgrep", flag, ",".join(map(str, ids))]).split() if t.isdigit()}


class Family:
    """The processes this test proves it owns, each pinned by its identity (start time, user ID)."""

    def __init__(self, floor: int):
        self.floor = floor
        self.leaders: list[subprocess.Popen] = []
        self.known: dict[int, tuple[str, int]] = {}
        self.why: dict[int, str] = {}
        self.classic: set[int] = set()
        self.writes: dict[int, set[str]] = {}
        self.foreign: dict[int, tuple[tuple[str, int], str]] = {}
        self.anchors: list[tuple[Path, tuple[str, ...]]] = []
        self.signals: list[str] = []
        self.excluded = {os.getpid(), os.getppid()}

    # -- identities --------------------------------------------------------------------------------------

    def leader_pids(self) -> set[int]:
        return {p.pid for p in self.leaders}

    def _groups(self) -> list[int]:
        """Group IDs of unreaped leaders: reserved, so membership in them proves ownership."""
        return [p.pid for p in self.leaders if p.returncode is None]

    def current(self, pid: int) -> bool:
        info = probe(pid) if pid in self.known else None
        return info is not None and (info.start, info.uid) == self.known[pid]

    def live(self) -> set[int]:
        return {pid for pid in list(self.known) if self.current(pid)}

    def started(self, pid: int) -> int | None:
        return start_second(self.known[pid][0]) if pid in self.known else None

    def describe(self, pid: int) -> str:
        start = self.known[pid][0] if pid in self.known else "?"
        return f"process {pid} ({self.why.get(pid, 'unknown')}; started {start})"

    def live_foreign(self) -> dict[int, str]:
        found = {}
        for pid, (identity, note) in list(self.foreign.items()):
            info = probe(pid)
            if info is not None and (info.start, info.uid) == identity and not self.current(pid):
                found[pid] = note
        return found

    def _admit(self, pid: int, reason: str, *, classic: bool = False, not_after: int | None = None) -> bool:
        """Record pid as proven when it passes the guards. A live process that fails them is noted as foreign."""
        if pid <= 1 or pid in self.excluded:
            return False
        info = probe(pid)
        if info is None:
            return False
        identity = (info.start, info.uid)
        if self.known.get(pid) == identity:
            if classic:
                self.classic.add(pid)
            return True
        began = start_second(info.start)
        problem = None
        if info.uid != os.geteuid():
            problem = "another user ID"
        elif began is None or began < self.floor:
            problem = "started before the test began"
        elif not_after is not None and began > not_after:
            return False            # a stale parent link: that PID now names a younger process
        if problem is not None:
            self.foreign[pid] = (identity, f"process {pid}: {reason}, but {problem}")
            return False
        self.known[pid] = identity   # also replaces a dead identity whose PID was reused
        self.why[pid] = reason
        self.writes.pop(pid, None)
        self.classic.discard(pid)
        if classic:
            self.classic.add(pid)
        self.foreign.pop(pid, None)
        return True

    def launch(self, argv: list[str], *, cwd: Path, env: dict, out, err) -> subprocess.Popen:
        popen = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                 start_new_session=True)
        self.leaders.append(popen)
        self._admit(popen.pid, "launch handle", classic=True)
        return popen

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

    # -- discovery ---------------------------------------------------------------------------------------

    def plan_files(self) -> list[Path]:
        files = []
        for directory, names in self.anchors:
            for name in names:
                path = directory / name
                if path.is_file() and not path.is_symlink():
                    files.append(path.resolve())
        return files

    def write_holders(self) -> list[tuple[int, str]]:
        """(PID, file name) for every write handle on the plan's own files: `lsof -- <files>` only."""
        files = self.plan_files()
        if not files:
            return []
        wanted = {str(p): p.name for p in files}
        out = _query(["lsof", "-nP", "-w", "-F", "pfatn", "--", *wanted])
        found, pid, access, kind = [], None, "", ""
        for line in out.splitlines():
            tag, value = line[:1], line[1:]
            if tag == "p":
                pid, access, kind = (int(value) if value.isdigit() else None), "", ""
            elif tag == "f":
                access, kind = "", ""
            elif tag == "a":
                access = value.strip()
            elif tag == "t":
                kind = value
            elif tag == "n" and pid is not None and kind == "REG" and access in ("w", "u") and value in wanted:
                found.append((pid, wanted[value]))
        return found

    def refresh(self, handles: bool = False) -> None:
        groups = self._groups()
        for pid in _pgrep("-g", groups):
            self._admit(pid, "member of a launch group", classic=True)
        if handles:
            for pid, name in self.write_holders():
                if self._admit(pid, f"write handle on {name}"):
                    self.writes.setdefault(pid, set()).add(name)
        for _ in range(16):
            before = dict(self.known)
            self._close(groups)
            if self.known == before:
                break

    def _close(self, groups: list[int]) -> None:
        own_group = os.getpgid(0)
        for parent in sorted(set(groups) | self.live()):
            alive = (lambda p=parent: p in groups or self.current(p))
            if not alive():
                continue
            children = _pgrep("-P", [parent])
            if not alive():
                continue
            classic = parent in self.classic or parent in groups
            for child in children:
                self._admit(child, f"child of proven {parent}", classic=classic)
        for pid in sorted(self.live()):
            info = probe(pid)
            if info is None:
                continue
            if info.ppid > 1 and info.ppid not in self.excluded and not self.current(info.ppid):
                parent = probe(info.ppid)
                again = probe(pid)
                if parent is not None and again is not None and again.ppid == info.ppid \
                        and (again.start, again.uid) == self.known.get(pid):
                    self._admit(info.ppid, f"parent of proven {pid}", not_after=start_second(info.start))
            if info.pgid > 1 and info.pgid not in groups and info.pgid != own_group:
                members = _pgrep("-g", [info.pgid])
                again = probe(pid)
                if again is not None and again.pgid == info.pgid and (again.start, again.uid) == self.known.get(pid):
                    for member in members:
                        self._admit(member, f"member of the process group of proven {pid}")

    def ancestors(self, pid: int) -> list[int]:
        """The proven ancestors of pid, nearest first, following PPIDs while each parent is proven."""
        chain, seen, info = [], {pid}, probe(pid)
        while info is not None and info.ppid > 1 and info.ppid not in seen and self.current(info.ppid):
            chain.append(info.ppid)
            seen.add(info.ppid)
            info = probe(info.ppid)
        return chain

    def ordered(self) -> list[int]:
        """Live proven processes, roots first, so that a runner dies before it can react to its server's death."""
        return sorted(self.live(), key=lambda pid: (len(self.ancestors(pid)), pid))

    def listening(self, exclude=()) -> set[int]:
        pids = sorted(self.live() - set(exclude))
        if not pids:
            return set()
        out = _query(["lsof", "-nP", "-w", "-a", "-p", ",".join(map(str, pids)), "-iTCP", "-sTCP:LISTEN", "-t"])
        found = {int(t) for t in out.split() if t.isdigit()} & set(pids)
        return {pid for pid in found if self.current(pid)}

    # -- signals -----------------------------------------------------------------------------------------

    def send(self, pid: int, sig: int) -> bool:
        if pid <= 1 or pid in self.excluded:
            return False
        if pid not in self._groups() and not self.current(pid):
            return False
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            return False
        self.signals.append(f"{signal.Signals(sig).name} -> {pid} ({self.why.get(pid, 'launch handle')})")
        return True

    def kill_proven(self, sig: int, order: list[int] | None = None) -> None:
        for group in self._groups():
            try:
                os.killpg(group, sig)
                self.signals.append(f"{signal.Signals(sig).name} -> launch group {group}")
            except (ProcessLookupError, PermissionError):
                pass
        for pid in (self.ordered() if order is None else order):
            self.send(pid, sig)

    def wait_gone(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.refresh()
            if not self.live() and all(self.exit_code(p) is not None for p in self.leaders):
                return True
            time.sleep(0.2)
        return False

    def cleanup(self) -> list[int]:
        """End every proven process, then reap the leaders; returns the proven processes still alive."""
        for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 10.0)):
            try:
                self.refresh(handles=True)
            except Exception:
                pass
            self.kill_proven(sig)
            if self.wait_gone(grace):
                break
        for popen in self.leaders:
            if popen.returncode is None:
                try:
                    popen.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        return sorted(self.live())


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
    """Temporary locations for one test, a seeded cache, its plan, and the processes it proves it owns."""

    def __init__(self, root: Path):
        self.root = root
        self.cache, self.runs, self.scratch, self.work = (root / n for n in ("cache", "runs", "scratch", "work"))
        self.inputs, self.logs = root / "inputs", root / "logs"
        for directory in (self.cache, self.runs, self.scratch, self.work, self.inputs, self.logs):
            directory.mkdir(parents=True)
        self.vars = {k: v for k, v in os.environ.items() if _passed_on(k)}
        self.vars.update(SLM_EVAL_CACHE=str(self.cache), SLM_EVAL_RUNS=str(self.runs),
                         SLM_EVAL_SCRATCH=str(self.scratch))
        began = time.time()
        time.sleep(math.ceil(began) - began + 0.05)
        self.family = Family(floor=math.ceil(began))
        self.runner_state: Path | None = None

    def start(self, *args: object) -> tuple[subprocess.Popen, Path, Path]:
        name = uuid.uuid4().hex[:8]
        out_path, err_path = self.logs / f"{name}.out", self.logs / f"{name}.err"
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            popen = self.family.launch(_command() + [str(a) for a in args], cwd=self.work, env=self.vars, out=out,
                                       err=err)
        return popen, out_path, err_path

    def wait_exit(self, popen: subprocess.Popen, timeout: float, poll: float = POLL) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.family.refresh()
            code = self.family.exit_code(popen)
            if code is not None:
                self.family.refresh()
                return code
            time.sleep(poll)
        try:
            os.killpg(popen.pid, signal.SIGKILL)   # the unreaped leader's group cannot have been reused
        except (ProcessLookupError, PermissionError):
            pass
        fail(f"{popen.args!r} did not exit within {timeout:.0f} s")

    def run(self, *args: object, timeout: float = RUN_WAIT, poll: float = POLL) -> tuple[int, Path, Path]:
        """Run a command to its exit; returns (exit code, stdout file, stderr file), never the output itself."""
        popen, out_path, err_path = self.start(*args)
        return self.wait_exit(popen, timeout, poll), out_path, err_path

    def cleanup(self) -> list[str]:
        """Teardown, kept apart from the test's result; returns what it found, sent and left."""
        notes, by_test = [], len(self.family.signals)
        notes.append("signals sent by the test: " + ("; ".join(self.family.signals) or "none"))
        try:
            state = runner_status(self.runner_state).get("state") if self.runner_state else None
            notes.append(f"plan state at teardown: {state}")
            if state == "running":
                # The contract's own stop for this test's plan, which may be run by an unproven process (§13.4).
                popen, _, _ = self.start("runner", "pause", "--now")
                deadline = time.monotonic() + CONTROL_TIMEOUT
                while time.monotonic() < deadline and self.family.exit_code(popen) is None:
                    time.sleep(POLL)
                notes.append(f"runner pause --now exited {self.family.exit_code(popen)}")
                deadline = min(deadline, time.monotonic() + TEARDOWN_WAIT)   # a crashed plan may never leave `running`
                while time.monotonic() < deadline and runner_status(self.runner_state).get("state") == "running":
                    time.sleep(POLL)
                notes.append(f"plan state after pause: {runner_status(self.runner_state).get('state')}")
            self.family.refresh(handles=True)
            notes.append("proven live before cleanup: "
                         + ("; ".join(self.family.describe(p) for p in sorted(self.family.live())) or "none"))
        except (Exception, pytest.skip.Exception, pytest.fail.Exception) as exc:
            notes.append(f"teardown step raised {type(exc).__name__}")
        left = self.family.cleanup()
        notes.append("signals sent by teardown: " + ("; ".join(self.family.signals[by_test:]) or "none"))
        if left:
            notes.append("LEFTOVER proven processes still alive: " + "; ".join(self.family.describe(p) for p in left))
        unproven = self.family.live_foreign()
        if unproven:
            notes.append("adjoining unproven processes, never signalled: " + "; ".join(unproven.values()))
        notes.append("limit: processes outside these proofs cannot be observed; their absence is not claimed")
        return notes


@pytest.fixture
def env(tmp_path: Path, request):
    require_live()
    environment = Env(tmp_path)
    try:
        seed_cache(environment)
        yield environment
    finally:
        notes = environment.cleanup()
        (environment.logs / "teardown.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
        request.node.user_properties.append(("vs_teardown", " | ".join(notes)))
        if any(n.startswith("LEFTOVER") or "raised" in n for n in notes):
            warnings.warn(f"VS teardown left proven processes or failed; see {environment.logs / 'teardown.txt'}",
                          stacklevel=1)


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
    missing = [tool for tool in ("pgrep", "ps", "lsof") if shutil.which(tool, path=_TOOL_ENV["PATH"]) is None]
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
        fail("SLM_EVAL_TEST_FIXTURE_PROTOCOL must name a protocol with role: fixture (§4)")
    alias = dig(data, "runtime", "model_alias")
    if alias is not None and not FIXTURE_ALIAS.fullmatch(str(alias)):
        fail(f"the fixture protocol's alias {alias!r} is not a fixture-<n> alias (§10)")
    if not HEX64.fullmatch(str(dig(data, "model", "chat_template", "sha256"))):
        fail("the fixture protocol has no 64-hex chat_template.sha256 (§4)")
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


# ---------------------------------------------------------------------------
# runs and their records
# ---------------------------------------------------------------------------


def complete_lines(run_dir: Path) -> list[bytes]:
    path = run_dir / "items.jsonl"
    if not path.is_file():
        return []
    data = path.read_bytes()
    return data[:data.rfind(b"\n") + 1].splitlines()


def wait_until(env: Env, predicate, timeout: float, what: str, poll: float = POLL):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        env.family.refresh()
        value = predicate()
        if value:
            return value
        time.sleep(poll)
    fail(f"timed out after {timeout:.0f} s waiting for {what}")


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
        if not (copy_path.is_file() and sha256_file(copy_path) == doc["subset_sha256"]):
            fail("a subset run has no stored subset.json with run.json subset_sha256")
        ids = json.loads(copy_path.read_text(encoding="utf-8"))["ids"]
    return {(i, s) for i in ids or [] for s in range(k)}


def record_problems(lines: list[bytes], first: int = 1) -> tuple[list[str], list[tuple]]:
    """§6.1 for complete lines: a JSON object with valid raw-output and reference hashes, a 64-hex
    prompt_sha256 and a §8 thinking_state. Returns (problems, pairs in order); problems name line numbers."""
    problems, pairs = [], []
    for n, line in enumerate(lines, start=first):
        try:
            record = json.loads(line)
        except ValueError:
            problems.append(f"items.jsonl line {n} is not JSON")
            continue
        if not isinstance(record, dict):
            problems.append(f"items.jsonl line {n} is not a JSON object")
            continue
        pairs.append((record.get("item_id"), record.get("sample_index")))
        if sha256_bytes(str(record.get("raw_output")).encode("utf-8")) != record.get("raw_output_sha256"):
            problems.append(f"line {n}: raw_output_sha256 mismatch")
        if sha256_bytes(str(record.get("reference_answer")).encode("utf-8")) != record.get("reference_sha256"):
            problems.append(f"line {n}: reference_sha256 mismatch")
        if not HEX64.fullmatch(str(record.get("prompt_sha256"))):
            problems.append(f"line {n}: prompt_sha256 is not 64-hex")
        if record.get("thinking_state") not in THINKING_STATES:
            problems.append(f"line {n}: thinking_state is not a §8 state")
    return problems, pairs


def durable_prefix(run_dir: Path, data: bytes, event: str) -> tuple[bytes, int, list[tuple]]:
    """Split items.jsonl at its last line terminator into the durable prefix and a tail, which may be empty or a
    physically incomplete line (P5). Every prefix line must be a completed record of a distinct planned pair."""
    cut = data.rfind(b"\n") + 1
    prefix, tail = data[:cut], data[cut:]
    problems, durable = record_problems(prefix.splitlines())
    if len(set(durable)) != len(durable):
        problems.append("the durable prefix repeats an item × sample pair")
    outside = set(durable) - planned_pairs(run_dir)
    if outside:
        problems.append(f"{len(outside)} pairs in the durable prefix are outside the planned tier × k")
    if problems:
        fail(f"the durable record prefix left by {event} is not a set of completed records:\n" + "\n".join(problems))
    return prefix, len(tail), durable


def after_resume(run_dir: Path, prefix: bytes, durable: list[tuple], event: str) -> list[tuple]:
    """After the resumed plan completes: the exact prefix is kept, every later line is a whole, hash-valid record,
    no pair of the prefix is generated again, and every planned pair appears exactly once."""
    items = run_dir / "items.jsonl"
    after = items.read_bytes() if items.is_file() else b""
    if not after.startswith(prefix):
        fail(f"the durable prefix of {len(durable)} completed records from before {event} was not kept byte for "
             "byte by resume")
    problems = []
    if after and not after.endswith(b"\n"):
        problems.append("items.jsonl ends in a partial line after resume")
    later_problems, later = record_problems(after[len(prefix):].splitlines(), first=len(durable) + 1)
    problems += later_problems
    again = set(durable) & set(later)
    if again:
        problems.append(f"{len(again)} pairs completed before {event} were generated again")
    pairs = durable + later
    if len(pairs) != len(set(pairs)):
        problems.append("an item × sample pair was generated twice")
    planned = planned_pairs(run_dir)
    if set(pairs) != planned:
        problems.append(f"{len(set(pairs) & planned)} of {len(planned)} planned pairs recorded, "
                        f"{len(set(pairs) - planned)} unplanned")
    if problems:
        fail("after resume:\n" + "\n".join(problems))
    return later


def snapshot(*roots: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for root in roots for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------
# the plan, its runner and ownership proof (§13.1 to §13.4)
# ---------------------------------------------------------------------------


def runner_status(state: Path | None) -> dict:
    if state is None:
        return {}
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
    env.family.anchors.append((env.runner_state, STATE_FILES))
    code, out_path, err_path = env.run("runner", "start", "--plan", plan_path, "--authorization", auth_path,
                                       timeout=CONTROL_TIMEOUT, poll=FAST_POLL)
    if code != EXIT_OK:
        fail(f"runner start exited {code} (stdout {out_path}, stderr {err_path})")
    return plan, env.runner_state


def runner_run_dir(env: Env, state: Path) -> Path:
    """The run directory named by status.json once it holds a completed item; registered as a plan file root."""
    def ready():
        status = runner_status(state)
        if status.get("state") == "completed":
            not_run("the fixture plan completed before its first item was observed")
        run_id = status.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return None
        run_dir = env.runs / run_id
        if Path(run_id).name != run_id or run_id in (".", "..") or run_dir.resolve().parent != env.runs.resolve():
            fail("status.json run_id does not name a run directory in the test's own run store")
        return run_dir if complete_lines(run_dir) else None
    run_dir = wait_until(env, ready, STARTUP, "the runner's first generated item", poll=FAST_POLL)
    env.family.anchors.append((run_dir, RUN_FILES))
    return run_dir


def runner_proof(family: Family, servers: set[int]) -> tuple[set[int], list[str]]:
    """The proven runner processes and the proofs still missing (module docstring, "Runner proof")."""
    missing = [] if servers else [SERVER_GAP]
    live = family.live()
    spawners = {p for s in servers for p in family.ancestors(s)}
    writers = {p for p in live if family.writes.get(p, set()) & RUNNER_STATE_FILES}
    launched = (live & family.classic) - family.leader_pids()
    runners = (spawners | writers | launched) - servers
    if servers and not runners:
        missing.append(RUNNER_GAP)
    for pid in sorted(servers | runners):
        chain = family.ancestors(pid)
        top = chain[-1] if chain else pid
        info = probe(top)
        if info is None:
            missing.append(f"process {top} ended while its ancestry was checked")
        elif info.ppid > 1 and info.ppid not in family.excluded:
            missing.append(f"the ancestry of proven process {pid} continues to process {info.ppid}, which is not "
                           "provably owned")
    foreign = family.live_foreign()
    if foreign:
        missing.append("processes adjoining the proven ones are not provably owned (" + "; ".join(foreign.values())
                       + "), so a crash of the proven ones would not end the whole plan")
    return runners, missing


def prove(env: Env, state: Path, *, runner: bool) -> tuple[set[int], set[int]]:
    """Seek, for up to PROOF_WAIT s, a proven listening model server and, with `runner`, a proven runner.
    Returns (servers, runners); NOT RUN, naming the missing proof, otherwise. Nothing is signalled."""
    deadline, seen = time.monotonic() + PROOF_WAIT, []
    while True:
        plan_state = runner_status(state).get("state")
        if plan_state == "completed":
            not_run("the fixture plan completed before its processes were proven owned (missing proofs seen: "
                    + ("; ".join(seen) or "none recorded") + "); no process was signalled")
        env.family.refresh(handles=True)
        servers = env.family.listening()
        runners, missing = runner_proof(env.family, servers) if runner else (set(), [] if servers else [SERVER_GAP])
        if not missing:
            return servers, runners
        seen += [m for m in missing if m not in seen]
        if time.monotonic() >= deadline:
            not_run(f"after {PROOF_WAIT:.0f} s (plan state {plan_state!r}), missing proofs seen: " + "; ".join(seen)
                    + "; no process was signalled")
        time.sleep(PROOF_POLL)


def resume_to_completion(env: Env, state: Path) -> None:
    """`runner resume`, then wait for the plan to settle (§13.3 states); it must settle as completed."""
    before = runner_status(state)
    code, out_path, err_path = env.run("runner", "resume", timeout=CONTROL_TIMEOUT, poll=FAST_POLL)
    if code != EXIT_OK:
        fail(f"runner resume exited {code} (stdout {out_path}, stderr {err_path})")
    wait_until(env, lambda: runner_status(state) != before, STARTUP, "status.json to change after resume")
    status = wait_until(env, lambda: s if (s := runner_status(state)).get("state") in SETTLED else None, RUN_WAIT,
                        "the plan to settle")
    if status.get("state") != "completed":
        fail(f"the resumed plan settled as {status.get('state')!r}, not completed")


def items_bytes(run_dir: Path) -> bytes:
    path = run_dir / "items.jsonl"
    return path.read_bytes() if path.is_file() else b""


# ---------------------------------------------------------------------------
# tests: runner pause (§13.4) and runner crash recovery (§13.4, §6.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("now", [False, True], ids=["pause", "pause-now"])
def test_vs_fx_18_runner_pause_stops_the_proven_server_and_resume_completes(env, now):
    """VS-FX-18 · live + harness · §13.4 pause takes effect after the item in progress (`--now`: at once, the
    item discarded and regenerated on resume); "the runner writes a checkpoint, stops the model server … and
    sets state paused"; resume "continues from the last checkpoint"; §6.3 "Completed pairs are never
    regenerated … The item in progress is regenerated from scratch"; §6.1 no partial generation in items.jsonl;
    P5; R4 and B10-6 owned processes only.

    Proposed successor of VS-FX-14, whose functional checks are kept; only the ownership observation changes,
    and the checkpoint checks of VS-FX-17 are added. During the smoke step, with the model server provably
    owned (by launch, or by a write handle on the plan's own files, even when the runner detached):
    `runner pause` (or `--now`) exits 0; the state becomes `paused`; the observed server process is gone and no
    proven process listens. At that checkpoint items.jsonl splits into a durable prefix (to its last line
    terminator) of completed, hash-valid records of distinct planned pairs, and a tail. `runner resume` exits 0
    and the plan completes; items.jsonl begins with the exact prefix bytes; every later line is a whole,
    hash-valid record; no prefix pair appears again, so at most the uncommitted item was redone; every planned
    pair appears exactly once; `run.json` records the pause and the resume.
    No record content is printed.
    """
    _, state = runner_plan(env)
    run_dir = runner_run_dir(env, state)
    servers, _ = prove(env, state, runner=False)
    proofs = "; ".join(env.family.describe(p) for p in sorted(servers))
    code, out_path, err_path = env.run("runner", "pause", *(["--now"] if now else []), timeout=CONTROL_TIMEOUT,
                                       poll=FAST_POLL)
    if code != EXIT_OK:
        if runner_status(state).get("state") == "completed":
            not_run("the fixture plan completed before the pause was requested")
        fail(f"runner pause exited {code} (stdout {out_path}, stderr {err_path})")
    status = wait_until(env, lambda: s if (s := runner_status(state)).get("state") in ("paused", "completed") else None,
                        STARTUP, "state paused")
    if status.get("state") == "completed":
        not_run("the fixture plan completed before the pause took effect")
    time.sleep(3)
    env.family.refresh(handles=True)
    still = servers & env.family.live()
    if still:
        fail("the model server is still running while paused: "
             + "; ".join(env.family.describe(p) for p in sorted(still)))
    listening = env.family.listening()
    if listening:
        fail("a proven process is listening while paused: "
             + "; ".join(env.family.describe(p) for p in sorted(listening)))
    prefix, tail, durable = durable_prefix(run_dir, items_bytes(run_dir), "the pause")

    resume_to_completion(env, state)
    later = after_resume(run_dir, prefix, durable, "the pause")
    doc = run_json(run_dir)
    types = [e.get("type") for e in doc.get("events") or [] if isinstance(e, dict)]
    statuses = [a.get("status") for a in doc.get("attempts") or [] if isinstance(a, dict)]
    if not (("pause" in types and "resume" in types) or "paused" in statuses):
        fail(f"run.json records no pause and resume: events {types}, attempts {statuses}")
    print(f"VS-FX-18: server proven as {proofs}; durable prefix {len(durable)} records and tail {tail} bytes at "
          f"the pause; {len(later)} records added by resume")


def test_vs_fx_19_a_crashed_runner_recovers_only_on_resume(env):
    """VS-FX-19 · live + harness · §13.4 "The same command recovers after a crash, a reboot or a power loss
    (state interrupted). Nothing restarts automatically"; §6.3 "Only missing item × sample pairs are generated.
    Completed pairs are never regenerated … Each resume appends an attempt record"; §6.1 no partial generation in
    items.jsonl; P5 resume "discards only an incomplete tail and regenerates at most the uncommitted item";
    B10-4; R4 and B10-6 only a proven owned runner is killed.

    Proposed successor of VS-FX-15, whose assertions are kept; only the ownership observation changes, and the
    checkpoint checks of VS-FX-17 are added. During the smoke step, with the runner and its model server
    provably owned (module docstring, "Runner proof") and pairs remaining, every proven process is killed
    (SIGKILL), roots first. For QUIET_WATCH seconds no proven process exists and no file in the runner state or
    the run directory changes; a process proven only after the crash that started before it makes the test
    NOT RUN, one that started after it fails it. The post-crash items.jsonl splits into a durable prefix of
    completed, hash-valid records of distinct planned pairs and a tail (empty or physically incomplete). `runner
    resume` exits 0 and the plan completes; items.jsonl begins with the exact prefix bytes; every later line is
    a whole, hash-valid record; no prefix pair appears again; every planned pair appears exactly once; the run
    has at least two attempts.
    No record content is printed.
    """
    _, state = runner_plan(env)
    run_dir = runner_run_dir(env, state)
    servers, runners = prove(env, state, runner=True)
    if len(complete_lines(run_dir)) >= len(planned_pairs(run_dir)):
        not_run("every planned pair was written before the crash could be injected")
    order = env.family.ordered()
    proofs = "; ".join(env.family.describe(p) for p in order)
    now = time.time()
    time.sleep(math.ceil(now) - now + 0.01)      # a fresh second, so later processes can be ordered against it
    fault = math.floor(time.time())
    env.family.kill_proven(signal.SIGKILL, order)
    if not env.family.wait_gone(30):
        fail("proven processes survived SIGKILL: "
             + "; ".join(env.family.describe(p) for p in sorted(env.family.live())))
    if runner_status(state).get("state") == "completed":
        not_run("the fixture plan completed before the crash took effect")
    crashed = items_bytes(run_dir)

    before = snapshot(state, run_dir)
    revealed: dict[int, str] = {}
    deadline = time.monotonic() + QUIET_WATCH
    while True:
        env.family.refresh(handles=True)
        for pid in env.family.live():
            revealed.setdefault(pid, env.family.describe(pid))
        if time.monotonic() >= deadline:
            break
        time.sleep(PROOF_POLL)
    after = snapshot(state, run_dir)
    changed = sorted(str(Path(k).relative_to(env.runs)) for k in set(before) | set(after)
                     if before.get(k) != after.get(k))
    survivors = {pid: text for pid, text in revealed.items() if (env.family.started(pid) or 0) < fault}
    if survivors:
        not_run("the crash did not end the whole plan: " + "; ".join(survivors.values()) + " started before the "
                "fault but was proven owned only after it, so the proof before the fault was incomplete and later "
                f"changes cannot be attributed ({len(changed)} files changed; processes started after the fault: "
                f"{sorted(set(revealed) - set(survivors))}); every revealed process is ended at teardown")
    if revealed:
        fail("work restarted on its own: an owned process exists after the crash, started after it: "
             + "; ".join(revealed.values()))
    if changed:
        fail("work restarted on its own: runner state or run files changed after the crash: " + ", ".join(changed))
    prefix, tail, durable = durable_prefix(run_dir, crashed, "the crash")

    resume_to_completion(env, state)
    later = after_resume(run_dir, prefix, durable, "the crash")
    attempts = run_json(run_dir).get("attempts") or []
    if len(attempts) < 2:
        fail(f"the resumed run has {len(attempts)} attempt(s); a resume appends an attempt record (§6.3)")
    print(f"VS-FX-19: killed {proofs}; runner {sorted(runners)}, server {sorted(servers)}; durable prefix "
          f"{len(durable)} records, incomplete tail {tail} bytes; {len(later)} records added by resume")
