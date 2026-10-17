"""Visible specification tests — `slm-eval sensitivity` (contract v0.3 §3, §6.1, §7.1).

Contract-only: written before any implementation commit was supplied, with
no implementation code seen. Hermetic: base and variant runs are complete
§6.1 run directories written into each test's temporary SLM_EVAL_RUNS; no
network, dataset, model or runtime is needed.

§7.1 (v0.3): factors are compared on the run-directory protocol copies;
protocol_version and all provenance labels are ignored; model.artifact
(file, sha256, quantization together) is one factor; every other differing
leaf field is its own factor, named by its dotted path. Zero or several
factors, or a different manifest, tier or item set, exit 3 with no output.

The base protocol is the committed protocol.yaml when it can be found
(SLM_EVAL_TEST_PROTOCOL, or a protocol.yaml inside the repository that
contains the pytest working directory) and parsed; otherwise the contract
§4 draft defaults. Each variant is a new protocol version derived from it.
For a {value, provenance} field the factor may be named with or without the
final `.value` (§7.1's examples omit it). SLM_EVAL_TEST_CMD overrides the
CLI command.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

TIMEOUT = 300
EXIT_OK, EXIT_INTEGRITY, EXIT_PRECONDITION = 0, 2, 3
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
METHOD = "paired bootstrap over items, primary scorer"
PROVENANCE = ("source-known-paper", "source-known-blog", "vendor-filled", "inferred", "local-choice")
N = 16

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


class Env:
    """A temporary cache, run store and working directory for one test."""

    def __init__(self, root: Path):
        self.cache = root / "cache"
        self.runs = root / "runs"
        self.work = root / "work"
        for directory in (self.cache, self.runs, self.work):
            directory.mkdir(parents=True)
        self.vars = {k: v for k, v in os.environ.items()
                     if k not in ("SLM_EVAL_CACHE", "SLM_EVAL_RUNS", "SLM_EVAL_TEST_CMD", "SLM_EVAL_TEST_PROTOCOL")}
        self.vars["SLM_EVAL_CACHE"] = str(self.cache)
        self.vars["SLM_EVAL_RUNS"] = str(self.runs)

    def run(self, *args: object) -> subprocess.CompletedProcess:
        return subprocess.run(
            _command() + [str(a) for a in args],
            cwd=self.work, env=self.vars, stdin=subprocess.DEVNULL, capture_output=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT,
        )


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


# ---------------------------------------------------------------------------
# fixture protocols and run directories (§4, §5, §6.1)
# ---------------------------------------------------------------------------

DRAFT_PROTOCOL = {
    "protocol_version": "v1-draft",
    "model": {"artifact": {"file": "qwen3-8b-q4_k_m.gguf", "sha256": "a" * 64, "quantization": "Q4_K_M",
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


def _protocol_candidates() -> list[Path]:
    configured = os.environ.get("SLM_EVAL_TEST_PROTOCOL", "").strip()
    if configured:
        return [Path(configured).expanduser()]
    root = _repo_root()
    if root is None:
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        if "protocol.yaml" in filenames:
            found.append(Path(dirpath) / "protocol.yaml")
    return sorted(found, key=lambda p: (bool(TEST_DIRS & set(p.relative_to(root).parts)), str(p)))


def dump(data: dict) -> bytes:
    """JSON text, which every YAML parser also reads."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def base_protocol() -> tuple[bytes, dict, str]:
    """(raw bytes, parsed data, note) of the base protocol."""
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        for path in _protocol_candidates():
            try:
                raw = path.read_bytes()
                data = yaml.safe_load(raw.decode("utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and "protocol_version" in data:
                return raw, data, f"base protocol: committed {path}"
    data = copy.deepcopy(DRAFT_PROTOCOL)
    return dump(data), data, "base protocol: contract §4 draft defaults (committed protocol not found)"


def variant_of(base: tuple[bytes, dict, str], changes: dict, tag: str) -> tuple[bytes, dict, str]:
    """A new protocol version: `changes` maps dotted paths to new values."""
    data = copy.deepcopy(base[1])
    data["protocol_version"] = f"{data.get('protocol_version')}-visible-{tag}"
    for dotted, value in changes.items():
        node = data
        keys = dotted.split(".")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
    return dump(data), data, f"variant {tag} of {base[2]}"


def dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def fixture_manifest(item_ids, protocol_data: dict) -> bytes:
    ids = sorted(set(item_ids))
    doc = {
        "manifest_version": "1",
        "source": dig(protocol_data, "dataset", "source") or {},
        "membership_derivation": {"method": "unique_id", "independent_source": {
            "url": "https://example.invalid/visible-suite-index", "revision": "e" * 40, "sha256": "f" * 64}},
        "normalization": {"version": "visible-1", "near_duplicate": {"method": "visible", "threshold": 0.85}},
        "items": [{"id": i, "role": "dev", "content_sha256": sha256_text(i), "subject": "Counting & Probability",
                   "level": 4} for i in ids],
        "exclusions": [],
        "tiers": {"seed": 3, "smoke": ids, "validation": []},
        "provenance_note": "visible-suite fixture manifest",
    }
    return dump(doc)


def item(index: int) -> str:
    return f"visible-item-{index:03d}"


def write_run(env: Env, correct: int, *, protocol: tuple[bytes, dict, str], manifest_bytes: bytes | None = None,
              seed_base: int = 8800) -> tuple[str, Path]:
    """A k=1 smoke run of N items whose first `correct` are correct under both scorers."""
    raw_protocol, data, _ = protocol
    run_id = f"visible-{uuid.uuid4().hex[:12]}"
    run_dir = env.runs / run_id
    run_dir.mkdir()
    ids = [item(i) for i in range(N)]
    if manifest_bytes is None:
        manifest_bytes = fixture_manifest(ids, protocol_data=base_protocol()[1])
    lines = []
    for i, item_id in enumerate(ids):
        reference = str(60 + i)
        answer = reference if i < correct else str(600 + i)
        raw = "<think>\nCount the complement instead.\n</think>\n\nHence \\boxed{" + answer + "}."
        lines.append({
            "item_id": item_id, "sample_index": 0, "seed": seed_base,
            "prompt_sha256": sha256_text("visible fixture prompt " + item_id),
            "raw_output": raw, "raw_output_sha256": sha256_text(raw),
            "thinking_present": True, "truncated": False, "finish_reason": "stop", "tokens_generated": 90 + i,
            "reference_answer": reference, "reference_sha256": sha256_text(reference),
            "extraction": {"answer": answer, "rule": "boxed_last", "status": "ok"},
            "verdicts": {"primary": i < correct, "secondary": i < correct},
            "timing_ms": 700 + 11 * i,
        })
    scorers = [{"id": dig(data, "scorers", role, "id"), "pin": dig(data, "scorers", role, "pin")}
               for role in ("primary", "secondary")]
    run_json = {
        "run_id": run_id,
        "protocol_version": data.get("protocol_version"),
        "protocol_sha256": sha256_bytes(raw_protocol),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "tier": "smoke", "k": 1, "seeds": [seed_base],
        "model_artifact_sha256": dig(data, "model", "artifact", "sha256"),
        "runtime_build": dig(data, "runtime", "build"),
        "adapter": {"id": "visible-suite-adapter", "version": "0.1.0"},
        "extractor": {"id": dig(data, "extraction", "id"), "version": dig(data, "extraction", "version")},
        "scorers": scorers,
        "executor": "visible-suite fixture",
        "host": {"os": "Darwin", "arch": "arm64", "memory_gb": 32},
        "attempts": [{"n": 1, "started": "2026-10-06T13:00:00Z", "ended": "2026-10-06T13:25:00Z",
                      "status": "completed", "resumed_from": None}],
        "status": "completed",
    }
    (run_dir / "protocol.yaml").write_bytes(raw_protocol)
    (run_dir / "manifest.json").write_bytes(manifest_bytes)
    (run_dir / "run.json").write_text(json.dumps(run_json, indent=2) + "\n", encoding="utf-8")
    with open(run_dir / "items.jsonl", "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    for name in ("runtime-verification.json", "artifact-verification.json"):
        (run_dir / name).write_text(json.dumps({"fixture": "visible-suite", "passed": True}) + "\n", encoding="utf-8")
    return run_id, run_dir


# ---------------------------------------------------------------------------
# helpers for the comparisons
# ---------------------------------------------------------------------------


def top_k_values(base) -> tuple[object, int]:
    current = dig(base[1], "sampling", "top_k", "value")
    return current, (int(current) * 2 if isinstance(current, int) and current > 0 else 40)


def other_provenance(current) -> str:
    return next(p for p in PROVENANCE if p != current)


def sensitivity(env: Env, base_id: str, *variant_ids: str, name: str = "sensitivity.json"):
    out = env.work / name
    args: list[object] = ["sensitivity", "--base", base_id]
    for variant_id in variant_ids:
        args += ["--variant", variant_id]
    return env.run(*args, "--out", out), out


def loose_equal(a, b) -> bool:
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def comparison_problems(c: dict, variant_id: str, factors: set[str], base_acc: float, variant_acc: float,
                        base_value=None, variant_value=None) -> list[str]:
    problems = []
    if c.get("variant_run") != variant_id:
        problems.append(f"variant_run {c.get('variant_run')!r}")
    if c.get("factor") not in factors:
        problems.append(f"factor {c.get('factor')!r} not in {sorted(factors)}")
    if base_value is not None and not loose_equal(c.get("base_value"), base_value):
        problems.append(f"base_value {c.get('base_value')!r} != {base_value!r}")
    if variant_value is not None and not loose_equal(c.get("variant_value"), variant_value):
        problems.append(f"variant_value {c.get('variant_value')!r} != {variant_value!r}")
    if c.get("paired_items") != N:
        problems.append(f"paired_items {c.get('paired_items')!r} != {N}")
    for key, expected in (("base_accuracy", base_acc), ("variant_accuracy", variant_acc),
                          ("effect", variant_acc - base_acc)):
        value = c.get(key)
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value - expected) <= 1e-9):
            problems.append(f"{key} {value!r} != {expected}")
    interval = c.get("effect_interval_95")
    if not (isinstance(interval, list) and len(interval) == 2
            and all(isinstance(x, (int, float)) and math.isfinite(x) for x in interval)
            and -1.0 <= interval[0] <= variant_acc - base_acc <= interval[1] <= 1.0):
        problems.append(f"effect_interval_95 {interval!r} does not bracket the effect within [-1, 1]")
    if c.get("method") != METHOD:
        problems.append(f"method {c.get('method')!r}")
    for key in ("bootstrap_seed", "bootstrap_resamples"):
        if not (isinstance(c.get(key), int) and not isinstance(c.get(key), bool)):
            problems.append(f"{key} {c.get(key)!r} is not an integer")
    if isinstance(c.get("bootstrap_resamples"), int) and c["bootstrap_resamples"] <= 0:
        problems.append("bootstrap_resamples is not positive")
    return problems


def single_comparison(env: Env, proc: subprocess.CompletedProcess, out: Path, note: str) -> dict:
    assert proc.returncode == EXIT_OK, _explain(proc) + "\n" + note
    doc = json.loads(out.read_text(encoding="utf-8"))
    comparisons = doc.get("comparisons")
    assert isinstance(comparisons, list) and len(comparisons) == 1, f"expected one comparison: {comparisons!r}"
    return comparisons[0]


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_vs_se_01_single_factor_file(env):
    """VS-SE-01 · §7.1 schema and rules: base 4/16 correct, a variant
    differing only in sampling.top_k with 8/16 correct on the same items;
    exit 0 and a sensitivity file with version "1", the base run, the
    SHA-256 of the base protocol copy, and one comparison: factor
    sampling.top_k, base and variant values, 16 paired items, accuracies
    0.25 / 0.5, effect 0.25 bracketed by its interval, the stated method,
    and integer bootstrap seed and resample count."""
    base = base_protocol()
    old, new = top_k_values(base)
    base_id, base_dir = write_run(env, 4, protocol=base)
    variant_id, _ = write_run(env, 8, protocol=variant_of(base, {"sampling.top_k.value": new}, "topk"))
    proc, out = sensitivity(env, base_id, variant_id)
    c = single_comparison(env, proc, out, base[2])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc.get("sensitivity_version") == "1"
    assert doc.get("base_run") == base_id
    assert doc.get("base_protocol_sha256") == sha256_bytes((base_dir / "protocol.yaml").read_bytes())
    problems = comparison_problems(c, variant_id, {"sampling.top_k", "sampling.top_k.value"}, 0.25, 0.5, old, new)
    assert not problems, "; ".join(problems)


def test_vs_se_02_written_path_is_printed(env):
    """VS-SE-02 · §3 "prints each written path to stdout": sensitivity prints
    the --out path it wrote."""
    base = base_protocol()
    _, new = top_k_values(base)
    base_id, _ = write_run(env, 4, protocol=base)
    variant_id, _ = write_run(env, 8, protocol=variant_of(base, {"sampling.top_k.value": new}, "topk"))
    proc, out = sensitivity(env, base_id, variant_id)
    assert proc.returncode == EXIT_OK, _explain(proc)
    printed = set()
    for line in proc.stdout.splitlines():
        if line.strip():
            path = Path(line.strip())
            printed.add((path if path.is_absolute() else env.work / path).resolve())
    assert out.resolve() in printed, "the --out path is not printed on stdout\n" + _explain(proc)


def test_vs_se_03_provenance_and_version_changes_are_ignored(env):
    """VS-SE-03 · §7.1 "protocol_version and all provenance labels are
    ignored": a variant changing sampling.top_k's value and its provenance
    label (and, like every variant, protocol_version) differs in exactly one
    factor, sampling.top_k: exit 0 with one comparison."""
    base = base_protocol()
    old, new = top_k_values(base)
    provenance = other_provenance(dig(base[1], "sampling", "top_k", "provenance"))
    base_id, _ = write_run(env, 4, protocol=base)
    variant = variant_of(base, {"sampling.top_k.value": new, "sampling.top_k.provenance": provenance}, "topk-prov")
    variant_id, _ = write_run(env, 8, protocol=variant)
    proc, out = sensitivity(env, base_id, variant_id)
    c = single_comparison(env, proc, out, base[2])
    problems = comparison_problems(c, variant_id, {"sampling.top_k", "sampling.top_k.value"}, 0.25, 0.5, old, new)
    assert not problems, "; ".join(problems)


def test_vs_se_04_provenance_only_change_is_zero_factors(env):
    """VS-SE-04 · §7.1 provenance labels ignored + "zero factors differ"
    exits 3 and writes no output: a variant whose only changes are a
    provenance label and protocol_version."""
    base = base_protocol()
    provenance = other_provenance(dig(base[1], "reasoning_mode", "provenance"))
    base_id, _ = write_run(env, 4, protocol=base)
    variant_id, _ = write_run(env, 8, protocol=variant_of(base, {"reasoning_mode.provenance": provenance}, "prov"))
    proc, out = sensitivity(env, base_id, variant_id)
    assert proc.returncode == EXIT_PRECONDITION, _explain(proc)
    assert not out.exists(), "a sensitivity file was written although no factor differs"


def test_vs_se_05_leaf_field_named_by_dotted_path(env):
    """VS-SE-05 · §7.1 "Every other differing leaf field is its own factor,
    named by its dotted path": a variant changing prompt.template gives one
    comparison with factor "prompt.template" (accuracies 0.25 / 0.125)."""
    base = base_protocol()
    template = str(dig(base[1], "prompt", "template")) + "\nShow every step."
    base_id, _ = write_run(env, 4, protocol=base)
    variant_id, _ = write_run(env, 2, protocol=variant_of(base, {"prompt.template": template}, "template"))
    proc, out = sensitivity(env, base_id, variant_id)
    c = single_comparison(env, proc, out, base[2])
    problems = comparison_problems(c, variant_id, {"prompt.template"}, 0.25, 0.125)
    assert not problems, "; ".join(problems)


def test_vs_se_06_artifact_file_and_hash_are_one_factor(env):
    """VS-SE-06 · §7.1 "model.artifact (file, sha256, quantization together)
    counts as one factor": a variant whose artifact file name and sha256
    both change (same quantization) gives one comparison with factor
    model.artifact (accuracies 0.25 / 0.375)."""
    base = base_protocol()
    file_name = str(dig(base[1], "model", "artifact", "file") or "model.gguf")
    changes = {"model.artifact.file": "rebuilt-" + file_name,
               "model.artifact.sha256": sha256_text("visible variant artifact")}
    base_id, _ = write_run(env, 4, protocol=base)
    variant_id, _ = write_run(env, 6, protocol=variant_of(base, changes, "artifact"))
    proc, out = sensitivity(env, base_id, variant_id)
    c = single_comparison(env, proc, out, base[2])
    problems = comparison_problems(c, variant_id, {"model.artifact"}, 0.25, 0.375)
    assert not problems, "; ".join(problems)


def test_vs_se_07_artifact_plus_another_field_is_two_factors(env):
    """VS-SE-07 · §7.1 "more than one [factor differs]" exits 3 and writes no
    output: a variant changing model.artifact and sampling.top_k."""
    base = base_protocol()
    _, new = top_k_values(base)
    changes = {"model.artifact.sha256": sha256_text("another visible artifact"), "sampling.top_k.value": new}
    base_id, _ = write_run(env, 4, protocol=base)
    variant_id, _ = write_run(env, 6, protocol=variant_of(base, changes, "two"))
    proc, out = sensitivity(env, base_id, variant_id)
    assert proc.returncode == EXIT_PRECONDITION, _explain(proc)
    assert not out.exists(), "a sensitivity file was written for a variant differing in two factors"


def test_vs_se_08_altered_base_protocol_copy_exits_2(env):
    """VS-SE-08 · §6.1 sensitivity "read[s] only these copies, and exit[s] 2
    if a copy's hash fails": an altered protocol.yaml copy in the base run
    makes sensitivity exit 2."""
    base = base_protocol()
    _, new = top_k_values(base)
    base_id, base_dir = write_run(env, 4, protocol=base)
    variant_id, _ = write_run(env, 8, protocol=variant_of(base, {"sampling.top_k.value": new}, "topk"))
    copy_path = base_dir / "protocol.yaml"
    copy_path.write_bytes(copy_path.read_bytes() + b"\n")
    proc, _ = sensitivity(env, base_id, variant_id)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)


