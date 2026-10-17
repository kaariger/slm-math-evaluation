"""Visible specification tests — §10 runtime security and §3 verify-runtime (contract v0.5; §10 as amended in v0.4 and v0.5).

Contract-only: written before any runtime implementation commit was
supplied, with no runtime code seen. Tests use only the `slm-eval` CLI, the
server it starts, and the contract's files.

VS-RT-01 is hermetic. Every other test needs the REAL MODEL AND A LIVE
SERVER and is skipped unless all of the following are present:

  SLM_EVAL_TEST_RUNTIME=1     the pinned llama.cpp runtime and the real model
                              artifact are available to the implementation
  SLM_EVAL_TEST_SEED_CACHE    a populated dataset cache holding the pinned
                              dataset and models/<model.artifact.file> (§2);
                              copied into each test's temporary SLM_EVAL_CACHE
                              (files over 256 MiB are symlinked, not copied)
  committed protocol          SLM_EVAL_TEST_PROTOCOL, or the protocol.yaml in
                              the repository containing the pytest working
                              directory
  committed manifest          SLM_EVAL_TEST_MANIFEST, or the manifest found in
                              the repository (only for tests that start a run)
  tools                       `ps` and `lsof` on PATH (or /usr/sbin/lsof)

Every test uses its own temporary SLM_EVAL_CACHE, SLM_EVAL_RUNS and working
directory. Commands that start the runtime run in their own session; their
whole process tree is tracked and killed after the test, so a failing test
never leaks a server. SIGINT is sent to the CLI process only, never to its
process group. SLM_EVAL_TEST_CMD overrides the CLI command (shell-split).
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
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

EXIT_OK, EXIT_PRECONDITION = 0, 3
LARGE_FILE = 256 * 1024 * 1024
STARTUP = 1800.0
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
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

# ---------------------------------------------------------------------------
# environment, repository files, gating
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


class Env:
    """A temporary dataset cache, run store and working directory for one test."""

    def __init__(self, root: Path):
        self.root = root
        self.cache = root / "cache"
        self.runs = root / "runs"
        self.work = root / "work"
        for directory in (self.cache, self.runs, self.work):
            directory.mkdir(parents=True)
        self.vars = {k: v for k, v in os.environ.items()
                     if k not in ("SLM_EVAL_CACHE", "SLM_EVAL_RUNS") and not k.startswith("SLM_EVAL_TEST_")}
        self.vars["SLM_EVAL_CACHE"] = str(self.cache)
        self.vars["SLM_EVAL_RUNS"] = str(self.runs)

    def run(self, *args: object, timeout: float = 3600) -> subprocess.CompletedProcess:
        return subprocess.run(_command() + [str(a) for a in args], cwd=self.work, env=self.vars,
                              stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=timeout)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _explain(proc) -> str:
    return (f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
            f"--- stdout ---\n{(proc.stdout or '')[-2000:]}\n--- stderr ---\n{(proc.stderr or '')[-2000:]}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _candidates(accept, configured_var: str) -> list[Path]:
    configured = os.environ.get(configured_var, "").strip()
    if configured:
        return [Path(configured).expanduser()]
    root = _repo_root()
    if root is None:
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if accept(path):
                found.append(path)
    return [p for p in found if not TEST_DIRS & set(p.relative_to(root).parts)] or found


def _find_in_repo(accept, configured_var: str, what: str) -> Path:
    found = _candidates(accept, configured_var)
    if len(found) != 1:
        pytest.fail(f"expected one committed {what}, found {found}; set {configured_var}", pytrace=False)
    return found[0]


def protocol_path() -> Path:
    return _find_in_repo(lambda p: p.name == "protocol.yaml", "SLM_EVAL_TEST_PROTOCOL", "protocol.yaml")


def _is_manifest(path: Path) -> bool:
    if path.suffix != ".json":
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(doc, dict) and "manifest_version" in doc and "items" in doc


def manifest_path() -> Path:
    return _find_in_repo(_is_manifest, "SLM_EVAL_TEST_MANIFEST", "split manifest")


def require_live() -> None:
    if os.environ.get("SLM_EVAL_TEST_RUNTIME", "").strip() != "1":
        pytest.skip("needs the real model and a live server: set SLM_EVAL_TEST_RUNTIME=1")
    if not os.environ.get("SLM_EVAL_TEST_SEED_CACHE", "").strip():
        pytest.skip("needs SLM_EVAL_TEST_SEED_CACHE holding the dataset and models/ (§2)")
    if shutil.which("ps") is None or _lsof() is None:
        pytest.skip("needs ps and lsof")


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


# ---------------------------------------------------------------------------
# process trees, sockets and HTTP
# ---------------------------------------------------------------------------


def _lsof() -> str | None:
    return shutil.which("lsof") or ("/usr/sbin/lsof" if Path("/usr/sbin/lsof").exists() else None)


def _ps_table() -> dict[int, tuple[int, str]]:
    out = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,command="], capture_output=True,
                         encoding="utf-8", errors="replace").stdout
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) == 3 else "")
    return table


def _command_now(pid: int) -> str | None:
    line = subprocess.run(["ps", "-ww", "-o", "stat=,command=", "-p", str(pid)], capture_output=True,
                          encoding="utf-8", errors="replace").stdout.strip()
    if not line or line.startswith("Z"):
        return None
    return line.split(None, 1)[1] if " " in line else ""


def _environment(pid: int) -> str:
    proc_environ = Path(f"/proc/{pid}/environ")
    if proc_environ.exists():
        try:
            return proc_environ.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            return ""
    return subprocess.run(["ps", "-E", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True,
                          encoding="utf-8", errors="replace").stdout


def _listening(pids: list[int]) -> set[tuple[int, str, int]]:
    if not pids:
        return set()
    out = subprocess.run([_lsof(), "-nP", "-a", "-p", ",".join(map(str, pids)), "-iTCP", "-sTCP:LISTEN", "-Fpn"],
                         capture_output=True, encoding="utf-8", errors="replace").stdout
    found, pid = set(), None
    for line in out.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            host, _, port = line[1:].rpartition(":")
            if port.isdigit():
                found.add((pid, host.strip("[]"), int(port)))
    return found


def _port_open(port: int) -> bool:
    out = subprocess.run([_lsof(), "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True,
                         encoding="utf-8", errors="replace").stdout
    return bool(out.strip())


def ephemeral_range() -> tuple[int, int]:
    try:
        low = subprocess.run(["sysctl", "-n", "net.inet.ip.portrange.first"], capture_output=True, text=True)
        high = subprocess.run(["sysctl", "-n", "net.inet.ip.portrange.last"], capture_output=True, text=True)
        if low.returncode == 0 and high.returncode == 0:
            return int(low.stdout), int(high.stdout)
    except (OSError, ValueError):
        pass
    try:
        low_text, high_text = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()
        return int(low_text), int(high_text)
    except (OSError, ValueError):
        return 49152, 65535


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


PREFLIGHT_HEADERS = {"Origin": "http://localhost:8000", "Access-Control-Request-Headers": "authorization, content-type"}


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


class Live:
    """A CLI process in its own session, with its process tree and listening sockets tracked."""

    def __init__(self, env: Env, *args: object):
        tag = uuid.uuid4().hex[:8]
        self.out_path, self.err_path = env.root / f"{tag}.stdout", env.root / f"{tag}.stderr"
        self._out, self._err = open(self.out_path, "wb"), open(self.err_path, "wb")
        self.args = _command() + [str(a) for a in args]
        self.popen = subprocess.Popen(self.args, cwd=env.work, env=env.vars, stdin=subprocess.DEVNULL,
                                      stdout=self._out, stderr=self._err, start_new_session=True)
        self.tree: dict[int, str] = {}
        self.sockets: set[tuple[int, str, int]] = set()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.kill()

    def observe(self) -> list[int]:
        table = _ps_table()
        children: dict[int, list[int]] = {}
        for pid, (ppid, _) in table.items():
            children.setdefault(ppid, []).append(pid)
        live, stack = [], [self.popen.pid]
        while stack:
            pid = stack.pop()
            if pid in table:
                live.append(pid)
                self.tree.setdefault(pid, table[pid][1])
            stack.extend(children.get(pid, []))
        self.sockets |= _listening(live)
        return live

    def wait_listening(self, timeout: float = STARTUP) -> list[int]:
        deadline = time.time() + timeout
        while time.time() < deadline and self.popen.poll() is None:
            self.observe()
            if self.sockets:
                return sorted({port for _, _, port in self.sockets})
            time.sleep(0.2)
        return []

    def wait_ready(self, ports: list[int], timeout: float = STARTUP) -> bool:
        """Wait until /health answers 200 on every listening port (or the process exits)."""
        deadline = time.time() + timeout
        while time.time() < deadline and self.popen.poll() is None:
            if all(http(port, "GET", "/health", {})[0] == 200 for port in ports):
                return True
            time.sleep(0.5)
        return False

    def interrupt(self, timeout: float = 300) -> int | None:
        try:
            os.kill(self.popen.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.observe()
            if self.popen.poll() is not None:
                return self.popen.returncode
            time.sleep(0.3)
        return None

    def survivors(self) -> list[int]:
        return [pid for pid, cmd in self.tree.items() if pid != self.popen.pid and _command_now(pid) == cmd]

    def completed(self) -> subprocess.CompletedProcess:
        for fh in (self._out, self._err):
            fh.flush()
        return subprocess.CompletedProcess(self.args, self.popen.returncode,
                                           self.out_path.read_bytes().decode("utf-8", "replace"),
                                           self.err_path.read_bytes().decode("utf-8", "replace"))

    def kill(self) -> None:
        if self.popen.poll() is None:
            self.popen.kill()
            try:
                self.popen.wait(timeout=30)
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


def verify_runtime(env: Env, protocol: Path) -> subprocess.CompletedProcess:
    with Live(env, "verify-runtime", "--protocol", protocol) as live:
        deadline = time.time() + 3600
        while live.popen.poll() is None and time.time() < deadline:
            live.observe()
            time.sleep(0.3)
        assert live.popen.poll() is not None, "verify-runtime did not finish within an hour"
        return live.completed()


def record_path(env: Env, protocol: Path) -> Path:
    return env.runs / "_verification" / sha256_file(protocol) / "runtime-verification.json"


def start_run_server(env: Env) -> tuple[Live, list[int]]:
    """Seed the cache, write both verification records, start a smoke run and wait for its server."""
    protocol, manifest = protocol_path(), manifest_path()
    seed_cache(env)
    artifact = env.run("verify-artifact", "--protocol", protocol)
    assert artifact.returncode == EXIT_OK, "verify-artifact failed\n" + _explain(artifact)
    runtime = verify_runtime(env, protocol)
    assert runtime.returncode == EXIT_OK, "verify-runtime failed\n" + _explain(runtime)
    live = Live(env, "run", "--protocol", protocol, "--manifest", manifest, "--tier", "smoke", "--k", "1")
    ports = live.wait_listening()
    if not ports or not live.wait_ready(ports):
        live.kill()
        pytest.fail("the run's server never became ready\n" + _explain(live.completed()), pytrace=False)
    return live, ports


# ---------------------------------------------------------------------------
# reading runtime-verification.json without assuming field names
# ---------------------------------------------------------------------------


def _walk(obj, path=()):
    yield path, obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _walk(value, path + (str(key),))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _walk(value, path + (index,))


STATUS_KEY_WORDS = ("status", "code", "key", "auth", "with")


def _statuses(entry: dict) -> list[int]:
    """HTTP statuses in an entry: integers 100-599 under status-like keys (not timings or ports)."""
    return [v for path, v in _walk(entry)
            if isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599
            and any(isinstance(k, str) and any(w in k.lower() for w in STATUS_KEY_WORDS) for k in path)]


def _classification(entry: dict) -> list[str]:
    return [v.lower() for v in entry.values() if isinstance(v, str) and v.lower() in ("protected", "exempt")]


def _is_options(entry: dict) -> bool:
    return any(isinstance(v, str) and v.upper() == "OPTIONS" for v in entry.values())


def _lengths(entry: dict) -> list[int]:
    """Body lengths in an entry: non-negative integers under length/size/bytes keys."""
    return [v for path, v in _walk(entry)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0
            and any(isinstance(k, str) and any(w in k.lower() for w in ("length", "size", "bytes")) for k in path)]


def options_entries(doc) -> list[dict]:
    """OPTIONS probe records (§10 v0.5): an object naming one route and the method OPTIONS, or an
    `options` object inside a route-keyed object. Statuses of the enclosing route object are
    included, since that is where a nested record would keep the data-bearing method's result."""
    entries = []
    for _, value in _walk(doc):
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
    """Route records: an object naming one route ("/...") as a value, or keyed by the route, with a
    protected/exempt classification and its HTTP statuses."""
    entries = []
    for _, value in _walk(doc):
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
# tests
# ---------------------------------------------------------------------------


