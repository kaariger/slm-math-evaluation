"""Visible specification tests — `slm-eval report` (contract v0.3 §3, §6.1, §7).

Contract-only: written before any implementation commit was supplied, with
no implementation code seen. Hermetic: each test writes complete §6.1 run
directories into its own temporary SLM_EVAL_RUNS and needs no network,
dataset, model or runtime. Stored verdicts are chosen so that every
expected aggregate is known exactly; the report is computed from them.

v0.3 fixes the report's contents (§7), its location (`--out-dir`, default
the run directory, §3) and that accuracies, rates and V are fractions in
[0, 1] (§3), but not its JSON field names. These tests therefore look for
values, and for values under keys containing a keyword, not for exact names.

Fixture runs are recorded against the committed protocol.yaml when it can be
found (SLM_EVAL_TEST_PROTOCOL, or a protocol.yaml inside the repository that
contains the pytest working directory) and parsed; otherwise against the
contract §4 draft defaults. SLM_EVAL_TEST_CMD overrides the CLI command.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
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


def printed_paths(proc: subprocess.CompletedProcess, cwd: Path) -> set[Path]:
    paths = set()
    for line in proc.stdout.splitlines():
        if line.strip():
            path = Path(line.strip())
            paths.add((path if path.is_absolute() else cwd / path).resolve())
    return paths


# ---------------------------------------------------------------------------
# fixture protocol and run directories (§4, §5, §6.1)
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


def fixture_protocol() -> tuple[bytes, dict, str]:
    """(raw bytes, parsed data, note) of the protocol that fixture runs are recorded against."""
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
                return raw, data, f"fixture protocol: committed {path}"
    data = copy.deepcopy(DRAFT_PROTOCOL)
    return dump(data), data, "fixture protocol: contract §4 draft defaults (committed protocol not found)"


def dump(data: dict) -> bytes:
    """JSON text, which every YAML parser also reads."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


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
        "items": [{"id": i, "role": "dev", "content_sha256": sha256_text(i), "subject": "Geometry", "level": 3}
                  for i in ids],
        "exclusions": [],
        "tiers": {"seed": 11, "smoke": ids, "validation": []},
        "provenance_note": "visible-suite fixture manifest",
    }
    return dump(doc)


def item(index: int) -> str:
    return f"visible-item-{index:03d}"


def record(item_id: str, sample: int, *, answer: str | None, reference: str, primary: bool, secondary: bool,
           status: str = "ok", rule: str = "boxed_last", truncated: bool = False, think: bool = True,
           raw: str | None = None) -> dict:
    if raw is None:
        final = "So the result is \\boxed{" + answer + "}." if answer is not None else "I cannot settle this."
        raw = ("<think>\nLook for an invariant first.\n</think>\n\n" + final) if think else final
    return {
        "item_id": item_id, "sample_index": sample, "raw_output": raw, "reference_answer": reference,
        "extraction": {"answer": answer, "rule": rule if answer is not None or rule != "boxed_last" else "none",
                       "status": status},
        "verdicts": {"primary": primary, "secondary": secondary},
        "truncated": truncated, "thinking_present": think,
    }


def verdict_records(n: int, primary_correct: int, secondary_correct: int, sample: int = 0) -> list[dict]:
    """n items; the first `primary_correct` are primary-correct, the first `secondary_correct` secondary-correct."""
    out = []
    for i in range(n):
        reference = str(40 + i)
        primary, secondary = i < primary_correct, i < secondary_correct
        out.append(record(item(i), sample, answer=reference if (primary or secondary) else str(900 + i),
                          reference=reference, primary=primary, secondary=secondary))
    return out


