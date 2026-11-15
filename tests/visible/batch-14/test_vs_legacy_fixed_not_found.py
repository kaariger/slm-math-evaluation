"""Visible specification tests — the §10 fixed not-found exception in the legacy runtime-security regressions of the canonical legacy Qwen protocol, acting only on processes proven owned through the test's own launch handles (contract v0.8.1 §3, §10).

Contract-only: written from the approved interface, the maintainer's readings
and the registered legacy test sources (read as text; never imported or run),
with no implementation code seen. Tests use only the `slm-eval` CLI, the
processes it starts, the server it starts and the contract's files.

VS-LR-27, VS-LR-28, VS-LR-29 and VS-LR-30 succeed VS-LR-02, VS-LR-03,
VS-LR-09 and VS-LR-10 (batch 13), which carry the original capabilities
VS-RT-03, VS-RT-04, VS-RT-10 and VS-RT-11. They keep those predicates and
apply the exception that contract v0.6 added to §10 and v0.8.1 keeps: "an
unauthenticated request that receives a fixed, data-free not-found response
(for example a static 404 or "File Not Found" body that is identical for every
such request) is not answering. The verification must probe /, /index.html and
every web-UI path the pinned build serves, and record each status and body."

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
                              (LEGACY_MANIFEST_SHA256); VS-LR-28 and VS-LR-29
  tools                       `ps`, `pgrep` and `lsof`; os.waitid with WNOWAIT

These are regressions of the legacy Qwen path only. They never retarget the
legacy protocol to a fixture or another model. No key is looked up: no
process's arguments or environment are read.

Fixed not-found responses, live (VS-LR-28, VS-LR-29). A response to a request
without a valid key is a fixed, data-free not-found response when its body is
data-free (below) and either its status is 404, or its body says "not found"
(any case; also not_found, not-found, notfound) and its status and body bytes
equal the server's replies to the same request on two never-served paths
(/vs-lr-absent-<random>), which are themselves identical. Such a response is
not answering: it stands where the predecessors required a refusal (401/403),
and an OPTIONS request answered this way counts as not answered. A 404 whose
body is not data-free is a violation; a response of another status is never
taken for a not-found response because of its status and body equality alone.

Data-free means, for a body decoded as UTF-8: it does not contain the model
alias, ".gguf", the test's dataset cache, run store, scratch or working
directory, the home directory or the probe request's prompt; it has no
path-like string (a JSON string, or a double-quoted string in other text,
starting with / or ~ and longer than one character, other than the requested
route itself); and, when it is JSON, no key naming a data surface (a key
containing path, file, prompt, output, slot, propert, props, generation,
model, content, choice, token, completion, embedding, template or ctx, unless
its value is the requested route) and no number other than an HTTP status code
under a status or code key. These checks bound the judgment; they do not prove
that a body carries no data.

Fixed not-found responses, records (VS-LR-27, VS-LR-30). runtime-verification.json
is authoritative. A response body counts as recorded when the record gives it
as text: a string, or a JSON value, under a key whose name contains "body" and
not sha, hash, digest, length, len, size, bytes, encoding, type, format or mime.
A route record without a 401/403 shows a fixed, data-free not-found response
when, leaving aside any nested `options` record, it records one status for
all its responses, at least one body, all bodies the same, that body is
data-free and either the status is 404 or the body says "not found", and
every route record showing a not-found response with that status and method
records that same body (identical for every such request). Such a route must
still be classified protected: the approved exemption names only /health,
/v1/models and aliases of those two handlers. Values under body keys are never
taken for route names, classifications, methods or statuses.

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
the test's own files and the listening sockets of proven PIDs (`lsof -p`).
Every identity is re-read immediately before a signal; a changed or missing
identity means the PID no longer names that process, and nothing is sent.

Child environment. The CLI receives only PATH, HOME, USER, LOGNAME, LANG,
LC_ALL, LC_CTYPE, TZ and TMPDIR from the test's environment, any further names
listed in SLM_EVAL_TEST_PASS_ENV (a name that looks like a credential, or an
SLM_EVAL_ name, is refused: NOT RUN), and the test's own SLM_EVAL_CACHE,
SLM_EVAL_RUNS and SLM_EVAL_SCRATCH. Process tools run with only PATH, TZ,
HOME and the C locale.

Diagnostics. Failures name files, ports, routes, methods, statuses, counts and
fixed reasons; route names taken from a record are cut to 80 characters. No
response body, record content, generation or CLI output is printed: the CLI's
stdout and stderr stay in named files, and an unexpected error becomes a
failure naming only its type, a safe detail and the line.

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

Timing: model start-up is awaited for up to STARTUP seconds and a verification
for up to VERIFY_TIMEOUT seconds. No speed is required.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split);
SLM_EVAL_TEST_REPO names the repository root (default: the nearest ancestor of
the working directory whose pyproject.toml mentions slm-eval). No
SLM_EVAL_TEST_* variable is passed to the implementation.
"""