def test_vs_rt_01_verify_runtime_without_model_file_exits_3(env):
    """VS-RT-01 · hermetic · §3 exit 3 (missing file) + §2 "Model files live
    under models/ in this cache": with an empty SLM_EVAL_CACHE (so no model
    file), verify-runtime exits 3, writes no runtime-verification.json and
    leaves no process behind. Uses the committed protocol when found, else
    a §4 draft protocol written to the test directory."""
    found = _candidates(lambda p: p.name == "protocol.yaml", "SLM_EVAL_TEST_PROTOCOL")
    if len(found) == 1:
        protocol = found[0]
    else:
        protocol = env.work / "protocol.yaml"
        protocol.write_text(json.dumps({
            "protocol_version": "v1-draft",
            "model": {"artifact": {"file": "qwen3-8b-q4_k_m.gguf", "sha256": "a" * 64, "quantization": "Q4_K_M",
                                   "provenance": "local-choice"}},
            "runtime": {"build": "b10412", "invocation": "keyed-run-scoped-server", "provenance": "local-choice"},
            "prompt": {"template": "{problem}\n\nPlease reason step by step, and put your final answer within "
                                   "\\boxed{}.", "system_prompt": None, "few_shot": 0, "provenance": "vendor-filled"},
            "reasoning_mode": {"value": "hybrid", "provenance": "inferred"},
            "sampling": {name: {"value": value, "provenance": "local-choice"} for name, value in (
                ("temperature", 0.6), ("top_p", 0.95), ("top_k", 20), ("min_p", 0), ("presence_penalty", 0),
                ("max_output_tokens", 32768), ("seed_policy", "seed = seed_base + sample_index"))},
            "extraction": {"id": "visible-suite-extractor", "version": "0.1.0"},
            "scorers": {"primary": {"id": "prm800k-grader", "pin": "b" * 40},
                        "secondary": {"id": "math-verify", "pin": "0.1.0"}},
            "dataset": {"source": {"url": "https://example.invalid/visible", "commit": "c" * 40,
                                   "files": [{"path": "test.jsonl", "sha256": "d" * 64}]}},
        }, indent=2), encoding="utf-8")
    with Live(env, "verify-runtime", "--protocol", protocol) as live:
        deadline = time.time() + 300
        while live.popen.poll() is None and time.time() < deadline:
            live.observe()
            time.sleep(0.2)
        proc = live.completed()
        assert proc.returncode == EXIT_PRECONDITION, _explain(proc)
        assert not list(env.runs.rglob("runtime-verification.json")), "a verification record was written"
        time.sleep(1)
        assert not live.survivors(), f"processes survived: {live.survivors()}"


