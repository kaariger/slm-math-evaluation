"""Visible specification tests — runtime-verification.json route and OPTIONS records with the §10 fixed not-found exception and its admissible fixed_not_found label, for the canonical legacy Qwen protocol, acting only on processes proven owned through the test's own launch handles (contract v0.8.1 §3, §10).

Contract-only: written from the approved interface, the maintainer's readings
(including the Human's B14-3 and B14-4 readings) and the registered legacy test
sources (read as text; never imported or run), with no implementation code
seen. Tests use only the `slm-eval` CLI, the processes it starts and the
contract's files.

VS-LR-31 and VS-LR-32 succeed VS-LR-27 and VS-LR-30 (batch 14), which succeed
VS-LR-02 and VS-LR-10 (batch 13) and carry the original capabilities VS-RT-03
and VS-RT-11. They keep those predicates and change only how the record's
entries are read: a record may identify a fixed, data-free not-found response
with the metadata label fixed_not_found instead of classifying the route
protected. The label proves nothing by itself; the entry's recorded status,
body and method must show the exception. Ordinary protected and exempt
handling, and the two exempt handlers and their aliases, are unchanged.

Every test needs THE REAL LEGACY MODEL AND A LIVE SERVER (its verify-runtime),
and is skipped as NOT RUN unless all of these are present:

  SLM_EVAL_TEST_RUNTIME=1     the pinned llama.cpp runtime and the real model
                              artifact are available to the implementation
  SLM_EVAL_TEST_SEED_CACHE    a dataset cache holding the pinned dataset and
                              models/<model.artifact.file> (§2); copied into
                              each test's temporary SLM_EVAL_CACHE (files over
                              256 MiB are symlinked, not copied)
  legacy protocol             SLM_EVAL_TEST_PROTOCOL, or the repository file,
                              whose SHA-256 is that of the canonical legacy
                              protocol (LEGACY_PROTOCOL_SHA256)
  tools                       `ps`, `pgrep` and `lsof`; os.waitid with WNOWAIT

These are regressions of the legacy Qwen path only. They never retarget the
legacy protocol to a fixture or another model. No key is looked up: no
process's arguments or environment are read.

Reading the record (no field name is assumed beyond these recognisers, which
are partial and are not a schema):
  - Entries. A route record is an object naming one route ("/...") as a value,
    or keyed by the route, classified protected or exempt, or carrying the
    label fixed_not_found (a string value, any case, with _ - or space between
    the words, or a key of that name set to true). Labelled entries are kept
    and counted for coverage like any other. An OPTIONS record is an object
    naming one route and the method OPTIONS, or an `options` object inside a
    route-keyed object; a label on an OPTIONS record excuses nothing.
  - Statuses are integers 100-599 under status-like keys. Bodies are strings
    or JSON values under keys containing "body" or "bodies" (not length, len, size,
    bytes, encoding, type, format or mime); a key also containing base64 or
    b64 is decoded as UTF-8; a key also containing sha, hash or digest is a
    digest, which records a body without its content. Values under body keys
    are never taken for routes, classifications, methods or statuses.
  - Without a valid key. A status or body whose key path contains without,
    nokey, unkeyed, unauth, noauth, anonymous, keyless or missingkey (letters only,
    any case) was received without the key; withkey, withauth, authenticated,
    authorized, keyed or validkey marks one received with it. Where the record
    marks them, only the response without the key is judged, so a lawful
    keyed response may differ. Where it does not, the entry's statuses must be
    one status and its bodies one body, or the association is unresolved.
  - In an OPTIONS record, a status under a key containing "data" is the
    data-bearing method's result without the key; the other statuses are the
    preflight's own. Without such keys the two are mixed, as before.
  - The method is a GET/POST/PUT/PATCH/DELETE/HEAD value of the entry, or of
    a key containing "method" ("data" keys give an OPTIONS record's
    data-bearing method).

A route record shows a fixed, data-free not-found response when its response
without the key is one status S with one body B, B is data-free (below), S is
404 or B says "not found" (any case; also not_found, not-found, notfound), the
entry names its method, and every route record showing a not-found response
with S and that method records the same B (identical for every such request).

Data-free means, for a body decoded as UTF-8: it does not contain the model
alias, ".gguf", the test's dataset cache, run store, scratch or working
directory, the home directory or the prompt `2+2=`; it has no path-like string
(a JSON string, or a double-quoted string in other text, starting with / or ~
and longer than one character, other than the requested route itself); and,
when it is JSON, no key naming a data surface (a key containing path, file,
prompt, output, slot, propert, props, generation, model, content, choice,
token, completion, embedding, template or ctx, unless its value is the
requested route) and no number other than an HTTP status code under a status
or code key. These checks bound the judgment; they do not prove that a body
carries no data.

Outcomes. A definite violation fails the test. Where the record's
representation cannot establish the evidence (an unmarked status or body
association, a body recorded only as a digest, an entry naming no method, or
a fixed, data-free response that is neither a 404 nor recognisably a
not-found response), the case is skipped as NOT RUN naming each unresolved
entry: unresolved evidence is not a product failure, and NOT RUN is never a
pass. Missing routes, statuses, bodies, OPTIONS records and lengths, varying
not-found bodies, data in a response without the key and a non-empty answered
preflight fail as before.

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

Diagnostics. Failures and NOT RUN reasons name files, routes, methods,
statuses, counts and fixed reasons; route names taken from a record are cut to
80 characters. No response body, record content or CLI output is printed: the
CLI's stdout and stderr stay in named files, and an unexpected error becomes a
failure naming only its type, a safe detail and the line.

Teardown is not an assertion. It signals each unreaped launch group, then each
proven process individually (parents before children), first SIGTERM and then
SIGKILL; no delivery order is guaranteed. What it found, sent and left is
written to logs/teardown.txt in the test's temporary directory and recorded as
the pytest user property `vs_teardown`; a proven process left alive, or a
teardown error, also raises a warning. Processes outside these proofs cannot
be observed, so their absence is never claimed.

Timing: a verification is awaited for up to VERIFY_TIMEOUT seconds. No speed
is required.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split);
SLM_EVAL_TEST_REPO names the repository root (default: the nearest ancestor of
the working directory whose pyproject.toml mentions slm-eval). No
SLM_EVAL_TEST_* variable is passed to the implementation.
"""

