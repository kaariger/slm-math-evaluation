"""Visible specification tests — legacy runtime security, verification records, runs, resume and tier regressions for the canonical legacy Qwen protocol, acting only on processes proven owned through the test's own launch handles (contract v0.8.1 §2, §3, §5.1, §6, §7, §8, §10).

Contract-only: written from the approved interface, the maintainer's readings
and the registered legacy test sources (read as text; never imported or run),
with no implementation code seen. Tests use only the `slm-eval` CLI, the
processes it starts, the server it starts and the contract's files.

VS-LR-01 to VS-LR-26 succeed the 26 legacy cases VS-RT-02 to VS-RT-11,
HO-RT-01 to HO-RT-03, HO-RT-05 to HO-RT-11, HO-RT-12a to HO-RT-12d, HO-RT-13 and
HO-RT-14, keeping their predicates. Those cases find processes through a table
of every process's command line, re-identify PIDs by command string, read
process environments, and test ports machine-wide. Here a process is acted on,
or its arguments read, only after it is proven owned, and every identity is
re-checked first.

Every test needs THE REAL LEGACY MODEL AND A LIVE SERVER, and is skipped as NOT
RUN unless all of these are present:

  SLM_EVAL_TEST_RUNTIME=1     the pinned llama.cpp runtime and the real model
                              artifact are available to the implementation
  SLM_EVAL_TEST_SEED_CACHE    a dataset cache holding the pinned dataset and
                              models/<model.artifact.file> (§2); copied into
                              each test's temporary SLM_EVAL_CACHE (files over
                              256 MiB are symlinked, not copied)
  legacy protocol             SLM_EVAL_TEST_PROTOCOL, or the repository file,
                              whose SHA-256 is that of the canonical legacy
                              protocol (LEGACY_PROTOCOL_SHA256)
  legacy manifest             SLM_EVAL_TEST_MANIFEST, or the repository file,
                              whose SHA-256 is that of the committed manifest
                              (LEGACY_MANIFEST_SHA256)
  tools                       `ps`, `pgrep` and `lsof`; os.waitid with WNOWAIT

VS-LR-06 and VS-LR-15 also need SLM_EVAL_TEST_KEY_LOOKUP=1, which enables the
key lookup described below. It is a design proposal: it needs a separate Human
ruling before it is used with a real server.

These are regressions of the legacy Qwen path only. They never retarget the
legacy protocol to a fixture or another model, and they check the legacy
record fields, not the fields added in v0.8.

Process ownership. Each test uses fresh temporary locations and first waits
for the next whole second, its start second. A process is proven owned, and
recorded with its identity (start time and user ID, from `ps -o
stat=,uid=,ppid=,pgid=,lstart= -p <pid>`), when it is:
  1. a CLI process the test launched, in a new session and process group. Its
     handle is not reaped until teardown, so its PID and group ID cannot be
     reused while the test runs; exit statuses are read without reaping;
  2. a member of such a launch group (`pgrep -g`);
  3. a holder of a WRITE handle on a contract-declared file inside the test's
     fresh run store: a run directory's §6.1 files, or a `_verification/<sha>/`
     record (`lsof -- <those files>`, read-only);
  4. a child of a proven process (`pgrep -P`), checked before and after the
     listing;
  5. the parent of a proven process (its PPID, never 1), re-read after the
     parent's identity is taken, and started no later than that child;
  6. a member of a proven process's own process group (`pgrep -g`), checked
     before and after the listing.
Each must also be alive, not a zombie, of the test's effective user ID and
started no earlier than the test's start second; the pytest process and its
parent never qualify. A live process met by rules 3 to 6 that fails a guard is
recorded as adjoining but unproven, and is never signalled or read. A process
name, a port, a PID or a parent link alone never proves ownership. These
guards bound what is claimed; they are not a cryptographic proof of
application ownership. No process table is scanned. `lsof` is asked only about
the test's own files, the listening sockets of proven PIDs (`lsof -p`), and the
listeners of a port a proven server of the test used. Every identity is
re-read immediately before a signal; a changed or missing identity means the
PID no longer names that process, and nothing is sent.

Attribution. A process belongs to a command (launch) when its identity was
first proven after that command was launched; the tests judge survivors only of
the last command they launched. "Survivors" of a command are its proven
processes, other than the launched CLI itself, whose identity is still live.

Ports. After a command exits, each port a proven server of it listened on is
re-checked with `lsof -iTCP:<port> -sTCP:LISTEN`, reading only PIDs. A listener
that is proven, or that is unproven but has the test's user ID and started
after the test began (for example a process that escaped observation), means
the port is still listening. A listener that started before the test, or has
another user ID, cannot be the test's process: the port was released and
reused, and this is noted, not failed.

Key lookup (VS-LR-06, VS-LR-15; enabled only by SLM_EVAL_TEST_KEY_LOOKUP=1).
Only after the CLI was launched with the allowlisted environment below, and
only for a listening server proven through the launch lineage (rules 1, 2 and
4) whose identity is re-checked before and after, the test reads that one
process's argument vector and initial environment into memory (macOS sysctl
KERN_PROCARGS2; Linux /proc/<pid>/cmdline and environ). Walking the entries in
place, it copies out only the values of `--api-key <v>`, `--api-key=<v>` and
`LLAMA_API_KEY=<v>`, and notes whether an `--api-key-file` argument is
present; no other argument or variable is copied or kept, and the read buffer
is zeroed (transient copies cannot be guaranteed wiped). No other process's
arguments or environment are read. The values are held in objects whose
representation is withheld, compared with file bytes, and never printed,
stored or digested.

Child environment. The CLI receives only PATH, HOME, USER, LOGNAME, LANG,
LC_ALL, LC_CTYPE, TZ and TMPDIR from the test's environment, any further names
listed in SLM_EVAL_TEST_PASS_ENV (a name that looks like a credential, or an
SLM_EVAL_ name, is refused: NOT RUN), and the test's own SLM_EVAL_CACHE,
SLM_EVAL_RUNS and SLM_EVAL_SCRATCH. Process tools run with only PATH, TZ and
the C locale.

Diagnostics. Failures name files, line numbers, pairs, counts, routes,
statuses and process IDs. No record content, generation, key material or CLI
output is printed: the CLI's stdout and stderr stay in named files, and an
unexpected error becomes a failure naming only its type, a safe detail and the
line.

NOT RUN. Missing capabilities and inputs, a missing ownership proof, and a run
that completes before the condition under test can be observed are skipped as
NOT RUN, naming the exact need. A NOT RUN skip is never a pass.

Teardown is not an assertion. It signals each unreaped launch group, then each
proven process individually (parents before children), first SIGTERM and then
SIGKILL; no delivery order is guaranteed. What it found, sent and left is
written to logs/teardown.txt in the test's temporary directory and recorded as
the pytest user property `vs_teardown`; a proven process left alive, or a
teardown error, also raises a warning. Processes outside these proofs cannot
be observed, so their absence is never claimed.

Timing: model start-up is awaited for up to STARTUP seconds, a verification
for up to VERIFY_TIMEOUT seconds and a complete run for up to
SLM_EVAL_TEST_RUN_TIMEOUT seconds (default 14400). No speed is required.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split);
SLM_EVAL_TEST_REPO names the repository root (default: the nearest ancestor of
the working directory whose pyproject.toml mentions slm-eval). No
SLM_EVAL_TEST_* variable is passed to the implementation.
"""

from __future__ import annotations

import ctypes
import functools
import hashlib
import json
import math
import mmap
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import warnings
from pathlib import Path
from typing import NamedTuple

import pytest

EXIT_OK, EXIT_INTEGRITY, EXIT_PRECONDITION = 0, 2, 3
STARTUP = 1800.0
VERIFY_TIMEOUT = 3600.0
CLI_TIMEOUT = 300.0
INTERRUPT_TIMEOUT = 300.0
GRACE = 15.0
ITEM_WAIT = 900.0
TEARDOWN_GRACE = (5.0, 10.0)
LARGE_FILE = 256 * 1024 * 1024
LEGACY_PROTOCOL_SHA256 = "ce48a2448c61af2839e62d63137b06d96ca7bc1b81ce68809adf2af36da8f0ee"
LEGACY_MANIFEST_SHA256 = "a10c02e7ac60b563a4d112cad92c2540ce58d6c0a49fa73f844091df66e48c71"
SEARCH_SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
REPO_SKIP_DIRS = {"node_modules", "__pycache__", "site-packages", "dist", "build", "venv", "env"}
HEX64 = re.compile(r"[0-9a-f]{64}")
Z95 = 1.959963984540054

MODEL_ALIAS = "qwen3-8b"
EXEMPT = ("/health", "/v1/models")
PROBE_GET = ("/", "/index.html", "/health", "/v1/health", "/models", "/v1/models", "/api/tags", "/props",
             "/v1/props", "/slots", "/metrics", "/lora-adapters")
PROBE_POST = ("/completion", "/completions", "/v1/completions", "/chat/completions", "/v1/chat/completions",
              "/tokenize", "/detokenize", "/embedding", "/embeddings", "/v1/embeddings", "/infill",
              "/apply-template", "/rerank", "/v1/rerank")
GENERATION_ROUTES = {"/completion", "/completions", "/v1/completions", "/chat/completions", "/v1/chat/completions"}
WRONG_KEYS = {
    "no key": {},
    "wrong bearer key": {"Authorization": "Bearer visible-suite-wrong-key"},
    "wrong x-api-key": {"X-Api-Key": "visible-suite-wrong-key"},
}
PROBE_BODY = json.dumps({"prompt": "2+2=", "n_predict": 1, "max_tokens": 1, "content": "2+2=", "tokens": [1],
                         "messages": [{"role": "user", "content": "2+2="}]}).encode("utf-8")
PREFLIGHT_HEADERS = {"Origin": "http://localhost:8000", "Access-Control-Request-Headers": "authorization, content-type"}

# Contract-declared files in the test's fresh run store: a run directory (§6.1, §6.4, §7) and a verification
# record directory (§3).
RUN_FILES = ("items.jsonl", "run.json", "protocol.yaml", "manifest.json", "runtime-verification.json",
             "artifact-verification.json", "rescore.json", "report.json", "report.md")
VERIFICATION_FILES = ("runtime-verification.json", "artifact-verification.json")

SERVER_GAP = ("no listening model server is provably owned: no process listening on TCP was in the test's launch "
              "group or lineage, or held a write handle on this test's fresh run-store files (or descended from or "
              "adjoined such a process), with the test's user ID and a start after the test began")


def not_run(reason: str):
    pytest.skip(f"NOT RUN: {reason}")


def fail(message: str):
    """Fail without a traceback or assertion introspection, which could show record contents or key material."""
    raise pytest.fail.Exception(message, pytrace=False) from None


def _summary(exc: BaseException) -> str:
    line = None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") == __name__:
            line = tb.tb_lineno
        tb = tb.tb_next
    detail = ""
    if isinstance(exc, json.JSONDecodeError):
        detail = f": {exc.msg} at line {exc.lineno} column {exc.colno}"
    elif isinstance(exc, OSError):
        detail = f": {exc.strerror or ''} {exc.filename or ''}".rstrip()
    elif isinstance(exc, KeyError) and exc.args and isinstance(exc.args[0], str) and len(exc.args[0]) <= 40:
        detail = f": {exc.args[0]!r}"
    elif isinstance(exc, subprocess.TimeoutExpired):
        detail = f" after {exc.timeout} s"
    return (f"unexpected {type(exc).__name__}{detail} at line {line} of this test module; other details are withheld "
            "so that no record content, generation or key material reaches the diagnostics")