def test_vs_rt_02_verify_runtime_record_location(env):
    """VS-RT-02 · REAL MODEL + LIVE SERVER · §3 verify-runtime: with the
    committed protocol, verify-runtime exits 0, writes
    $SLM_EVAL_RUNS/_verification/<SHA-256 of the protocol file>/runtime-verification.json
    as JSON, and prints that path on stdout."""
    require_live()
    protocol = protocol_path()
    seed_cache(env)
    proc = verify_runtime(env, protocol)
    assert proc.returncode == EXIT_OK, _explain(proc)
    record = record_path(env, protocol)
    assert record.is_file(), f"{record} was not written\n" + _explain(proc)
    json.loads(record.read_text(encoding="utf-8"))
    shown = set()
    for line in proc.stdout.splitlines():
        if line.strip():
            path = Path(line.strip())
            shown.add((path if path.is_absolute() else env.work / path).resolve())
    assert record.resolve() in shown, "the record path is not printed on stdout\n" + _explain(proc)


def test_vs_rt_03_verification_record_contents(env):
    """VS-RT-03 · REAL MODEL + LIVE SERVER · §10 verification record + §3
    verify-runtime checks: runtime-verification.json lists the routes probed,
    each with a protected/exempt classification and at least two HTTP
    statuses (with and without the key); the list covers /health,
    /v1/models, /props, /slots and at least one generation route; /health
    and /v1/models are exempt; every protected route shows 401 or 403 (its
    response without the key); every exempt route shows a 2xx status; and
    every route that shows no 401/403 at all is classified exempt. Records
    of OPTIONS probes are judged by VS-RT-11, not by these rules."""
    require_live()
    protocol = protocol_path()
    seed_cache(env)
    proc = verify_runtime(env, protocol)
    assert proc.returncode == EXIT_OK, _explain(proc)
    entries = [e for e in route_entries(json.loads(record_path(env, protocol).read_text(encoding="utf-8")))
               if not e["options"]]
    assert entries, "no route entries (route, protected/exempt, statuses) found in runtime-verification.json"
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
    assert not problems, "; ".join(problems)