from __future__ import annotations

import functools
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
import urllib.error
import urllib.request
import uuid
import warnings
from pathlib import Path
from typing import NamedTuple

import pytest

EXIT_OK = 0
STARTUP = 1800.0
VERIFY_TIMEOUT = 3600.0
INTERRUPT_TIMEOUT = 300.0
TEARDOWN_GRACE = (5.0, 10.0)
LARGE_FILE = 256 * 1024 * 1024
LEGACY_PROTOCOL_SHA256 = "ce48a2448c61af2839e62d63137b06d96ca7bc1b81ce68809adf2af36da8f0ee"
LEGACY_MANIFEST_SHA256 = "a10c02e7ac60b563a4d112cad92c2540ce58d6c0a49fa73f844091df66e48c71"
SEARCH_SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}

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

# §10 fixed not-found exception (contract v0.6, kept in v0.8.1)
ROOT_ROUTES = ("/", "/index.html")
ABSENT_PREFIX = "/vs-lr-absent-"
ABSENT_PROBES = 2
NOT_FOUND = re.compile(r"not[\s_-]*found", re.IGNORECASE)
PROBE_ECHOES = ("2+2=",)
BODY_META_WORDS = ("sha", "hash", "digest", "length", "len", "size", "bytes", "encoding", "type", "format", "mime")
DATA_KEY_WORDS = ("path", "file", "prompt", "output", "slot", "propert", "props", "generation", "model", "content",
                  "choice", "token", "completion", "embedding", "template", "ctx")
NUMBER_KEY_WORDS = ("status", "code")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
SHOWN = 80

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
    """Fail without a traceback or assertion introspection, which could show record contents or response bodies."""
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
            "so that no record content or response body reaches the diagnostics")


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


def shown(route: str) -> str:
    """A route name taken from a record, cut for diagnostics."""
    return route if len(route) <= SHOWN else route[:SHOWN] + "..."


# ---------------------------------------------------------------------------
# process identities
# ---------------------------------------------------------------------------

_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_TOOL_ENV = {"PATH": _SYSTEM_PATH, "LC_ALL": "C", "LANG": "C"}
for _name in ("TZ", "HOME"):
    if os.environ.get(_name):
        _TOOL_ENV[_name] = os.environ[_name]
_TOOLS = {name: shutil.which(name, path=os.pathsep.join(filter(None, (os.environ.get("PATH"), _SYSTEM_PATH))))
          for name in ("ps", "pgrep", "lsof")}


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


def walk(obj, path=()):
    yield path, obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk(value, path + (str(key),))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk(value, path + (index,))


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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


def absent_paths() -> list[str]:
    """Paths no server serves, for the server's own not-found reply."""
    return [ABSENT_PREFIX + uuid.uuid4().hex[:16] for _ in range(ABSENT_PROBES)]


# ---------------------------------------------------------------------------
# the §10 fixed not-found exception
# ---------------------------------------------------------------------------