def test_vs_se_09_wrong_record_hash_in_variant_exits_2(env):
    """VS-SE-09 · §6.1 "Every command that reads items.jsonl checks each
    record's raw_output_sha256 ... and exits 2 on any mismatch": a variant
    record whose raw output was edited after its hash was recorded makes
    sensitivity exit 2."""
    base = base_protocol()
    _, new = top_k_values(base)
    base_id, _ = write_run(env, 4, protocol=base)
    variant_id, variant_dir = write_run(env, 8, protocol=variant_of(base, {"sampling.top_k.value": new}, "topk"))
    path = variant_dir / "items.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    lines[9]["raw_output"] = lines[9]["raw_output"].replace("Hence", "Thus")
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    proc, _ = sensitivity(env, base_id, variant_id)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)


def test_vs_se_10_output_is_reproducible(env):
    """VS-SE-10 · §3 sensitivity "Deterministic" + §7.1 "the output is
    reproducible": two invocations over the same base and variant give the
    same JSON."""
    base = base_protocol()
    _, new = top_k_values(base)
    base_id, _ = write_run(env, 4, protocol=base)
    variant_id, _ = write_run(env, 8, protocol=variant_of(base, {"sampling.top_k.value": new}, "topk"))
    first, out_a = sensitivity(env, base_id, variant_id, name="first.json")
    second, out_b = sensitivity(env, base_id, variant_id, name="second.json")
    assert first.returncode == EXIT_OK, _explain(first)
    assert second.returncode == EXIT_OK, _explain(second)
    assert json.loads(out_a.read_text(encoding="utf-8")) == json.loads(out_b.read_text(encoding="utf-8"))