def test_vs_rt_04_key_required_except_the_exemption(env):
    """VS-RT-04 · REAL MODEL + LIVE SERVER · §10 "required on every route
    except the approved exemption ... Nothing else may answer without the
    key" (data-bearing GET and POST; OPTIONS preflight is VS-RT-10): while a
    smoke run's server is up, /health and /v1/models answer
    200 without a key, and every other probed route, requested with no key,
    a wrong bearer key or a wrong X-Api-Key, is refused (401/403), absent
    (404), or is an alias of an exempt handler (a GET whose body matches the
    /health or /v1/models response)."""
    require_live()
    live, ports = start_run_server(env)
    with live:
        results: dict[tuple, tuple] = {}
        baselines: dict[int, set] = {}
        deadline = time.time() + 240
        pending = {(port, method, path, variant) for port in ports
                   for method, paths in (("GET", PROBE_GET), ("POST", PROBE_POST))
                   for path in paths for variant in WRONG_KEYS}
        while pending and time.time() < deadline and live.popen.poll() is None:
            for port in ports:
                exempt_bodies = set()
                for route in EXEMPT:
                    status, body = http(port, "GET", route, {})
                    assert status == 200, f"{route} on port {port} answered {status} without a key"
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
    assert not violations, "routes answered without a valid key: " + "; ".join(violations)
    assert any(status in (401, 403) for status, _ in results.values()), (
        f"no route refused a missing key; {len(pending)} probes never got a definitive answer")