def data_free_problems(body: bytes, route: str, env: Env) -> list[str]:
    """Why a response body is not data-free (see the module docstring); empty when no check finds data. Reasons
    are fixed phrases: no body content is returned."""
    text = body.decode("utf-8", "replace")
    lower = text.lower()
    reasons = []
    if MODEL_ALIAS in lower:
        reasons.append("it names the model alias")
    if ".gguf" in lower:
        reasons.append("it names a model file")
    for label, directory in (("dataset cache", env.cache), ("run store", env.runs), ("scratch location", env.scratch),
                             ("working directory", env.work), ("home directory", Path.home())):
        if any(len(name) > 1 and name in text for name in {str(directory), str(directory.resolve())}):
            reasons.append(f"it names the {label}")
    if any(echo in text for echo in PROBE_ECHOES):
        reasons.append("it echoes the probe request")
    echo = lambda s: s == route or s.startswith(route + " ")
    try:
        doc = json.loads(text)
    except ValueError:
        strings = re.findall(r'"([^"\\]*)"', text)
    else:
        strings = [v for _, v in walk(doc) if isinstance(v, str)]
        for path, value in walk(doc):
            key = path[-1] if path and isinstance(path[-1], str) else None
            if key is not None and any(w in key.lower() for w in DATA_KEY_WORDS) \
                    and not (isinstance(value, str) and echo(value)):
                reasons.append("it has a key naming a data surface")
                break
        for path, value in walk(doc):
            key = path[-1] if path and isinstance(path[-1], str) else ""
            if is_number(value) and not (is_int(value) and 100 <= value <= 599
                                         and any(w in key.lower() for w in NUMBER_KEY_WORDS)):
                reasons.append("it carries a number that is not an HTTP status code")
                break
    if any(len(s) > 1 and s.startswith(("/", "~")) and not echo(s) for s in strings):
        reasons.append("it contains a path-like string")
    return reasons


def not_found_problem(status: int, body: bytes, absent: list, route: str, env: Env) -> str | None:
    """None when a live response to a request without a valid key is a fixed, data-free not-found response; else
    the reason. `absent` holds the server's (status, body) replies to the same request on never-served paths."""
    reasons = data_free_problems(body, route, env)
    if reasons:
        return "its body is not data-free (" + "; ".join(reasons) + ")"
    if status == 404:
        return None
    if not NOT_FOUND.search(body.decode("utf-8", "replace")):
        return "it is neither a 404 nor a body that says not found"
    replies = [reply for reply in absent if reply[0] is not None and reply[0] != 503]
    if len(replies) < ABSENT_PROBES:
        return "the server's reply to never-served paths was not obtained"
    if len(set(replies)) != 1:
        return "the server's replies to never-served paths differ, so it has no fixed not-found reply"
    if replies[0] != (status, body):
        return "it differs from the server's reply to never-served paths"
    return None


# ---------------------------------------------------------------------------
# reading runtime-verification.json without assuming field names
# ---------------------------------------------------------------------------

STATUS_KEY_WORDS = ("status", "code", "key", "auth", "with")


def _is_body_key(key) -> bool:
    if not isinstance(key, str):
        return False
    lower = key.lower()
    return "body" in lower and not any(word in lower for word in BODY_META_WORDS)


def _under_body(path) -> bool:
    return any(_is_body_key(k) for k in path)


def _under_options(path) -> bool:
    return any(isinstance(k, str) and k.lower() == "options" for k in path)


def _plain(entry: dict) -> list:
    """An entry's own values outside body keys."""
    return [v for k, v in entry.items() if not _is_body_key(k)]


def _statuses(entry: dict, skip_options: bool = False) -> list[int]:
    """HTTP statuses in an entry: integers 100-599 under status-like keys (not timings, ports or recorded bodies)."""
    return [v for path, v in walk(entry)
            if is_int(v) and 100 <= v <= 599 and not _under_body(path)
            and not (skip_options and _under_options(path))
            and any(isinstance(k, str) and any(w in k.lower() for w in STATUS_KEY_WORDS) for k in path)]