def withheld(test):
    """An unexpected error inside a test becomes a failure naming only its type, a safe detail and the line. It is
    raised after the handler, so no traceback, local variable or chained exception is shown."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        summary = None
        try:
            return test(*args, **kwargs)
        except Exception as exc:
            summary = _summary(exc)
        fail(summary)
    return run


# ---------------------------------------------------------------------------
# process identities
# ---------------------------------------------------------------------------

_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_TOOL_ENV = {"PATH": _SYSTEM_PATH, "LC_ALL": "C", "LANG": "C"}
if os.environ.get("TZ"):
    _TOOL_ENV["TZ"] = os.environ["TZ"]
_TOOLS = {name: shutil.which(name, path=os.pathsep.join(filter(None, (os.environ.get("PATH"), _SYSTEM_PATH))))
          for name in ("ps", "pgrep", "lsof", "sysctl")}


def _query(argv: list[str]) -> str:
    tool = _TOOLS.get(argv[0])
    if tool is None:
        return ""
    return subprocess.run([tool, *argv[1:]], stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
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
        self.foreign: dict[int, tuple[tuple[str, int], str]] = {}
        self.anchors: list[tuple[Path, tuple[str, ...]]] = []
        self.signals: list[str] = []
        self.lookups: list[int] = []
        self.excluded = {os.getpid(), os.getppid()}

    # -- identities --------------------------------------------------------------------------------------

    def _groups(self) -> list[int]:
        """Group IDs of unreaped leaders: reserved, so membership in them proves ownership."""
        return [p.pid for p in self.leaders if p.returncode is None]

    def current(self, pid: int) -> bool:
        info = probe(pid) if pid in self.known else None
        return info is not None and (info.start, info.uid) == self.known[pid]

    def live(self) -> set[int]:
        return {pid for pid in list(self.known) if self.current(pid)}

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

    def anchor(self, directory: Path, names: tuple[str, ...]) -> None:
        if (directory, names) not in self.anchors:
            self.anchors.append((directory, names))

    def own_files(self) -> list[Path]:
        files = []
        for directory, names in self.anchors:
            for name in names:
                path = directory / name
                if path.is_file() and not path.is_symlink():
                    files.append(path.resolve())
        return files

    def write_holders(self) -> list[tuple[int, str]]:
        """(PID, file name) for every write handle on the test's own files: `lsof -- <files>` only."""
        files = self.own_files()
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
                self._admit(pid, f"write handle on {name}")
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
        """Live proven processes, parents before children."""
        return sorted(self.live(), key=lambda pid: (len(self.ancestors(pid)), pid))

    def sockets(self, pids) -> set[tuple[int, str, int]]:
        """(PID, host, port) of every TCP listening socket of these proven, live PIDs: `lsof -p` only."""
        pids = sorted(p for p in pids if self.current(p))
        if not pids:
            return set()
        out = _query(["lsof", "-nP", "-w", "-a", "-p", ",".join(map(str, pids)), "-iTCP", "-sTCP:LISTEN", "-Fpn"])
        found, pid = set(), None
        for line in out.splitlines():
            if line.startswith("p"):
                pid = int(line[1:]) if line[1:].isdigit() else None
            elif line.startswith("n") and pid in pids:
                host, _, port = line[1:].rpartition(":")
                if port.isdigit():
                    found.add((pid, host.strip("[]"), int(port)))
        return {entry for entry in found if self.current(entry[0])}

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

    def kill_proven(self, sig: int) -> None:
        for group in self._groups():
            try:
                os.killpg(group, sig)
                self.signals.append(f"{signal.Signals(sig).name} -> launch group {group}")
            except (ProcessLookupError, PermissionError):
                pass
        for pid in self.ordered():
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
        for sig, grace in zip((signal.SIGTERM, signal.SIGKILL), TEARDOWN_GRACE):
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