def test_vs_rt_05_model_listing_shows_alias_and_no_path(env):
    """VS-RT-05 · REAL MODEL + LIVE SERVER · §10 "Model alias": /v1/models,
    read without a key, answers 200 with JSON naming the model qwen3-8b; no
    string in it, or in the /health response, is a file path (starts with /
    or ~, or contains .gguf, the dataset cache path or the home directory)."""
    require_live()
    live, ports = start_run_server(env)
    with live:
        listings = {port: http(port, "GET", "/v1/models", {}) for port in ports}
        health = {port: http(port, "GET", "/health", {}) for port in ports}
        live.interrupt()
    home, cache = str(Path.home()), str(env.cache.resolve())
    for port in ports:
        status, body = listings[port]
        assert status == 200, f"/v1/models on port {port} answered {status} without a key"
        doc = json.loads(body.decode("utf-8"))
        names = [v for _, v in _walk(doc) if isinstance(v, str)]
        assert MODEL_ALIAS in names, f"model alias {MODEL_ALIAS!r} not listed: {names[:10]}"
        for route, (_, raw) in (("/v1/models", listings[port]), ("/health", health[port])):
            text = raw.decode("utf-8", "replace")
            strings = names if route == "/v1/models" else re.findall(r'"([^"\\]*)"', text)
            paths = [s for s in strings if s.startswith(("/", "~")) or ".gguf" in s.lower()]
            assert not paths, f"{route} exposes path-like values {paths[:5]}"
            assert cache not in text and home not in text, f"{route} exposes a local directory"