def _classification(entry: dict) -> list[str]:
    return [v.lower() for v in _plain(entry) if isinstance(v, str) and v.lower() in ("protected", "exempt")]


def _is_options(entry: dict) -> bool:
    return any(isinstance(v, str) and v.upper() == "OPTIONS" for v in _plain(entry))


def _method(entry: dict) -> str | None:
    methods = [v.upper() for v in _plain(entry) if isinstance(v, str) and v.upper() in HTTP_METHODS]
    return methods[0] if methods else None


def _routes(entry: dict) -> list[str]:
    return [v for v in _plain(entry) if isinstance(v, str) and v.startswith("/")]


def _lengths(entry: dict) -> list[int]:
    """Body lengths in an entry: non-negative integers under length/size/bytes keys."""
    return [v for path, v in walk(entry)
            if is_int(v) and v >= 0 and not _under_body(path)
            and any(isinstance(k, str) and any(w in k.lower() for w in ("length", "size", "bytes")) for k in path)]


def _bodies(entry: dict, skip_options: bool = False) -> list[str]:
    """Response bodies an entry records as text: strings or JSON values under body keys (see the module docstring)."""
    found = []
    for path, value in walk(entry):
        if not path or not _is_body_key(path[-1]) or _under_body(path[:-1]):
            continue
        if skip_options and _under_options(path):
            continue
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, (dict, list)):
            found.append(json.dumps(value, sort_keys=True))
    return found


def route_records(doc) -> list[dict]:
    """Route records: an object naming one route ("/...") as a value, or keyed by the route, with a protected/exempt
    classification, its HTTP statuses and its recorded bodies."""
    entries = []

    def add(route: str, value: dict) -> None:
        entries.append({"route": route, "class": _classification(value)[0], "statuses": _statuses(value),
                        "own_statuses": _statuses(value, skip_options=True),
                        "bodies": _bodies(value, skip_options=True), "method": _method(value),
                        "options": _is_options(value)})

    for path, value in walk(doc):
        if not isinstance(value, dict) or _under_body(path):
            continue
        if len(_routes(value)) == 1 and len(_classification(value)) == 1:
            add(_routes(value)[0], value)
        for key, sub in value.items():
            if isinstance(key, str) and key.startswith("/") and isinstance(sub, dict) and len(_classification(sub)) == 1:
                add(key, sub)
    return entries


def options_records(doc) -> list[dict]:
    """OPTIONS probe records (§10): an object naming one route and the method OPTIONS, or an `options` object inside
    a route-keyed object. Statuses of the enclosing route object are included, since that is where a nested record
    would keep the data-bearing method's result."""
    entries = []
    for path, value in walk(doc):
        if not isinstance(value, dict) or _under_body(path):
            continue
        if len(_routes(value)) == 1 and _is_options(value):
            entries.append({"route": _routes(value)[0], "statuses": _statuses(value), "lengths": _lengths(value),
                            "own": _statuses(value), "bodies": _bodies(value)})
        for key, sub in value.items():
            if isinstance(key, str) and key.startswith("/") and isinstance(sub, dict):
                for inner_key, inner in sub.items():
                    if isinstance(inner_key, str) and inner_key.lower() == "options" and isinstance(inner, dict):
                        entries.append({"route": key, "statuses": _statuses(inner) + _statuses(sub),
                                        "lengths": _lengths(inner), "own": _statuses(inner),
                                        "bodies": _bodies(inner)})
    return entries


