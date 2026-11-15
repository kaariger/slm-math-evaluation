"""Visible specification tests — the v0.8.1 clarifications: exact subset draw, attempt-log hash rule and qwen3 stray closing tag (contract v0.8.1 §5.2, §8, §13.3).

Contract-only: written before any v0.8.1 implementation commit was supplied,
with no implementation code seen. Tests use only the `slm-eval` CLI and the
contract's file schemas and, where a named capability binds it, the §8
`split_reasoning` function.

Resources (stated in each docstring):
  hermetic  the `slm-eval` CLI only. Each test has its own temporary
            SLM_EVAL_CACHE, SLM_EVAL_RUNS, SLM_EVAL_SCRATCH and working
            directory, and needs no network, dataset, model or runtime.
  repo      the committed split manifest with the pinned SHA-256 and, once
            one exists, the committed subset file, found by content in the
            repository that contains the pytest working directory, or through
            SLM_EVAL_TEST_MANIFEST and SLM_EVAL_TEST_SUBSET.
  harness   the installed harness revision for `bindings.harness_revision`
            (§13.2): SLM_EVAL_TEST_HARNESS_REVISION, or else `git rev-parse
            HEAD` of the repository that contains the pytest working
            directory.
  callable  SLM_EVAL_TEST_SPLIT_CALLABLE: a "module:attribute" binding of the
            §8 `split_reasoning` function, imported in the pytest process.

Runner commands write stdout and stderr to files rather than pipes. Every CLI
process carries a unique VISIBLE_SUITE_TAG environment variable; after each
test, every process carrying the tag is terminated.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable, and no variable whose name contains TOKEN, SECRET,
PASSWORD or API_KEY, is passed to the implementation.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
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

TIMEOUT = 300
WAIT = 120.0
EXIT_OK = 0
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
ZERO_HASH = "0" * 64
PINNED_MANIFEST_SHA256 = "a10c02e7ac60b563a4d112cad92c2540ce58d6c0a49fa73f844091df66e48c71"
RUNTIME_BUILD = "b10412"
LOG_KEYS = {"seq", "prev_sha256", "at_utc", "step", "event", "detail"}
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


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def dump(data) -> bytes:
    """JSON text, which every YAML parser also reads."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


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


# ---------------------------------------------------------------------------
# §5.2 as clarified in v0.8.1: the independent rebuild
# ---------------------------------------------------------------------------


