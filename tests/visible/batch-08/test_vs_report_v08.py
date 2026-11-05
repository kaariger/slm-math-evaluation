"""Visible specification tests — v0.8 report additions: fixture runs and published figures that are not whole counts (contract v0.8 §4, §7).

Contract-only: written before any v0.8 implementation commit was supplied,
with no implementation code seen. Hermetic: each test writes a complete §6.1
run directory (v0.8 form) into its own temporary SLM_EVAL_RUNS, leaves
SLM_EVAL_CACHE empty, and needs no network, dataset, model or runtime.

Runs are recorded against the committed protocol (or the §4 draft defaults)
with the v0.8 fields added. Stored verdicts are ones every correct
implementation must reproduce: an identical integer answer is correct and a
different integer is incorrect (§9). The report's field names are not fixed
by §7, so the `not_whole_count` flag and the comparison reading are found by
value.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable is passed to the implementation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

TIMEOUT = 300
EXIT_OK, EXIT_PRECONDITION = 0, 3
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
READINGS = {"CONSISTENT", "PROTOCOL-SENSITIVE", "UNEXPLAINED"}
FLAG = "not_whole_count"

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
    """A temporary cache, run store, scratch and working directory for one test."""

    def __init__(self, root: Path):
        self.root = root
        self.cache, self.runs, self.scratch, self.work = (root / n for n in ("cache", "runs", "scratch", "work"))
        for directory in (self.cache, self.runs, self.scratch, self.work):
            directory.mkdir(parents=True)
        self.vars = {k: v for k, v in os.environ.items() if _passed_on(k)}
        self.vars.update(SLM_EVAL_CACHE=str(self.cache), SLM_EVAL_RUNS=str(self.runs),
                         SLM_EVAL_SCRATCH=str(self.scratch))

    def run(self, *args: object) -> subprocess.CompletedProcess:
        return subprocess.run(_command() + [str(a) for a in args], cwd=self.work, env=self.vars,
                              stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT)

    def snapshot(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.root)): p.read_bytes()
                for base in (self.cache, self.runs, self.scratch, self.work)
                for p in sorted(base.rglob("*")) if p.is_file()}


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


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


# ---------------------------------------------------------------------------
# protocols (§4) and run directories (§6.1)
# ---------------------------------------------------------------------------

DRAFT_PROTOCOL = {
    "protocol_version": "v1-draft",
    "model": {"artifact": {"file": "visible-suite-model-q4_k_m.gguf", "sha256": "a" * 64, "quantization": "Q4_K_M",
                           "provenance": "local-choice"}},
    "runtime": {"build": "b10412", "invocation": "keyed-run-scoped-server", "provenance": "local-choice"},
    "prompt": {"template": "{problem}\n\nPlease reason step by step, and put your final answer within \\boxed{}.",
               "system_prompt": None, "few_shot": 0, "provenance": "vendor-filled"},
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
    "extraction": {"id": "visible-suite-extractor", "version": "0.1.0"},
    "scorers": {"primary": {"id": "prm800k-grader", "pin": "b" * 40},
                "secondary": {"id": "math-verify", "pin": "0.1.0"}},
    "dataset": {"source": {"url": "https://example.invalid/visible-suite-dataset", "commit": "c" * 40,
                           "files": [{"path": "test.jsonl", "sha256": "d" * 64}]}},
}


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


def _base_protocol() -> dict:
    """The first committed qwen3 (or adapter-less) protocol, else the §4 draft defaults."""
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
                    return copy.deepcopy(data)
    return copy.deepcopy(DRAFT_PROTOCOL)


def v08_protocol(role: str) -> tuple[bytes, dict]:
    data = _base_protocol()
    data["role"] = role
    model = data.setdefault("model", {})
    model.setdefault("adapter", {"id": "qwen3", "version": "1.0.0"})
    model.setdefault("chat_template", {"source": "embedded", "sha256": sha256_text("visible-suite chat template")})
    if dig(data, "reasoning_mode", "value") not in ("hybrid", "forced_think", "forced_non_think"):
        data["reasoning_mode"] = {"value": "hybrid", "provenance": "inferred"}
    data.setdefault("resources", {
        "max_memory_gb": {"value": 24, "provenance": "local-choice"},
        "min_free_memory_gb": {"value": 4, "provenance": "local-choice"},
        "stuck_after_awake_s": {"value": 600, "provenance": "local-choice"},
    })
    return dump(data), data


def write_run(env: Env, answers: list[str], *, role: str = "target") -> tuple[str, Path]:
    """A complete v0.8 §6.1 run directory: k = 1, one item per answer, reference "7"."""
    raw_protocol, data = v08_protocol(role)
    run_id = f"visible-{uuid.uuid4().hex[:12]}"
    run_dir = env.runs / run_id
    run_dir.mkdir()
    ids = [f"visible-item-{n:03d}" for n in range(len(answers))]
    manifest = dump({
        "manifest_version": "1", "source": dig(data, "dataset", "source") or {},
        "membership_derivation": {"method": "unique_id", "independent_source": None},
        "normalization": {"version": "visible-1", "near_duplicate": {"method": "visible", "threshold": 0.85}},
        "items": [{"id": i, "role": "dev", "content_sha256": sha256_text(i), "subject": "Algebra", "level": 2}
                  for i in ids],
        "exclusions": [], "tiers": {"seed": 7, "smoke": ids, "validation": []},
        "provenance_note": "visible-suite fixture manifest",
    })
    with open(run_dir / "items.jsonl", "w", encoding="utf-8") as fh:
        for n, (item_id, answer) in enumerate(zip(ids, answers)):
            raw = "<think>\nCheck small cases.\n</think>\n\nThe answer is \\boxed{" + answer + "}."
            correct = answer == "7"
            fh.write(json.dumps({
                "item_id": item_id, "sample_index": 0, "seed": 4100,
                "prompt_sha256": sha256_text("visible fixture prompt " + item_id),
                "raw_output": raw, "raw_output_sha256": sha256_text(raw),
                "thinking_present": True, "thinking_state": "present", "truncated": False,
                "finish_reason": "stop", "tokens_generated": 60, "infra_retries": 0,
                "reference_answer": "7", "reference_sha256": sha256_text("7"),
                "extraction": {"answer": answer, "rule": "boxed_last", "status": "ok"},
                "verdicts": {"primary": correct, "secondary": correct},
                "timing_ms": 600 + 9 * n, "awake_ms": 590 + 9 * n,
            }) + "\n")
    sampling = {k: v.get("value") for k, v in (data.get("sampling") or {}).items() if isinstance(v, dict)}
    run_json = {
        "run_id": run_id, "protocol_version": data.get("protocol_version"),
        "protocol_sha256": sha256_bytes(raw_protocol), "manifest_sha256": sha256_bytes(manifest),
        "tier": "smoke", "k": 1, "seeds": [4100],
        "model_artifact_sha256": dig(data, "model", "artifact", "sha256"),
        "runtime_build": dig(data, "runtime", "build"),
        "adapter": dict(dig(data, "model", "adapter")),
        "extractor": {"id": dig(data, "extraction", "id"), "version": dig(data, "extraction", "version")},
        "scorers": [{"id": dig(data, "scorers", r, "id"), "pin": dig(data, "scorers", r, "pin")}
                    for r in ("primary", "secondary")],
        "role": role, "subset_sha256": None, "chat_template_sha256": dig(data, "model", "chat_template", "sha256"),
        "effective": {"reasoning": dig(data, "reasoning_mode", "value"), "template_args": {}, "sampling": sampling},
        "executor": "visible-suite fixture", "host": {"os": "Darwin", "arch": "arm64", "memory_gb": 32},
        "attempts": [{"n": 1, "started": "2026-10-27T09:00:00Z", "ended": "2026-10-27T09:20:00Z",
                      "status": "completed", "resumed_from": None}],
        "events": [], "status": "completed",
    }
    (run_dir / "protocol.yaml").write_bytes(raw_protocol)
    (run_dir / "manifest.json").write_bytes(manifest)
    (run_dir / "run.json").write_text(json.dumps(run_json, indent=2) + "\n", encoding="utf-8")
    for name in ("runtime-verification.json", "artifact-verification.json"):
        (run_dir / name).write_text(json.dumps({"fixture": "visible-suite", "passed": True}) + "\n", encoding="utf-8")
    return run_id, run_dir


def _walk(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield None, value
            yield from _walk(value)


def flag_raised(doc) -> bool:
    """True when the report carries `not_whole_count` as a string value or as a key with a truthy value."""
    for key, value in _walk(doc):
        if key == FLAG and value:
            return True
        if value == FLAG:
            return True
    return False


def readings(doc) -> set[str]:
    return {value for _, value in _walk(doc) if isinstance(value, str) and value in READINGS}


def report_doc(env: Env, run_id: str, run_dir: Path, value: str) -> dict:
    proc = env.run("report", "--run", run_id, "--compare-published", value)
    assert proc.returncode == EXIT_OK, "report failed\n" + _explain(proc)
    path = run_dir / "report.json"
    assert path.is_file(), "report exited 0 without writing report.json in the run directory\n" + _explain(proc)
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("out_dir", [False, True], ids=["default-out-dir", "explicit-out-dir"])
def test_vs_rf_01_a_fixture_run_cannot_be_reported(env, out_dir):
    """VS-RF-01 · hermetic · §7 "A run whose protocol has role: fixture can't be reported: report exits 3";
    §4 fixture runs are never reported; §3 written paths only on stdout.

    A completed run whose protocol copy (and run.json) has `role: fixture` →
    `report` exits 3; no report.json or report.md anywhere; no file added or
    changed; empty stdout.
    """
    run_id, _ = write_run(env, ["7", "7", "4"], role="fixture")
    before = env.snapshot()
    args = ["report", "--run", run_id]
    if out_dir:
        args += ["--out-dir", env.work / "reports"]
    proc = env.run(*args)
    assert proc.returncode == EXIT_PRECONDITION, "expected exit 3\n" + _explain(proc)
    assert env.snapshot() == before, "a refused report wrote or changed files"
    assert not (env.work / "reports").exists() or not any((env.work / "reports").iterdir()), \
        "a refused report wrote into --out-dir"
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)


def test_vs_rf_02_a_published_value_that_is_not_a_whole_count_is_flagged(env):
    """VS-RF-02 · hermetic · §7 "if V × (items × samples) is not a whole number, the report flags V as
    not_whole_count … It is still shown as a reference"; comparison reading rules.

    Three items, k = 1, `--compare-published 0.5` (0.5 × 3 = 1.5) → exit 0;
    report.json carries `not_whole_count`, and still gives a comparison
    reading (CONSISTENT, PROTOCOL-SENSITIVE or UNEXPLAINED).
    """
    run_id, run_dir = write_run(env, ["7", "7", "4"])
    doc = report_doc(env, run_id, run_dir, "0.5")
    assert flag_raised(doc), f"report.json does not flag {FLAG}: {json.dumps(doc)[:1500]}"
    assert readings(doc), f"report.json gives no comparison reading: {json.dumps(doc)[:1500]}"


def test_vs_rf_03_a_whole_count_is_not_flagged(env):
    """VS-RF-03 · hermetic · §7 not_whole_count applies only when V × (items × samples) is not whole.

    Four items, k = 1, `--compare-published 0.5` (0.5 × 4 = 2) → exit 0;
    report.json does not raise `not_whole_count` (absent, or a false value).
    """
    run_id, run_dir = write_run(env, ["7", "7", "4", "4"])
    doc = report_doc(env, run_id, run_dir, "0.5")
    assert not flag_raised(doc), f"report.json flags {FLAG} for a whole count: {json.dumps(doc)[:1500]}"