def recorded_not_found(entries: list[dict], env: Env) -> tuple[dict[int, tuple[int, str]], dict[int, str]]:
    """For route records without a 401/403: ({index: (status, body)} where the record shows a fixed, data-free
    not-found response, {index: reason} where it does not); see the module docstring."""
    found, why = {}, {}
    for index, entry in enumerate(entries):
        statuses, bodies = entry["own_statuses"], [b.strip() for b in entry["bodies"]]
        if not bodies:
            why[index] = "no response body is recorded as text"
        elif len(set(statuses)) != 1:
            why[index] = (f"its recorded statuses {sorted(set(statuses))} are not one status" if statuses
                          else "no status is recorded")
        elif len(set(bodies)) != 1:
            why[index] = "its recorded bodies differ"
        else:
            reasons = data_free_problems(bodies[0].encode("utf-8"), entry["route"], env)
            if reasons:
                why[index] = "its body is not data-free (" + "; ".join(reasons) + ")"
            elif statuses[0] != 404 and not NOT_FOUND.search(bodies[0]):
                why[index] = "it is neither a 404 nor a body that says not found"
            else:
                found[index] = (statuses[0], bodies[0])
    groups: dict[tuple, set] = {}
    for index, (status, body) in found.items():
        groups.setdefault((status, entries[index]["method"]), set()).add(body)
    for index in list(found):
        status = found[index][0]
        variants = groups[(status, entries[index]["method"])]
        if len(variants) > 1:
            why[index] = f"{len(variants)} different not-found bodies are recorded with status {status}, so none is fixed"
            del found[index]
    return found, why


def recorded_options_not_found(entries: list[dict], env: Env) -> tuple[set[int], dict[int, str]]:
    """OPTIONS records whose own 2xx answer with a body is a fixed, data-free not-found response: ({indexes},
    {index: reason})."""
    candidates, why = {}, {}
    for index, entry in enumerate(entries):
        bodies = [b.strip() for b in entry["bodies"]]
        if not bodies:
            why[index] = "no OPTIONS response body is recorded as text"
        elif len(set(bodies)) != 1:
            why[index] = "its recorded OPTIONS bodies differ"
        else:
            reasons = data_free_problems(bodies[0].encode("utf-8"), entry["route"], env)
            if reasons:
                why[index] = "its body is not data-free (" + "; ".join(reasons) + ")"
            elif not NOT_FOUND.search(bodies[0]):
                why[index] = "its body does not say not found"
            else:
                candidates[index] = bodies[0]
    group = lambda i: tuple(sorted({s for s in entries[i]["own"] if 200 <= s < 300}))
    groups: dict[tuple, set] = {}
    for index, body in candidates.items():
        groups.setdefault(group(index), set()).add(body)
    accepted = set()
    for index in candidates:
        if len(groups[group(index)]) > 1:
            why[index] = "different not-found OPTIONS bodies are recorded with the same status, so none is fixed"
        else:
            accepted.add(index)
    return accepted, why


# ---------------------------------------------------------------------------
# verification and the run's server
# ---------------------------------------------------------------------------


def record_path(env: Env, protocol: Path) -> Path:
    """§3: $SLM_EVAL_RUNS/_verification/<protocol_sha256>/runtime-verification.json."""
    return env.runs / "_verification" / sha256_file(protocol) / "runtime-verification.json"


def verify_runtime(env: Env, protocol: Path, poll: float = 0.3) -> Launch:
    live = env.launch("verify-runtime", "--protocol", protocol)
    if live.wait(VERIFY_TIMEOUT, poll) is None:
        fail("verify-runtime did not finish within an hour" + live.where())
    return live


def verified_record(env: Env):
    protocol = legacy_protocol()
    seed_cache(env)
    live = verify_runtime(env, protocol)
    if live.exit != EXIT_OK:
        fail(f"verify-runtime exited {live.exit}, expected 0" + live.where())
    return read_record(record_path(env, protocol))


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
    live = env.launch("run", "--protocol", protocol, "--manifest", manifest, "--tier", "smoke", "--k", 1)
    ports = await_server(live, STARTUP, "slm-eval run")
    deadline = time.monotonic() + STARTUP
    while time.monotonic() < deadline and live.running():
        if all(http(port, "GET", "/health", {})[0] == 200 for port in ports):
            return live, ports
        time.sleep(0.5)
    fail("the run's server never became ready (no 200 from /health on 127.0.0.1 on every proven listening port)"
         + live.where())


