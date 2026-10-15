"""Shared support code for the slm-eval MATH-500 holdout suite.

Written by the independent test author from the interface contract alone
(v0.2, then aligned with v0.3 before registration), before any
implementation existed. The tests use only the ``slm-eval`` command line and
the contract's file and run-store schemas; nothing here imports
implementation code.

Environment variables read by the suite. They are never passed on to the
implementation (every ``SLM_EVAL_HOLDOUT_*`` variable is removed from the
child environment, and ``SLM_EVAL_CACHE`` / ``SLM_EVAL_RUNS`` always point at
per-test temporary directories):

  SLM_EVAL_HOLDOUT_CMD          command that invokes the CLI, shell-split
                                (default: ``slm-eval`` found on PATH)
  SLM_EVAL_HOLDOUT_REPO         repository root (default: the nearest ancestor
                                of the pytest working directory whose
                                pyproject.toml mentions ``slm-eval``)
  SLM_EVAL_HOLDOUT_PROTOCOL     committed protocol file (default: discovered
                                inside the repository)
  SLM_EVAL_HOLDOUT_MANIFEST     committed split manifest (default: discovered
                                inside the repository)
  SLM_EVAL_HOLDOUT_NETWORK=1    tests may run ``slm-eval data fetch``
  SLM_EVAL_HOLDOUT_SEED_CACHE   populated dataset cache that each test's
                                temporary cache is seeded from (copied; files
                                larger than 256 MiB are symlinked); runtime
                                tests need its models/ directory (§2)
  SLM_EVAL_HOLDOUT_RUNTIME=1    the real model artifact and pinned runtime are
                                available to the implementation
  SLM_EVAL_HOLDOUT_RUN_TIMEOUT  seconds allowed for one real run (default 14400)
"""

from __future__ import annotations

import copy
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
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import pytest

ENV_PREFIX = "SLM_EVAL_HOLDOUT_"
CLI_TIMEOUT = 300

EXIT_OK = 0
EXIT_OTHER = 1
EXIT_INTEGRITY = 2
EXIT_PRECONDITION = 3
EXIT_SECURITY = 4

LABELS = ("CONSISTENT", "PROTOCOL-SENSITIVE", "UNEXPLAINED")
PROVENANCE_VALUES = frozenset(
    {"source-known-paper", "source-known-blog", "vendor-filled", "inferred", "local-choice"}
)
REASONING_MODES = ("hybrid", "forced_think", "forced_non_think")
SAMPLING_FIELDS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "presence_penalty",
    "max_output_tokens",
    "seed_policy",
)
SENSITIVITY_METHOD = "paired bootstrap over items, primary scorer"
Z95 = 1.959963984540054

HEX64 = re.compile(r"[0-9a-f]{64}")
SKIP_DIRS = frozenset(
    {"node_modules", "__pycache__", "site-packages", "dist", "build", "venv", "env"}
)
TEST_DIR_NAMES = frozenset({"tests", "test", "fixtures", "testdata", "test_data"})
TEXT_FIELD_NAMES = frozenset(
    {"problem", "solution", "answer", "question", "text", "prompt", "reference", "reference_answer"}
)
LARGE_FILE = 256 * 1024 * 1024


# ---------------------------------------------------------------------------
# hashing and small predicates
# ---------------------------------------------------------------------------


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