def test_vs_rt_06_loopback_only_on_an_ephemeral_port(env):
    """VS-RT-06 · REAL MODEL + LIVE SERVER · §10 "bound to 127.0.0.1 only, on
    an ephemeral port": every TCP listening socket of the run's process tree,
    observed for ten seconds after the server is ready, is on 127.0.0.1 and
    its port lies in the OS ephemeral range."""
    require_live()
    live, _ = start_run_server(env)
    with live:
        deadline = time.time() + 10
        while time.time() < deadline and live.popen.poll() is None:
            live.observe()
            time.sleep(0.5)
        sockets = set(live.sockets)
        live.interrupt()
    low, high = ephemeral_range()
    wrong_host = sorted({(host, port) for _, host, port in sockets if host != "127.0.0.1"})
    wrong_port = sorted({port for _, _, port in sockets if not low <= port <= high})
    assert sockets, "no listening socket observed"
    assert not wrong_host, f"sockets not bound to 127.0.0.1: {wrong_host}"
    assert not wrong_port, f"ports outside the ephemeral range {low}-{high}: {wrong_port}"


def test_vs_rt_07_key_is_never_written(env):
    """VS-RT-07 · REAL MODEL + LIVE SERVER · §10 "generated per run, in memory
    only ... never written to disk, logs, artifacts, or stdout": the server
    is not given a key file (--api-key-file); the key seen in its arguments
    or environment appears in no file under the test's cache, run store or
    working directory (including runtime-verification.json and the CLI's
    captured stdout and stderr). Skips if the key value is not observable."""
    require_live()
    live, _ = start_run_server(env)
    with live:
        keys = set()
        for pid in live.observe():
            keys.update(re.findall(r"--api-key(?:=|\s+)(\S+)", live.tree.get(pid, "")))
            keys.update(re.findall(r"\bLLAMA_API_KEY=(\S+)", _environment(pid)))
        commands = dict(live.tree)
        live.interrupt()
        live.completed()
    assert not [cmd for cmd in commands.values() if "--api-key-file" in cmd], "the key was passed through a file"
    if not keys:
        pytest.skip("the API key is not observable from the server's arguments or environment")
    for key in keys:
        needle = key.encode("utf-8")
        hits = [str(p) for p in env.root.rglob("*")
                if p.is_file() and not p.is_symlink() and p.stat().st_size <= LARGE_FILE and needle in p.read_bytes()]
        assert not hits, f"the API key was written to {len(hits)} file(s): {sorted(hits)[:10]}"