# ---------------------------------------------------------------------------
# tests: §3 verify-runtime and §10 runtime security with the fixed not-found exception
# ---------------------------------------------------------------------------


@withheld
def test_vs_lr_27_verification_record_contents(env):
    """VS-LR-27 (succeeds VS-LR-02; capability VS-RT-03) · REAL MODEL + LIVE SERVER · §10 verification record +
    §3 verify-runtime checks, with the fixed not-found exception: runtime-verification.json lists the routes probed,
    each with a protected/exempt classification and at least two HTTP statuses (with and without the key); the list
    covers /health, /v1/models, /props, /slots, at least one generation route, / and /index.html, and records a
    response body for / and for /index.html; /health and /v1/models are exempt; every exempt route shows a 2xx
    status; and every route that shows no 401/403 is classified exempt, or is classified protected and shows a
    fixed, data-free not-found response (module docstring). Records of OPTIONS probes are judged by VS-LR-30."""
    entries = [e for e in route_records(verified_record(env)) if not e["options"]]
    if not entries:
        fail("no route entries (route, protected/exempt, statuses) found in runtime-verification.json")
    by_route: dict[str, dict] = {}
    for entry in entries:
        by_route.setdefault(entry["route"], entry)
    problems = []
    for needed in ("/health", "/v1/models", "/props", "/slots") + ROOT_ROUTES:
        if needed not in by_route:
            problems.append(f"{needed} not listed")
    if not GENERATION_ROUTES & set(by_route):
        problems.append("no generation route listed")
    for route in EXEMPT:
        if route in by_route and by_route[route]["class"] != "exempt":
            problems.append(f"{route} is not classified exempt")
    for route in ROOT_ROUTES:
        if route in by_route and not any(e["bodies"] for e in entries if e["route"] == route):
            problems.append(f"{route}: no response body recorded (§10: record each status and body)")
    for entry in entries:
        if len(entry["statuses"]) < 2:
            problems.append(f"{shown(entry['route'])}: fewer than two statuses recorded")
        if entry["class"] == "exempt" and not any(200 <= s < 300 for s in entry["statuses"]):
            problems.append(f"{shown(entry['route'])}: exempt but no 2xx status")
    unrefused = [e for e in entries if not any(s in (401, 403) for s in e["statuses"])]
    found, why = recorded_not_found(unrefused, env)
    for index, entry in enumerate(unrefused):
        if entry["class"] == "exempt" or index in found:
            continue
        if entry["class"] == "protected":
            problems.append(f"{shown(entry['route'])}: protected but no 401/403 without the key, and no fixed, "
                            f"data-free not-found response recorded ({why.get(index, 'not shown')})")
        problems.append(f"{shown(entry['route'])}: open without the key but not classified exempt")
    if problems:
        fail("; ".join(problems))