from __future__ import annotations

import base64
import binascii
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
import uuid
import warnings
from pathlib import Path
from typing import NamedTuple

import pytest

EXIT_OK = 0
VERIFY_TIMEOUT = 3600.0
TEARDOWN_GRACE = (5.0, 10.0)
LARGE_FILE = 256 * 1024 * 1024
LEGACY_PROTOCOL_SHA256 = "ce48a2448c61af2839e62d63137b06d96ca7bc1b81ce68809adf2af36da8f0ee"
SEARCH_SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}

MODEL_ALIAS = "qwen3-8b"
EXEMPT = ("/health", "/v1/models")
GENERATION_ROUTES = {"/completion", "/completions", "/v1/completions", "/chat/completions", "/v1/chat/completions"}

# §10 fixed not-found exception (contract v0.6, kept in v0.8.1) and its admissible label (B14-4)
ROOT_ROUTES = ("/", "/index.html")
NOT_FOUND = re.compile(r"not[\s_-]*found", re.IGNORECASE)
PROBE_ECHOES = ("2+2=",)
DATA_KEY_WORDS = ("path", "file", "prompt", "output", "slot", "propert", "props", "generation", "model", "content",
                  "choice", "token", "completion", "embedding", "template", "ctx")
NUMBER_KEY_WORDS = ("status", "code")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
SHOWN = 80
LABEL = "fixednotfound"
UNAUTH_MARKS = ("without", "nokey", "unkeyed", "unauth", "noauth", "anonymous", "keyless", "missingkey")
AUTH_MARKS = ("withkey", "withauth", "authenticated", "authorized", "authorised", "keyed", "validkey")
BODY_META_WORDS = ("length", "len", "size", "bytes", "encoding", "type", "format", "mime")
BODY_DIGEST_WORDS = ("sha", "hash", "digest")
BODY_BASE64_WORDS = ("base64", "b64")