def test_vs_rt_08_server_terminated_on_normal_exit(env):
    """VS-RT-08 · REAL MODEL + LIVE SERVER · §10 "terminated on normal exit":
    after verify-runtime exits 0, no process it started is still running and
    no port it listened on is still listening. Skips if no server was
    observed while it ran."""
    require_live()
    protocol = protocol_path()
    seed_cache(env)
    with Live(env, "verify-runtime", "--protocol", protocol) as live:
        deadline = time.time() + 3600
        while live.popen.poll() is None and time.time() < deadline:
            live.observe()
            time.sleep(0.2)
        assert live.popen.poll() == EXIT_OK, _explain(live.completed())
        if not live.sockets:
            pytest.skip("no listening server observed during verify-runtime")
        ports = sorted({port for _, _, port in live.sockets})
        deadline = time.time() + 15
        while time.time() < deadline and (live.survivors() or any(_port_open(p) for p in ports)):
            time.sleep(0.5)
        assert not live.survivors(), f"processes survived verify-runtime: {live.survivors()}"
        assert not [p for p in ports if _port_open(p)], "a port is still listening after verify-runtime exited"


def test_vs_rt_09_server_terminated_on_interrupt(env):
    """VS-RT-09 · REAL MODEL + LIVE SERVER · §10 "terminated ... on
    interrupt": SIGINT sent to the `slm-eval run` process alone makes it
    exit, after which no process of its tree is running and no port it
    listened on is still listening."""
    require_live()
    live, ports = start_run_server(env)
    with live:
        assert live.interrupt() is not None, "slm-eval run did not exit after SIGINT"
        deadline = time.time() + 15
        while time.time() < deadline and (live.survivors() or any(_port_open(p) for p in ports)):
            time.sleep(0.5)
        assert not live.survivors(), f"processes survived the interrupt: {live.survivors()}"
        assert not [p for p in ports if _port_open(p)], "a port is still listening after the interrupt"


def test_vs_rt_10_preflight_without_key_carries_no_body(env):
    """VS-RT-10 · REAL MODEL + LIVE SERVER · §10 CORS preflight (v0.5): "an
    OPTIONS request on any route may be answered without the key, provided
    the response body is empty. The same route's data-bearing methods must
    still require the key." While a smoke run's server is up, every probed
    route's OPTIONS request with CORS preflight headers and no key is refused
    (401/403), absent (404), or answered with an empty body; and wherever
    OPTIONS is answered, the route's data-bearing method without the key is
    refused (401/403/404) unless it is /health, /v1/models or a GET alias of
    their handlers."""
    require_live()
    live, ports = start_run_server(env)
    with live:
        exempt_bodies = {port: {signature(http(port, "GET", route, {})[1]) for route in EXEMPT} for port in ports}
        problems, answered = [], 0
        for port in ports:
            for method, paths in (("GET", PROBE_GET), ("POST", PROBE_POST)):
                for path in paths:
                    status, body = http(port, "OPTIONS", path, {**PREFLIGHT_HEADERS,
                                                                "Access-Control-Request-Method": method})
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
    assert not problems, "; ".join(problems)
    if not answered:
        pytest.skip("no route answered OPTIONS without the key; nothing further to check")


def test_vs_rt_11_record_covers_options_probes(env):
    """VS-RT-11 · REAL MODEL + LIVE SERVER · §10 verification record (v0.5):
    "for each OPTIONS probe, records its status and body length, and the
    result of the same route's data-bearing method without the key".
    runtime-verification.json holds at least one OPTIONS probe record, one of
    them on a non-exempt route; each has a body length and at least two HTTP
    statuses; on every non-exempt route the data-bearing result is 401 or 403;
    and no OPTIONS probe on a non-exempt route answered with a 2xx shows a
    non-zero body length. Exempt routes are /health, /v1/models and any route
    the record classifies as exempt (their aliases)."""
    require_live()
    protocol = protocol_path()
    seed_cache(env)
    proc = verify_runtime(env, protocol)
    assert proc.returncode == EXIT_OK, _explain(proc)
    record = json.loads(record_path(env, protocol).read_text(encoding="utf-8"))
    exempt = set(EXEMPT) | {e["route"] for e in route_entries(record) if e["class"] == "exempt"}
    entries = options_entries(record)
    assert entries, "no OPTIONS probe records found in runtime-verification.json"
    assert any(e["route"] not in exempt for e in entries), "no OPTIONS probe on a non-exempt route"
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
    assert not problems, "; ".join(problems)