def is_hex64(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def loose_equal(a: Any, b: Any) -> bool:
    """Equality that tolerates a value being rendered as a string."""
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def new_run_id(tag: str = "fixture") -> str:
    return f"holdout-{tag}-{uuid.uuid4().hex[:10]}"


def item_id(index: int) -> str:
    return f"test/holdout/{index:03d}.json"


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------


def env_flag(name: str) -> bool:
    return os.environ.get(ENV_PREFIX + name, "").strip().lower() in {"1", "true", "yes", "on"}


def env_path(name: str) -> Path | None:
    value = os.environ.get(ENV_PREFIX + name, "").strip()
    return Path(value).expanduser() if value else None


def run_timeout() -> int:
    value = os.environ.get(ENV_PREFIX + "RUN_TIMEOUT", "").strip()
    return int(value) if value else 14400


def cli_command() -> list[str]:
    override = os.environ.get(ENV_PREFIX + "CMD", "").strip()
    if override:
        return shlex.split(override)
    found = shutil.which("slm-eval")
    if found is None:
        pytest.fail(
            "the `slm-eval` entry point (contract §3) is not on PATH; run the suite "
            "inside the project environment or set SLM_EVAL_HOLDOUT_CMD",
            pytrace=False,
        )
    return [found]


def describe(proc: subprocess.CompletedProcess, limit: int = 3000) -> str:
    out = proc.stdout or ""
    err = proc.stderr or ""
    return (
        f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
        f"--- stdout (last {limit} chars) ---\n{out[-limit:]}\n"
        f"--- stderr (last {limit} chars) ---\n{err[-limit:]}"
    )


class Sandbox:
    """Per-test dataset cache, run store and working directory."""

    def __init__(self, root: Path):
        self.root = root
        self.cache = root / "cache"
        self.runs = root / "runs"
        self.work = root / "work"
        for directory in (self.cache, self.runs, self.work):
            directory.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(ENV_PREFIX) and key not in ("SLM_EVAL_CACHE", "SLM_EVAL_RUNS")
        }
        env["SLM_EVAL_CACHE"] = str(self.cache)
        env["SLM_EVAL_RUNS"] = str(self.runs)
        self.env = env

    def cli(self, *args: Any, timeout: float = CLI_TIMEOUT, cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run one CLI command that does not start the runtime."""
        cmd = cli_command() + [str(a) for a in args]
        try:
            return subprocess.run(
                cmd,
                cwd=str(cwd or self.work),
                env=self.env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"`slm-eval {' '.join(map(str, args))}` did not finish within {timeout}s", pytrace=False)

    def spawn(self, *args: Any, cwd: Path | None = None) -> "Spawned":
        """Start a CLI command in its own session (for commands that start the runtime)."""
        return Spawned(self, [str(a) for a in args], cwd or self.work)

    def finish(self, *args: Any, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run a runtime-starting command to completion, never leaking its processes."""
        with self.spawn(*args, cwd=cwd) as sp:
            while True:
                sp.observe()
                code = sp.wait(1.0)
                if code is not None:
                    break
                if time.time() - sp.started > timeout:
                    sp.close()
                    pytest.fail(f"`slm-eval {' '.join(map(str, args))}` did not finish within {timeout}s", pytrace=False)
            return sp.completed()

    def run_dirs(self) -> list[Path]:
        """Run directories; `_verification/` holds verification records, not a run (§2)."""
        return sorted(p for p in self.runs.iterdir() if p.is_dir() and not p.name.startswith("_"))

    def verification_record(self, protocol_sha256: str, name: str) -> Path:
        """§3: $SLM_EVAL_RUNS/_verification/<protocol_sha256>/<name>."""
        return self.runs / "_verification" / protocol_sha256 / name


# ---------------------------------------------------------------------------
# processes, sockets and HTTP (runtime tests only)
# ---------------------------------------------------------------------------


def process_table() -> dict[int, tuple[int, str]]:
    res = subprocess.run(
        ["ps", "-A", "-ww", "-o", "pid=,ppid=,command="],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    table: dict[int, tuple[int, str]] = {}
    for line in res.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        table[pid] = (ppid, parts[2] if len(parts) > 2 else "")
    return table


def descendants(root_pid: int, table: dict[int, tuple[int, str]]) -> list[int]:
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    found: list[int] = []
    stack = [root_pid]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def command_of(pid: int) -> str | None:
    res = subprocess.run(
        ["ps", "-ww", "-o", "stat=,command=", "-p", str(pid)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    line = res.stdout.strip()
    if not line:
        return None
    stat, _, command = line.partition(" ")
    if stat.startswith("Z"):
        return None
    return command.strip()


def pid_alive_as(pid: int, command: str) -> bool:
    """True if `pid` is alive and still runs the command recorded for it."""
    current = command_of(pid)
    return current is not None and current == command


def environment_of(pid: int) -> str:
    """Best-effort view of a process environment (empty when not observable)."""
    proc_environ = Path(f"/proc/{pid}/environ")
    try:
        if proc_environ.exists():
            return proc_environ.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""
    res = subprocess.run(
        ["ps", "-E", "-ww", "-o", "command=", "-p", str(pid)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return res.stdout


def lsof_binary() -> str:
    found = shutil.which("lsof") or ("/usr/sbin/lsof" if Path("/usr/sbin/lsof").exists() else None)
    if found is None:
        pytest.skip("lsof is required to inspect the runtime's listening sockets")
    return found


def listening_sockets(pids: Iterable[int]) -> list[tuple[int, str, int]]:
    """(pid, host, port) for every TCP LISTEN socket owned by `pids`."""
    pids = [p for p in pids]
    if not pids:
        return []
    res = subprocess.run(
        [lsof_binary(), "-nP", "-a", "-p", ",".join(map(str, pids)), "-iTCP", "-sTCP:LISTEN", "-Fpn"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    sockets: list[tuple[int, str, int]] = []
    current: int | None = None
    for line in res.stdout.splitlines():
        if line.startswith("p"):
            try:
                current = int(line[1:])
            except ValueError:
                current = None
        elif line.startswith("n") and current is not None:
            name = line[1:]
            host, _, port = name.rpartition(":")
            try:
                sockets.append((current, host.strip("[]"), int(port)))
            except ValueError:
                continue
    return sockets


def port_listening(port: int) -> bool:
    res = subprocess.run(
        [lsof_binary(), "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return bool(res.stdout.strip())


def ephemeral_port_range() -> tuple[int, int]:
    try:
        first = subprocess.run(["sysctl", "-n", "net.inet.ip.portrange.first"], capture_output=True, text=True)
        last = subprocess.run(["sysctl", "-n", "net.inet.ip.portrange.last"], capture_output=True, text=True)
        if first.returncode == 0 and last.returncode == 0:
            return int(first.stdout.strip()), int(last.stdout.strip())
    except (OSError, ValueError):
        pass
    try:
        low, high = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()
        return int(low), int(high)
    except (OSError, ValueError):
        return 49152, 65535


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_status(url: str, method: str = "GET", headers: dict | None = None, body: bytes | None = None,
                timeout: float = 5.0) -> int | None:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with _NO_PROXY_OPENER.open(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


PROBE_GET = (
    "/", "/health", "/v1/health", "/models", "/v1/models", "/props", "/slots", "/metrics", "/api/tags",
)
PROBE_POST = (
    "/completion", "/completions", "/v1/completions", "/chat/completions", "/v1/chat/completions",
    "/tokenize", "/detokenize", "/embedding", "/embeddings", "/v1/embeddings", "/infill",
    "/apply-template", "/rerank",
)
PROBE_BODY = json.dumps(
    {"prompt": "1+1=", "n_predict": 1, "max_tokens": 1, "content": "1+1=", "tokens": [1],
     "messages": [{"role": "user", "content": "1+1="}]}
).encode("utf-8")
PROBE_HEADERS = {
    "no-key": {},
    "wrong-bearer": {"Authorization": "Bearer holdout-wrong-key-0000000000000000"},
    "wrong-x-api-key": {"X-Api-Key": "holdout-wrong-key-0000000000000000"},
}


class Spawned:
    """A CLI process started in its own session, with its process tree tracked."""

    def __init__(self, sandbox: Sandbox, args: list[str], cwd: Path):
        tag = uuid.uuid4().hex[:8]
        self.stdout_path = sandbox.root / f"spawn-{tag}.stdout"
        self.stderr_path = sandbox.root / f"spawn-{tag}.stderr"
        self._out = open(self.stdout_path, "wb")
        self._err = open(self.stderr_path, "wb")
        self.args = cli_command() + args
        self.started = time.time()
        self.proc = subprocess.Popen(
            self.args,
            cwd=str(cwd),
            env=sandbox.env,
            stdin=subprocess.DEVNULL,
            stdout=self._out,
            stderr=self._err,
            start_new_session=True,
        )
        self.pid = self.proc.pid
        self.seen: dict[int, str] = {}
        self.listeners: dict[tuple[int, int], str] = {}

    def __enter__(self) -> "Spawned":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def observe(self) -> list[int]:
        """Record every live process in the tree; return the live pids."""
        table = process_table()
        pids = [self.pid] + descendants(self.pid, table)
        live = [p for p in pids if p in table]
        for pid in live:
            self.seen.setdefault(pid, table[pid][1])
        return live

    def observe_listeners(self) -> list[tuple[int, str, int]]:
        sockets = listening_sockets(self.observe())
        for pid, host, port in sockets:
            self.listeners[(pid, port)] = host
        return sockets

    def wait(self, timeout: float) -> int | None:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def interrupt(self) -> None:
        try:
            os.kill(self.pid, signal.SIGINT)
        except ProcessLookupError:
            pass

    def wait_for_listener(self, timeout: float) -> list[tuple[int, str, int]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return []
            sockets = self.observe_listeners()
            if sockets:
                return sockets
            time.sleep(0.25)
        return []

    def stdout(self) -> str:
        self._out.flush()
        return self.stdout_path.read_bytes().decode("utf-8", "replace")

    def stderr(self) -> str:
        self._err.flush()
        return self.stderr_path.read_bytes().decode("utf-8", "replace")

    def completed(self) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(self.args, self.proc.returncode, self.stdout(), self.stderr())

    def survivors(self) -> list[int]:
        """Processes of the tree (other than the CLI itself) still alive."""
        return [pid for pid, cmd in self.seen.items() if pid != self.pid and pid_alive_as(pid, cmd)]

    def close(self) -> None:
        """Last-resort cleanup so that a failing test never leaks a server."""
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        for pid in self.survivors():
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        for fh in (self._out, self._err):
            if not fh.closed:
                fh.close()


def wait_until(predicate: Callable[[], bool], timeout: float, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# filesystem helpers
# ---------------------------------------------------------------------------


def iter_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def snapshot(*roots: Path) -> dict[str, tuple[int, int]]:
    """Path -> (size, mtime_ns) for every file under `roots`, hidden files included."""
    state: dict[str, tuple[int, int]] = {}
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    st = path.lstat()
                except OSError:
                    continue
                state[str(path)] = (st.st_size, st.st_mtime_ns)
    return state


def file_bytes(*roots: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                path = Path(dirpath) / name
                out[str(path.relative_to(root))] = path.read_bytes()
    return out


def files_containing(needle: bytes, *roots: Path) -> list[str]:
    hits: list[str] = []
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    if path.is_symlink() or path.stat().st_size > LARGE_FILE:
                        continue
                    if needle in path.read_bytes():
                        hits.append(str(path))
                except OSError:
                    continue
    return hits


def replicate_tree(source: Path, target: Path) -> None:
    """Copy `source` into `target`; files above LARGE_FILE are symlinked, not copied."""
    for dirpath, _, filenames in os.walk(source, followlinks=True):
        rel = Path(dirpath).relative_to(source)
        (target / rel).mkdir(parents=True, exist_ok=True)
        for name in filenames:
            src = Path(dirpath) / name
            dst = target / rel / name
            real = src.resolve()
            if real.stat().st_size > LARGE_FILE:
                os.symlink(real, dst)
            else:
                shutil.copy2(real, dst)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> Path:
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


# ---------------------------------------------------------------------------
# JSON tree helpers: the contract fixes some field names and leaves others
# open, so these helpers look for values without assuming unspecified names
# ---------------------------------------------------------------------------


def walk(obj: Any, path: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    yield path, obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk(value, path + (str(key),))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk(value, path + (index,))


def numbers(obj: Any) -> list[float]:
    return [float(v) for _, v in walk(obj) if is_number(v)]


def strings(obj: Any) -> list[str]:
    return [v for _, v in walk(obj) if isinstance(v, str)]


def all_keys(obj: Any) -> list[str]:
    return [p[-1] for p, _ in walk(obj) if p and isinstance(p[-1], str)]


def text_of(obj: Any) -> str:
    return "\n".join(all_keys(obj) + strings(obj))


def _key_hit(path: tuple, needles: tuple[str, ...]) -> bool:
    return any(isinstance(k, str) and any(n in k.lower() for n in needles) for k in path)


def numbers_under(obj: Any, *needles: str) -> list[float]:
    lowered = tuple(n.lower() for n in needles)
    return [float(v) for p, v in walk(obj) if is_number(v) and _key_hit(p, lowered)]


def subtrees_mentioning(obj: Any, needle: str) -> list[Any]:
    """Dicts that have a key or string value containing `needle`, and values under such keys."""
    needle = needle.lower()
    found = []
    for _, value in walk(obj):
        if isinstance(value, dict):
            if any(needle in k.lower() for k in value) or any(
                isinstance(v, str) and needle in v.lower() for v in value.values()
            ):
                found.append(value)
            for key, sub in value.items():
                if needle in key.lower():
                    found.append(sub)
    return found


def has_close(values: Iterable[float], target: float, tol: float = 5e-4) -> bool:
    """§3: accuracies and rates are fractions in [0, 1], so no percent form is accepted."""
    return any(abs(value - target) <= tol for value in values)


_LO = frozenset({"lo", "low", "lower", "lb"})
_HI = frozenset({"hi", "high", "upper", "ub"})


def _tokens(key: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", key.lower()))


def intervals(obj: Any) -> list[tuple[float, float]]:
    found: list[tuple[float, float]] = []
    for _, value in walk(obj):
        if isinstance(value, list) and len(value) == 2 and all(is_number(x) for x in value):
            found.append((float(value[0]), float(value[1])))
        elif isinstance(value, dict):
            lows = [float(x) for k, x in value.items() if is_number(x) and _tokens(k) & _LO]
            highs = [float(x) for k, x in value.items() if is_number(x) and _tokens(k) & _HI]
            found.extend((a, b) for a in lows for b in highs)
    return found


def has_interval(obj: Any, lo: float, hi: float, tol: float = 1e-3) -> bool:
    return any(abs(a - lo) <= tol and abs(b - hi) <= tol for a, b in intervals(obj))


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


_LABEL_RE = re.compile(r"^(CONSISTENT|PROTOCOL-SENSITIVE|UNEXPLAINED)\b")


def labels_in_json(obj: Any) -> set[str]:
    found = set()
    for value in strings(obj):
        match = _LABEL_RE.match(value.strip())
        if match:
            found.add(match.group(1))
    return found


def label_in_text(label: str, text: str) -> bool:
    return re.search(rf"(?<![A-Z-]){re.escape(label)}(?![A-Z-])", text) is not None


# ---------------------------------------------------------------------------
# rescore.json (§6.4)
# ---------------------------------------------------------------------------

RESCORE_RECORD_KEYS = frozenset({"item_id", "sample_index", "stored", "recomputed", "match"})


def _verdict_pair(value: Any) -> tuple[bool, bool] | None:
    if isinstance(value, dict) and isinstance(value.get("primary"), bool) and isinstance(value.get("secondary"), bool):
        return value["primary"], value["secondary"]
    return None


def rescore_problems(doc: Any, run_id: str, pairs: set[tuple[str, int]]) -> list[str]:
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
            problems.append(f"record lacks {sorted(RESCORE_RECORD_KEYS)}: {record!r}"[:300])
            continue
        stored, recomputed = _verdict_pair(record["stored"]), _verdict_pair(record["recomputed"])
        if stored is None or recomputed is None:
            problems.append(f"record {record['item_id']}#{record['sample_index']}: stored/recomputed not "
                            "{primary: bool, secondary: bool}")
        elif record["match"] is not (stored == recomputed):
            problems.append(f"record {record['item_id']}#{record['sample_index']}: match disagrees with stored/recomputed")
        seen.append((record["item_id"], record["sample_index"]))
    if len(seen) != len(set(seen)) or set(seen) != set(pairs):
        problems.append(f"records cover {sorted(set(seen))[:5]}..., expected each of {len(pairs)} item x sample pairs once")
    matches = [r.get("match") for r in records if isinstance(r, dict)]
    if isinstance(doc.get("all_match"), bool) and doc["all_match"] is not all(m is True for m in matches):
        problems.append("all_match disagrees with the records' match flags")
    return problems


def rescore_by_pair(doc: dict) -> dict[tuple[str, int], dict]:
    return {(r["item_id"], r["sample_index"]): r for r in doc.get("records") or []
            if isinstance(r, dict) and "item_id" in r and "sample_index" in r}


# ---------------------------------------------------------------------------
# repository discovery and the committed contract files
# ---------------------------------------------------------------------------


class YamlUnavailable(Exception):
    pass


def yaml_load(text: str) -> Any:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        pass
    try:
        from ruamel.yaml import YAML  # type: ignore
    except ImportError as exc:
        raise YamlUnavailable("no YAML parser (PyYAML or ruamel.yaml) is importable") from exc
    return YAML(typ="safe", pure=True).load(text)


def yaml_or_skip(text: str) -> Any:
    try:
        return yaml_load(text)
    except YamlUnavailable as exc:
        pytest.skip(str(exc))


@functools.lru_cache(maxsize=None)
def _repo_root_or_none() -> Path | None:
    configured = env_path("REPO")
    if configured is not None:
        return configured.resolve() if configured.is_dir() else None
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        pyproject = candidate / "pyproject.toml"
        try:
            if pyproject.is_file() and "slm-eval" in pyproject.read_text(encoding="utf-8", errors="replace"):
                return candidate
        except OSError:
            continue
    return None


def _choose(root: Path, candidates: list[Path], preferred: str) -> tuple[Path | None, str]:
    if not candidates:
        return None, "no candidate found"
    non_test = [
        p for p in candidates if not (TEST_DIR_NAMES & {part.lower() for part in p.relative_to(root).parts[:-1]})
    ]
    pool = non_test or candidates
    if len(pool) == 1:
        return pool[0], ""
    named = [p for p in pool if p.name == preferred]
    if len(named) == 1:
        return named[0], ""
    return None, "ambiguous candidates: " + ", ".join(str(p) for p in pool[:10])


def _is_protocol_file(path: Path) -> bool:
    data = yaml_load(path.read_text(encoding="utf-8"))
    return isinstance(data, dict) and "protocol_version" in data and "sampling" in data


def _is_manifest_file(path: Path) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    return isinstance(data, dict) and "manifest_version" in data and "items" in data


@functools.lru_cache(maxsize=None)
def _discover(kind: str) -> tuple[Path | None, str]:
    configured = env_path("PROTOCOL" if kind == "protocol" else "MANIFEST")
    if configured is not None:
        return (configured, "") if configured.is_file() else (None, f"configured path {configured} is not a file")
    root = _repo_root_or_none()
    if root is None:
        return None, "repository root not found"
    suffixes = (".yaml", ".yml") if kind == "protocol" else (".json",)
    accept = _is_protocol_file if kind == "protocol" else _is_manifest_file
    candidates = []
    for path in iter_files(root):
        if path.suffix.lower() not in suffixes:
            continue
        try:
            if path.stat().st_size <= 50_000_000 and accept(path):
                candidates.append(path)
        except YamlUnavailable:
            return None, "no YAML parser available to discover the protocol file"
        except Exception:
            continue
    return _choose(root, candidates, "protocol.yaml" if kind == "protocol" else "manifest.json")


def protocol_path() -> Path:
    path, why = _discover("protocol")
    if path is None:
        pytest.fail(f"committed protocol file not located ({why}); set SLM_EVAL_HOLDOUT_PROTOCOL", pytrace=False)
    return path


def manifest_path() -> Path:
    path, why = _discover("manifest")
    if path is None:
        pytest.fail(f"committed split manifest not located ({why}); set SLM_EVAL_HOLDOUT_MANIFEST", pytrace=False)
    return path


def dig(obj: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj


# Contract §4 v1 draft defaults with every placeholder filled in. Used only
# when the committed protocol cannot be located or parsed.
SYNTHETIC_PROTOCOL: dict = {
    "protocol_version": "v1-draft",
    "model": {
        "artifact": {
            "file": "Qwen3-8B-Q4_K_M.gguf",
            "sha256": "1" * 64,
            "quantization": "Q4_K_M",
            "provenance": "local-choice",
        }
    },
    "runtime": {"build": "b10412", "invocation": "keyed-run-scoped-server", "provenance": "local-choice"},
    "prompt": {
        "template": "{problem}\n\nPlease reason step by step, and put your final answer within \\boxed{}.",
        "system_prompt": None,
        "few_shot": 0,
        "provenance": "vendor-filled",
    },
    "reasoning_mode": {"value": "hybrid", "provenance": "inferred"},
    "sampling": {
        "temperature": {"value": 0.6, "provenance": "source-known-paper"},
        "top_p": {"value": 0.95, "provenance": "source-known-paper"},
        "top_k": {"value": 20, "provenance": "vendor-filled"},
        "min_p": {"value": 0, "provenance": "vendor-filled"},
        "presence_penalty": {"value": 0, "provenance": "local-choice"},
        "max_output_tokens": {"value": 32768, "provenance": "source-known-paper"},
        "seed_policy": {"value": "seed = seed_base + sample_index", "provenance": "local-choice"},
    },
    "extraction": {"id": "holdout-extractor", "version": "0.0.0"},
    "scorers": {
        "primary": {"id": "prm800k-grader", "pin": "0" * 40},
        "secondary": {"id": "math-verify", "pin": "0.0.0"},
    },
    "dataset": {
        "source": {
            "url": "https://example.invalid/holdout-dataset",
            "commit": "0" * 40,
            "files": [{"path": "test.jsonl", "sha256": "2" * 64}],
        }
    },
}


def dump_protocol(data: dict) -> bytes:
    """Serialize a protocol as JSON, which every YAML 1.1/1.2 parser accepts."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


class ProtocolContext:
    """The protocol fixture runs are recorded against.

    The committed protocol is used when it can be located and parsed, so that
    fixture runs carry the same extractor, scorer, model and runtime identities
    as the implementation; otherwise the contract §4 example is used.
    """

    def __init__(self, raw: bytes, data: dict, path: Path | None):
        self.raw = raw
        self.data = data
        self.path = path

    @property
    def discovered(self) -> bool:
        return self.path is not None

    @property
    def note(self) -> str:
        if self.discovered:
            return f"fixture protocol: committed file {self.path}"
        return "fixture protocol: SYNTHETIC (committed protocol not located/parsed)"

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.raw)

    @property
    def version(self) -> str:
        return str(self.data.get("protocol_version"))

    @property
    def extractor(self) -> dict:
        extraction = self.data.get("extraction") or {}
        return {"id": extraction.get("id"), "version": extraction.get("version")}

    @property
    def scorers(self) -> list[dict]:
        scorers = self.data.get("scorers") or {}
        out = []
        for role in ("primary", "secondary"):
            entry = scorers.get(role) or {}
            out.append({"id": entry.get("id"), "pin": entry.get("pin")})
        return out

    @property
    def model_sha256(self) -> str:
        return str(dig(self.data, "model", "artifact", "sha256"))

    @property
    def runtime_build(self) -> str:
        return str(dig(self.data, "runtime", "build"))

    def variant(self, changes: dict[tuple[str, ...], Any], suffix: str) -> tuple[bytes, dict]:
        """A new protocol version differing from this one in `changes` only."""
        data = copy.deepcopy(self.data)
        data["protocol_version"] = f"{self.version}-holdout-{suffix}"
        for path, value in changes.items():
            node = data
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = value
        return dump_protocol(data), data


@functools.lru_cache(maxsize=None)
def protocol_context() -> ProtocolContext:
    path, _ = _discover("protocol")
    if path is not None:
        try:
            raw = path.read_bytes()
            data = yaml_load(raw.decode("utf-8"))
            if isinstance(data, dict):
                return ProtocolContext(raw, data, path)
        except Exception:
            pass
    data = copy.deepcopy(SYNTHETIC_PROTOCOL)
    return ProtocolContext(dump_protocol(data), data, None)


def committed_protocol() -> tuple[Path, bytes, dict]:
    path = protocol_path()
    raw = path.read_bytes()
    data = yaml_or_skip(raw.decode("utf-8"))
    assert isinstance(data, dict), f"{path} does not parse to a mapping"
    return path, raw, data


def pinned_source() -> dict:
    """dataset.source from the committed protocol, else the committed manifest's source."""
    ctx = protocol_context()
    if ctx.discovered and isinstance(dig(ctx.data, "dataset", "source"), dict):
        return ctx.data["dataset"]["source"]
    return read_json(manifest_path())["source"]


# ---------------------------------------------------------------------------
# run-store fixtures (§6.1)
# ---------------------------------------------------------------------------


def boxed(answer: str) -> str:
    return "\\boxed{" + answer + "}"


def think(final: str, thought: str = "Let me work through this carefully, one step at a time.") -> str:
    return "<think>\n" + thought + "\n</think>\n\n" + final


def make_line(iid: str, sample_index: int, raw_output: str, reference_answer: str, *, answer: str | None,
              rule: str, status: str, primary: bool, secondary: bool, thinking_present: bool | None = None,
              truncated: bool = False, finish_reason: str | None = None,
              tokens_generated: int | None = None) -> dict:
    return {
        "item_id": iid,
        "sample_index": sample_index,
        "raw_output": raw_output,
        "reference_answer": reference_answer,
        "extraction": {"answer": answer, "rule": rule, "status": status},
        "verdicts": {"primary": primary, "secondary": secondary},
        "thinking_present": ("</think>" in raw_output) if thinking_present is None else thinking_present,
        "truncated": truncated,
        "finish_reason": finish_reason or ("length" if truncated else "stop"),
        "tokens_generated": tokens_generated if tokens_generated is not None else max(1, len(raw_output) // 4),
    }


def boxed_line(iid: str, sample_index: int, reference: str, answer: str, *, primary: bool | None = None,
               secondary: bool | None = None, with_think: bool = True, prefix: str = "") -> dict:
    final = prefix + "The final answer is " + boxed(answer) + "."
    raw = think(final) if with_think else final
    correct = answer == reference
    return make_line(
        iid, sample_index, raw, reference, answer=answer, rule="boxed_last", status="ok",
        primary=correct if primary is None else primary,
        secondary=correct if secondary is None else secondary,
    )


def no_answer_line(iid: str, sample_index: int, reference: str, *, with_think: bool = True) -> dict:
    final = "I was not able to finish this problem."
    raw = think(final) if with_think else final
    return make_line(iid, sample_index, raw, reference, answer=None, rule="none", status="no_answer",
                     primary=False, secondary=False)


def truncated_line(iid: str, sample_index: int, reference: str) -> dict:
    raw = "<think>\nStill working through the cases, and the next one to check is"
    return make_line(iid, sample_index, raw, reference, answer=None, rule="none", status="truncated",
                     primary=False, secondary=False, thinking_present=True, truncated=True,
                     finish_reason="length")


def pattern_lines(n: int, primary_correct: Iterable[int], secondary_correct: Iterable[int], *,
                  sample_index: int = 0, think_ids: Iterable[int] | None = None) -> list[dict]:
    """n items whose stored verdicts follow the given index sets."""
    primary_set, secondary_set = set(primary_correct), set(secondary_correct)
    think_set = set(range(n)) if think_ids is None else set(think_ids)
    lines = []
    for i in range(n):
        reference = str(100 + i)
        right = i in primary_set or i in secondary_set
        lines.append(
            boxed_line(item_id(i), sample_index, reference, reference if right else str(500 + i),
                       primary=i in primary_set, secondary=i in secondary_set, with_think=i in think_set)
        )
    return lines


def fixture_manifest(item_ids: Iterable[str], protocol_data: dict, *, note: str = "holdout fixture manifest",
                     tiers: Iterable[str] = ("smoke",)) -> bytes:
    """A §5 manifest whose dev items are `item_ids`, placed in each named tier."""
    ids = sorted(set(item_ids))
    tier_names = set(tiers)
    doc = {
        "manifest_version": "1",
        "source": dig(protocol_data, "dataset", "source") or {},
        "membership_derivation": {
            "method": "unique_id",
            "independent_source": {"url": "https://example.invalid/holdout-index", "revision": "0" * 40,
                                   "sha256": sha256_text("holdout independent source")},
        },
        "normalization": {"version": "holdout-1", "near_duplicate": {"method": "holdout", "threshold": 0.9}},
        "items": [{"id": i, "role": "dev", "content_sha256": sha256_text(i), "subject": "Algebra", "level": 1}
                  for i in ids],
        "exclusions": [],
        "tiers": {"seed": 0, "smoke": ids if "smoke" in tier_names else [],
                  "validation": ids if "validation" in tier_names else []},
        "provenance_note": note,
    }
    return (json.dumps(doc, indent=2) + "\n").encode("utf-8")


def write_run(runs_dir: Path, lines: list[dict], *, ctx: ProtocolContext, run_id: str | None = None,
              protocol_bytes: bytes | None = None, protocol_data: dict | None = None, seed_base: int = 1000,
              tier: str = "smoke", manifest_bytes: bytes | None = None, status: str = "completed",
              overrides: dict | None = None) -> tuple[str, Path]:
    """Write a complete §6.1 run directory and return (run_id, run_dir).

    The directory holds byte copies of its protocol (`protocol.yaml`) and
    manifest (`manifest.json`) whose SHA-256 values are the recorded
    protocol_sha256 and manifest_sha256 (§6.1).
    """
    run_id = run_id or new_run_id()
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    protocol_bytes = ctx.raw if protocol_bytes is None else protocol_bytes
    protocol_data = ctx.data if protocol_data is None else protocol_data
    k = max(line["sample_index"] for line in lines) + 1
    seeds = [seed_base + s for s in range(k)]
    if manifest_bytes is None:
        manifest_bytes = fixture_manifest((line["item_id"] for line in lines), protocol_data)
    records = []
    for index, line in enumerate(lines):
        records.append({
            "item_id": line["item_id"],
            "sample_index": line["sample_index"],
            "seed": seed_base + line["sample_index"],
            "prompt_sha256": sha256_text("holdout fixture prompt for " + line["item_id"]),
            "raw_output": line["raw_output"],
            "raw_output_sha256": sha256_text(line["raw_output"]),
            "thinking_present": line["thinking_present"],
            "truncated": line["truncated"],
            "finish_reason": line["finish_reason"],
            "tokens_generated": line["tokens_generated"],
            "reference_answer": line["reference_answer"],
            "reference_sha256": sha256_text(line["reference_answer"]),
            "extraction": dict(line["extraction"]),
            "verdicts": dict(line["verdicts"]),
            "timing_ms": 1000 + 10 * index,
        })
    scorers = []
    for role in ("primary", "secondary"):
        entry = dig(protocol_data, "scorers", role) or {}
        scorers.append({"id": entry.get("id"), "pin": entry.get("pin")})
    extraction = protocol_data.get("extraction") or {}
    run_json = {
        "run_id": run_id,
        "protocol_version": protocol_data.get("protocol_version"),
        "protocol_sha256": sha256_bytes(protocol_bytes),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "tier": tier,
        "k": k,
        "seeds": seeds,
        "model_artifact_sha256": dig(protocol_data, "model", "artifact", "sha256"),
        "runtime_build": dig(protocol_data, "runtime", "build"),
        "adapter": {"id": "holdout-fixture-adapter", "version": "0.0.0"},
        "extractor": {"id": extraction.get("id"), "version": extraction.get("version")},
        "scorers": scorers,
        "executor": "holdout-fixture",
        "host": {"os": "Darwin", "arch": "arm64", "memory_gb": 64},
        "attempts": [{
            "n": 1,
            "started": "2026-10-06T10:00:00Z",
            "ended": "2026-10-06T10:30:00Z",
            "status": status,
            "resumed_from": None,
        }],
        "status": status,
    }
    if overrides:
        run_json.update(overrides)
    write_json(run_dir / "run.json", run_json)
    with open(run_dir / "items.jsonl", "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    verification = {"holdout_fixture": True, "passed": True, "deviations": [], "checks": {}}
    write_json(run_dir / "runtime-verification.json", verification)
    write_json(run_dir / "artifact-verification.json", verification)
    (run_dir / "protocol.yaml").write_bytes(protocol_bytes)
    (run_dir / "manifest.json").write_bytes(manifest_bytes)
    return run_id, run_dir


def rewrite_items(run_dir: Path, change: Callable[[dict], None]) -> None:
    path = run_dir / "items.jsonl"
    records = read_jsonl(path)
    for record in records:
        change(record)
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def pairs_of(lines: Iterable[dict]) -> set[tuple[str, int]]:
    return {(line["item_id"], line["sample_index"]) for line in lines}


def sensitivity_doc(base_run: str, base_sha: str, comparisons: list[dict]) -> dict:
    """A §7.1 sensitivity file; each comparison gives factor, base/variant accuracy and interval."""
    out = []
    for index, c in enumerate(comparisons):
        out.append({
            "variant_run": c.get("variant_run", f"holdout-variant-{index}"),
            "factor": c["factor"],
            "base_value": c.get("base_value", "base"),
            "variant_value": c.get("variant_value", "variant"),
            "paired_items": c.get("paired_items", 20),
            "base_accuracy": c["base_accuracy"],
            "variant_accuracy": c["variant_accuracy"],
            "effect": c["variant_accuracy"] - c["base_accuracy"],
            "effect_interval_95": list(c["interval"]),
            "method": SENSITIVITY_METHOD,
            "bootstrap_seed": 20261006,
            "bootstrap_resamples": 2000,
        })
    return {
        "sensitivity_version": "1",
        "base_run": base_run,
        "base_protocol_sha256": base_sha,
        "comparisons": out,
    }


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------


def run_report(sandbox: Sandbox, run_id: str, *extra: Any) -> tuple[subprocess.CompletedProcess, Any, str]:
    """Run `report` without --out-dir and read report.json / report.md from the run directory (§3)."""
    proc = sandbox.cli("report", "--run", run_id, *extra)
    assert proc.returncode == EXIT_OK, describe(proc)
    run_dir = sandbox.runs / run_id
    json_path, md_path = run_dir / "report.json", run_dir / "report.md"
    assert json_path.is_file() and md_path.is_file(), (
        "report.json and report.md not both written to the run directory (the default --out-dir, §3)\n"
        + describe(proc)
    )
    return proc, read_json(json_path), md_path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# manifests (§5, §5.1)
# ---------------------------------------------------------------------------


def manifest_problems(manifest: Any, *, strict_verdict_source: bool = True) -> list[str]:
    """Schema and invariant violations of a §5 manifest."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest is not a JSON object"]
    for key in ("manifest_version", "source", "membership_derivation", "normalization", "items",
                "exclusions", "tiers", "provenance_note"):
        if key not in manifest:
            problems.append(f"missing top-level key {key!r}")
    if manifest.get("manifest_version") != "1":
        problems.append(f"manifest_version is {manifest.get('manifest_version')!r}, expected '1'")
    items = manifest.get("items") or []
    if not isinstance(items, list):
        return problems + ["items is not a list"]
    ids: list[str] = []
    roles: dict[str, str] = {}
    for entry in items:
        if not isinstance(entry, dict):
            problems.append("item entry is not an object")
            continue
        iid = entry.get("id")
        if not isinstance(iid, str) or not iid:
            problems.append(f"item without a string id: {entry!r}"[:200])
            continue
        ids.append(iid)
        roles[iid] = entry.get("role")
        if entry.get("role") not in ("test", "dev"):
            problems.append(f"item {iid}: role {entry.get('role')!r} not in test|dev")
        if not is_hex64(entry.get("content_sha256")):
            problems.append(f"item {iid}: content_sha256 is not 64 lowercase hex")
        if not isinstance(entry.get("subject"), str):
            problems.append(f"item {iid}: subject is not a string")
        if not is_int(entry.get("level")):
            problems.append(f"item {iid}: level is not an integer")
        leaked = TEXT_FIELD_NAMES & set(entry)
        if leaked:
            problems.append(f"item {iid}: carries text fields {sorted(leaked)}")
    if len(ids) != len(set(ids)):
        problems.append("item ids are not unique")
    dev = {i for i, r in roles.items() if r == "dev"}
    test = {i for i, r in roles.items() if r == "test"}
    excluded: set[str] = set()
    for entry in manifest.get("exclusions") or []:
        if not isinstance(entry, dict):
            problems.append("exclusion entry is not an object")
            continue
        eid = entry.get("id")
        if eid in test:
            problems.append(f"exclusion targets test item {eid}")
        elif eid not in dev:
            problems.append(f"exclusion id {eid!r} is not a dev item of this manifest")
        if entry.get("match") not in ("exact", "near"):
            problems.append(f"exclusion {eid}: match {entry.get('match')!r} not in exact|near")
        if entry.get("verdict") not in ("exclude", "keep"):
            problems.append(f"exclusion {eid}: verdict {entry.get('verdict')!r} not in exclude|keep")
        if not is_number(entry.get("similarity")) or not 0.0 <= float(entry.get("similarity")) <= 1.0:
            problems.append(f"exclusion {eid}: similarity {entry.get('similarity')!r} not a number in [0, 1]")
        if not isinstance(entry.get("matched_id"), str):
            problems.append(f"exclusion {eid}: matched_id is not a string")
        if strict_verdict_source and entry.get("verdict_source") != "maintainer":
            problems.append(f"exclusion {eid}: verdict_source {entry.get('verdict_source')!r} is not 'maintainer'")
        if entry.get("verdict") == "exclude":
            excluded.add(eid)
    tiers = manifest.get("tiers")
    if not isinstance(tiers, dict):
        return problems + ["tiers is not an object"]
    if not is_int(tiers.get("seed")):
        problems.append("tiers.seed is not an integer")
    for name in ("smoke", "validation"):
        members = tiers.get(name)
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            problems.append(f"tiers.{name} is not a list of ids")
            continue
        if len(members) != len(set(members)):
            problems.append(f"tiers.{name} has duplicate ids")
        not_dev = [m for m in members if m not in dev]
        if not_dev:
            problems.append(f"tiers.{name} contains non-dev ids, e.g. {not_dev[:3]}")
        dropped = [m for m in members if m in excluded]
        if dropped:
            problems.append(f"tiers.{name} contains excluded ids, e.g. {dropped[:3]}")
    return problems


def manifest_roles(manifest: dict) -> tuple[set[str], set[str]]:
    test = {e["id"] for e in manifest.get("items", []) if isinstance(e, dict) and e.get("role") == "test"}
    dev = {e["id"] for e in manifest.get("items", []) if isinstance(e, dict) and e.get("role") == "dev"}
    return test, dev


def long_strings(obj: Any, limit: int, skip_keys: Iterable[str] = ()) -> list[tuple[tuple, str]]:
    skip = set(skip_keys)
    return [
        (p, v) for p, v in walk(obj)
        if isinstance(v, str) and len(v) > limit and not (p and p[-1] in skip)
    ]


# ---------------------------------------------------------------------------
# real dataset access (data tests only)
# ---------------------------------------------------------------------------


def populate_cache(sandbox: Sandbox) -> None:
    """Fill the sandbox dataset cache from the seed cache, else via `data fetch`."""
    seed = env_path("SEED_CACHE")
    if seed is not None:
        if not seed.is_dir():
            pytest.fail(f"SLM_EVAL_HOLDOUT_SEED_CACHE={seed} is not a directory", pytrace=False)
        replicate_tree(seed, sandbox.cache)
        return
    if env_flag("NETWORK"):
        proc = sandbox.cli("data", "fetch", timeout=3600)
        assert proc.returncode == EXIT_OK, "`slm-eval data fetch` failed\n" + describe(proc)
        return
    pytest.skip("needs SLM_EVAL_HOLDOUT_SEED_CACHE or SLM_EVAL_HOLDOUT_NETWORK=1")


def files_by_sha(root: Path, wanted: Iterable[str]) -> dict[str, Path]:
    wanted = set(wanted)
    found: dict[str, Path] = {}
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > LARGE_FILE:
                    continue
                digest = sha256_file(path)
            except OSError:
                continue
            if digest in wanted and digest not in found:
                found[digest] = path
    return found


def cached_records(root: Path) -> list[dict]:
    """Records with a string `problem` field from JSON / JSONL files in the cache."""
    records: list[dict] = []
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
            rows: list[Any] = []
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


def records_by_id(records: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for record in records:
        for key in ("unique_id", "id"):
            if isinstance(record.get(key), str):
                out.setdefault(record[key], record)
    return out


def leaked_problems(texts: Iterable[str], records: Iterable[dict], min_len: int = 40) -> list[str]:
    """Problem texts (whitespace-normalized, at least `min_len` chars) found inside `texts`."""
    haystacks = [normalize_ws(t) for t in texts if t and len(t) >= min_len]
    if not haystacks:
        return []
    leaks = []
    for record in records:
        problem = normalize_ws(record["problem"])
        if len(problem) < min_len:
            continue
        probe = problem[:120]
        if any(probe in h for h in haystacks):
            leaks.append(problem[:60])
    return leaks


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


def per_id_verdicts(pairs: list[dict]) -> dict[str, str]:
    """One verdict per flagged dev id, alternating exclude / keep over sorted ids."""
    ids = sorted({str(p.get("id")) for p in pairs})
    return {iid: ("exclude" if index % 2 == 0 else "keep") for index, iid in enumerate(ids)}


def verdicts_doc(flagged_path: Path, pairs: list[dict], by_id: dict[str, str]) -> dict:
    return {
        "verdicts_version": "1",
        "flagged_sha256": sha256_file(flagged_path),
        "verdicts": [{"pair": p["pair"], "verdict": by_id[str(p.get("id"))]} for p in pairs],
    }


# ---------------------------------------------------------------------------
# real runs (runtime tests only)
# ---------------------------------------------------------------------------


def verify_before_run(sandbox: Sandbox, protocol: Path) -> None:
    """Produce the verification artifacts a run copies in at start (§6.1)."""
    artifact = sandbox.cli("verify-artifact", "--protocol", protocol, timeout=3600)
    assert artifact.returncode == EXIT_OK, "verify-artifact failed before run\n" + describe(artifact)
    runtime = sandbox.finish("verify-runtime", "--protocol", protocol, timeout=3600)
    assert runtime.returncode == EXIT_OK, "verify-runtime failed before run\n" + describe(runtime)


def wait_for_run_dir(sandbox: Sandbox, sp: Spawned, timeout: float) -> Path | None:
    found: list[Path] = []

    def ready() -> bool:
        sp.observe()
        dirs = sandbox.run_dirs()
        if dirs:
            found.append(dirs[0])
            return True
        return sp.proc.poll() is not None

    wait_until(ready, timeout, 0.5)
    return found[0] if found else None


def wait_for_items(sandbox: Sandbox, sp: Spawned, minimum: int, timeout: float) -> Path | None:
    found: list[Path] = []

    def ready() -> bool:
        sp.observe_listeners()
        for run_dir in sandbox.run_dirs():
            if len(complete_jsonl_lines(run_dir / "items.jsonl")) >= minimum:
                found.append(run_dir)
                return True
        return sp.proc.poll() is not None

    wait_until(ready, timeout, 1.0)
    return found[0] if found else None


def interrupt_and_wait(sp: Spawned, timeout: float = 300) -> int:
    sp.interrupt()
    deadline = time.time() + timeout
    while time.time() < deadline:
        sp.observe()
        code = sp.wait(0.5)
        if code is not None:
            return code
    pytest.fail(f"slm-eval did not exit within {timeout}s of SIGINT", pytrace=False)