# Contract-declared files in the test's fresh run store: a run directory (§6.1, §6.4, §7) and a verification
# record directory (§3).
RUN_FILES = ("items.jsonl", "run.json", "protocol.yaml", "manifest.json", "runtime-verification.json",
             "artifact-verification.json", "rescore.json", "report.json", "report.md")
VERIFICATION_FILES = ("runtime-verification.json", "artifact-verification.json")


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
# the §10 fixed not-found exception: data-free bodies
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


def says_not_found(status: int, body: str) -> bool:
    return status == 404 or NOT_FOUND.search(body) is not None


# ---------------------------------------------------------------------------
# reading runtime-verification.json without assuming field names
# ---------------------------------------------------------------------------

STATUS_KEY_WORDS = ("status", "code", "key", "auth", "with")


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower()) if isinstance(text, str) else ""


def _body_kind(key) -> str | None:
    """'text', 'base64' or 'digest' for a key naming a recorded response body; None for any other key."""
    lower = key.lower() if isinstance(key, str) else ""
    if "body" not in lower and "bodies" not in lower:
        return None
    if any(word in lower for word in BODY_BASE64_WORDS):
        return "base64"
    if any(word in lower for word in BODY_DIGEST_WORDS):
        return "digest"
    if any(word in lower for word in BODY_META_WORDS):
        return None
    return "text"


def _under_body(path) -> bool:
    return any(_body_kind(k) is not None for k in path)


def _under_options(path) -> bool:
    return any(isinstance(k, str) and k.lower() == "options" for k in path)


def _side(path) -> str | None:
    """'unauth' for a value received without a valid key, 'auth' for one received with it, None if unmarked."""
    keys = [_norm(k) for k in path if isinstance(k, str)]
    if any(mark in key for key in keys for mark in UNAUTH_MARKS):
        return "unauth"
    if any(mark in key for key in keys for mark in AUTH_MARKS):
        return "auth"
    return None


def _is_data(path) -> bool:
    return any("data" in _norm(k) for k in path if isinstance(k, str))


def _plain(entry: dict) -> list:
    """An entry's own values outside body keys."""
    return [v for k, v in entry.items() if _body_kind(k) is None]


def _routes(entry: dict) -> list[str]:
    return [v for v in _plain(entry) if isinstance(v, str) and v.startswith("/")]


def _classification(entry: dict) -> list[str]:
    return [v.lower() for v in _plain(entry) if isinstance(v, str) and v.lower() in ("protected", "exempt")]


def _is_options(entry: dict) -> bool:
    return any(isinstance(v, str) and v.upper() == "OPTIONS" for v in _plain(entry))


def _labelled(entry: dict) -> bool:
    """Whether the entry carries the fixed_not_found label (outside bodies and nested OPTIONS records)."""
    for path, value in walk(entry):
        if _under_body(path) or _under_options(path):
            continue
        if isinstance(value, str) and _norm(value) == LABEL:
            return True
        if value is True and path and _norm(path[-1]) == LABEL:
            return True
    return False


def _method(entry: dict, data: bool = False) -> str | None:
    """The one HTTP method the entry names (an own value, or under a key containing "method"); with data=True the
    method under a key containing "data". None when there is none or more than one."""
    found = set()
    for path, value in walk(entry):
        if not path or not isinstance(value, str) or value.upper() not in HTTP_METHODS:
            continue
        if _under_body(path) or _under_options(path) or _is_data(path) != data:
            continue
        if len(path) == 1 or "method" in _norm(path[-1]):
            found.add(value.upper())
    return found.pop() if len(found) == 1 else None


