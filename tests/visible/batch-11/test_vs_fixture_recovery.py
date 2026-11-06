"""Visible specification tests — crash recovery of a fixture run that keeps its durable record prefix, acting only on provably owned processes (contract v0.8.1 §6.1, §6.3, §10).

Contract-only: written from the approved interface and the maintainer's
readings, with no implementation code seen. Tests use only the `slm-eval`
CLI, the processes it starts and the contract's files. Failures name files,
line numbers, pairs and counts; no record content and no CLI output is
printed (the CLI's stdout and stderr stay in named files).

Every test needs a REAL FIXTURE MODEL AND THE PINNED RUNTIME and is skipped as
NOT RUN unless all of these are present:

  SLM_EVAL_TEST_RUNTIME=1        the pinned llama.cpp runtime is available to
                                 the implementation
  SLM_EVAL_TEST_FIXTURE_PROTOCOL a `role: fixture` protocol (§4) for a small
                                 model, with a neutral `fixture-<n>` alias when
                                 it names one
  SLM_EVAL_TEST_SEED_CACHE       a dataset cache holding the pinned dataset and
                                 models/<model.artifact.file>; copied into each
                                 test's temporary SLM_EVAL_CACHE (files over
                                 256 MiB are symlinked)
  split manifest                 SLM_EVAL_TEST_MANIFEST, or the committed
                                 manifest with the pinned SHA-256
  tools                          `pgrep`, `ps` and `lsof`

A fixture is never evidence of target compatibility.

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
(for example a model server outside the launched process group and lineage)
is skipped as NOT RUN, naming that need. A test whose fixture run finishes
before its fault is injected is NOT RUN. A NOT RUN skip is never a pass and
never credits fault coverage.

Timing: model start-up is awaited for up to STARTUP seconds and a run for up
to RUN_WAIT seconds; no speed is required.

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
import time
import uuid
from pathlib import Path

import pytest

EXIT_OK = 0
STARTUP = 1800.0
RUN_WAIT = 3600.0
POLL = 0.5
LARGE_FILE = 256 * 1024 * 1024
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
PINNED_MANIFEST_SHA256 = "a10c02e7ac60b563a4d112cad92c2540ce58d6c0a49fa73f844091df66e48c71"
FIXTURE_ALIAS = re.compile(r"fixture-\d+")
HEX64 = re.compile(r"[0-9a-f]{64}")
THINKING_STATES = {"absent", "empty", "present", "unterminated"}

SERVER_GAP = ("the run's model server is not provably owned: no process in the launched process group or its "
              "lineage was listening on TCP. Observing it needs a process-identity interface this suite does not "
              "have, so no fault was injected")


def not_run(reason: str):
    pytest.skip(f"NOT RUN: {reason}")


def fail(message: str):
    """Fail without a traceback or assertion introspection, which could show record contents."""
    raise pytest.fail.Exception(message, pytrace=False) from None


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
        fail(f"{popen.args!r} did not exit within {timeout:.0f} s")

    def run(self, *args: object, timeout: float = RUN_WAIT) -> tuple[int, Path, Path]:
        """Run a command to its exit; returns (exit code, stdout file, stderr file), never the output itself."""
        popen, out_path, err_path = self.start(*args)
        return self.wait_exit(popen, timeout), out_path, err_path

    def cleanup(self) -> None:
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


# ---------------------------------------------------------------------------
# runs and their records
# ---------------------------------------------------------------------------


def verify(env: Env, protocol: Path) -> None:
    for command in ("verify-artifact", "verify-runtime"):
        code, out_path, err_path = env.run(command, "--protocol", protocol, timeout=STARTUP)
        if code != EXIT_OK:
            fail(f"{command} exited {code} on the fixture protocol (stdout {out_path}, stderr {err_path})")


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


def wait_first_item(env: Env, popen: subprocess.Popen) -> Path:
    def ready():
        dirs = run_dirs(env)
        if dirs and complete_lines(dirs[0]):
            return dirs[0]
        code = env.owned.exit_code(popen)
        if code == EXIT_OK:
            not_run("the fixture run finished before its first item was observed")
        if code is not None:
            fail(f"the run exited with {code} before its first item")
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
        fail("owned processes survived SIGKILL")
    if env.owned.exit_code(popen) != -signal.SIGKILL:
        not_run(f"the run exited with {env.owned.exit_code(popen)} on its own before the crash took effect")
    path = run_dir / "items.jsonl"
    return run_dir, path.read_bytes() if path.is_file() else b""


# ---------------------------------------------------------------------------
# test: crash, durable prefix and resume (§6.1, §6.3; P5; B10-4)
# ---------------------------------------------------------------------------


def test_vs_fx_17_a_crashed_run_keeps_its_durable_prefix_and_resumes(env):
    """VS-FX-17 · live · §6.3 resume: "Only missing item × sample pairs are generated. Completed pairs are
    never regenerated … Each resume appends an attempt record … The item in progress is regenerated from
    scratch"; §6.1 "A partial generation is never written to items.jsonl. It is recorded as an interrupted
    event"; P5 resume "discards only an incomplete tail and regenerates at most the uncommitted item"; B10-4;
    §10 cleanup.

    Proposed replacement for VS-FX-11's intent, without its held pre-recovery whole-line assertion. A smoke
    run is crashed after its first item, while pairs remain and its owned server is listening (SIGKILL to the
    owned process group and every owned descendant). Its items.jsonl then splits into the durable prefix (up
    to the last line terminator) and a tail, which may be empty or a physically incomplete line.
      Before recovery: every line of the prefix is a JSON object with valid raw-output and reference hashes,
      for a distinct planned pair.
      `run --resume` → exit 0. After it: items.jsonl begins with the exact prefix bytes, so only the
      incomplete tail may have been discarded; every line is a whole, hash-valid record; no pair completed
      before the crash appears again, so at most the uncommitted pair was regenerated; every planned pair
      appears exactly once; ≥ 2 attempts, the later ones with `resumed_from`; an `interrupted` event (or
      attempt) is recorded; no owned process is left, or listening.
    No record content is printed.
    """
    run_dir, before = crashed_run(env)
    items = run_dir / "items.jsonl"
    cut = before.rfind(b"\n") + 1
    prefix, tail = before[:cut], before[cut:]
    planned = planned_pairs(run_dir)

    problems, durable = record_problems(prefix.splitlines())
    if len(set(durable)) != len(durable):
        problems.append("the durable prefix repeats an item × sample pair")
    outside = set(durable) - planned
    if outside:
        problems.append(f"{len(outside)} pairs in the durable prefix are outside the planned tier × k")
    if problems:
        fail("the durable record prefix left by the crash is not a set of completed records:\n"
             + "\n".join(problems))

    code, out_path, err_path = env.run("run", "--resume", run_dir.name)
    if code != EXIT_OK:
        fail(f"run --resume exited {code} (stdout {out_path}, stderr {err_path})")
    after = items.read_bytes() if items.is_file() else b""
    if not after.startswith(prefix):
        fail(f"the durable prefix of {len(durable)} completed records was not kept byte for byte by resume")

    problems = []
    if after and not after.endswith(b"\n"):
        problems.append("items.jsonl ends in a partial line after resume")
    later_problems, later = record_problems(after[len(prefix):].splitlines(), first=len(durable) + 1)
    problems += later_problems
    again = set(durable) & set(later)
    if again:
        problems.append(f"{len(again)} pairs completed before the crash were generated again")
    pairs = durable + later
    if len(pairs) != len(set(pairs)):
        problems.append("an item × sample pair was generated twice")
    if set(pairs) != planned:
        problems.append(f"{len(set(pairs) & planned)} of {len(planned)} planned pairs recorded, "
                        f"{len(set(pairs) - planned)} unplanned")
    doc = run_json(run_dir)
    attempts = doc.get("attempts") or []
    if not (len(attempts) >= 2 and all(a.get("resumed_from") is not None for a in attempts[1:])):
        problems.append(f"{len(attempts)} attempts, or a later attempt without resumed_from")
    interrupted = any(e.get("type") == "interrupted" for e in doc.get("events") or []) or \
        any(a.get("status") == "interrupted" for a in attempts)
    if not interrupted:
        problems.append("the crash is recorded neither as an interrupted event nor as an interrupted attempt")
    env.owned.refresh()
    if env.owned.listening():
        problems.append("an owned server is still listening after the resumed run")
    if env.owned.live():
        problems.append(f"owned processes remain after the resumed run exited: {sorted(env.owned.live())}")
    if problems:
        fail("after resume:\n" + "\n".join(problems))
    print(f"VS-FX-17: durable prefix {len(durable)} records; incomplete tail {len(tail)} bytes before resume; "
          f"{len(later)} records added by resume")