def write_run(env: Env, records: list[dict], *, protocol: tuple[bytes, dict, str],
              seed_base: int = 5200) -> tuple[str, Path]:
    """A complete §6.1 run directory; returns (run_id, run_dir)."""
    raw_protocol, data, _ = protocol
    run_id = f"visible-{uuid.uuid4().hex[:12]}"
    run_dir = env.runs / run_id
    run_dir.mkdir()
    manifest_bytes = fixture_manifest((r["item_id"] for r in records), data)
    k = max(r["sample_index"] for r in records) + 1
    lines = []
    for index, r in enumerate(records):
        lines.append({
            "item_id": r["item_id"],
            "sample_index": r["sample_index"],
            "seed": seed_base + r["sample_index"],
            "prompt_sha256": sha256_text("visible fixture prompt " + r["item_id"]),
            "raw_output": r["raw_output"],
            "raw_output_sha256": sha256_text(r["raw_output"]),
            "thinking_present": r["thinking_present"],
            "truncated": r["truncated"],
            "finish_reason": "length" if r["truncated"] else "stop",
            "tokens_generated": 60 + len(r["raw_output"]) // 4,
            "reference_answer": r["reference_answer"],
            "reference_sha256": sha256_text(r["reference_answer"]),
            "extraction": dict(r["extraction"]),
            "verdicts": dict(r["verdicts"]),
            "timing_ms": 500 + 3 * index,
        })
    scorers = [{"id": dig(data, "scorers", role, "id"), "pin": dig(data, "scorers", role, "pin")}
               for role in ("primary", "secondary")]
    run_json = {
        "run_id": run_id,
        "protocol_version": data.get("protocol_version"),
        "protocol_sha256": sha256_bytes(raw_protocol),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "tier": "smoke",
        "k": k,
        "seeds": [seed_base + s for s in range(k)],
        "model_artifact_sha256": dig(data, "model", "artifact", "sha256"),
        "runtime_build": dig(data, "runtime", "build"),
        "adapter": {"id": "visible-suite-adapter", "version": "0.1.0"},
        "extractor": {"id": dig(data, "extraction", "id"), "version": dig(data, "extraction", "version")},
        "scorers": scorers,
        "executor": "visible-suite fixture",
        "host": {"os": "Darwin", "arch": "arm64", "memory_gb": 32},
        "attempts": [{"n": 1, "started": "2026-10-06T11:00:00Z", "ended": "2026-10-06T11:40:00Z",
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
# reading report.json without assuming field names
# ---------------------------------------------------------------------------


def walk(obj, path=()):
    yield path, obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk(value, path + (str(key),))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk(value, path + (index,))


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def numbers(obj) -> list[float]:
    return [float(v) for _, v in walk(obj) if is_number(v)]


def numbers_under(obj, needle: str) -> list[float]:
    """Numbers whose key path contains a key with `needle` in it."""
    return [float(v) for p, v in walk(obj)
            if is_number(v) and any(isinstance(k, str) and needle in k.lower() for k in p)]


def close(values, target: float, tol: float = 5e-4) -> bool:
    return any(abs(v - target) <= tol for v in values)


def text_of(obj) -> str:
    return "\n".join([str(k) for p, _ in walk(obj) for k in p[-1:] if isinstance(k, str)]
                     + [v for _, v in walk(obj) if isinstance(v, str)])


def intervals(obj) -> list[tuple[float, float]]:
    """[lo, hi] number pairs, and lo/low/lower + hi/high/upper keyed number pairs."""
    lows, highs = {"lo", "low", "lower", "lb"}, {"hi", "high", "upper", "ub"}
    found = []
    for _, value in walk(obj):
        if isinstance(value, list) and len(value) == 2 and all(is_number(x) for x in value):
            found.append((float(value[0]), float(value[1])))
        elif isinstance(value, dict):
            tokens = {k: set(re.split(r"[^a-z0-9]+", k.lower())) for k in value}
            los = [float(v) for k, v in value.items() if is_number(v) and tokens[k] & lows]
            his = [float(v) for k, v in value.items() if is_number(v) and tokens[k] & highs]
            found.extend((a, b) for a in los for b in his)
    return found


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def labels_in(obj) -> set[str]:
    pattern = re.compile(r"^(CONSISTENT|PROTOCOL-SENSITIVE|UNEXPLAINED)\b")
    return {m.group(1) for _, v in walk(obj) if isinstance(v, str) for m in [pattern.match(v.strip())] if m}


def report(env: Env, run_id: str, *extra: object) -> tuple[subprocess.CompletedProcess, dict, str]:
    proc = env.run("report", "--run", run_id, *extra)
    assert proc.returncode == EXIT_OK, _explain(proc)
    run_dir = env.runs / run_id
    assert (run_dir / "report.json").is_file() and (run_dir / "report.md").is_file(), (
        "report.json and report.md are not both in the run directory (default --out-dir, §3)\n" + _explain(proc))
    doc = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    return proc, doc, (run_dir / "report.md").read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_vs_rp_01_default_location_and_printed_paths(env):
    """VS-RP-01 · §3 report: without --out-dir, report.json and report.md are
    written to the run directory, and both written paths are printed on
    stdout."""
    run_id, run_dir = write_run(env, verdict_records(25, 20, 18), protocol=fixture_protocol())
    proc, _, md = report(env, run_id)
    printed = printed_paths(proc, env.work)
    for name in ("report.json", "report.md"):
        assert (run_dir / name).resolve() in printed, f"{name} path not printed on stdout\n" + _explain(proc)
    assert md.strip(), "report.md is empty"


def test_vs_rp_02_out_dir(env):
    """VS-RP-02 · §3 report `--out-dir <dir>`: both files are written to the
    named directory (not the run directory) and both paths are printed."""
    run_id, run_dir = write_run(env, verdict_records(25, 20, 18), protocol=fixture_protocol())
    out_dir = env.work / "reports" / "smoke"
    out_dir.mkdir(parents=True)
    proc = env.run("report", "--run", run_id, "--out-dir", out_dir)
    assert proc.returncode == EXIT_OK, _explain(proc)
    printed = printed_paths(proc, env.work)
    for name in ("report.json", "report.md"):
        assert (out_dir / name).is_file(), f"{name} not written to --out-dir\n" + _explain(proc)
        assert (out_dir / name).resolve() in printed, f"{name} path not printed on stdout\n" + _explain(proc)
        assert not (run_dir / name).exists(), f"{name} also written to the run directory"


def test_vs_rp_03_accuracy_and_wilson_as_fractions(env):
    """VS-RP-03 · §7 "per scorer: accuracy, with a Wilson 95% interval for
    k=1" + §3 "all accuracies and rates are fractions in [0, 1]": 25 items,
    primary 20 and secondary 18 correct; report.json carries 0.8 and 0.72
    with their Wilson intervals, and every number under an accuracy key
    lies in [0, 1]."""
    run_id, _ = write_run(env, verdict_records(25, 20, 18), protocol=fixture_protocol())
    _, doc, _ = report(env, run_id)
    assert close(numbers(doc), 0.8) and close(numbers(doc), 0.72), "per-scorer accuracies 0.8 / 0.72 missing"
    for successes in (20, 18):
        lo, hi = wilson(successes, 25)
        assert any(abs(a - lo) <= 1e-3 and abs(b - hi) <= 1e-3 for a, b in intervals(doc)), (
            f"Wilson interval [{lo:.4f}, {hi:.4f}] for {successes}/25 missing")
    accuracies = numbers_under(doc, "accuracy")
    assert accuracies, "report.json has no accuracy entries"
    assert all(0.0 <= v <= 1.0 for v in accuracies), f"accuracy values outside [0, 1]: {accuracies}"


def _counts_fixture() -> list[dict]:
    """25 outputs: format failures 3, truncations 4, fallback extractions 2,
    thinking_present 10 (rate 0.4), grader disagreements 5."""
    recs = []
    for i in range(3):      # no_answer, not truncated: format failures
        recs.append(record(item(i), 0, answer=None, reference="5", primary=False, secondary=False,
                           status="no_answer", rule="none", think=False))
    recs.append(record(item(3), 0, answer=None, reference="5", primary=False, secondary=False, status="no_answer",
                       rule="none", truncated=True, raw="<think>\nwe still need the case n = "))
    for i in (4, 5):        # truncated inside the thinking
        recs.append(record(item(i), 0, answer=None, reference="5", primary=False, secondary=False,
                           status="truncated", rule="none", truncated=True, raw="<think>\nconsider the parity of"))
    recs.append(record(item(6), 0, answer="5", reference="5", primary=True, secondary=True, truncated=True))
    for i in (7, 8):        # fallback extractions
        recs.append(record(item(i), 0, answer="5", reference="5", primary=True, secondary=True, status="fallback",
                           rule="fallback-final-number", think=False, raw="The final number is 5"))
    for i in range(9, 25):
        disagree = i < 14
        think = i < 15
        wrong = i >= 20
        recs.append(record(item(i), 0, answer="7" if not wrong else "8", reference="7",
                           primary=not wrong, secondary=(not wrong) and not disagree, think=think))
    return recs


def test_vs_rp_04_counts_follow_the_definitions(env):
    """VS-RP-04 · §7 counts and their definitions: of 25 outputs, 3 are
    no_answer and not truncated (format failures), 4 are truncated (one of
    them no_answer, one with an ok answer), 2 are fallback extractions, 10
    have thinking_present (rate 0.4) and 5 have differing verdicts; the
    report carries 3, 4, 2, 0.4 and 5 (counts, or rates as fractions)."""
    recs = _counts_fixture()
    assert sum(r["thinking_present"] for r in recs) == 10
    run_id, _ = write_run(env, recs, protocol=fixture_protocol())
    _, doc, _ = report(env, run_id)
    n = 25

    def reported(needle, count):
        values = numbers_under(doc, needle)
        return any(v == count for v in values) or close(values, count / n)

    assert reported("format", 3), "format failures (no_answer and not truncated) should be 3"
    assert reported("truncat", 4), "truncations (truncated: true) should be 4"
    assert reported("fallback", 2), "fallback extractions should be 2"
    assert close(numbers_under(doc, "think"), 0.4) or 10 in numbers_under(doc, "think"), "thinking rate should be 0.4"
    assert reported("disagree", 5), "grader disagreements should be 5"


@pytest.mark.parametrize("value", ["1.2", "-0.1"], ids=["VS-RP-05a-above-one", "VS-RP-05b-negative"])
def test_vs_rp_05_published_value_outside_unit_interval_exits_3(env, value):
    """VS-RP-05a..b · §3 report: "<value> is a fraction in [0, 1]. A value
    outside that range exits 3." No report is written."""
    run_id, run_dir = write_run(env, verdict_records(25, 20, 18), protocol=fixture_protocol())
    proc = env.run("report", "--run", run_id, "--compare-published", value)
    assert proc.returncode == EXIT_PRECONDITION, _explain(proc)
    assert not (run_dir / "report.json").exists() and not (run_dir / "report.md").exists()


@pytest.mark.parametrize(
    "value, comparison, expected",
    [
        ("0.7", None, "CONSISTENT"),
        ("0.85", None, "PROTOCOL-SENSITIVE"),
        ("0.95", None, "UNEXPLAINED"),
        ("0.3", {"factor": "sampling.top_p", "interval": [-0.35, -0.25]}, "PROTOCOL-SENSITIVE"),
    ],
    ids=["VS-RP-06a-consistent", "VS-RP-06b-grader-range", "VS-RP-06c-unexplained", "VS-RP-06d-sensitivity"],
)
def test_vs_rp_06_comparison_reading(env, value, comparison, expected):
    """VS-RP-06a..d · §7 comparison reading, rules in order: primary 15/25 =
    0.6 (Wilson [0.407, 0.766]), secondary 22/25 = 0.88. V = 0.7 is inside
    the primary interval (CONSISTENT); V = 0.85 is outside it but inside
    [0.6, 0.88] (PROTOCOL-SENSITIVE); V = 0.95 meets no condition
    (UNEXPLAINED); V = 0.3 gives d = -0.3 inside [min(0, -0.35), max(0,
    -0.25)] for a sampling.top_p comparison (PROTOCOL-SENSITIVE, naming
    sampling.top_p). report.json carries exactly that label and report.md
    states it."""
    protocol = fixture_protocol()
    run_id, run_dir = write_run(env, verdict_records(25, 15, 22), protocol=protocol)
    extra: list[object] = ["--compare-published", value]
    if comparison is not None:
        sensitivity = {
            "sensitivity_version": "1", "base_run": run_id,
            "base_protocol_sha256": sha256_bytes((run_dir / "protocol.yaml").read_bytes()),
            "comparisons": [{
                "variant_run": f"visible-variant-{uuid.uuid4().hex[:8]}", "factor": comparison["factor"],
                "base_value": "0.95", "variant_value": "0.8", "paired_items": 25,
                "base_accuracy": 0.6, "variant_accuracy": 0.3, "effect": -0.3,
                "effect_interval_95": comparison["interval"],
                "method": "paired bootstrap over items, primary scorer",
                "bootstrap_seed": 4242, "bootstrap_resamples": 1000}],
        }
        path = env.work / "sensitivity.json"
        path.write_text(json.dumps(sensitivity, indent=2), encoding="utf-8")
        extra += ["--sensitivity", path]
    _, doc, md = report(env, run_id, *extra)
    assert labels_in(doc) == {expected}, f"expected {expected}; report.json carries {sorted(labels_in(doc))}"
    assert expected in md, f"report.md does not state {expected}"
    if comparison is not None:
        assert comparison["factor"] in text_of(doc) and comparison["factor"] in md, "satisfying factor not named"


def test_vs_rp_07_three_seed_aggregates(env):
    """VS-RP-07 · §7 "for k>1: per-item seed mean, an item-bootstrap
    interval, and the spread across seeds" + "per-seed results": 10 items,
    k=3, seed accuracies 0.5, 0.6 and 0.7; report.json carries all three, the
    seed mean 0.6, a bootstrap interval within [0, 1] containing 0.6, and a
    spread entry."""
    recs = []
    for sample, correct in enumerate((5, 6, 7)):
        recs += verdict_records(10, correct, correct, sample=sample)
    run_id, _ = write_run(env, recs, protocol=fixture_protocol())
    _, doc, _ = report(env, run_id)
    for expected in (0.5, 0.6, 0.7):
        assert close(numbers(doc), expected), f"{expected} missing from report.json"
    near_bootstrap = [iv for path, value in walk(doc)
                      if any(isinstance(k, str) and "bootstrap" in k.lower() for k in path)
                      or (isinstance(value, dict)
                          and any(isinstance(v, str) and "bootstrap" in v.lower() for v in value.values()))
                      for iv in intervals(value)]
    assert any(0.0 <= lo <= 0.6 <= hi <= 1.0 for lo, hi in near_bootstrap), (
        f"no item-bootstrap interval containing 0.6; found {near_bootstrap}")
    keys = [p[-1].lower() for p, _ in walk(doc) if p and isinstance(p[-1], str)]
    assert any(t in k for k in keys for t in ("spread", "std", "range", "variance")), "no spread-across-seeds entry"


def test_vs_rp_08_no_per_item_text_in_report(env):
    """VS-RP-08 · §2 data policy + §7 "aggregates only": distinctive phrases
    planted in raw outputs, extracted answers and reference answers appear
    in neither report.json nor report.md."""
    planted, recs = [], []
    for i in range(8):
        phrase, answer, reference = (f"marker{uuid.uuid4().hex}" for _ in range(3))
        planted += [phrase, answer, reference]
        raw = f"<think>\nThe {phrase} step comes first.\n</think>\n\nThe answer is \\boxed{{{answer}}}."
        recs.append(record(item(i), 0, answer=answer, reference=reference, primary=i % 3 == 0,
                           secondary=i % 2 == 0, raw=raw))
    run_id, _ = write_run(env, recs, protocol=fixture_protocol())
    _, doc, md = report(env, run_id, "--compare-published", "0.4")
    blob = json.dumps(doc, ensure_ascii=False) + md
    leaked = [p for p in planted if p in blob]
    assert not leaked, f"{len(leaked)} planted per-item strings appear in the committed report"


def test_vs_rp_09_altered_protocol_copy_exits_2(env):
    """VS-RP-09 · §6.1 report "read[s] only these copies, and exit[s] 2 if a
    copy's hash fails": an altered protocol.yaml copy makes report exit 2."""
    run_id, run_dir = write_run(env, verdict_records(25, 20, 18), protocol=fixture_protocol())
    copy_path = run_dir / "protocol.yaml"
    copy_path.write_bytes(copy_path.read_bytes() + b"# altered\n")
    proc = env.run("report", "--run", run_id)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)


def test_vs_rp_10_wrong_reference_hash_exits_2(env):
    """VS-RP-10 · §6.1 "Every command that reads items.jsonl checks each
    record's ... reference_sha256, and exits 2 on any mismatch": a record
    whose reference_sha256 is wrong makes report exit 2."""
    run_id, run_dir = write_run(env, verdict_records(25, 20, 18), protocol=fixture_protocol())
    path = run_dir / "items.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    lines[17]["reference_sha256"] = sha256_text("a different reference")
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    proc = env.run("report", "--run", run_id)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)