@withheld
def test_vs_lr_28_key_required_except_the_exemption(env):
    """VS-LR-28 (succeeds VS-LR-03; capability VS-RT-04) · REAL MODEL + LIVE SERVER · §10 "required on every route
    except the approved exemption ... Nothing else may answer without the key", with the fixed not-found exception
    (data-bearing GET and POST; OPTIONS preflight is VS-LR-29): while a smoke run's proven server is up, /health and
    /v1/models answer 200 without a key on each of its proven listening ports, and every other probed route,
    requested with no key, a wrong bearer key or a wrong X-Api-Key, is refused (401/403), is an alias of an exempt
    handler (a GET whose body matches the /health or /v1/models response), or receives a fixed, data-free not-found
    response (module docstring); at least one probe is refused."""
    live, ports = start_run_server(env)
    never_served = absent_paths()
    results: dict[tuple, tuple] = {}
    absent: dict[tuple, list] = {}
    baselines: dict[int, set] = {}
    deadline = time.monotonic() + 240
    pending = {(port, method, path, variant) for port in ports
               for method, paths in (("GET", PROBE_GET), ("POST", PROBE_POST))
               for path in paths for variant in WRONG_KEYS}
    pending_absent = {(port, method, path, variant) for port in ports for method in ("GET", "POST")
                      for path in never_served for variant in WRONG_KEYS}
    while (pending or pending_absent) and time.monotonic() < deadline and live.running():
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
            results[combo] = (status, body)
            pending.discard(combo)
        for combo in sorted(pending_absent):
            port, method, path, variant = combo
            status, body = http(port, method, path, WRONG_KEYS[variant])
            if status is None or status == 503:
                continue
            absent.setdefault((port, method, variant), []).append((status, body))
            pending_absent.discard(combo)
        time.sleep(0.5)
    live.interrupt()
    violations = []
    for (port, method, path, variant), (status, body) in sorted(results.items(), key=lambda item: item[0]):
        if path in EXEMPT or status in (401, 403):
            continue
        if method == "GET" and 200 <= status < 300 and signature(body) in baselines.get(port, set()):
            continue
        problem = not_found_problem(status, body, absent.get((port, method, variant), []), path, env)
        if problem is not None:
            violations.append(f"{method} :{port}{path} [{variant}] -> {status}: {problem}")
    if violations:
        fail("routes answered without a valid key: " + "; ".join(violations))
    if not any(status in (401, 403) for status, _ in results.values()):
        fail(f"no route refused a missing key; {len(pending)} probes never got a definitive answer")


@withheld
def test_vs_lr_29_preflight_without_key_carries_no_body(env):
    """VS-LR-29 (succeeds VS-LR-09; capability VS-RT-10) · REAL MODEL + LIVE SERVER · §10 CORS preflight: "an OPTIONS
    request on any route may be answered without the key, provided the response body is empty. The same route's
    data-bearing methods must still require the key", with the fixed not-found exception. While a smoke run's proven
    server is up, every probed route's OPTIONS request with CORS preflight headers and no key is refused (401/403),
    receives a fixed, data-free not-found response (module docstring), or is answered with an empty body; and
    wherever OPTIONS is answered, the route's data-bearing method without the key is refused (401/403), receives a
    fixed, data-free not-found response, or is /health, /v1/models or a GET alias of their handlers. NOT RUN if no
    route answered OPTIONS without the key."""
    live, ports = start_run_server(env)
    exempt_bodies = {port: {signature(http(port, "GET", route, {})[1]) for route in EXEMPT} for port in ports}
    never_served = absent_paths()
    preflight = lambda method: {**PREFLIGHT_HEADERS, "Access-Control-Request-Method": method}
    absent_options = {(port, method): [http(port, "OPTIONS", path, preflight(method)) for path in never_served]
                      for port in ports for method in ("GET", "POST")}
    absent_data = {(port, method): [http(port, method, path, {}) for path in never_served]
                   for port in ports for method in ("GET", "POST")}
    problems, answered = [], 0
    for port in ports:
        for method, paths in (("GET", PROBE_GET), ("POST", PROBE_POST)):
            for path in paths:
                status, body = http(port, "OPTIONS", path, preflight(method))
                if status is None or status in (401, 403):
                    continue
                problem = not_found_problem(status, body, absent_options[(port, method)], path, env)
                if problem is None:
                    continue                    # a fixed, data-free not-found response is not answering
                if status == 404:
                    problems.append(f"OPTIONS :{port}{path} -> 404: {problem}")
                    continue
                answered += 1
                if body:
                    problems.append(f"OPTIONS :{port}{path} -> {status} with a {len(body)}-byte body, not a fixed, "
                                    f"data-free not-found response: {problem}")
                data_status, data_body = http(port, method, path, {})
                if path in EXEMPT or data_status in (401, 403, None, 503):
                    continue
                if method == "GET" and 200 <= data_status < 300 and signature(data_body) in exempt_bodies[port]:
                    continue
                data_problem = not_found_problem(data_status, data_body, absent_data[(port, method)], path, env)
                if data_problem is not None:
                    problems.append(f"{method} :{port}{path} -> {data_status} without the key after an open "
                                    f"preflight: {data_problem}")
    live.interrupt()
    if problems:
        fail("; ".join(problems))
    if not answered:
        not_run("no route answered OPTIONS without the key; nothing further to check")