def largest_remainder(counts: dict[str, int], size: int) -> dict[str, int]:
    """§5.2 allocation, in exact arithmetic: proportional shares, largest remainders first, ties by name."""
    total = sum(counts.values())
    quotas = {s: Fraction(size * n, total) for s, n in counts.items()}
    allocation = {s: q.numerator // q.denominator for s, q in quotas.items()}
    remaining = size - sum(allocation.values())
    for subject in sorted(counts, key=lambda s: (-(quotas[s] - allocation[s]), s))[:remaining]:
        allocation[subject] += 1
    return allocation


def rebuild_ids(manifest: dict, size: int, seed: int) -> list[str]:
    """v0.8.1 §5.2: per subject, a fresh random.Random(seed).sample over its IDs in tiers.validation order;
    the result listed in tiers.validation order."""
    tier = list(manifest["tiers"]["validation"])
    subject = {item["id"]: item.get("subject") for item in manifest["items"]}
    allocation = largest_remainder(Counter(subject[i] for i in tier), size)
    drawn = set()
    for name, count in allocation.items():
        drawn |= set(random.Random(seed).sample([i for i in tier if subject[i] == name], count))
    return [i for i in tier if i in drawn]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def shuffled_tier_manifest(subject_counts: list[tuple[str, int]]) -> tuple[bytes, dict]:
    """A §5 manifest whose `tiers.validation` order differs from `items` order (items grouped by subject,
    the tier interleaved and reversed)."""
    items = [{"id": f"dev/visible/{_slug(s)}/{n:03d}.json", "role": "dev",
              "content_sha256": sha256_text(f"{s}/{n}"), "subject": s, "level": 1 + n % 5}
             for s, count in subject_counts for n in range(count)]
    by_subject = [[i["id"] for i in items if i["subject"] == s] for s, _ in subject_counts]
    interleaved = []
    while any(by_subject):
        for queue in by_subject:
            if queue:
                interleaved.append(queue.pop(0))
    tier = list(reversed(interleaved))
    tests = [{"id": f"test/visible/{n:03d}.json", "role": "test", "content_sha256": sha256_text(f"test/{n}"),
              "subject": "Algebra", "level": 3} for n in range(3)]
    doc = {
        "manifest_version": "1",
        "source": {"url": "https://example.invalid/visible-suite-dataset", "commit": "c" * 40,
                   "files": [{"path": "test.jsonl", "sha256": "d" * 64}]},
        "membership_derivation": {"method": "unique_id", "independent_source": None},
        "normalization": {"version": "visible-1", "near_duplicate": {"method": "visible", "threshold": 0.85}},
        "items": tests + items,
        "exclusions": [],
        "tiers": {"seed": 7, "smoke": [], "validation": tier},
        "provenance_note": "visible-suite synthetic manifest; IDs and subjects only",
    }
    return dump(doc), doc


def subset_ids(env: Env, manifest: Path, size: int, seed: int) -> list:
    out = env.work / f"subset-{uuid.uuid4().hex[:6]}.json"
    proc = env.run("data", "subset", "--manifest", manifest, "--tier", "validation", "--size", size,
                   "--stratify", "subject", "--seed", seed, "--out", out)
    assert proc.returncode == EXIT_OK, "data subset failed\n" + _explain(proc)
    assert out.is_file(), "data subset exited 0 without writing --out\n" + _explain(proc)
    return json.loads(out.read_text(encoding="utf-8")).get("ids")


CL01_SUBJECTS = [("Algebra", 9), ("Geometry", 6), ("Number Theory", 7), ("Prealgebra", 5)]


@pytest.mark.parametrize("size,seed", [(10, 11), (13, 20261028), (27, 3)], ids=["size10", "size13", "whole-tier"])
def test_vs_cl_01_subset_ids_equal_the_independent_rebuild(env, size, seed):
    """VS-CL-01 · hermetic · v0.8.1 §5.2: ids "listed in the order of IDs in the manifest's
    tiers.validation list"; "for each subject, create a fresh random.Random(seed) and call .sample over that
    subject's IDs in tiers.validation order"; brief objective 7.

    A four-subject tier whose `tiers.validation` order differs from `items`
    order → `ids` equal the independent rebuild exactly, in content and in
    order.
    """
    raw, doc = shuffled_tier_manifest(CL01_SUBJECTS)
    manifest = env.inputs / "manifest.json"
    manifest.write_bytes(raw)
    expected = rebuild_ids(doc, size, seed)
    got = subset_ids(env, manifest, size, seed)
    assert got == expected, f"independent rebuild:\n{expected}\nCLI wrote:\n{got}"


def _json_files(accept, configured_var: str) -> list[Path]:
    configured = os.environ.get(configured_var, "").strip()
    if configured:
        return [Path(configured).expanduser()]
    root = _repo_root()
    if root is None:
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix != ".json":
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if isinstance(doc, dict) and accept(doc):
                found.append(path)
    return [p for p in found if not TEST_DIRS & set(p.relative_to(root).parts)] or found


def test_vs_cl_02_committed_subset_equals_the_independent_rebuild():
    """VS-CL-02 · repo · v0.8.1 §5.2; brief objective 7 "independently rebuilding the validation-subset file".

    Each committed subset that references the pinned manifest: `ids` equal
    the independent rebuild from that manifest with the subset's own size
    and seed. Skipped until a subset is committed.
    """
    manifests = [p for p in _json_files(lambda d: "manifest_version" in d and "items" in d, "SLM_EVAL_TEST_MANIFEST")
                 if sha256_bytes(p.read_bytes()) == PINNED_MANIFEST_SHA256]
    if not manifests:
        pytest.skip(f"no committed manifest with the pinned SHA-256 {PINNED_MANIFEST_SHA256}")
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    subsets = _json_files(lambda d: "subset_version" in d and "ids" in d, "SLM_EVAL_TEST_SUBSET")
    if not subsets:
        pytest.skip("no committed subset file yet (§5.2)")
    failures = []
    for path in subsets:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("manifest_sha256") != PINNED_MANIFEST_SHA256:
            failures.append(f"{path}: manifest_sha256 {doc.get('manifest_sha256')!r} is not the pinned manifest")
            continue
        expected = rebuild_ids(manifest, doc.get("size"), doc.get("seed"))
        if doc.get("ids") != expected:
            failures.append(f"{path}: ids differ from the independent rebuild (size {doc.get('size')}, "
                            f"seed {doc.get('seed')})")
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# §13.3 as clarified in v0.8.1: attempt-log hashes
# ---------------------------------------------------------------------------


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


def approval_plan(*gates: str) -> bytes:
    return dump({"plan_version": "1", "steps": [{"type": "approval", "id": gate} for gate in gates]})


def authorization(plan: bytes, harness: str) -> bytes:
    return dump({
        "authorization_version": "1", "plan_sha256": sha256_bytes(plan),
        "scope": "visible-suite hermetic runner check: approval steps only",
        "bindings": {"protocols": [], "artifacts": [], "specs": [], "manifest": PINNED_MANIFEST_SHA256,
                     "subsets": [], "runtime_build": RUNTIME_BUILD, "harness_revision": harness},
        "attempts_allowed": 1, "stop_conditions": ["any integrity failure"],
        "approved_by": "maintainer", "approved_utc": "2026-10-28T00:00:00Z",
    })


def log_lines(path: Path) -> list[bytes]:
    """Lines without their terminators (LF, or CR LF)."""
    lines = path.read_bytes().split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return [line[:-1] if line.endswith(b"\r") else line for line in lines]


def exact_chain_problems(lines: list[bytes]) -> list[str]:
    """v0.8.1 §13.3: prev_sha256 is the SHA-256 of the previous line without its terminator; 64 zeros first."""
    problems = []
    for n, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except ValueError:
            problems.append(f"line {n} is not JSON")
            continue
        if not isinstance(record, dict) or not LOG_KEYS <= set(record):
            problems.append(f"line {n} lacks {sorted(LOG_KEYS)}")
            continue
        expected = ZERO_HASH if n == 1 else sha256_bytes(lines[n - 2])
        if record["prev_sha256"] != expected:
            problems.append(f"line {n}: prev_sha256 {record['prev_sha256']!r}, expected {expected}")
    return problems


def head_hash(lines: list[bytes]) -> str:
    return sha256_bytes(lines[-1]) if lines else ZERO_HASH


def test_vs_cl_03_runner_log_uses_the_exact_hash_rule(env):
    """VS-CL-03 · hermetic + harness · v0.8.1 §13.3: prev_sha256 is "the SHA-256 of the previous line's UTF-8
    bytes, excluding its line terminator"; "The first line's prev_sha256 is 64 zeros"; the head hash is the
    SHA-256 of the last line by the same rule.

    An approval-only plan, started and waiting for approval → the log's
    first `prev_sha256` is 64 zeros, every later one hashes the previous line
    without its terminator, and `runner status` prints the last line's hash
    by the same rule.
    """
    plan = approval_plan("visible-gate-1")
    plan_path, auth_path = env.inputs / "plan.yaml", env.inputs / "authorization.json"
    plan_path.write_bytes(plan)
    auth_path.write_bytes(authorization(plan, harness_revision()))
    proc = env.run("runner", "start", "--plan", plan_path, "--authorization", auth_path)
    assert proc.returncode == EXIT_OK, "runner start failed\n" + _explain(proc)
    state = env.runs / "_runner" / sha256_bytes(plan)
    deadline, status = time.time() + WAIT, None
    while time.time() < deadline:
        try:
            status = json.loads((state / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = None
        if isinstance(status, dict) and status.get("state") == "waiting_approval":
            break
        time.sleep(0.3)
    assert isinstance(status, dict) and status.get("state") == "waiting_approval", f"last status {status!r}"
    time.sleep(2)
    lines = log_lines(state / "attempts.log.jsonl")
    assert lines, "attempts.log.jsonl is empty while waiting for approval"
    problems = exact_chain_problems(lines)
    assert not problems, "attempts.log.jsonl departs from v0.8.1 §13.3:\n" + "\n".join(problems)
    proc = env.run("runner", "status")
    assert proc.returncode == EXIT_OK, "runner status failed\n" + _explain(proc)
    assert head_hash(log_lines(state / "attempts.log.jsonl")) in proc.stdout, \
        "the head hash (last line, no terminator) is not on stdout\n" + _explain(proc)


def write_runner_state(env: Env, lines: list[bytes]) -> Path:
    """A consistent §13.3 state directory: stored copies, status.json and the given log lines."""
    plan = approval_plan("visible-gate-1")
    auth = authorization(plan, "f" * 40)
    state = env.runs / "_runner" / sha256_bytes(plan)
    state.mkdir(parents=True)
    (state / "plan.yaml").write_bytes(plan)
    (state / "authorization.json").write_bytes(auth)
    (state / "status.json").write_text(json.dumps({
        "plan_sha256": sha256_bytes(plan), "authorization_sha256": sha256_bytes(auth),
        "state": "waiting_approval", "step": 0, "run_id": None, "progress": {"done": 0, "total": 1},
        "last_event": "approval", "updated_utc": "2026-10-28T09:00:00Z"}, indent=2) + "\n", encoding="utf-8")
    (state / "attempts.log.jsonl").write_bytes(b"".join(line + b"\n" for line in lines))
    return state


def synthetic_log(count: int) -> list[bytes]:
    lines, previous = [], ZERO_HASH
    for seq in range(1, count + 1):
        line = json.dumps({"seq": seq, "prev_sha256": previous, "at_utc": f"2026-10-28T09:00:0{seq}Z",
                           "step": 0, "event": "visible-event", "detail": None}).encode("utf-8")
        lines.append(line)
        previous = sha256_bytes(line)
    return lines


@pytest.mark.parametrize("count", [0, 3], ids=["empty-log", "three-lines"])
def test_vs_cl_04_status_prints_the_head_hash_of_a_given_log(env, count):
    """VS-CL-04 · hermetic · v0.8.1 §13.3: "runner status prints the head hash: the SHA-256 of the last
    line's UTF-8 bytes, excluding its line terminator … The head hash of an empty log is 64 zeros"; §3 runner
    status writes nothing.

    A consistent runner state directory written by the test (stored copies
    whose hashes match status.json, state `waiting_approval`) with an empty
    log, or a three-line log built by the §13.3 rule → `runner status` exits
    0 and prints 64 zeros, or the last line's hash without its terminator; no
    file changes.
    """
    lines = synthetic_log(count)
    write_runner_state(env, lines)
    before = env.snapshot()
    proc = env.run("runner", "status")
    assert proc.returncode == EXIT_OK, "runner status failed\n" + _explain(proc)
    assert head_hash(lines) in proc.stdout, f"head hash {head_hash(lines)} is not on stdout\n" + _explain(proc)
    assert env.snapshot() == before, "runner status changed files"


# ---------------------------------------------------------------------------
# §8 as clarified in v0.8.1: qwen3 with a stray closing tag
# ---------------------------------------------------------------------------

STRAY_CLOSE = "The answer is \\boxed{7}.</think>No box here."

DRAFT_PROTOCOL = {
    "protocol_version": "v1-draft",
    "role": "target",
    "model": {"adapter": {"id": "qwen3", "version": "1.0.0"},
              "artifact": {"file": "visible-suite-model-q4_k_m.gguf", "sha256": "a" * 64, "quantization": "Q4_K_M",
                           "conversion_record_sha256": None, "provenance": "local-choice"},
              "chat_template": {"source": "embedded", "sha256": "e" * 64}},
    "runtime": {"build": RUNTIME_BUILD, "invocation": "keyed-run-scoped-server", "provenance": "local-choice"},
    "prompt": {"template": "{problem}\n\nPlease reason step by step, and put your final answer within \\boxed{}.",
               "system_prompt": None, "few_shot": 0, "provenance": "vendor-filled"},
    "reasoning_mode": {"value": "hybrid", "provenance": "inferred"},
    "sampling": {"temperature": {"value": 0.6, "provenance": "source-known-paper"},
                 "max_output_tokens": {"value": 32768, "provenance": "source-known-paper"},
                 "seed_policy": {"value": "seed = seed_base + sample_index", "provenance": "local-choice"}},
    "extraction": {"id": "visible-suite-extractor", "version": "0.1.0"},
    "scorers": {"primary": {"id": "prm800k-grader", "pin": "b" * 40},
                "secondary": {"id": "math-verify", "pin": "0.1.0"}},
    "dataset": {"source": {"url": "https://example.invalid/visible-suite-dataset", "commit": "c" * 40,
                           "files": [{"path": "test.jsonl", "sha256": "d" * 64}]}},
}


def qwen3_protocol() -> tuple[bytes, dict]:
    """The committed qwen3 (or adapter-less) protocol with the adapter named, else the draft defaults."""
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    root = _repo_root()
    if yaml is not None and root is not None:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS | TEST_DIRS)
            for name in sorted(filenames):
                if not name.endswith((".yaml", ".yml")):
                    continue
                try:
                    data = yaml.safe_load((Path(dirpath) / name).read_text(encoding="utf-8"))
                except Exception:
                    continue
                if (isinstance(data, dict) and "protocol_version" in data and isinstance(data.get("model"), dict)
                        and (dig(data, "model", "adapter", "id") or "qwen3") == "qwen3"):
                    data = copy.deepcopy(data)
                    data["model"].setdefault("adapter", {"id": "qwen3", "version": "1.0.0"})
                    if dig(data, "reasoning_mode", "value") not in ("hybrid", "forced_think", "forced_non_think"):
                        data["reasoning_mode"] = {"value": "hybrid", "provenance": "inferred"}
                    return dump(data), data
    data = copy.deepcopy(DRAFT_PROTOCOL)
    return dump(data), data


def write_qwen3_run(env: Env, raw_output: str, correct: bool) -> str:
    raw_protocol, data = qwen3_protocol()
    run_id = f"visible-{uuid.uuid4().hex[:12]}"
    run_dir = env.runs / run_id
    run_dir.mkdir()
    item_id = "visible-stray-close"
    manifest = dump({
        "manifest_version": "1", "source": dig(data, "dataset", "source") or {},
        "membership_derivation": {"method": "unique_id", "independent_source": None},
        "normalization": {"version": "visible-1", "near_duplicate": {"method": "visible", "threshold": 0.85}},
        "items": [{"id": item_id, "role": "dev", "content_sha256": sha256_text(item_id), "subject": "Algebra",
                   "level": 2}],
        "exclusions": [], "tiers": {"seed": 7, "smoke": [item_id], "validation": []},
        "provenance_note": "visible-suite fixture manifest",
    })
    record = {
        "item_id": item_id, "sample_index": 0, "seed": 4100,
        "prompt_sha256": sha256_text("visible fixture prompt " + item_id),
        "raw_output": raw_output, "raw_output_sha256": sha256_text(raw_output),
        "thinking_present": False, "thinking_state": "absent", "truncated": False, "finish_reason": "stop",
        "tokens_generated": 30, "infra_retries": 0,
        "reference_answer": "7", "reference_sha256": sha256_text("7"),
        "extraction": {"answer": "7", "rule": "boxed_last", "status": "ok"},
        "verdicts": {"primary": correct, "secondary": correct},
        "timing_ms": 500, "awake_ms": 490,
    }
    sampling = {k: v.get("value") for k, v in (data.get("sampling") or {}).items() if isinstance(v, dict)}
    run_json = {
        "run_id": run_id, "protocol_version": data.get("protocol_version"),
        "protocol_sha256": sha256_bytes(raw_protocol), "manifest_sha256": sha256_bytes(manifest),
        "tier": "smoke", "k": 1, "seeds": [4100],
        "model_artifact_sha256": dig(data, "model", "artifact", "sha256"), "runtime_build": dig(data, "runtime", "build"),
        "adapter": dict(dig(data, "model", "adapter")),
        "extractor": {"id": dig(data, "extraction", "id"), "version": dig(data, "extraction", "version")},
        "scorers": [{"id": dig(data, "scorers", r, "id"), "pin": dig(data, "scorers", r, "pin")}
                    for r in ("primary", "secondary")],
        "role": data.get("role", "target"), "subset_sha256": None,
        "chat_template_sha256": dig(data, "model", "chat_template", "sha256"),
        "effective": {"reasoning": dig(data, "reasoning_mode", "value"), "template_args": {}, "sampling": sampling},
        "executor": "visible-suite fixture", "host": {"os": "Darwin", "arch": "arm64", "memory_gb": 32},
        "attempts": [{"n": 1, "started": "2026-10-28T09:00:00Z", "ended": "2026-10-28T09:05:00Z",
                      "status": "completed", "resumed_from": None}],
        "events": [], "status": "completed",
    }
    (run_dir / "protocol.yaml").write_bytes(raw_protocol)
    (run_dir / "manifest.json").write_bytes(manifest)
    (run_dir / "run.json").write_text(json.dumps(run_json, indent=2) + "\n", encoding="utf-8")
    (run_dir / "items.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    for name in ("runtime-verification.json", "artifact-verification.json"):
        (run_dir / name).write_text(json.dumps({"fixture": "visible-suite", "passed": True}) + "\n", encoding="utf-8")
    return run_id


def test_vs_cl_05_qwen3_stray_close_keeps_the_whole_output_as_final(env):
    """VS-CL-05 · hermetic · v0.8.1 §8: "With no opening delimiter, thinking is absent and final is the whole
    raw output, even if a stray closing delimiter appears … This never applies to qwen3" (the
    opening-delimiter template exception); §3 rescore; §9.

    A qwen3 run whose only output is `The answer is \\boxed{7}.</think>No box
    here.` → the whole output is final, so the answer 7 is correct; `rescore`
    exits 0 and reproduces the stored correct verdicts (`all_match: true`).
    Taking the text after the stray close would score it incorrect.
    """
    run_id = write_qwen3_run(env, STRAY_CLOSE, correct=True)
    proc = env.run("rescore", "--run", run_id)
    assert proc.returncode == EXIT_OK, "rescore failed\n" + _explain(proc)
    path = env.runs / run_id / "rescore.json"
    assert path.is_file(), "rescore exited 0 without writing rescore.json\n" + _explain(proc)
    doc = json.loads(path.read_text(encoding="utf-8"))
    record = (doc.get("records") or [{}])[0]
    assert doc.get("all_match") is True and record.get("match") is True, \
        f"the stray close changed the verdict: {json.dumps(doc)[:800]}"


def test_vs_cl_06_qwen3_split_of_a_stray_close_is_absent():
    """VS-CL-06 · callable · v0.8.1 §8 split_reasoning for qwen3.

    `split_reasoning(<the VS-CL-05 output>, "qwen3")` → (None, the whole
    output, "absent").
    """
    spec = os.environ.get("SLM_EVAL_TEST_SPLIT_CALLABLE", "").strip()
    if not spec:
        pytest.skip("set SLM_EVAL_TEST_SPLIT_CALLABLE=module:attribute to bind the §8 function")
    module_name, _, attribute = spec.partition(":")
    split = importlib.import_module(module_name)
    for part in attribute.split("."):
        split = getattr(split, part)
    assert tuple(split(STRAY_CLOSE, "qwen3")) == (None, STRAY_CLOSE, "absent")
