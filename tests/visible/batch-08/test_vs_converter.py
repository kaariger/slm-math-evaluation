"""Visible specification tests — model converter preflight, credentials and committed specs (contract v0.8 §2, §3, §12).

Contract-only: written before any v0.8 implementation commit was supplied,
with no implementation code seen. Tests use only the `slm-eval` CLI and the
contract's file schemas.

Resources (stated in each docstring):
  hermetic   the `slm-eval` CLI only, with a synthetic §12.1 spec naming a
             source that does not exist. Disk floors are set far above any
             real free space, so every conforming implementation stops in
             preflight, before any download. Each test has its own temporary
             SLM_EVAL_CACHE, SLM_EVAL_RUNS, SLM_EVAL_SCRATCH and working
             directory.
  converter  SLM_EVAL_TEST_CONVERTER=1: the pinned conversion tool is
             installed as a committed spec requires, so a floor breach is the
             only reason left to refuse. Also needs a committed spec. Skipped
             otherwise. Nothing is downloaded.
  repo       committed model specs, found by content in the repository that
             contains the pytest working directory and parsed with PyYAML.
             Skipped if none exist yet.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable, and no variable whose name contains TOKEN, SECRET,
PASSWORD or API_KEY, is passed to the implementation, except the sentinel
credentials that VS-CV-03 sets on purpose.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

TIMEOUT = 300
EXIT_PRECONDITION = 3
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
HUGE_FLOOR_GB = "1000000000"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
TARGET_ALIASES = {"qwen3-8b", "phi-4-reasoning", "phi-4-reasoning-plus", "gemma-4-12b"}
FIXTURE_ALIAS = re.compile(r"fixture-\d+")
THINKING_CONTROLS = {"none", "always", "template_arg", "runtime_flag"}

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
    """A temporary cache, run store, scratch and working directory for one test; inputs live apart."""

    def __init__(self, root: Path):
        self.root = root
        self.cache, self.runs, self.scratch, self.work = (root / n for n in ("cache", "runs", "scratch", "work"))
        self.inputs = root / "inputs"
        for directory in (self.cache, self.runs, self.scratch, self.work, self.inputs):
            directory.mkdir(parents=True)
        self.vars = {k: v for k, v in os.environ.items() if _passed_on(k)}
        self.vars.update(SLM_EVAL_CACHE=str(self.cache), SLM_EVAL_RUNS=str(self.runs),
                         SLM_EVAL_SCRATCH=str(self.scratch))

    def run(self, *args: object) -> subprocess.CompletedProcess:
        return subprocess.run(_command() + [str(a) for a in args], cwd=self.work, env=self.vars,
                              stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT)

    def files_written(self) -> list[str]:
        """Every regular file created under the cache, run store, scratch or working directory."""
        return [str(p.relative_to(self.root)) for base in (self.cache, self.runs, self.scratch, self.work)
                for p in sorted(base.rglob("*")) if p.is_file()]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _explain(proc: subprocess.CompletedProcess) -> str:
    return (f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}")


def dump(data) -> bytes:
    """JSON text, which every YAML parser also reads."""
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


def dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


# ---------------------------------------------------------------------------
# specs (§12.1)
# ---------------------------------------------------------------------------

SYNTHETIC_SPEC = {
    "spec_version": "1",
    "name": "fixture-1",
    "role": "fixture",
    "source": {"repo": "visible-suite/does-not-exist", "commit": "0123456789abcdef0123456789abcdef01234567",
               "files": [{"path": "config.json", "sha256": "e" * 64},
                         {"path": "model.safetensors", "sha256": "f" * 64}]},
    "architecture": "VisibleSuiteForCausalLM",
    "text_only": True,
    "template": {"inserts_system_message": False, "thinking_control": "none"},
    "conversion": {"tool": {"release": "b10412", "script_sha256": "1" * 64, "python_lock_sha256": "2" * 64},
                   "intermediate": "bf16", "quantization": "Q4_K_M", "calibration": "none"},
    "expected_sizes_gb": {"source": 1, "intermediate": 1, "output": 1},
    "sanity": {"reference": None, "perplexity_text_sha256": "3" * 64, "max_perplexity_ratio": 1.1},
}


def write_spec(env: Env, data: dict | None = None) -> Path:
    path = env.inputs / "model-spec.yaml"
    path.write_bytes(dump(copy.deepcopy(data or SYNTHETIC_SPEC)))
    return path


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


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    return yaml


def committed_specs() -> list[tuple[Path, dict]]:
    """(path, parsed) for every committed file holding a §12.1 spec, test directories excluded."""
    root, yaml = _repo_root(), _yaml()
    if root is None or yaml is None:
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS | TEST_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix not in (".yaml", ".yml", ".json"):
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and "spec_version" in data and "conversion" in data:
                found.append((path, data))
    return found


def committed_runtime_builds() -> set[str]:
    root, yaml = _repo_root(), _yaml()
    if root is None or yaml is None:
        return set()
    builds = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS | TEST_DIRS)
        for name in filenames:
            if name.endswith((".yaml", ".yml")):
                try:
                    data = yaml.safe_load((Path(dirpath) / name).read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(data, dict) and "protocol_version" in data and dig(data, "runtime", "build"):
                    builds.add(str(dig(data, "runtime", "build")))
    return builds


# ---------------------------------------------------------------------------
# tests: preflight disk floors (§12.3) and credentials (§3, §12.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("floor_var", ["SLM_EVAL_FLOOR_GB", "SLM_EVAL_SCRATCH_FLOOR_GB"])
def test_vs_cv_01_floor_breach_in_preflight_exits_3_and_writes_nothing(env, floor_var):
    """VS-CV-01 · hermetic · §12.3 the converter "refuses to start the step (exit 3) unless free space after
    that peak stays above the floor"; §3 "a disk-floor breach exits 3"; §12.2 preflight comes first.

    One floor set to 10^9 GB → `model convert` exits 3; no file is written
    under the cache, run store, scratch or working directory (no download,
    no `.partial`, no record); empty stdout. Any other preflight failure in
    this environment (for example a missing conversion tool) is also exit 3,
    so the order of checks does not matter.
    """
    env.vars[floor_var] = HUGE_FLOOR_GB
    proc = env.run("model", "convert", "--spec", write_spec(env))
    assert proc.returncode == EXIT_PRECONDITION, "expected exit 3\n" + _explain(proc)
    assert env.files_written() == [], f"a refused conversion wrote files: {env.files_written()}"
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)


def test_vs_cv_02_floor_breach_with_the_tool_installed(env):
    """VS-CV-02 · converter + repo · §12.3 preflight floor; §3 exit 3.

    With the pinned conversion tool installed, a committed spec and the
    model-cache floor at 10^9 GB → `model convert` exits 3 and writes no file
    under the cache, run store, scratch or working directory. Nothing is
    downloaded.
    """
    if os.environ.get("SLM_EVAL_TEST_CONVERTER", "").strip() != "1":
        pytest.skip("needs the pinned conversion tool: set SLM_EVAL_TEST_CONVERTER=1")
    specs = committed_specs()
    if not specs:
        pytest.skip("no committed model spec yet (§12.1)")
    env.vars["SLM_EVAL_FLOOR_GB"] = HUGE_FLOOR_GB
    proc = env.run("model", "convert", "--spec", specs[0][0])
    assert proc.returncode == EXIT_PRECONDITION, "expected exit 3\n" + _explain(proc)
    assert env.files_written() == [], f"a refused conversion wrote files: {env.files_written()}"


def test_vs_cv_03_download_credentials_are_never_printed_or_written(env):
    """VS-CV-03 · hermetic · §3 "never prints secrets"; §12.5 "Download credentials, if any, come from the
    environment and are never written anywhere"; brief hard invariant (no credential in any log or file).

    A unique sentinel in HF_TOKEN and HUGGING_FACE_HUB_TOKEN; `model convert`
    and `model sanity` on a synthetic spec (floors at 10^9 GB, so nothing is
    downloaded) → the sentinel appears in no stdout or stderr, and in no file
    under the cache, run store, scratch or working directory.
    """
    sentinel = "hf" + "_" + "VisibleSuiteSentinel" + uuid.uuid4().hex
    env.vars.update(HF_TOKEN=sentinel, HUGGING_FACE_HUB_TOKEN=sentinel, SLM_EVAL_FLOOR_GB=HUGE_FLOOR_GB,
                    SLM_EVAL_SCRATCH_FLOOR_GB=HUGE_FLOOR_GB)
    spec = write_spec(env)
    leaks = []
    for args in (("model", "convert", "--spec", spec), ("model", "sanity", "--spec", spec)):
        proc = env.run(*args)
        if sentinel in proc.stdout or sentinel in proc.stderr:
            leaks.append(f"`{' '.join(map(str, args[:2]))}` printed the credential")
    for name in env.files_written():
        if sentinel.encode() in (env.root / name).read_bytes():
            leaks.append(f"{name} holds the credential")
    assert not leaks, "\n".join(leaks)


def test_vs_cv_04_sanity_without_a_converted_file_exits_3(env):
    """VS-CV-04 · hermetic · §3 model sanity "Runs the spec's sanity check (§12.4) on the converted file";
    §3 exit 3 (missing file).

    A valid synthetic spec and an empty cache (nothing converted) → `model
    sanity` exits 3; no sanity record or other file written; empty stdout.
    """
    proc = env.run("model", "sanity", "--spec", write_spec(env))
    assert proc.returncode == EXIT_PRECONDITION, "expected exit 3\n" + _explain(proc)
    assert env.files_written() == [], f"a failing sanity check wrote files: {env.files_written()}"
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)


# ---------------------------------------------------------------------------
# tests: committed specs (repo)
# ---------------------------------------------------------------------------


def spec_problems(data: dict, builds: set[str]) -> list[str]:
    problems = []
    expect = {"spec_version": "1", "text_only": True}
    for key, value in expect.items():
        if data.get(key) != value:
            problems.append(f"{key} is {data.get(key)!r}, expected {value!r}")
    name = data.get("name")
    if name not in TARGET_ALIASES and not FIXTURE_ALIAS.fullmatch(str(name)):
        problems.append(f"name {name!r} is not a §10 alias")
    if data.get("role") not in ("target", "fixture"):
        problems.append(f"role {data.get('role')!r} is not target or fixture")
    repo = dig(data, "source", "repo")
    if not (isinstance(repo, str) and re.fullmatch(r"[^/\s]+/[^/\s]+", repo)):
        problems.append(f"source.repo {repo!r} is not owner/name")
    commit = dig(data, "source", "commit")
    if not HEX40.fullmatch(str(commit)):
        problems.append(f"source.commit {commit!r} is not a pinned 40-hex commit")
    files = dig(data, "source", "files")
    if not isinstance(files, list) or not files:
        problems.append("source.files is empty")
    for entry in files or []:
        path = str(dig(entry, "path"))
        if path.startswith(("/", "~")) or ".." in Path(path).parts:
            problems.append(f"source file path {path!r} is not relative to the source repository")
        if not HEX64.fullmatch(str(dig(entry, "sha256"))):
            problems.append(f"source file {path!r} has no 64-hex sha256")
    if not dig(data, "architecture"):
        problems.append("architecture is empty")
    if not isinstance(dig(data, "template", "inserts_system_message"), bool):
        problems.append("template.inserts_system_message is not a boolean")
    if dig(data, "template", "thinking_control") not in THINKING_CONTROLS:
        problems.append(f"template.thinking_control {dig(data, 'template', 'thinking_control')!r}")
    release = dig(data, "conversion", "tool", "release")
    if builds and release not in builds:
        problems.append(f"conversion.tool.release {release!r} is not the pinned runtime build {sorted(builds)}")
    for key in ("script_sha256", "python_lock_sha256"):
        if not HEX64.fullmatch(str(dig(data, "conversion", "tool", key))):
            problems.append(f"conversion.tool.{key} is not 64-hex")
    for key, value in (("intermediate", "bf16"), ("quantization", "Q4_K_M"), ("calibration", "none")):
        if dig(data, "conversion", key) != value:
            problems.append(f"conversion.{key} is {dig(data, 'conversion', key)!r}, expected {value!r}")
    for key in ("source", "intermediate", "output"):
        value = dig(data, "expected_sizes_gb", key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            problems.append(f"expected_sizes_gb.{key} {value!r} is not a positive number")
    reference = dig(data, "sanity", "reference")
    if reference is not None:
        if not (isinstance(reference, dict) and reference.get("repo") and reference.get("file")
                and re.fullmatch(r"[0-9a-f]{7,64}", str(reference.get("commit")))
                and HEX64.fullmatch(str(reference.get("sha256")))):
            problems.append(f"sanity.reference {reference!r} lacks repo, hex commit, file and 64-hex sha256")
    if not HEX64.fullmatch(str(dig(data, "sanity", "perplexity_text_sha256"))):
        problems.append("sanity.perplexity_text_sha256 is not 64-hex")
    ratio = dig(data, "sanity", "max_perplexity_ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or ratio <= 0:
        problems.append(f"sanity.max_perplexity_ratio {ratio!r} is not a positive number")
    return problems


def test_vs_cv_05_committed_specs_follow_the_spec_schema():
    """VS-CV-05 · repo · §12.1 spec schema: pinned 40-hex source commit, never a branch name; publisher
    hashes; §10 alias as name; text_only; conversion tool release equal to the one pinned runtime build
    (§1); bf16 → Q4_K_M with no calibration; positive expected sizes; sanity fields.

    Every committed spec satisfies these rules. Skipped until a spec exists.
    """
    specs = committed_specs()
    if not specs:
        pytest.skip("no committed model spec yet (§12.1)")
    builds = committed_runtime_builds()
    failures = {str(path): problems for path, data in specs if (problems := spec_problems(data, builds))}
    assert not failures, "committed specs depart from §12.1:\n" + json.dumps(failures, indent=2)


EXPECTED_THINKING_CONTROL = {"qwen3-8b": "runtime_flag", "phi-4-reasoning": "always",
                             "phi-4-reasoning-plus": "always", "gemma-4-12b": "template_arg"}


def test_vs_cv_06_target_specs_declare_their_familys_thinking_control():
    """VS-CV-06 · repo · §12.1 `template.thinking_control`, read against §4 "Reasoning modes by adapter"
    (qwen3: the runtime's reasoning flag; phi4-reasoning: none, the model always thinks; gemma4: a
    chat-template argument).

    Each committed target spec declares qwen3-8b `runtime_flag`, the two
    Phi-4-reasoning models `always` and gemma-4-12b `template_arg`. Skipped
    until a target spec exists.
    """
    specs = [(p, d) for p, d in committed_specs() if d.get("name") in EXPECTED_THINKING_CONTROL]
    if not specs:
        pytest.skip("no committed target spec yet (§12.1)")
    wrong = {str(p): dig(d, "template", "thinking_control") for p, d in specs
             if dig(d, "template", "thinking_control") != EXPECTED_THINKING_CONTROL[d["name"]]}
    assert not wrong, f"thinking_control departs from the §4 adapter table: {wrong}"