def _status_evidence(entry: dict, skip_options: bool = False) -> list[tuple[str | None, bool, int]]:
    """(side, data-bearing, status) for each HTTP status in an entry: integers 100-599 under status-like keys, not
    under a recorded body (and, with skip_options, not in a nested OPTIONS record)."""
    return [(_side(path), _is_data(path), v) for path, v in walk(entry)
            if is_int(v) and 100 <= v <= 599 and not _under_body(path)
            and not (skip_options and _under_options(path))
            and any(isinstance(k, str) and any(w in k.lower() for w in STATUS_KEY_WORDS) for k in path)]


def _statuses(entry: dict, skip_options: bool = False) -> list[int]:
    return [status for _, _, status in _status_evidence(entry, skip_options)]


def _lengths(entry: dict) -> list[int]:
    """Body lengths in an entry: non-negative integers under length/size/bytes keys."""
    return [v for path, v in walk(entry)
            if is_int(v) and v >= 0 and not _under_body(path)
            and any(isinstance(k, str) and any(w in k.lower() for w in ("length", "size", "bytes")) for k in path)]


def _decoded(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def _body_evidence(entry: dict, skip_options: bool = False) -> tuple[list[tuple[str | None, str]], int]:
    """((side, text) for each response body the entry records with its content, number of bodies recorded only as
    a digest or as undecodable base64)."""
    texts, opaque = [], 0
    for path, value in walk(entry):
        kind = _body_kind(path[-1]) if path else None
        if kind is None or _under_body(path[:-1]) or (skip_options and _under_options(path)):
            continue
        side = _side(path)
        for item in (value if isinstance(value, list) else [value]):
            if kind == "digest":
                opaque += isinstance(item, str) and bool(item)
            elif kind == "base64":
                text = _decoded(item)
                if text is None:
                    opaque += 1
                else:
                    texts.append((side, text))
            elif isinstance(item, str):
                texts.append((side, item))
            elif isinstance(item, (dict, list)):
                texts.append((side, json.dumps(item, sort_keys=True)))
    return texts, opaque


def route_records(doc) -> list[dict]:
    """Route records: an object naming one route ("/...") as a value, or keyed by the route, classified protected or
    exempt, or labelled fixed_not_found; with its statuses, recorded bodies and method."""
    entries = []

    def admissible(value: dict) -> bool:
        classes = _classification(value)
        return len(classes) == 1 or (not classes and _labelled(value))

    def add(route: str, value: dict) -> None:
        classes = _classification(value)
        texts, opaque = _body_evidence(value, skip_options=True)
        entries.append({"route": route, "class": classes[0] if classes else None, "labelled": _labelled(value),
                        "statuses": _statuses(value), "evidence": _status_evidence(value, skip_options=True),
                        "bodies": texts, "opaque": opaque, "method": _method(value), "options": _is_options(value)})

    for path, value in walk(doc):
        if not isinstance(value, dict) or _under_body(path):
            continue
        if len(_routes(value)) == 1 and admissible(value):
            add(_routes(value)[0], value)
        for key, sub in value.items():
            if isinstance(key, str) and key.startswith("/") and isinstance(sub, dict) and admissible(sub):
                add(key, sub)
    return entries


def options_records(doc) -> list[dict]:
    """OPTIONS probe records (§10): an object naming one route and the method OPTIONS, or an `options` object inside
    a route-keyed object. `statuses` keeps the predecessors' reading (a nested record includes the enclosing route
    object's statuses); `data` holds the data-bearing method's results where the record marks them, and `own` the
    preflight's own statuses (all statuses when nothing is marked)."""
    entries = []
    for path, value in walk(doc):
        if not isinstance(value, dict) or _under_body(path):
            continue
        if len(_routes(value)) == 1 and _is_options(value):
            evidence = _status_evidence(value)
            texts, opaque = _body_evidence(value)
            entries.append({"route": _routes(value)[0], "statuses": [s for _, _, s in evidence],
                            "lengths": _lengths(value), "own": [s for _, data, s in evidence if not data],
                            "data": [s for _, data, s in evidence if data], "bodies": [t for _, t in texts],
                            "opaque": opaque, "data_method": _method(value, data=True)})
        for key, sub in value.items():
            if isinstance(key, str) and key.startswith("/") and isinstance(sub, dict):
                for inner_key, inner in sub.items():
                    if isinstance(inner_key, str) and inner_key.lower() == "options" and isinstance(inner, dict):
                        inner_evidence = _status_evidence(inner)
                        route_evidence = _status_evidence(sub, skip_options=True)
                        texts, opaque = _body_evidence(inner)
                        entries.append({
                            "route": key, "statuses": _statuses(inner) + _statuses(sub), "lengths": _lengths(inner),
                            "own": [s for _, data, s in inner_evidence if not data],
                            "data": [s for _, data, s in inner_evidence if data]
                            + [s for side, _, s in route_evidence if side == "unauth"],
                            "bodies": [t for _, t in texts], "opaque": opaque,
                            "data_method": _method(inner, data=True) or _method(sub)})
    return entries


# ---------------------------------------------------------------------------
# judging a route record's response without the key
# ---------------------------------------------------------------------------


class Judged(NamedTuple):
    outcome: str            # refused | response | not_found | unresolved | violation
    status: int | None
    body: str | None
    reason: str


def unkeyed_response(entry: dict, env: Env) -> Judged:
    """The route record's response without a valid key, from its own statuses and bodies (nested OPTIONS records
    aside): refused, a (status, body) response, unresolved or a violation (see the module docstring)."""
    evidence, texts = entry["evidence"], entry["bodies"]
    marked = [s for side, _, s in evidence if side == "unauth"]
    statuses = marked or [s for _, _, s in evidence]
    if any(s in (401, 403) for s in statuses):
        return Judged("refused", None, None, "")
    all_data = bool(texts) and all(data_free_problems(t.encode("utf-8"), entry["route"], env) for _, t in texts)
    if len(set(statuses)) != 1:
        if all_data:
            return Judged("violation", None, None, "every recorded body is not data-free")
        what = "its responses without the key have different statuses" if marked \
            else "the record does not say which recorded status was received without the key"
        return Judged("unresolved", None, None, f"{what} ({sorted(set(statuses))})" if statuses
                      else "no status is recorded")
    status = statuses[0]
    unkeyed = [t.strip() for side, t in texts if side == "unauth"]
    if unkeyed:
        if len(set(unkeyed)) != 1:
            return Judged("violation", status, None, "its responses without the key have different bodies, so they "
                                                     "are not fixed")
        return Judged("response", status, unkeyed[0], "")
    if not texts:
        if entry["opaque"]:
            return Judged("unresolved", status, None, "its body is recorded only as a digest or undecodable base64, "
                                                      "so its content cannot be checked")
        return Judged("violation", status, None, "no response body is recorded as text")
    if all(side == "auth" for side, _ in texts):
        return Judged("violation", status, None, "no response body without the key is recorded")
    if all(side is None for side, _ in texts) and len({t.strip() for _, t in texts}) == 1 \
            and len({s for _, _, s in evidence}) == 1:
        return Judged("response", status, texts[0][1].strip(), "")
    if all_data:
        return Judged("violation", status, None, "every recorded body is not data-free")
    return Judged("unresolved", status, None, "the record does not say which recorded body was received without "
                                              "the key")


def judge_records(entries: list[dict], env: Env) -> list[Judged]:
    """One judgment per non-exempt route record: refused, not_found (a fixed, data-free not-found response),
    unresolved or violation. Fixedness is judged across the record, per status and method."""
    judged = []
    for entry in entries:
        result = unkeyed_response(entry, env)
        if result.outcome == "response":
            reasons = data_free_problems(result.body.encode("utf-8"), entry["route"], env)
            if reasons:
                result = Judged("violation", result.status, result.body,
                                "its response without the key is not data-free (" + "; ".join(reasons) + ")")
            elif not says_not_found(result.status, result.body):
                result = Judged("unresolved", result.status, result.body,
                                f"its response without the key ({result.status}) passes the data checks but is "
                                "neither a 404 nor recognisably a not-found response")
            elif entry["method"] is None:
                result = Judged("unresolved", result.status, result.body,
                                "the record does not name the method that received the not-found response")
            else:
                result = Judged("not_found", result.status, result.body, "")
        judged.append(result)
    groups: dict[tuple, set] = {}
    for entry, result in zip(entries, judged):
        if result.outcome == "not_found":
            groups.setdefault((result.status, entry["method"]), set()).add(result.body)
    for index, (entry, result) in enumerate(zip(entries, judged)):
        if result.outcome == "not_found":
            variants = groups[(result.status, entry["method"])]
            if len(variants) > 1:
                judged[index] = Judged("violation", result.status, result.body,
                                       f"{len(variants)} different not-found bodies are recorded with status "
                                       f"{result.status} and method {entry['method']}, so none is fixed")
    return judged


def options_not_found(entries: list[dict], env: Env) -> tuple[set[int], dict[int, str]]:
    """OPTIONS records whose own 2xx answer with a body is a recorded fixed, data-free not-found response:
    ({indexes}, {index: reason})."""
    candidates, why = {}, {}
    for index, entry in enumerate(entries):
        bodies = [b.strip() for b in entry["bodies"]]
        if not bodies:
            why[index] = ("its OPTIONS body is recorded only as a digest or undecodable base64" if entry["opaque"]
                          else "no OPTIONS response body is recorded as text")
        elif len(set(bodies)) != 1:
            why[index] = "its recorded OPTIONS bodies differ"
        else:
            reasons = data_free_problems(bodies[0].encode("utf-8"), entry["route"], env)
            if reasons:
                why[index] = "its body is not data-free (" + "; ".join(reasons) + ")"
            elif not NOT_FOUND.search(bodies[0]):
                why[index] = "its body is not recognisably a not-found response"
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
# verification
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


def conclude(problems: list[str], unresolved: list[str]) -> None:
    """Definite violations fail; otherwise unresolved evidence is NOT RUN (never a pass)."""
    if problems:
        extra = f"; {len(unresolved)} further entries unresolved" if unresolved else ""
        fail("; ".join(problems) + extra)
    if unresolved:
        not_run("the record's representation leaves these entries unresolved (evidence for Human ruling, not a "
                "product failure): " + "; ".join(unresolved))


# ---------------------------------------------------------------------------
# tests: §3 verify-runtime and §10 verification records with the fixed not-found exception
# ---------------------------------------------------------------------------


@withheld
def test_vs_lr_31_verification_record_contents(env):
    """VS-LR-31 (succeeds VS-LR-27 and VS-LR-02; capability VS-RT-03) · REAL MODEL + LIVE SERVER · §10 verification
    record + §3 verify-runtime checks, with the fixed not-found exception and its fixed_not_found label:
    runtime-verification.json lists the routes probed, each classified protected or exempt or labelled
    fixed_not_found, with at least two HTTP statuses (with and without the key); the list covers /health,
    /v1/models, /props, /slots, at least one generation route, / and /index.html, and records a response body for
    / and for /index.html; /health and /v1/models are exempt; every exempt route shows a 2xx status, carries no
    fixed_not_found label and, unless it is /health or /v1/models, does not record a not-found response; every other
    route either refuses without the key (401/403; a labelled route must not) or shows a fixed, data-free not-found
    response without the key (module docstring). NOT RUN when the representation leaves an entry unresolved and
    nothing fails. Records of OPTIONS probes are judged by VS-LR-32."""
    entries = [e for e in route_records(verified_record(env)) if not e["options"]]
    if not entries:
        fail("no route entries (route, protected/exempt/fixed_not_found, statuses) found in runtime-verification.json")
    by_route: dict[str, dict] = {}
    for entry in entries:
        by_route.setdefault(entry["route"], entry)
    problems, unresolved = [], []
    for needed in ("/health", "/v1/models", "/props", "/slots") + ROOT_ROUTES:
        if needed not in by_route:
            problems.append(f"{needed} not listed")
    if not GENERATION_ROUTES & set(by_route):
        problems.append("no generation route listed")
    for route in EXEMPT:
        if route in by_route and by_route[route]["class"] != "exempt":
            problems.append(f"{route} is not classified exempt")
    for route in ROOT_ROUTES:
        recorded = [e for e in entries if e["route"] == route]
        if recorded and not any(e["bodies"] for e in recorded):
            if any(e["opaque"] for e in recorded):
                unresolved.append(f"{route}: its response body is recorded only as a digest or undecodable base64")
            else:
                problems.append(f"{route}: no response body recorded (§10: record each status and body)")
    for entry in entries:
        route = shown(entry["route"])
        if len(entry["statuses"]) < 2:
            problems.append(f"{route}: fewer than two statuses recorded")
        if entry["class"] != "exempt":
            continue
        if not any(200 <= s < 300 for s in entry["statuses"]):
            problems.append(f"{route}: exempt but no 2xx status")
        if entry["labelled"]:
            problems.append(f"{route}: classified exempt and labelled fixed_not_found")
        elif entry["route"] not in EXEMPT:
            result = unkeyed_response(entry, env)
            if result.outcome == "response" and says_not_found(result.status, result.body):
                problems.append(f"{route}: classified exempt, but its recorded response without the key is a "
                                "not-found response, not one of the two exempt handlers")
    candidates = [e for e in entries if e["class"] != "exempt"]
    for entry, result in zip(candidates, judge_records(candidates, env)):
        route = shown(entry["route"])
        if result.outcome == "refused":
            if entry["labelled"]:
                problems.append(f"{route}: labelled fixed_not_found, but its response without the key is a refusal "
                                "(401/403)")
        elif result.outcome == "unresolved":
            unresolved.append(f"{route}: {result.reason}")
        elif result.outcome == "violation":
            if entry["class"] == "protected":
                problems.append(f"{route}: protected but no 401/403 without the key, and no fixed, data-free "
                                f"not-found response recorded ({result.reason})")
                problems.append(f"{route}: open without the key but not classified exempt")
            else:
                problems.append(f"{route}: labelled fixed_not_found, but the record shows no fixed, data-free "
                                f"not-found response without the key ({result.reason})")
    conclude(problems, unresolved)


@withheld
def test_vs_lr_32_record_covers_options_probes(env):
    """VS-LR-32 (succeeds VS-LR-30 and VS-LR-10; capability VS-RT-11) · REAL MODEL + LIVE SERVER · §10 verification
    record: "for each OPTIONS probe, records its status and body length, and the result of the same route's
    data-bearing method without the key", with the fixed not-found exception and its fixed_not_found label.
    runtime-verification.json holds at least one OPTIONS probe record, one of them on a non-exempt route; each has a
    body length and at least two HTTP statuses; on every non-exempt route the data-bearing result without the key is
    401 or 403, or the route's records show a fixed, data-free not-found response whose status the OPTIONS record's
    data-bearing result shows (where the record separates that result from the preflight's own status; otherwise its
    other statuses must be 401, 403 or an empty preflight's 2xx); and no OPTIONS probe on a non-exempt route answered
    with a 2xx shows a non-zero body length unless that answer is recorded as a fixed, data-free not-found response.
    Exempt routes are /health, /v1/models and any route the record classifies as exempt (their aliases); a
    fixed_not_found label exempts nothing. NOT RUN when the representation leaves an entry unresolved and nothing
    fails."""
    record = verified_record(env)
    routes = [e for e in route_records(record) if not e["options"]]
    exempt = set(EXEMPT) | {e["route"] for e in routes if e["class"] == "exempt"}
    entries = options_records(record)
    if not entries:
        fail("no OPTIONS probe records found in runtime-verification.json")
    if not any(e["route"] not in exempt for e in entries):
        fail("no OPTIONS probe on a non-exempt route")
    candidates = [e for e in routes if e["class"] != "exempt"]
    judged: dict[str, list[tuple[dict, Judged]]] = {}
    for entry, result in zip(candidates, judge_records(candidates, env)):
        judged.setdefault(entry["route"], []).append((entry, result))

    def verdict(route: str, method: str | None) -> tuple[str, object]:
        items = judged.get(route, [])
        if method is not None and any(e["method"] == method for e, _ in items):
            items = [(e, r) for e, r in items if e["method"] == method]
        if not items:
            return "absent", "no route record shows a fixed not-found response for it"
        for outcome in ("violation", "unresolved"):
            for _, result in items:
                if result.outcome == outcome:
                    return outcome, result.reason
        if all(r.outcome == "refused" for _, r in items):
            return "refused", None
        found = {r.status for _, r in items if r.outcome == "not_found"}
        if len(found) == 1 and all(r.outcome == "not_found" for _, r in items):
            return "not_found", found.pop()
        return "unresolved", "its route records disagree between a refusal and a not-found response"

    accepted, options_why = options_not_found(entries, env)
    problems, unresolved = [], []
    for index, entry in enumerate(entries):
        route = shown(entry["route"])
        if not entry["lengths"]:
            problems.append(f"{route}: OPTIONS record has no body length")
        if len(entry["statuses"]) < 2:
            problems.append(f"{route}: OPTIONS record lacks the data-bearing method's status")
        if entry["route"] in exempt:
            continue
        explicit = bool(entry["data"])
        results = entry["data"] if explicit else entry["statuses"]
        if not any(s in (401, 403) for s in results):
            outcome, detail = verdict(entry["route"], entry["data_method"])
            if outcome == "not_found":
                status = detail
                if explicit:
                    wrong = sorted({s for s in entry["data"] if s != status})
                    if wrong:
                        problems.append(f"{route}: the data-bearing method answered {wrong} without the key, not the "
                                        f"route's fixed not-found status {status}")
                else:
                    empty = all(n == 0 for n in entry["lengths"])
                    if status not in entry["statuses"] or not all(
                            s in (status, 401, 403) or (200 <= s < 300 and empty) for s in entry["statuses"]):
                        unresolved.append(f"{route}: its OPTIONS record does not separate the preflight's own status "
                                          "from the data-bearing result, and its statuses do not show only the "
                                          f"route's fixed not-found status {status}")
            elif outcome == "unresolved":
                unresolved.append(f"{route}: {detail}")
            elif outcome == "refused":
                problems.append(f"{route}: data-bearing method not refused without the key, although the route's own "
                                "record shows a refusal")
            else:
                problems.append(f"{route}: data-bearing method not refused without the key, and no fixed, data-free "
                                f"not-found response recorded ({detail})")
        if any(200 <= s < 300 for s in entry["own"]) and any(n > 0 for n in entry["lengths"]) \
                and index not in accepted:
            problems.append(f"{route}: OPTIONS answered with a non-empty body, not recorded as a fixed, data-free "
                            f"not-found response ({options_why.get(index, 'not shown')})")
    conclude(problems, unresolved)