@withheld
def test_vs_lr_30_record_covers_options_probes(env):
    """VS-LR-30 (succeeds VS-LR-10; capability VS-RT-11) · REAL MODEL + LIVE SERVER · §10 verification record: "for
    each OPTIONS probe, records its status and body length, and the result of the same route's data-bearing method
    without the key", with the fixed not-found exception. runtime-verification.json holds at least one OPTIONS probe
    record, one of them on a non-exempt route; each has a body length and at least two HTTP statuses; on every
    non-exempt route the data-bearing result is 401 or 403, or the route's records show a fixed, data-free not-found
    response whose status the OPTIONS record shows, its other statuses being 401, 403 or an empty preflight's 2xx;
    and no OPTIONS probe on a non-exempt route answered with a 2xx shows a non-zero body length unless that answer is
    recorded as a fixed, data-free not-found response. Exempt routes are /health, /v1/models and any route the
    record classifies as exempt (their aliases)."""
    record = verified_record(env)
    routes = [e for e in route_records(record) if not e["options"]]
    exempt = set(EXEMPT) | {e["route"] for e in routes if e["class"] == "exempt"}
    entries = options_records(record)
    if not entries:
        fail("no OPTIONS probe records found in runtime-verification.json")
    if not any(e["route"] not in exempt for e in entries):
        fail("no OPTIONS probe on a non-exempt route")
    unrefused = [e for e in routes if not any(s in (401, 403) for s in e["statuses"])]
    found, why = recorded_not_found(unrefused, env)
    statuses_of: dict[str, set] = {}
    route_why: dict[str, str] = {}
    for index, entry in enumerate(unrefused):
        if index in found:
            statuses_of.setdefault(entry["route"], set()).add(found[index][0])
        else:
            route_why.setdefault(entry["route"], why.get(index, "not shown"))
    not_found_status = {r: next(iter(s)) for r, s in statuses_of.items() if len(s) == 1 and r not in route_why}
    accepted, options_why = recorded_options_not_found(entries, env)
    problems = []
    for index, entry in enumerate(entries):
        route = shown(entry["route"])
        if not entry["lengths"]:
            problems.append(f"{route}: OPTIONS record has no body length")
        if len(entry["statuses"]) < 2:
            problems.append(f"{route}: OPTIONS record lacks the data-bearing method's status")
        if entry["route"] in exempt:
            continue
        if not any(s in (401, 403) for s in entry["statuses"]):
            status = not_found_status.get(entry["route"])
            empty = all(n == 0 for n in entry["lengths"])
            if status is None:
                reason = route_why.get(entry["route"], "no route record shows a fixed not-found response for it")
                problems.append(f"{route}: data-bearing method not refused without the key, and no fixed, data-free "
                                f"not-found response recorded ({reason})")
            elif status not in entry["statuses"] or not all(
                    s in (status, 401, 403) or (200 <= s < 300 and empty) for s in entry["statuses"]):
                problems.append(f"{route}: data-bearing method not refused without the key, and its OPTIONS record "
                                f"does not show the route's fixed not-found status {status}")
        if any(200 <= s < 300 for s in entry["own"]) and any(n > 0 for n in entry["lengths"]) \
                and index not in accepted:
            problems.append(f"{route}: OPTIONS answered with a non-empty body, not recorded as a fixed, data-free "
                            f"not-found response ({options_why.get(index, 'not shown')})")
    if problems:
        fail("; ".join(problems))