def port_holders(port: int) -> set[int]:
    """PIDs listening on a port a proven server of the test used (PIDs only; nothing else is read)."""
    out = _query(["lsof", "-nP", "-w", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"])
    return {int(line[1:]) for line in out.splitlines() if line.startswith("p") and line[1:].isdigit()}


def held_ports(family: Family, ports) -> tuple[list[str], list[str]]:
    """(ports still listening, notes) after a command exited; see Ports in the module docstring."""
    held, notes = [], []
    for port in sorted(ports):
        for pid in sorted(port_holders(port)):
            info = probe(pid)
            if info is None:
                continue
            began = start_second(info.start)
            if family.current(pid):
                held.append(f"port {port} by proven {family.describe(pid)}")
            elif info.uid != os.geteuid():
                notes.append(f"port {port} is now held by process {pid} of another user ID: released and reused")
            elif began is None or began < family.floor:
                notes.append(f"port {port} is now held by process {pid}, which started before the test began: "
                             "released and reused")
            else:
                held.append(f"port {port} by process {pid} (not proven owned; it started {info.start}, after the "
                            "test began, with the test's user ID)")
    return held, notes


# ---------------------------------------------------------------------------
# the per-run key: a narrowly scoped lookup on one proven server (VS-LR-06, VS-LR-15)
# ---------------------------------------------------------------------------

_CTL_KERN, _KERN_ARGMAX, _KERN_PROCARGS2 = 1, 8, 49


class _Withheld:
    """One key value: compared with file bytes, never shown."""
    __slots__ = ("_value",)

    def __init__(self, value: bytes):
        self._value = value

    def __repr__(self) -> str:
        return "<withheld>"

    __str__ = __repr__


class KeyFacts:
    """What the key lookup found: withheld key values and whether a key file argument was given."""
    __slots__ = ("_keys", "key_file", "read", "gaps")

    def __init__(self):
        self._keys: list[_Withheld] = []
        self.key_file = False
        self.read: list[int] = []
        self.gaps: list[str] = []

    def __repr__(self) -> str:
        return f"KeyFacts({len(self._keys)} withheld value(s), key_file={self.key_file}, read={self.read})"

    def count(self) -> int:
        return len(self._keys)

    def _add(self, value: bytes) -> None:
        if value and all(k._value != value for k in self._keys):
            self._keys.append(_Withheld(value))

    def found_in(self, path: Path) -> bool:
        """True if a regular, non-symlinked file of at most 256 MiB contains any key value."""
        try:
            if path.is_symlink() or not path.is_file():
                return False
            size = path.stat().st_size
            if size == 0 or size > LARGE_FILE:
                return False
            with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as view:
                for index in range(len(self._keys)):
                    if view.find(self._keys[index]._value) != -1:
                        return True
        except (OSError, ValueError):
            return False
        return False


def _spans(raw: bytes, pos: int):
    """(start, end) of each NUL-terminated entry of raw from pos, without copying any entry."""
    while pos < len(raw):
        end = raw.find(b"\0", pos)
        end = len(raw) if end < 0 else end
        yield pos, end
        pos = end + 1


def _key_values_in(raw: bytes, argument_spans, environment_spans) -> tuple[list[bytes], bool]:
    """Only `--api-key <v>`, `--api-key=<v>` and `LLAMA_API_KEY=<v>` values, and whether an `--api-key-file`
    argument is present. No other argument or variable is copied out of raw."""
    keys, key_file, take = [], False, False
    for start, end in argument_spans:
        if take:
            keys.append(raw[start:end])
            take = False
        elif (end - start == len(b"--api-key-file") and raw.startswith(b"--api-key-file", start)) \
                or raw.startswith(b"--api-key-file=", start):
            key_file = True
        elif end - start == len(b"--api-key") and raw.startswith(b"--api-key", start):
            take = True
        elif raw.startswith(b"--api-key=", start):
            keys.append(raw[start + len(b"--api-key="):end])
    keys += [raw[start + len(b"LLAMA_API_KEY="):end] for start, end in environment_spans
             if raw.startswith(b"LLAMA_API_KEY=", start)]
    return keys, key_file


def _read_key_values(pid: int) -> tuple[list[bytes], bool] | None:
    """The key values and key-file fact of ONE process, from its argument vector and initial environment read in
    memory; None if unavailable. Every other argument and variable is skipped in place, never copied or kept."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        argmax, size = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
        names = (ctypes.c_int * 2)(_CTL_KERN, _KERN_ARGMAX)
        if libc.sysctl(names, ctypes.c_uint(2), ctypes.byref(argmax), ctypes.byref(size), None,
                       ctypes.c_size_t(0)) != 0 or argmax.value <= 0:
            return None
        buffer = ctypes.create_string_buffer(argmax.value)
        size = ctypes.c_size_t(argmax.value)
        names = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)
        try:
            if libc.sysctl(names, ctypes.c_uint(3), buffer, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
                return None
            raw = buffer.raw[:size.value]
        finally:
            ctypes.memset(buffer, 0, argmax.value)
        try:
            if len(raw) < 4:
                return None
            argc = int.from_bytes(raw[:4], sys.byteorder)
            pos = raw.find(b"\0", 4)                 # the executable path ends here, then NUL padding
            if pos < 0:
                return None
            while pos < len(raw) and raw[pos] == 0:
                pos += 1
            entries = _spans(raw, pos)
            argument_spans = [span for _, span in zip(range(argc), entries)]
            environment_spans = []
            for start, end in entries:
                if start == end:
                    break
                environment_spans.append((start, end))
            return _key_values_in(raw, argument_spans, environment_spans)
        finally:
            del raw
    if sys.platform.startswith("linux"):
        try:
            arguments = Path(f"/proc/{pid}/cmdline").read_bytes()
            environment = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            return None
        try:
            keys, key_file = _key_values_in(arguments, list(_spans(arguments, 0)), [])
            more, _ = _key_values_in(environment, [], list(_spans(environment, 0)))
            return keys + more, key_file
        finally:
            del arguments, environment
    return None


def key_facts(family: Family, pids) -> KeyFacts:
    """The key lookup (see the module docstring), on proven listening servers from the launch lineage only."""
    facts = KeyFacts()
    for pid in sorted(pids):
        if pid not in family.classic or not family.current(pid):
            facts.gaps.append(f"listening process {pid} is not proven through the test's launch lineage")
            continue
        identity = family.known[pid]
        found = _read_key_values(pid)
        if found is None or family.known.get(pid) != identity or not family.current(pid):
            facts.gaps.append(f"the arguments of process {pid} could not be read while its identity held")
            found = None
            continue
        family.lookups.append(pid)
        facts.read.append(pid)
        keys, key_file = found
        found = None
        facts.key_file = facts.key_file or key_file
        for index in range(len(keys)):
            facts._add(keys[index])
        del keys
    if not pids:
        facts.gaps.append(SERVER_GAP)
    elif facts.read and not facts.count():
        facts.gaps.append("the proven server has no --api-key argument and no LLAMA_API_KEY variable")
    return facts


def require_key_lookup() -> None:
    if os.environ.get("SLM_EVAL_TEST_KEY_LOOKUP", "").strip() != "1":
        not_run("the per-run key lookup on the proven test-owned server is not enabled: set "
                "SLM_EVAL_TEST_KEY_LOOKUP=1 only after the Human has ruled on its use with a real server")
    if not (sys.platform == "darwin" or sys.platform.startswith("linux")):
        not_run(f"no single-process argument reader on {sys.platform}")


# ---------------------------------------------------------------------------
# CLI, child environment and per-test locations
# ---------------------------------------------------------------------------


def _command() -> list[str]:
    override = os.environ.get("SLM_EVAL_TEST_CMD", "").strip()
    if override:
        return shlex.split(override)
    found = shutil.which("slm-eval")
    if found is None:
        fail("`slm-eval` is not on PATH; run inside the project environment or set SLM_EVAL_TEST_CMD")
    return [found]


_BASE_PASS = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR")
_CREDENTIAL_WORDS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "API_KEY", "APIKEY", "AUTH", "PRIVATE",
                     "COOKIE", "SESSION")
_CREDENTIAL_PREFIXES = ("HF_", "HUGGING", "ANTHROPIC", "OPENAI", "AWS_", "GH_", "GITHUB_", "GOOGLE_", "AZURE_",
                        "SLM_EVAL_")


def child_environment(locations: dict[str, str]) -> dict[str, str]:
    names = list(_BASE_PASS)
    for name in (n.strip() for n in os.environ.get("SLM_EVAL_TEST_PASS_ENV", "").split(",")):
        if not name:
            continue
        upper = name.upper()
        if any(word in upper for word in _CREDENTIAL_WORDS) or upper.startswith(_CREDENTIAL_PREFIXES):
            not_run(f"SLM_EVAL_TEST_PASS_ENV names {name}, which looks like a credential or a test/location "
                    "setting; it is never passed to the implementation")
        names.append(name)
    environment = {name: os.environ[name] for name in names if name in os.environ}
    environment.update(locations)
    return environment


class Launch:
    """One launched CLI command and the proven processes attributed to it."""

    def __init__(self, env: "Env", *args: object):
        self.env, self.family = env, env.family
        self.before = set(self.family.known.items())
        name = uuid.uuid4().hex[:8]
        self.out, self.err = env.logs / f"{name}.stdout", env.logs / f"{name}.stderr"
        self.args = _command() + [str(a) for a in args]
        with open(self.out, "wb") as out, open(self.err, "wb") as err:
            self.popen = self.family.launch(self.args, cwd=env.work, env=env.vars, out=out, err=err)
        self.sockets: set[tuple[int, str, int]] = set()
        self.exit: int | None = None

    def members(self) -> set[int]:
        return {pid for pid, identity in self.family.known.items() if (pid, identity) not in self.before}

    def observe(self) -> set[int]:
        self.env.anchor_store()
        self.family.refresh(handles=True)
        live = {pid for pid in self.members() if self.family.current(pid)}
        self.sockets |= self.family.sockets(live)
        return live

    def code(self) -> int | None:
        code = self.family.exit_code(self.popen)
        if code is not None:
            self.exit = code
        return code

    def running(self) -> bool:
        return self.code() is None

    def ports(self) -> list[int]:
        return sorted({port for _, _, port in self.sockets})

    def server_pids(self) -> set[int]:
        return {pid for pid, _, _ in self.sockets if self.family.current(pid)}

    def survivors(self) -> list[int]:
        return sorted(pid for pid in self.members() if pid != self.popen.pid and self.family.current(pid))

    def stdout_text(self) -> str:
        return self.out.read_bytes().decode("utf-8", "replace")

    def where(self) -> str:
        return f"; its stdout and stderr are in {self.out} and {self.err}"

    def wait(self, timeout: float, poll: float) -> int | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.observe()
            code = self.code()
            if code is not None:
                return code
            time.sleep(poll)
        return self.code()

    def interrupt(self, timeout: float = INTERRUPT_TIMEOUT) -> int | None:
        """SIGINT to the launched CLI process alone (never its group); its exit code, or None if it does not exit."""
        self.family.send(self.popen.pid, signal.SIGINT)
        return self.wait(timeout, 0.3)


class Env:
    """Temporary locations for one test and the processes it proves it owns."""

    def __init__(self, root: Path):
        self.root = root
        self.cache, self.runs, self.scratch, self.work, self.logs = (
            root / n for n in ("cache", "runs", "scratch", "work", "logs"))
        for directory in (self.cache, self.runs, self.scratch, self.work, self.logs):
            directory.mkdir(parents=True)
        self.vars = child_environment({"SLM_EVAL_CACHE": str(self.cache), "SLM_EVAL_RUNS": str(self.runs),
                                       "SLM_EVAL_SCRATCH": str(self.scratch)})
        began = time.time()
        time.sleep(math.ceil(began) - began + 0.05)
        self.family = Family(floor=math.ceil(began))
        self.notes: list[str] = []

    def launch(self, *args: object) -> Launch:
        return Launch(self, *args)

    def finish(self, *args: object, timeout: float, poll: float = 1.0) -> Launch:
        """Run a command to its exit, observing its processes; never leaks them (teardown ends them)."""
        live = Launch(self, *args)
        if live.wait(timeout, poll) is None:
            fail(f"`slm-eval {' '.join(map(str, args))}` did not finish within {timeout:.0f} s" + live.where())
        return live

    def run_dirs(self) -> list[Path]:
        """Run directories; `_verification/` holds verification records, not a run (§2)."""
        return sorted(p for p in self.runs.iterdir() if p.is_dir() and not p.name.startswith("_"))

    def anchor_store(self) -> None:
        """Register the contract-declared files of the test's own fresh run store for write-handle observation."""
        runs = self.runs.resolve()
        try:
            entries = sorted(self.runs.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if entry.name == "_verification":
                for sub in sorted(entry.iterdir()):
                    if sub.is_dir() and not sub.is_symlink() and sub.resolve().parent == entry.resolve() \
                            and entry.resolve().parent == runs:
                        self.family.anchor(sub, VERIFICATION_FILES)
            elif not entry.name.startswith("_") and entry.resolve().parent == runs:
                self.family.anchor(entry, RUN_FILES)

    def cleanup(self) -> list[str]:
        """Teardown, kept apart from the test's result; returns what it found, sent and left."""
        notes, by_test = list(self.notes), len(self.family.signals)
        notes.append("signals sent by the test: " + ("; ".join(self.family.signals) or "none"))
        notes.append("key lookups (values never recorded): "
                     + (", ".join(f"process {p}" for p in self.family.lookups) or "none"))
        try:
            self.family.refresh(handles=True)
            notes.append("proven live before cleanup: "
                         + ("; ".join(self.family.describe(p) for p in sorted(self.family.live())) or "none"))
        except Exception as exc:
            notes.append(f"teardown step raised {type(exc).__name__}")
        left = self.family.cleanup()
        notes.append("signals sent by teardown: " + ("; ".join(self.family.signals[by_test:]) or "none"))
        if left:
            notes.append("LEFTOVER proven processes still alive: " + "; ".join(self.family.describe(p) for p in left))
        unproven = self.family.live_foreign()
        if unproven:
            notes.append("adjoining unproven processes, never signalled or read: " + "; ".join(unproven.values()))
        notes.append("limit: processes outside these proofs cannot be observed; their absence is not claimed")
        return notes


@pytest.fixture
def env(tmp_path: Path, request):
    require_live()
    environment = Env(tmp_path)
    try:
        yield environment
    finally:
        notes = environment.cleanup()
        (environment.logs / "teardown.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
        request.node.user_properties.append(("vs_teardown", " | ".join(notes)))
        if any(n.startswith("LEFTOVER") or "raised" in n for n in notes):
            warnings.warn(f"VS teardown left proven processes or failed; see {environment.logs / 'teardown.txt'}",
                          stacklevel=1)


# ---------------------------------------------------------------------------
# capabilities and inputs
# ---------------------------------------------------------------------------


def require_live() -> None:
    if os.environ.get("SLM_EVAL_TEST_RUNTIME", "").strip() != "1":
        not_run("needs the real model and a live server: set SLM_EVAL_TEST_RUNTIME=1")
    if not os.environ.get("SLM_EVAL_TEST_SEED_CACHE", "").strip():
        not_run("needs SLM_EVAL_TEST_SEED_CACHE holding the dataset and models/ (§2)")
    missing = [tool for tool in ("ps", "pgrep", "lsof") if _TOOLS.get(tool) is None]
    if missing:
        not_run(f"needs {', '.join(missing)} for owned-process observation")
    if not (hasattr(os, "waitid") and hasattr(os, "WNOWAIT")):
        not_run("needs os.waitid with WNOWAIT to read exit statuses without releasing owned PIDs")


def seed_cache(env: Env) -> None:
    source = Path(os.environ["SLM_EVAL_TEST_SEED_CACHE"]).expanduser()
    if not source.is_dir():
        not_run(f"SLM_EVAL_TEST_SEED_CACHE={source} is not a directory")
    for dirpath, _, filenames in os.walk(source, followlinks=True):
        target = env.cache / Path(dirpath).relative_to(source)
        target.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            real = (Path(dirpath) / name).resolve()
            if real.stat().st_size > LARGE_FILE:
                os.symlink(real, target / name)
            else:
                shutil.copy2(real, target / name)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_root() -> Path | None:
    configured = os.environ.get("SLM_EVAL_TEST_REPO", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path.resolve() if path.is_dir() else None
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        pyproject = candidate / "pyproject.toml"
        try:
            if pyproject.is_file() and "slm-eval" in pyproject.read_text(encoding="utf-8", errors="replace"):
                return candidate
        except OSError:
            continue
    return None


def _bound_file(variable: str, sha256: str, suffixes: tuple[str, ...], what: str) -> Path:
    configured = os.environ.get(variable, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            not_run(f"{variable}={path} is not a file")
        if sha256_file(path) != sha256:
            not_run(f"{variable} is not the {what} (SHA-256 {sha256}); these legacy regressions are bound to it")
        return path
    root = repo_root()
    if root is not None:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SEARCH_SKIP_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                try:
                    if path.suffix in suffixes and path.stat().st_size <= 50_000_000 and sha256_file(path) == sha256:
                        return path
                except OSError:
                    continue
    not_run(f"needs the {what} (SHA-256 {sha256}): set {variable}")


def legacy_protocol() -> Path:
    return _bound_file("SLM_EVAL_TEST_PROTOCOL", LEGACY_PROTOCOL_SHA256, (".yaml", ".yml", ".json"),
                       "canonical legacy protocol")


def legacy_manifest() -> Path:
    return _bound_file("SLM_EVAL_TEST_MANIFEST", LEGACY_MANIFEST_SHA256, (".json",), "committed legacy manifest")


def protocol_data(path: Path) -> dict:
    """The canonical legacy protocol bytes are JSON text, which every YAML parser also reads."""
    return json.loads(path.read_bytes().decode("utf-8"))


def run_timeout() -> float:
    value = os.environ.get("SLM_EVAL_TEST_RUN_TIMEOUT", "").strip()
    return float(value) if value else 14400.0


# ---------------------------------------------------------------------------
# JSON helpers: the contract fixes some field names and leaves others open
# ---------------------------------------------------------------------------


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_record(path: Path):
    if not path.is_file():
        fail(f"{path} was not written")
    try:
        return read_json(path)
    except ValueError:
        fail(f"{path} is not JSON")


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def complete_jsonl_lines(path: Path) -> list[dict]:
    """Parse only newline-terminated, valid JSON lines (a run may be mid-write)."""
    if not path.exists():
        return []
    data = path.read_bytes().decode("utf-8", "replace")
    records = []
    for raw in data.split("\n")[:-1]:
        if not raw.strip():
            continue
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return records


def file_bytes(root: Path) -> dict[str, bytes]:
    out = {}
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            out[str(path.relative_to(root))] = path.read_bytes()
    return out


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in REPO_SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def walk(obj, path=()):
    yield path, obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk(value, path + (str(key),))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk(value, path + (index,))


def dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def is_hex64(value) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def text_of(obj) -> str:
    keys = [p[-1] for p, _ in walk(obj) if p and isinstance(p[-1], str)]
    return "\n".join(keys + [v for _, v in walk(obj) if isinstance(v, str)])


def numbers(obj) -> list[float]:
    return [float(v) for _, v in walk(obj) if is_number(v)]


def numbers_under(obj, *needles: str) -> list[float]:
    lowered = tuple(n.lower() for n in needles)
    return [float(v) for p, v in walk(obj) if is_number(v)
            and any(isinstance(k, str) and any(n in k.lower() for n in lowered) for k in p)]


def has_close(values, target: float, tol: float = 5e-4) -> bool:
    """§3: accuracies and rates are fractions in [0, 1], so no percent form is accepted."""
    return any(abs(value - target) <= tol for value in values)


_LO, _HI = frozenset({"lo", "low", "lower", "lb"}), frozenset({"hi", "high", "upper", "ub"})


def _tokens(key: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", key.lower()))


def intervals(obj) -> list[tuple[float, float]]:
    found = []
    for _, value in walk(obj):
        if isinstance(value, list) and len(value) == 2 and all(is_number(x) for x in value):
            found.append((float(value[0]), float(value[1])))
        elif isinstance(value, dict):
            lows = [float(x) for k, x in value.items() if is_number(x) and _tokens(k) & _LO]
            highs = [float(x) for k, x in value.items() if is_number(x) and _tokens(k) & _HI]
            found.extend((a, b) for a in lows for b in highs)
    return found


def has_interval(obj, lo: float, hi: float, tol: float = 1e-3) -> bool:
    return any(abs(a - lo) <= tol and abs(b - hi) <= tol for a, b in intervals(obj))


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


RESCORE_RECORD_KEYS = frozenset({"item_id", "sample_index", "stored", "recomputed", "match"})


def _verdict_pair(value):
    if isinstance(value, dict) and isinstance(value.get("primary"), bool) and isinstance(value.get("secondary"), bool):
        return value["primary"], value["secondary"]
    return None


def rescore_problems(doc, run_id: str, pairs: set) -> list[str]:
    """Violations of the §6.4 schema, and records not covering exactly `pairs`."""
    if not isinstance(doc, dict):
        return ["rescore.json is not a JSON object"]
    problems = []
    if doc.get("rescore_version") != "1":
        problems.append(f"rescore_version {doc.get('rescore_version')!r}, expected '1'")
    if doc.get("run_id") != run_id:
        problems.append(f"run_id {doc.get('run_id')!r}, expected {run_id!r}")
    if not isinstance(doc.get("all_match"), bool):
        problems.append(f"all_match {doc.get('all_match')!r} is not a boolean")
    records = doc.get("records")
    if not isinstance(records, list):
        return problems + ["records is not a list"]
    seen = []
    for record in records:
        if not isinstance(record, dict) or not RESCORE_RECORD_KEYS <= set(record):
            problems.append(f"a record lacks {sorted(RESCORE_RECORD_KEYS)}")
            continue
        stored, recomputed = _verdict_pair(record["stored"]), _verdict_pair(record["recomputed"])
        where = f"{record['item_id']}#{record['sample_index']}"
        if stored is None or recomputed is None:
            problems.append(f"record {where}: stored/recomputed not {{primary: bool, secondary: bool}}")
        elif record["match"] is not (stored == recomputed):
            problems.append(f"record {where}: match disagrees with stored/recomputed")
        seen.append((record["item_id"], record["sample_index"]))
    if len(seen) != len(set(seen)) or set(seen) != set(pairs):
        problems.append(f"records cover {len(set(seen))} distinct pairs ({len(seen)} records), expected each of "
                        f"{len(pairs)} item x sample pairs once")
    matches = [r.get("match") for r in records if isinstance(r, dict)]
    if isinstance(doc.get("all_match"), bool) and doc["all_match"] is not all(m is True for m in matches):
        problems.append("all_match disagrees with the records' match flags")
    return problems


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


def cached_records(root: Path) -> list[dict]:
    """Records with a string `problem` field from JSON / JSONL files in the cache."""
    records = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in (".jsonl", ".json"):
                continue
            try:
                if path.stat().st_size > LARGE_FILE:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rows = []
            try:
                parsed = json.loads(text)
                rows = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                for line in text.splitlines():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            records.extend(r for r in rows if isinstance(r, dict) and isinstance(r.get("problem"), str))
    return records


def records_by_id(records) -> dict[str, dict]:
    out = {}
    for record in records:
        for key in ("unique_id", "id"):
            if isinstance(record.get(key), str):
                out.setdefault(record[key], record)
    return out


def leaked_problems(texts, records, min_len: int = 40) -> int:
    """How many problem texts (whitespace-normalized, at least `min_len` chars) occur inside `texts`."""
    haystacks = [normalize_ws(t) for t in texts if t and len(t) >= min_len]
    if not haystacks:
        return 0
    count = 0
    for record in records:
        problem = normalize_ws(record["problem"])
        if len(problem) >= min_len and any(problem[:120] in h for h in haystacks):
            count += 1
    return count


def find_flagged_file(root: Path) -> Path | None:
    for path in iter_files(root):
        if path.suffix.lower() != ".json":
            continue
        try:
            data = read_json(path)
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and "flagged_version" in data and "pairs" in data:
            return path
    return None


# ---------------------------------------------------------------------------
# HTTP on the proven server's loopback ports
# ---------------------------------------------------------------------------

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(port: int, method: str, path: str, headers: dict) -> tuple[int | None, bytes]:
    body = PROBE_BODY if method == "POST" else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method,
                                     headers={**headers, **({"Content-Type": "application/json"} if body else {})})
    try:
        with _OPENER.open(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read() if error.fp else b""
    except (urllib.error.URLError, OSError, ValueError):
        return None, b""


def signature(body: bytes):
    """A comparable form of a response body; volatile `created` timestamps are dropped."""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return body.strip()

    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "created"}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    return json.dumps(strip(parsed), sort_keys=True)


def ephemeral_range() -> tuple[int, int]:
    try:
        low = _query(["sysctl", "-n", "net.inet.ip.portrange.first"]).strip()
        high = _query(["sysctl", "-n", "net.inet.ip.portrange.last"]).strip()
        if low.isdigit() and high.isdigit():
            return int(low), int(high)
    except (OSError, ValueError):
        pass
    try:
        low_text, high_text = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()
        return int(low_text), int(high_text)
    except (OSError, ValueError):
        return 49152, 65535


# ---------------------------------------------------------------------------
# reading runtime-verification.json without assuming field names
# ---------------------------------------------------------------------------

STATUS_KEY_WORDS = ("status", "code", "key", "auth", "with")


def _statuses(entry: dict) -> list[int]:
    """HTTP statuses in an entry: integers 100-599 under status-like keys (not timings or ports)."""
    return [v for path, v in walk(entry)
            if isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599
            and any(isinstance(k, str) and any(w in k.lower() for w in STATUS_KEY_WORDS) for k in path)]


def _classification(entry: dict) -> list[str]:
    return [v.lower() for v in entry.values() if isinstance(v, str) and v.lower() in ("protected", "exempt")]


def _is_options(entry: dict) -> bool:
    return any(isinstance(v, str) and v.upper() == "OPTIONS" for v in entry.values())


def _lengths(entry: dict) -> list[int]:
    """Body lengths in an entry: non-negative integers under length/size/bytes keys."""
    return [v for path, v in walk(entry)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0
            and any(isinstance(k, str) and any(w in k.lower() for w in ("length", "size", "bytes")) for k in path)]


def options_entries(doc) -> list[dict]:
    """OPTIONS probe records (§10): an object naming one route and the method OPTIONS, or an `options` object inside
    a route-keyed object. Statuses of the enclosing route object are included, since that is where a nested record
    would keep the data-bearing method's result."""
    entries = []
    for _, value in walk(doc):
        if not isinstance(value, dict):
            continue
        routes = [v for v in value.values() if isinstance(v, str) and v.startswith("/")]
        if len(routes) == 1 and _is_options(value):
            entries.append({"route": routes[0], "statuses": _statuses(value), "lengths": _lengths(value),
                            "own": _statuses(value)})
        for key, sub in value.items():
            if isinstance(key, str) and key.startswith("/") and isinstance(sub, dict):
                for inner_key, inner in sub.items():
                    if isinstance(inner_key, str) and inner_key.lower() == "options" and isinstance(inner, dict):
                        entries.append({"route": key, "statuses": _statuses(inner) + _statuses(sub),
                                        "lengths": _lengths(inner), "own": _statuses(inner)})
    return entries


def route_entries(doc) -> list[dict]:
    """Route records: an object naming one route ("/...") as a value, or keyed by the route, with a protected/exempt
    classification and its HTTP statuses."""
    entries = []
    for _, value in walk(doc):
        if not isinstance(value, dict):
            continue
        routes = [v for v in value.values() if isinstance(v, str) and v.startswith("/")]
        classes = _classification(value)
        if len(routes) == 1 and len(classes) == 1:
            entries.append({"route": routes[0], "class": classes[0], "statuses": _statuses(value),
                            "options": _is_options(value)})
        for key, sub in value.items():
            if isinstance(key, str) and key.startswith("/") and isinstance(sub, dict) and len(_classification(sub)) == 1:
                entries.append({"route": key, "class": _classification(sub)[0], "statuses": _statuses(sub),
                                "options": _is_options(sub)})
    return entries


# ---------------------------------------------------------------------------
# verification, runs and their servers
# ---------------------------------------------------------------------------


def verification_record(env: Env, protocol_sha256: str, name: str) -> Path:
    """§3: $SLM_EVAL_RUNS/_verification/<protocol_sha256>/<name>."""
    return env.runs / "_verification" / protocol_sha256 / name


def record_path(env: Env, protocol: Path) -> Path:
    return verification_record(env, sha256_file(protocol), "runtime-verification.json")


def verify_runtime(env: Env, protocol: Path, poll: float = 0.3) -> Launch:
    live = env.launch("verify-runtime", "--protocol", protocol)
    if live.wait(VERIFY_TIMEOUT, poll) is None:
        fail("verify-runtime did not finish within an hour" + live.where())
    return live


def await_server(live: Launch, timeout: float, what: str) -> list[int]:
    """Ports of the proven listening servers of `live`. A command that exits first fails, unless it exited 0 (the
    condition under test was missed: NOT RUN); a command still running without a proven listener is NOT RUN."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live.observe()
        if live.sockets:
            return live.ports()
        code = live.code()
        if code is not None:
            live.observe()
            if live.sockets:
                return live.ports()
            if code == EXIT_OK:
                not_run(f"{what} exited 0 while {SERVER_GAP}; nothing was observed")
            fail(f"{what} exited {code} before a listening model server of it was observed" + live.where())
        time.sleep(0.2)
    not_run(f"after {timeout:.0f} s {what} was still running, but {SERVER_GAP}")


def run_args(protocol: Path, manifest: Path, k: int = 1, seed_base: int | None = None) -> list:
    args = ["run", "--protocol", protocol, "--manifest", manifest, "--tier", "smoke", "--k", k]
    if seed_base is not None:
        args += ["--seed-base", seed_base]
    return args


def start_run_server(env: Env) -> tuple[Launch, list[int]]:
    """VS flow: seed the cache, write both verification records, start a smoke run, wait for its proven server and
    for /health to answer 200 on every proven listening port."""
    protocol, manifest = legacy_protocol(), legacy_manifest()
    seed_cache(env)
    artifact = env.finish("verify-artifact", "--protocol", protocol, timeout=VERIFY_TIMEOUT)
    if artifact.exit != EXIT_OK:
        fail(f"verify-artifact failed: exit {artifact.exit}" + artifact.where())
    runtime = verify_runtime(env, protocol)
    if runtime.exit != EXIT_OK:
        fail(f"verify-runtime failed: exit {runtime.exit}" + runtime.where())
    live = env.launch(*run_args(protocol, manifest))
    ports = await_server(live, STARTUP, "slm-eval run")
    deadline = time.monotonic() + STARTUP
    while time.monotonic() < deadline and live.running():
        if all(http(port, "GET", "/health", {})[0] == 200 for port in ports):
            return live, ports
        time.sleep(0.5)
    fail("the run's server never became ready (no 200 from /health on 127.0.0.1 on every proven listening port)"
         + live.where())


def prepared(env: Env) -> tuple[Path, Path]:
    """HO flow: seed the cache and produce the verification artifacts a run copies in at start (§6.1)."""
    protocol, manifest = legacy_protocol(), legacy_manifest()
    seed_cache(env)
    artifact = env.finish("verify-artifact", "--protocol", protocol, timeout=VERIFY_TIMEOUT)
    if artifact.exit != EXIT_OK:
        fail(f"verify-artifact failed before run: exit {artifact.exit}" + artifact.where())
    runtime = env.finish("verify-runtime", "--protocol", protocol, timeout=VERIFY_TIMEOUT)
    if runtime.exit != EXIT_OK:
        fail(f"verify-runtime failed before run: exit {runtime.exit}" + runtime.where())
    return protocol, manifest


def start_server(env: Env, protocol: Path, manifest: Path) -> tuple[Launch, set]:
    """HO flow: start a smoke run and wait for a proven listening server (not for /health)."""
    live = env.launch(*run_args(protocol, manifest))
    await_server(live, STARTUP, "slm-eval run")
    return live, set(live.sockets)


def wait_ports(live: Launch, timeout: float) -> list[int]:
    """Wait for a proven listener, returning its ports, or nothing; never fails or skips."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and live.running():
        live.observe()
        if live.sockets:
            return live.ports()
        time.sleep(0.25)
    return live.ports()


def interrupt_and_wait(live: Launch, timeout: float = INTERRUPT_TIMEOUT) -> int:
    code = live.interrupt(timeout)
    if code is None:
        fail(f"slm-eval did not exit within {timeout:.0f}s of SIGINT")
    return code


def wait_for_run_dir(env: Env, live: Launch, timeout: float) -> Path | None:
    deadline = time.monotonic() + timeout
    while True:
        live.observe()
        dirs = env.run_dirs()
        if dirs:
            return dirs[0]
        if live.code() is not None or time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def wait_for_items(env: Env, live: Launch, minimum: int, timeout: float) -> Path | None:
    deadline = time.monotonic() + timeout
    while True:
        live.observe()
        for run_dir in env.run_dirs():
            if len(complete_jsonl_lines(run_dir / "items.jsonl")) >= minimum:
                return run_dir
        if live.code() is not None or time.monotonic() >= deadline:
            return None
        time.sleep(1.0)


def assert_cleaned(env: Env, live: Launch, ports, survived: str, still: str) -> None:
    deadline = time.monotonic() + GRACE
    while time.monotonic() < deadline and (live.survivors() or held_ports(env.family, ports)[0]):
        time.sleep(0.5)
    survivors = live.survivors()
    if survivors:
        fail(f"{survived}: " + "; ".join(env.family.describe(p) for p in survivors))
    held, notes = held_ports(env.family, ports)
    env.notes += notes
    if held:
        fail(f"{still}: " + "; ".join(held))


def run_id_from(live: Launch, env: Env) -> str:
    """§3: `run` prints the run ID; any other stdout line is a written path, logs go to stderr."""
    lines = [line.strip() for line in live.stdout_text().splitlines() if line.strip()]
    ids = [line for line in lines if "/" not in line and (env.runs / line).is_dir()]
    if len(ids) != 1:
        fail(f"run must print its run ID on stdout exactly once; {len(ids)} run IDs printed" + live.where())
    return ids[0]


def smoke_ids(manifest: Path) -> list[str]:
    return list(read_json(manifest)["tiers"]["smoke"])


def run_report(env: Env, run_id: str) -> tuple[dict, str]:
    """Run `report` without --out-dir and read report.json / report.md from the run directory (§3)."""
    done = env.finish("report", "--run", run_id, timeout=CLI_TIMEOUT, poll=0.2)
    if done.exit != EXIT_OK:
        fail(f"report exited {done.exit}" + done.where())
    run_dir = env.runs / run_id
    json_path, md_path = run_dir / "report.json", run_dir / "report.md"
    if not (json_path.is_file() and md_path.is_file()):
        fail("report.json and report.md not both written to the run directory (the default --out-dir, §3)"
             + done.where())
    return read_json(json_path), md_path.read_text(encoding="utf-8", errors="replace")


def thinking_text_present(raw: str) -> bool:
    """§8 (qwen3, v0.7 meaning of thinking_present): the thinking text after the first <think>, up to the last
    </think> when the block is closed, has a non-whitespace character. False without an opening delimiter."""
    first = raw.find("<think>")
    if first < 0:
        return False
    last_open, last_close = raw.rfind("<think>"), raw.rfind("</think>")
    end = last_close if last_close > last_open else len(raw)
    region = raw[first + len("<think>"):end].replace("<think>", "").replace("</think>", "")
    return bool(region.strip())


# ---------------------------------------------------------------------------
# tests: §3 verify-runtime and §10 runtime security (successors of VS-RT-02 to VS-RT-11)
# ---------------------------------------------------------------------------


@withheld
def test_vs_lr_01_verify_runtime_record_location(env):
    """VS-LR-01 (succeeds VS-RT-02) · REAL MODEL + LIVE SERVER · §3 verify-runtime: with the canonical legacy
    protocol, verify-runtime exits 0, writes
    $SLM_EVAL_RUNS/_verification/<SHA-256 of the protocol file>/runtime-verification.json as JSON, and prints that
    path on stdout."""
    protocol = legacy_protocol()
    seed_cache(env)
    live = verify_runtime(env, protocol)
    if live.exit != EXIT_OK:
        fail(f"verify-runtime exited {live.exit}, expected 0" + live.where())
    record = record_path(env, protocol)
    read_record(record)
    shown = set()
    for line in live.stdout_text().splitlines():
        if line.strip():
            path = Path(line.strip())
            shown.add((path if path.is_absolute() else env.work / path).resolve())
    if record.resolve() not in shown:
        fail("the record path is not printed on stdout" + live.where())


@withheld
def test_vs_lr_02_verification_record_contents(env):
    """VS-LR-02 (succeeds VS-RT-03) · REAL MODEL + LIVE SERVER · §10 verification record + §3 verify-runtime checks:
    runtime-verification.json lists the routes probed, each with a protected/exempt classification and at least two
    HTTP statuses (with and without the key); the list covers /health, /v1/models, /props, /slots and at least one
    generation route; /health and /v1/models are exempt; every protected route shows 401 or 403 (its response
    without the key); every exempt route shows a 2xx status; and every route that shows no 401/403 at all is
    classified exempt. Records of OPTIONS probes are judged by VS-LR-10, not by these rules."""
    protocol = legacy_protocol()
    seed_cache(env)
    live = verify_runtime(env, protocol)
    if live.exit != EXIT_OK:
        fail(f"verify-runtime exited {live.exit}, expected 0" + live.where())
    entries = [e for e in route_entries(read_record(record_path(env, protocol))) if not e["options"]]
    if not entries:
        fail("no route entries (route, protected/exempt, statuses) found in runtime-verification.json")
    by_route = {}
    for entry in entries:
        by_route.setdefault(entry["route"], entry)
    problems = []
    for needed in ("/health", "/v1/models", "/props", "/slots"):
        if needed not in by_route:
            problems.append(f"{needed} not listed")
    if not GENERATION_ROUTES & set(by_route):
        problems.append("no generation route listed")
    for route in EXEMPT:
        if route in by_route and by_route[route]["class"] != "exempt":
            problems.append(f"{route} is not classified exempt")
    for entry in entries:
        refused = any(s in (401, 403) for s in entry["statuses"])
        if len(entry["statuses"]) < 2:
            problems.append(f"{entry['route']}: fewer than two statuses recorded")
        if entry["class"] == "protected" and not refused:
            problems.append(f"{entry['route']}: protected but no 401/403 without the key")
        if entry["class"] == "exempt" and not any(200 <= s < 300 for s in entry["statuses"]):
            problems.append(f"{entry['route']}: exempt but no 2xx status")
        if not refused and entry["class"] != "exempt":
            problems.append(f"{entry['route']}: open without the key but not classified exempt")
    if problems:
        fail("; ".join(problems))


@withheld
def test_vs_lr_03_key_required_except_the_exemption(env):
    """VS-LR-03 (succeeds VS-RT-04) · REAL MODEL + LIVE SERVER · §10 "required on every route except the approved
    exemption ... Nothing else may answer without the key" (data-bearing GET and POST; OPTIONS preflight is
    VS-LR-09): while a smoke run's proven server is up, /health and /v1/models answer 200 without a key on each of
    its proven listening ports, and every other probed route, requested with no key, a wrong bearer key or a wrong
    X-Api-Key, is refused (401/403), absent (404), or is an alias of an exempt handler (a GET whose body matches the
    /health or /v1/models response)."""
    live, ports = start_run_server(env)
    results: dict[tuple, tuple] = {}
    baselines: dict[int, set] = {}
    deadline = time.monotonic() + 240
    pending = {(port, method, path, variant) for port in ports
               for method, paths in (("GET", PROBE_GET), ("POST", PROBE_POST))
               for path in paths for variant in WRONG_KEYS}
    while pending and time.monotonic() < deadline and live.running():
        for port in ports:
            exempt_bodies = set()
            for route in EXEMPT:
                status, body = http(port, "GET", route, {})
                if status != 200:
                    fail(f"{route} on port {port} answered {status} without a key")
                exempt_bodies.add(signature(body))
            baselines[port] = exempt_bodies
        for combo in sorted(pending):
            port, method, path, variant = combo
            status, body = http(port, method, path, WRONG_KEYS[variant])
            if status is None or status == 503:
                continue
            results[combo] = (status, signature(body))
            pending.discard(combo)
        time.sleep(0.5)
    live.interrupt()
    violations = []
    for (port, method, path, variant), (status, sig) in sorted(results.items()):
        if path in EXEMPT or status in (401, 403, 404):
            continue
        if method == "GET" and 200 <= status < 300 and sig in baselines.get(port, set()):
            continue
        violations.append(f"{method} :{port}{path} [{variant}] -> {status}")
    if violations:
        fail("routes answered without a valid key: " + "; ".join(violations))
    if not any(status in (401, 403) for status, _ in results.values()):
        fail(f"no route refused a missing key; {len(pending)} probes never got a definitive answer")


@withheld
def test_vs_lr_04_model_listing_shows_alias_and_no_path(env):
    """VS-LR-04 (succeeds VS-RT-05) · REAL MODEL + LIVE SERVER · §10 "Model alias": /v1/models on each proven
    listening port, read without a key, answers 200 with JSON naming the model qwen3-8b; no string in it, or in the
    /health response, is a file path (starts with / or ~, or contains .gguf, the dataset cache path or the home
    directory)."""
    live, ports = start_run_server(env)
    listings = {port: http(port, "GET", "/v1/models", {}) for port in ports}
    health = {port: http(port, "GET", "/health", {}) for port in ports}
    live.interrupt()
    home, cache = str(Path.home()), str(env.cache.resolve())
    for port in ports:
        status, body = listings[port]
        if status != 200:
            fail(f"/v1/models on port {port} answered {status} without a key")
        try:
            doc = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            fail(f"/v1/models on port {port} did not answer JSON")
        names = [v for _, v in walk(doc) if isinstance(v, str)]
        if MODEL_ALIAS not in names:
            fail(f"model alias {MODEL_ALIAS!r} not listed: {[n[:80] for n in names[:10]]}")
        for route, (_, raw) in (("/v1/models", listings[port]), ("/health", health[port])):
            text = raw.decode("utf-8", "replace")
            strings = names if route == "/v1/models" else re.findall(r'"([^"\\]*)"', text)
            paths = [s for s in strings if s.startswith(("/", "~")) or ".gguf" in s.lower()]
            if paths:
                fail(f"{route} exposes path-like values {[p[:80] for p in paths[:5]]}")
            if cache in text or home in text:
                fail(f"{route} exposes a local directory")


@withheld
def test_vs_lr_05_loopback_only_on_an_ephemeral_port(env):
    """VS-LR-05 (succeeds VS-RT-06) · REAL MODEL + LIVE SERVER · §10 "bound to 127.0.0.1 only, on an ephemeral port":
    every TCP listening socket of the run's proven processes, observed for ten seconds after the server is ready,
    is on 127.0.0.1 and its port lies in the OS ephemeral range."""
    live, _ = start_run_server(env)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and live.running():
        live.observe()
        time.sleep(0.5)
    sockets = set(live.sockets)
    live.interrupt()
    low, high = ephemeral_range()
    wrong_host = sorted({(host, port) for _, host, port in sockets if host != "127.0.0.1"})
    wrong_port = sorted({port for _, _, port in sockets if not low <= port <= high})
    if not sockets:
        fail("no listening socket observed")
    if wrong_host:
        fail(f"sockets not bound to 127.0.0.1: {wrong_host}")
    if wrong_port:
        fail(f"ports outside the ephemeral range {low}-{high}: {wrong_port}")


@withheld
def test_vs_lr_06_key_is_never_written(env):
    """VS-LR-06 (succeeds VS-RT-07) · REAL MODEL + LIVE SERVER + KEY LOOKUP · §10 "generated per run, in memory only
    ... never written to disk, logs, artifacts, or stdout": the proven server is not given a key file
    (--api-key-file); the key read from its arguments or environment (the key lookup) appears in no file under the
    test's cache, run store, working directory or logs (including runtime-verification.json and the CLI's captured
    stdout and stderr). NOT RUN unless SLM_EVAL_TEST_KEY_LOOKUP=1, and NOT RUN if the key value is not
    observable."""
    require_key_lookup()
    live, _ = start_run_server(env)
    live.observe()
    facts = key_facts(env.family, live.server_pids())
    live.interrupt()
    if facts.key_file:
        fail("the key was passed through a file (--api-key-file)")
    if not facts.count():
        not_run("the API key is not observable: " + "; ".join(facts.gaps or ["no key value found"]))
    hits = sorted(str(p) for p in env.root.rglob("*") if facts.found_in(p))
    if hits:
        fail(f"the API key was written to {len(hits)} file(s): {hits[:10]}")


@withheld
def test_vs_lr_07_server_terminated_on_normal_exit(env):
    """VS-LR-07 (succeeds VS-RT-08) · REAL MODEL + LIVE SERVER · §10 "terminated on normal exit": after
    verify-runtime exits 0, no proven process it started is still running and no port its proven processes listened
    on is still listening. NOT RUN if no listening server of it was observed while it ran."""
    protocol = legacy_protocol()
    seed_cache(env)
    live = verify_runtime(env, protocol, poll=0.2)
    if live.exit != EXIT_OK:
        fail(f"verify-runtime exited {live.exit}, expected 0" + live.where())
    if not live.sockets:
        not_run("no listening server of verify-runtime was observed while it ran: " + SERVER_GAP)
    assert_cleaned(env, live, live.ports(), "processes survived verify-runtime",
                   "a port is still listening after verify-runtime exited")


@withheld
def test_vs_lr_08_server_terminated_on_interrupt(env):
    """VS-LR-08 (succeeds VS-RT-09) · REAL MODEL + LIVE SERVER · §10 "terminated ... on interrupt": SIGINT sent to the
    `slm-eval run` process alone makes it exit, after which no proven process of the run is running and no port its
    proven server listened on is still listening."""
    live, ports = start_run_server(env)
    if live.interrupt() is None:
        fail("slm-eval run did not exit after SIGINT")
    assert_cleaned(env, live, ports, "processes survived the interrupt", "a port is still listening after the interrupt")


@withheld
def test_vs_lr_09_preflight_without_key_carries_no_body(env):
    """VS-LR-09 (succeeds VS-RT-10) · REAL MODEL + LIVE SERVER · §10 CORS preflight: "an OPTIONS request on any route
    may be answered without the key, provided the response body is empty. The same route's data-bearing methods must
    still require the key." While a smoke run's proven server is up, every probed route's OPTIONS request with CORS
    preflight headers and no key is refused (401/403), absent (404), or answered with an empty body; and wherever
    OPTIONS is answered, the route's data-bearing method without the key is refused (401/403/404) unless it is
    /health, /v1/models or a GET alias of their handlers. NOT RUN if no route answered OPTIONS without the key."""
    live, ports = start_run_server(env)
    exempt_bodies = {port: {signature(http(port, "GET", route, {})[1]) for route in EXEMPT} for port in ports}
    problems, answered = [], 0
    for port in ports:
        for method, paths in (("GET", PROBE_GET), ("POST", PROBE_POST)):
            for path in paths:
                status, body = http(port, "OPTIONS", path, {**PREFLIGHT_HEADERS, "Access-Control-Request-Method": method})
                if status is None or status in (401, 403, 404):
                    continue
                answered += 1
                if body:
                    problems.append(f"OPTIONS :{port}{path} -> {status} with a {len(body)}-byte body")
                data_status, data_body = http(port, method, path, {})
                if path in EXEMPT or data_status in (401, 403, 404, None, 503):
                    continue
                if method == "GET" and 200 <= data_status < 300 and signature(data_body) in exempt_bodies[port]:
                    continue
                problems.append(f"{method} :{port}{path} -> {data_status} without the key after an open preflight")
    live.interrupt()
    if problems:
        fail("; ".join(problems))
    if not answered:
        not_run("no route answered OPTIONS without the key; nothing further to check")


@withheld
def test_vs_lr_10_record_covers_options_probes(env):
    """VS-LR-10 (succeeds VS-RT-11) · REAL MODEL + LIVE SERVER · §10 verification record: "for each OPTIONS probe,
    records its status and body length, and the result of the same route's data-bearing method without the key".
    runtime-verification.json holds at least one OPTIONS probe record, one of them on a non-exempt route; each has a
    body length and at least two HTTP statuses; on every non-exempt route the data-bearing result is 401 or 403; and
    no OPTIONS probe on a non-exempt route answered with a 2xx shows a non-zero body length. Exempt routes are
    /health, /v1/models and any route the record classifies as exempt (their aliases)."""
    protocol = legacy_protocol()
    seed_cache(env)
    live = verify_runtime(env, protocol)
    if live.exit != EXIT_OK:
        fail(f"verify-runtime exited {live.exit}, expected 0" + live.where())
    record = read_record(record_path(env, protocol))
    exempt = set(EXEMPT) | {e["route"] for e in route_entries(record) if e["class"] == "exempt"}
    entries = options_entries(record)
    if not entries:
        fail("no OPTIONS probe records found in runtime-verification.json")
    if not any(e["route"] not in exempt for e in entries):
        fail("no OPTIONS probe on a non-exempt route")
    problems = []
    for entry in entries:
        if not entry["lengths"]:
            problems.append(f"{entry['route']}: OPTIONS record has no body length")
        if len(entry["statuses"]) < 2:
            problems.append(f"{entry['route']}: OPTIONS record lacks the data-bearing method's status")
        if entry["route"] not in exempt and not any(s in (401, 403) for s in entry["statuses"]):
            problems.append(f"{entry['route']}: data-bearing method not refused without the key")
        if entry["route"] not in exempt and any(200 <= s < 300 for s in entry["own"]) \
                and any(n > 0 for n in entry["lengths"]):
            problems.append(f"{entry['route']}: OPTIONS answered with a non-empty body")
    if problems:
        fail("; ".join(problems))


# ---------------------------------------------------------------------------
# tests: §3 verification, §10 security, §6 runs, resume and §5.1 tiers (successors of HO-RT-01 to HO-RT-14)
# ---------------------------------------------------------------------------


@withheld
def test_vs_lr_11_verify_artifact_passes(env):
    """VS-LR-11 (succeeds HO-RT-01) · REAL MODEL ARTIFACT · §3 verify-artifact: with the canonical legacy protocol it
    exits 0 and writes $SLM_EVAL_RUNS/_verification/<protocol_sha256>/artifact-verification.json, which records the
    protocol's model SHA-256."""
    protocol = legacy_protocol()
    seed_cache(env)
    done = env.finish("verify-artifact", "--protocol", protocol, timeout=VERIFY_TIMEOUT, poll=0.5)
    if done.exit != EXIT_OK:
        fail(f"verify-artifact exited {done.exit}, expected 0" + done.where())
    record = verification_record(env, sha256_file(protocol), "artifact-verification.json")
    model_sha = dig(protocol_data(protocol), "model", "artifact", "sha256")
    if model_sha not in text_of(read_record(record)):
        fail("artifact-verification.json does not record the model sha256")


@withheld
def test_vs_lr_12_verify_artifact_hash_mismatch_exits_2(env):
    """VS-LR-12 (succeeds HO-RT-02) · REAL MODEL ARTIFACT · §3 verify-artifact "exit 2 on mismatch": a copy of the
    canonical legacy protocol whose model.artifact.sha256 is changed (nothing else) exits 2."""
    protocol = legacy_protocol()
    seed_cache(env)
    raw = protocol.read_bytes()
    model_sha = dig(protocol_data(protocol), "model", "artifact", "sha256")
    text = raw.decode("utf-8")
    if text.count(model_sha) != 1:
        fail("model sha256 does not occur exactly once in the protocol text")
    tampered = env.work / protocol.name
    tampered.write_text(text.replace(model_sha, "0" * 64), encoding="utf-8")
    done = env.finish("verify-artifact", "--protocol", tampered, timeout=VERIFY_TIMEOUT, poll=0.5)
    if done.exit != EXIT_INTEGRITY:
        fail(f"verify-artifact exited {done.exit}, expected 2" + done.where())


@withheld
def test_vs_lr_13_verify_runtime_passes_and_records_checks(env):
    """VS-LR-13 (succeeds HO-RT-03) · REAL RUNTIME · §3 verify-runtime + §10: it exits 0 and writes
    $SLM_EVAL_RUNS/_verification/<protocol_sha256>/runtime-verification.json covering each listed check: key
    enforcement, loopback-only bind, ephemeral port, cleanup, raw-capture fidelity and parameter equivalence."""
    protocol = legacy_protocol()
    seed_cache(env)
    done = env.finish("verify-runtime", "--protocol", protocol, timeout=VERIFY_TIMEOUT)
    if done.exit != EXIT_OK:
        fail(f"verify-runtime exited {done.exit}, expected 0" + done.where())
    record = verification_record(env, sha256_file(protocol), "runtime-verification.json")
    text = text_of(read_record(record)).lower()
    groups = {
        "key enforcement": ("key", "auth"),
        "loopback-only bind": ("loopback", "127.0.0.1", "bind"),
        "ephemeral port": ("ephemeral", "port"),
        "cleanup": ("cleanup", "clean_up", "terminat", "shutdown"),
        "raw-capture fidelity": ("raw", "fidelity", "capture"),
        "parameter equivalence": ("param", "equivalen"),
    }
    absent = [name for name, needles in groups.items() if not any(n in text for n in needles)]
    if absent:
        fail(f"runtime-verification.json does not cover: {absent}")


@withheld
def test_vs_lr_14_loopback_only_on_ephemeral_port(env):
    """VS-LR-14 (succeeds HO-RT-05) · REAL RUNTIME · §10 "bound to 127.0.0.1 only, on an ephemeral port": every
    listening TCP socket of the run's proven processes is on 127.0.0.1 and its port lies in the OS ephemeral
    range."""
    protocol, manifest = prepared(env)
    live, _ = start_server(env, protocol, manifest)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and live.running():
        live.observe()
        time.sleep(0.5)
    interrupt_and_wait(live)
    low, high = ephemeral_range()
    not_loopback = sorted(f"{pid}:{port} on {host}" for pid, host, port in live.sockets if host != "127.0.0.1")
    not_ephemeral = sorted({port for _, _, port in live.sockets if not low <= port <= high})
    if not_loopback:
        fail(f"sockets not bound to 127.0.0.1 only: {not_loopback}")
    if not_ephemeral:
        fail(f"ports outside the ephemeral range [{low}, {high}]: {not_ephemeral}")


@withheld
def test_vs_lr_15_api_key_never_written_or_printed(env):
    """VS-LR-15 (succeeds HO-RT-06) · REAL RUNTIME + KEY LOOKUP · §10 "never written to disk, logs, artifacts, or
    stdout": the proven server receives its key in memory (no --api-key-file); the key read from its arguments or
    environment (the key lookup), after the first completed item, appears in no file of the cache, run store,
    working directory or logs, in no repository file written during the test, and in neither stdout nor stderr.
    NOT RUN unless SLM_EVAL_TEST_KEY_LOOKUP=1, and NOT RUN if the key value is not observable."""
    require_key_lookup()
    protocol, manifest = prepared(env)
    since = time.time()
    live, _ = start_server(env, protocol, manifest)
    wait_for_items(env, live, 1, ITEM_WAIT)
    live.observe()
    facts = key_facts(env.family, live.server_pids())
    interrupt_and_wait(live)
    if facts.key_file:
        fail("the server was given its key through a file (--api-key-file)")
    if not facts.count():
        not_run("the API key value is not observable: " + "; ".join(facts.gaps or ["no key value found"]))
    hits = [str(p) for p in env.root.rglob("*") if facts.found_in(p)]
    repo = repo_root()
    for path in iter_files(repo) if repo is not None else []:
        try:
            if path.stat().st_mtime >= since - 1 and facts.found_in(path):
                hits.append(str(path))
        except OSError:
            continue
    if hits:
        fail(f"the API key was written to {len(hits)} file(s): {sorted(hits)[:10]}")


@withheld
def test_vs_lr_16_server_cleaned_up_on_normal_exit(env):
    """VS-LR-16 (succeeds HO-RT-07) · REAL RUNTIME · §10 "terminated on normal exit": after verify-runtime exits 0, no
    proven process it started survives and no port its proven processes listened on is still listening. NOT RUN if
    no listening server of it was observed."""
    protocol = legacy_protocol()
    seed_cache(env)
    live = env.launch("verify-runtime", "--protocol", protocol)
    code = live.wait(VERIFY_TIMEOUT, 0.25)
    if code != EXIT_OK:
        fail(f"verify-runtime exited {code}, expected 0" + live.where())
    if not live.sockets:
        not_run("no listening server of verify-runtime was observed: " + SERVER_GAP)
    assert_cleaned(env, live, live.ports(), "runtime processes survived the CLI", "ports still listening after the CLI exited")


@withheld
def test_vs_lr_17_server_cleaned_up_on_interrupt(env):
    """VS-LR-17 (succeeds HO-RT-08) · REAL RUNTIME · §10 "terminated ... on interrupt": SIGINT to the `slm-eval run`
    process alone makes it exit and leaves no surviving proven runtime process and no listening port of its proven
    server."""
    protocol, manifest = prepared(env)
    live, sockets = start_server(env, protocol, manifest)
    interrupt_and_wait(live)
    assert_cleaned(env, live, sorted({port for _, _, port in sockets}), "runtime processes survived the CLI",
                   "ports still listening after the CLI exited")


@withheld
def test_vs_lr_18_run_evidence_is_complete(env):
    """VS-LR-18 (succeeds HO-RT-09) · REAL MODEL AND RUNTIME · §3 run, §6.1 run evidence, §8/§9, §10 raw output: a
    smoke run with --k 2 --seed-base 7100 exits 0 and prints its run ID; the run directory holds byte copies of the
    protocol and manifest whose SHA-256 values are protocol_sha256 and manifest_sha256; run.json records the legacy
    §6.1 fields with identities equal to the protocol's (version, model hash, runtime build, extractor, both scorers
    with pins), k, seeds [7100, 7101], one completed attempt and status completed; both verification files are
    copied in; items.jsonl has one complete line per smoke item x sample, with consistent hashes, seed = seed_base +
    sample_index, the upstream reference answer, raw output containing the think block whenever thinking is
    reported, thinking reported whenever the raw output holds non-empty thinking text (§8), and None answers scored
    incorrect."""
    protocol, manifest = prepared(env)
    pdata = protocol_data(protocol)
    done = env.finish(*run_args(protocol, manifest, k=2, seed_base=7100), timeout=run_timeout())
    if done.exit != EXIT_OK:
        fail(f"run exited {done.exit}, expected 0" + done.where())
    run_id = run_id_from(done, env)
    run_dir = env.runs / run_id
    run = read_record(run_dir / "run.json")
    problems = []
    for key in ("run_id", "protocol_version", "protocol_sha256", "manifest_sha256", "tier", "k", "seeds",
                "model_artifact_sha256", "runtime_build", "adapter", "extractor", "scorers", "executor",
                "host", "attempts", "status"):
        if key not in run:
            problems.append(f"run.json lacks {key}")
    expect = {
        "run_id": run_id,
        "protocol_version": pdata.get("protocol_version"),
        "manifest_sha256": sha256_file(manifest),
        "tier": "smoke",
        "k": 2,
        "seeds": [7100, 7101],
        "model_artifact_sha256": dig(pdata, "model", "artifact", "sha256"),
        "runtime_build": dig(pdata, "runtime", "build"),
        "status": "completed",
    }
    problems += [f"run.json {k} = {run.get(k)!r}, expected {v!r}" for k, v in expect.items() if run.get(k) != v]
    if run.get("protocol_sha256") != sha256_file(protocol):
        problems.append("protocol_sha256 is not the SHA-256 of the protocol file")
    for name, source, key in (("protocol.yaml", protocol, "protocol_sha256"), ("manifest.json", manifest, "manifest_sha256")):
        copy_path = run_dir / name
        if not copy_path.is_file() or copy_path.read_bytes() != source.read_bytes():
            problems.append(f"{name} is not a byte copy of {source.name}")
        elif sha256_file(copy_path) != run.get(key):
            problems.append(f"{name} hash differs from run.json {key}")
    adapter = run.get("adapter") or {}
    if not (isinstance(adapter.get("id"), str) and adapter.get("id") and isinstance(adapter.get("version"), str)):
        problems.append(f"adapter {adapter!r} lacks id/version")
    extraction = pdata.get("extraction") or {}
    if run.get("extractor") != {"id": extraction.get("id"), "version": extraction.get("version")}:
        problems.append(f"extractor {run.get('extractor')!r} differs from the protocol's extraction")
    recorded = {(s.get("id"), s.get("pin")) for s in run.get("scorers") or [] if isinstance(s, dict)}
    for role in ("primary", "secondary"):
        entry = dig(pdata, "scorers", role) or {}
        if (entry.get("id"), entry.get("pin")) not in recorded:
            problems.append(f"scorers lack the protocol's {role} scorer {entry!r}")
    if not isinstance(run.get("executor"), str):
        problems.append("executor is not a string")
    host = run.get("host") or {}
    if not (isinstance(host.get("os"), str) and isinstance(host.get("arch"), str) and is_number(host.get("memory_gb"))):
        problems.append(f"host {host!r} lacks os/arch/memory_gb")
    attempts = run.get("attempts") or []
    if len(attempts) != 1 or attempts[0].get("status") != "completed" or attempts[0].get("resumed_from") is not None \
            or not all(k in attempts[0] for k in ("n", "started", "ended")):
        problems.append(f"attempts {attempts!r} is not one completed fresh attempt")
    for name in ("runtime-verification.json", "artifact-verification.json"):
        try:
            read_json(run_dir / name)
        except (OSError, ValueError):
            problems.append(f"{name} is not copied into the run directory as JSON")
    if problems:
        fail("; ".join(problems))

    smoke = smoke_ids(manifest)
    records = read_jsonl(run_dir / "items.jsonl")
    pairs = [(r.get("item_id"), r.get("sample_index")) for r in records]
    if len(records) != 2 * len(smoke):
        fail(f"{len(records)} lines for {len(smoke)} smoke items x 2")
    if len(set(pairs)) != len(pairs):
        fail("duplicate item x sample lines")
    if {r.get("item_id") for r in records} != set(smoke):
        fail("items.jsonl item set differs from the smoke tier")
    upstream = records_by_id(cached_records(env.cache))
    max_tokens = dig(pdata, "sampling", "max_output_tokens", "value")
    fields = ("item_id", "sample_index", "seed", "prompt_sha256", "raw_output", "raw_output_sha256",
              "thinking_present", "truncated", "finish_reason", "tokens_generated", "reference_answer",
              "reference_sha256", "extraction", "verdicts", "timing_ms")
    bad = []
    for r in records:
        where = f"{r.get('item_id')}#{r.get('sample_index')}"
        missing = [f for f in fields if f not in r]
        if missing:
            bad.append(f"{where}: missing {missing}")
            continue
        raw, ref = r["raw_output"], r["reference_answer"]
        extraction, verdicts = r["extraction"] or {}, r["verdicts"] or {}
        text = raw if isinstance(raw, str) else ""
        checks = [
            (r["sample_index"] in (0, 1), "sample_index not in 0..1"),
            (r["seed"] == 7100 + r["sample_index"], "seed != seed_base + sample_index"),
            (is_hex64(r["prompt_sha256"]), "prompt_sha256 not hex"),
            (isinstance(raw, str) and r["raw_output_sha256"] == sha256_text(raw), "raw_output_sha256 mismatch"),
            (isinstance(ref, str) and r["reference_sha256"] == sha256_text(ref), "reference_sha256 mismatch"),
            (r["item_id"] not in upstream or upstream[r["item_id"]].get("answer") == ref,
             "reference_answer differs from the upstream answer"),
            (set(extraction) >= {"answer", "rule", "status"}, "extraction lacks answer/rule/status"),
            (extraction.get("status") in ("ok", "fallback", "no_answer", "truncated"), "extraction.status invalid"),
            (isinstance(verdicts.get("primary"), bool) and isinstance(verdicts.get("secondary"), bool),
             "verdicts not booleans"),
            (extraction.get("answer") is not None or not (verdicts.get("primary") or verdicts.get("secondary")),
             "answer None scored correct"),
            (isinstance(r["thinking_present"], bool) and isinstance(r["truncated"], bool),
             "thinking_present/truncated not booleans"),
            ("</think>" not in text or r["thinking_present"] is True or not thinking_text_present(text),
             "think block present but thinking_present false"),
            (not r["thinking_present"] or r["truncated"] or "</think>" in text,
             "thinking reported but raw output lacks </think> (reasoning parsed away?)"),
            (isinstance(r["finish_reason"], str), "finish_reason not a string"),
            (is_int(r["tokens_generated"]) and 0 <= r["tokens_generated"]
             and (not is_int(max_tokens) or r["tokens_generated"] <= max_tokens), "tokens_generated out of range"),
            (is_number(r["timing_ms"]) and r["timing_ms"] >= 0, "timing_ms invalid"),
        ]
        bad += [f"{where}: {message}" for ok, message in checks if not ok]
    if bad:
        fail("; ".join(bad[:20]))


@withheld
def test_vs_lr_19_real_run_rescores_exactly_and_reports_aggregates(env):
    """VS-LR-19 (succeeds HO-RT-10) · REAL MODEL AND RUNTIME · §3 rescore `all_match`, §6.2, §6.4, §7 aggregates only:
    rescoring a completed k=1 smoke run exits 0 with a §6.4 rescore.json (all_match true, a matching record per item
    x sample), leaving the run's files untouched; its report (in the run directory) carries the primary accuracy and
    Wilson interval, the disagreement and truncation counts computed from items.jsonl, and no raw output, problem
    text or long reference answer."""
    protocol, manifest = prepared(env)
    done = env.finish(*run_args(protocol, manifest), timeout=run_timeout())
    if done.exit != EXIT_OK:
        fail(f"run exited {done.exit}, expected 0" + done.where())
    run_id = run_id_from(done, env)
    run_dir = env.runs / run_id
    before = file_bytes(run_dir)
    rescore = env.finish("rescore", "--run", run_id, timeout=3600, poll=0.5)
    if rescore.exit != EXIT_OK:
        fail(f"rescore exited {rescore.exit}, expected 0" + rescore.where())
    doc = read_record(run_dir / "rescore.json")
    records = read_jsonl(run_dir / "items.jsonl")
    problems = rescore_problems(doc, run_id, {(r["item_id"], r["sample_index"]) for r in records})
    if problems:
        fail("rescore.json violates §6.4: " + "; ".join(problems[:10]))
    if doc["all_match"] is not True:
        fail("rescore of a real run is not all_match")
    after = file_bytes(run_dir)
    changed = [name for name, data in before.items() if after.get(name) != data]
    if changed:
        fail(f"rescore modified original run files {changed}")

    report, md = run_report(env, run_id)
    n = len(records)
    primary = sum(1 for r in records if r["verdicts"]["primary"])
    disagreements = sum(1 for r in records if r["verdicts"]["primary"] != r["verdicts"]["secondary"])
    truncations = sum(1 for r in records if r["truncated"])
    if not has_close(numbers(report), primary / n):
        fail("primary accuracy not in report.json")
    lo, hi = wilson(primary, n)
    if not has_interval(report, lo, hi):
        fail("primary Wilson interval not in report.json")
    for needle, count in (("disagree", disagreements), ("truncat", truncations)):
        values = numbers_under(report, needle)
        if not (any(v == count for v in values) or has_close(values, count / n)):
            fail(f"{needle} count {count} not reported")
    blob = json.dumps(report, ensure_ascii=False) + "\n" + md
    raw_leaks = [r["item_id"] for r in records if len(r["raw_output"]) >= 80 and r["raw_output"][:80] in blob]
    ref_leaks = [r["item_id"] for r in records if len(r["reference_answer"]) >= 12 and r["reference_answer"] in blob]
    problem_leaks = leaked_problems([blob], cached_records(env.cache))
    if raw_leaks:
        fail(f"raw output text in the report for {raw_leaks[:5]}")
    if ref_leaks:
        fail(f"reference answers in the report for {ref_leaks[:5]}")
    if problem_leaks:
        fail(f"{problem_leaks} problem texts in the report")


@withheld
def test_vs_lr_20_resume_generates_only_missing_pairs(env):
    """VS-LR-20 (succeeds HO-RT-11) · REAL MODEL AND RUNTIME · §6.3 resume and §6.1 attempts: a smoke run interrupted
    after its first completed line records an interrupted attempt; `run --resume` exits 0, keeps every line completed
    before the interrupt unchanged, adds only the missing pairs (no duplicates, one line per smoke item), appends a
    second, completed attempt that records what it resumed from, and ends with status completed. NOT RUN if the run
    finished before the interrupt landed."""
    protocol, manifest = prepared(env)
    live = env.launch(*run_args(protocol, manifest))
    run_dir = wait_for_items(env, live, 1, run_timeout())
    if run_dir is None:
        fail("no completed items.jsonl line observed" + live.where())
    interrupt_and_wait(live)
    run = read_record(run_dir / "run.json")
    if run.get("status") == "completed":
        not_run("the run completed before SIGINT was delivered")
    attempts = run.get("attempts") or []
    if not (attempts and attempts[-1].get("status") == "interrupted"):
        fail(f"attempts after SIGINT: {attempts!r}")
    done = {(r["item_id"], r["sample_index"]): r for r in complete_jsonl_lines(run_dir / "items.jsonl")}
    resumed = env.finish("run", "--resume", run_dir.name, timeout=run_timeout())
    if resumed.exit != EXIT_OK:
        fail(f"run --resume exited {resumed.exit}, expected 0" + resumed.where())
    records = read_jsonl(run_dir / "items.jsonl")
    pairs = [(r["item_id"], r["sample_index"]) for r in records]
    if len(pairs) != len(set(pairs)):
        fail("resume duplicated item x sample lines")
    smoke = smoke_ids(manifest)
    if not ({i for i, _ in pairs} == set(smoke) and len(pairs) == len(smoke)):
        fail("resumed run does not hold exactly one line per smoke item")
    current = dict(zip(pairs, records))
    regenerated = [pair for pair, record in done.items() if current.get(pair) != record]
    if regenerated:
        fail(f"pairs completed before the interrupt were regenerated or altered: {regenerated[:5]}")
    run = read_record(run_dir / "run.json")
    attempts = run.get("attempts") or []
    if len(attempts) != 2:
        fail(f"expected two attempt records, found {attempts!r}")
    if not (attempts[0].get("status") == "interrupted" and attempts[1].get("status") == "completed"):
        fail(f"attempt statuses {[a.get('status') for a in attempts]!r}, expected ['interrupted', 'completed']")
    if attempts[1].get("resumed_from") is None:
        fail("the resumed attempt does not record resumed_from")
    if run.get("status") != "completed":
        fail(f"run status {run.get('status')!r} after resume")


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("protocol_sha256", "0" * 64),
        ("manifest_sha256", "0" * 64),
        ("model_artifact_sha256", "0" * 64),
        ("runtime_build", "b0-holdout"),
    ],
    ids=["VS-LR-21-protocol", "VS-LR-22-manifest", "VS-LR-23-model", "VS-LR-24-runtime"],
)
@withheld
def test_vs_lr_21_to_24_resume_refused_when_identity_differs(env, field, replacement):
    """VS-LR-21 to VS-LR-24 (succeed HO-RT-12a to HO-RT-12d, one per parameter) · REAL MODEL AND RUNTIME · §6.3
    "Resume is allowed only when the protocol, manifest, model, and runtime build are all identical to the original
    run. Any difference exits 2 and generates nothing" (and §6.1 copy hashes): after an interrupted smoke run, its
    recorded protocol / manifest / model hash or runtime build is changed in run.json; `run --resume` exits 2 and
    items.jsonl is unchanged. NOT RUN if the run finished before the interrupt landed."""
    protocol, manifest = prepared(env)
    live = env.launch(*run_args(protocol, manifest))
    run_dir = wait_for_run_dir(env, live, STARTUP)
    if run_dir is None:
        fail("no run directory appeared" + live.where())
    wait_ports(live, STARTUP)
    interrupt_and_wait(live)
    run_json = run_dir / "run.json"
    run = read_record(run_json)
    if run.get("status") == "completed":
        not_run("the run completed before SIGINT was delivered")
    if field not in run:
        fail(f"run.json lacks {field}")
    run[field] = replacement
    write_json(run_json, run)
    items = run_dir / "items.jsonl"
    before = items.read_bytes() if items.exists() else None
    resumed = env.finish("run", "--resume", run_dir.name, timeout=STARTUP)
    if resumed.exit != EXIT_INTEGRITY:
        fail(f"run --resume exited {resumed.exit}, expected 2" + resumed.where())
    after = items.read_bytes() if items.exists() else None
    if after != before:
        fail("resume generated output although identities differ")


@withheld
def test_vs_lr_25_used_tier_is_immutable(env):
    """VS-LR-25 (succeeds HO-RT-13) · REAL MODEL AND RUNTIME AND THE PINNED DATASET · §5.1 "A tier is immutable once it
    is used" + §3 apply-verdicts "the result would change a tier that any existing run has already used: exits 3",
    failing without changing anything: once a run has started on a freshly built manifest's smoke tier, verdicts
    excluding every flagged pair either exit 3 leaving the manifest byte-identical, or exit 0 leaving the smoke tier
    unchanged. NOT RUN if no pair is flagged."""
    protocol = legacy_protocol()
    seed_cache(env)
    manifest = env.work / "manifest.json"
    build = env.finish("data", "build-manifest", "--out", manifest, timeout=1800, poll=0.5)
    if build.exit != EXIT_OK:
        fail(f"data build-manifest exited {build.exit}, expected 0" + build.where())
    flagged = find_flagged_file(env.cache)
    if flagged is None:
        fail("no flagged file in the dataset cache")
    pairs = read_json(flagged).get("pairs") or []
    if not pairs:
        not_run("the pinned dataset produced no flagged pairs")
    for command in (("verify-artifact", "--protocol", protocol), ("verify-runtime", "--protocol", protocol)):
        done = env.finish(*command, timeout=VERIFY_TIMEOUT)
        if done.exit != EXIT_OK:
            fail(f"{command[0]} failed before run: exit {done.exit}" + done.where())
    live = env.launch(*run_args(protocol, manifest))
    run_dir = wait_for_run_dir(env, live, STARTUP)
    if run_dir is None:
        fail("no run directory appeared" + live.where())
    interrupt_and_wait(live)
    original = manifest.read_bytes()
    exclude_all = {str(p.get("id")): "exclude" for p in pairs}
    verdicts = write_json(env.root / "outside" / "verdicts.json", {
        "verdicts_version": "1", "flagged_sha256": sha256_file(flagged),
        "verdicts": [{"pair": p["pair"], "verdict": exclude_all[str(p.get("id"))]} for p in pairs]})
    applied = env.finish("data", "apply-verdicts", "--manifest", manifest, "--verdicts", verdicts, timeout=1800, poll=0.5)
    if applied.exit == EXIT_PRECONDITION:
        if manifest.read_bytes() != original:
            fail("apply-verdicts exited 3 but changed the manifest")
    else:
        if applied.exit != EXIT_OK:
            fail(f"apply-verdicts exited {applied.exit}, expected 3 or 0" + applied.where())
        smoke_before = json.loads(original.decode("utf-8"))["tiers"]["smoke"]
        if read_json(manifest)["tiers"]["smoke"] != smoke_before:
            fail("a used smoke tier was changed (expected exit 3)")


@withheld
def test_vs_lr_26_run_with_mismatched_manifest_source_exits_2(env):
    """VS-LR-26 (succeeds HO-RT-14) · REAL MODEL AND RUNTIME · §3 run "Exits 2 if the manifest source differs from the
    protocol's dataset.source": with both verification records present, a copy of the committed manifest whose
    source pins a different file hash than the protocol makes `run` exit 2."""
    protocol, manifest = prepared(env)
    doc = read_json(manifest)
    doc["source"]["files"][0]["sha256"] = "0" * 64
    tampered = write_json(env.work / "manifest.json", doc)
    done = env.finish(*run_args(protocol, tampered), timeout=STARTUP)
    if done.exit != EXIT_INTEGRITY:
        fail(f"run exited {done.exit}, expected 2" + done.where())
