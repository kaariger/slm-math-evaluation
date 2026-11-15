"""Visible specification tests — retained runs, conversion provenance and proof-run records (contract v0.8.1 §2, §4, §6, §10, §12.4, §12.5, §13.3, §13.5).

Contract-only: written before any v0.8.1 implementation commit was supplied,
with no implementation code seen. These tests read records that real
conversions and runs left behind; only VS-RC-01 runs the CLI, and it works on
copies. They never write into an evidence location.

Resources (stated in each docstring):
  retained  SLM_EVAL_TEST_RETAINED_RUNS: a directory holding retained
            Qwen3-8B MATH-500 run directories (§6.1). Each is copied into a
            temporary SLM_EVAL_RUNS before `rescore`.
  cache     SLM_EVAL_TEST_EVIDENCE_CACHE: a dataset cache whose `models/`
            holds converted and vendor model files with their conversion and
            sanity records (§12.4, §12.5).
  runs      SLM_EVAL_TEST_EVIDENCE_RUNS: the run store of the smoke and proof
            runs, with `_verification/` and `_runner/` (§2, §6, §13.3).
  repo      committed protocols, specs, the pinned manifest and any subset,
            found by content in the repository that contains the pytest
            working directory and parsed with PyYAML.

Records hold raw generations and must stay outside the repository (§2), so
these tests take their locations from the environment only.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable is passed to the implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

RESCORE_TIMEOUT = 3600
EXIT_OK = 0
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
HEX64 = re.compile(r"[0-9a-f]{64}")
ZERO_HASH = "0" * 64
MAX_TEXT_BYTES = 256 * 1024 * 1024
RUN_STATUSES = {"in_progress", "paused", "interrupted", "completed", "aborted", "stopped"}
ATTEMPT_STATUSES = {"completed", "aborted", "interrupted", "paused"}
EVENT_TYPES = {"sleep", "wake", "interrupted", "pause", "resume", "server_restart", "memory_wait", "infra_retry"}
RUNNER_STATES = {"running", "paused", "waiting_approval", "interrupted", "stopped", "completed"}
THINKING_STATES = {"absent", "empty", "present", "unterminated"}
LOG_KEYS = {"seq", "prev_sha256", "at_utc", "step", "event", "detail"}
SECRET_PATTERNS = {
    "hugging-face-token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "github-fine-grained-token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}"),
    "aws-access-key-id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key-block": re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
    "sk-style-api-key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"),
    "bearer-header": re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    "api-key-argument": re.compile(r"--api-key(?:=|\s+)[^\s-][^\s]{7,}"),
    "api-key-field": re.compile(r"(?i)\"?(?:x-)?api[_-]?key\"?\s*[:=]\s*\"?[A-Za-z0-9._~+/=-]{16,}"),
}

# ---------------------------------------------------------------------------
# shared helpers
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


def _passed_on(name: str) -> bool:
    upper = name.upper()
    return (name not in ("SLM_EVAL_CACHE", "SLM_EVAL_RUNS", "SLM_EVAL_SCRATCH") and not name.startswith("SLM_EVAL_TEST_")
            and not any(word in upper for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")))


def _explain(proc: subprocess.CompletedProcess) -> str:
    return (f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}")


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


def location(variable: str, what: str) -> Path:
    configured = os.environ.get(variable, "").strip()
    if not configured:
        pytest.skip(f"needs {what}: set {variable}")
    path = Path(configured).expanduser()
    if not path.is_dir():
        pytest.fail(f"{variable} is not a directory", pytrace=False)
    return path


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def run_dirs(store: Path) -> list[Path]:
    """Run directories of a §2 run store: children holding run.json, other than `_verification` and `_runner`."""
    return sorted(p for p in store.iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "run.json").is_file())


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


def _committed(accept) -> list[tuple[Path, bytes, dict]]:
    try:
        import yaml  # type: ignore
    except ImportError:
        pytest.skip("PyYAML is needed to read committed files")
    root = _repo_root()
    if root is None:
        pytest.skip("needs the repository that contains the pytest working directory")
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS | TEST_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix not in (".yaml", ".yml", ".json"):
                continue
            try:
                raw = path.read_bytes()
                data = yaml.safe_load(raw.decode("utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and accept(data):
                found.append((path, raw, data))
    return found


def committed_protocols() -> list[tuple[Path, bytes, dict]]:
    return _committed(lambda d: "protocol_version" in d and isinstance(d.get("model"), dict))


def committed_specs() -> list[tuple[Path, bytes, dict]]:
    return _committed(lambda d: "spec_version" in d and "conversion" in d)


def committed_subset_hashes() -> set[str]:
    return {sha256_bytes(raw) for _, raw, _ in _committed(lambda d: "subset_version" in d and "ids" in d)}


def items_of(run_dir: Path) -> list:
    records = []
    with open(run_dir / "items.jsonl", "rb") as fh:
        for line in fh:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    records.append(None)
    return records


def _utc(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def conversion_records(cache: Path) -> list[tuple[Path, bytes, dict]]:
    found = []
    for path in sorted((cache / "models").rglob("*.json")):
        doc = read_json(path)
        if isinstance(doc, dict) and "record_version" in doc and "spec_sha256" in doc and "output" in doc:
            found.append((path, path.read_bytes(), doc))
    return found


# ---------------------------------------------------------------------------
# objective 2: retained Qwen3-8B runs re-score to their stored verdicts
# ---------------------------------------------------------------------------


def test_vs_rc_01_retained_qwen3_runs_rescore_to_their_stored_verdicts(tmp_path):
    """VS-RC-01 · retained · brief objective 2 "Deterministic re-scoring of retained Qwen3-8B MATH-500 runs
    reproduces their stored verdicts"; §3 rescore; §4 compatibility (v0.7 runs unchanged); §6.4.

    Every retained run directory whose adapter is qwen3 (or unnamed) and
    whose status is `completed`, copied into a temporary run store → `rescore`
    exits 0 and writes `rescore.json` with `all_match: true`.
    """
    store = location("SLM_EVAL_TEST_RETAINED_RUNS", "the retained Qwen3-8B run directories")
    runs = [d for d in run_dirs(store)
            if (dig(read_json(d / "run.json"), "adapter", "id") or "qwen3") == "qwen3"
            and dig(read_json(d / "run.json"), "status") == "completed"]
    assert runs, "no completed qwen3 run directory found in SLM_EVAL_TEST_RETAINED_RUNS"
    variables = {k: v for k, v in os.environ.items() if _passed_on(k)}
    for name in ("cache", "runs", "scratch", "work"):
        (tmp_path / name).mkdir()
    variables.update(SLM_EVAL_CACHE=str(tmp_path / "cache"), SLM_EVAL_RUNS=str(tmp_path / "runs"),
                     SLM_EVAL_SCRATCH=str(tmp_path / "scratch"))
    failures = []
    for source in runs:
        target = tmp_path / "runs" / source.name
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("rescore.json"))
        proc = subprocess.run(_command() + ["rescore", "--run", source.name], cwd=tmp_path / "work", env=variables,
                              stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
                              timeout=RESCORE_TIMEOUT)
        doc = read_json(target / "rescore.json")
        if proc.returncode != EXIT_OK or not isinstance(doc, dict) or doc.get("all_match") is not True:
            mismatched = [f"{r.get('item_id')}#{r.get('sample_index')}" for r in (dig(doc, "records") or [])
                          if isinstance(r, dict) and r.get("match") is not True][:10]
            failures.append(f"{source.name}: exit {proc.returncode}, all_match "
                            f"{dig(doc, 'all_match')!r}, first mismatches {mismatched}\n{proc.stderr[-500:]}")
    assert not failures, "retained runs did not re-score to their stored verdicts:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# objective 5: conversion provenance, sanity results, verification, prompt hashes
# ---------------------------------------------------------------------------


def record_problems(cache: Path, doc: dict, spec_hashes: set[str]) -> list[str]:
    problems = []
    if doc.get("record_version") != "1":
        problems.append(f"record_version {doc.get('record_version')!r}")
    if doc.get("spec_sha256") not in spec_hashes:
        problems.append("spec_sha256 is not the SHA-256 of any committed spec")
    files = dig(doc, "source", "files")
    if not isinstance(files, list) or not files:
        problems.append("source.files is empty")
    for entry in files or []:
        expected, observed = dig(entry, "sha256_expected"), dig(entry, "sha256_observed")
        if not (HEX64.fullmatch(str(expected)) and expected == observed):
            problems.append(f"source file {dig(entry, 'path')!r}: expected {expected!r} ≠ observed {observed!r}")
    for key in ("release", "script_sha256", "python_lock_sha256"):
        if not dig(doc, "tool", key):
            problems.append(f"tool.{key} is missing")
    if dig(doc, "intermediate", "type") != "bf16" or not HEX64.fullmatch(str(dig(doc, "intermediate", "sha256"))):
        problems.append(f"intermediate {dig(doc, 'intermediate')!r} is not a hashed bf16 file")
    output = doc.get("output") or {}
    if (output.get("quantization"), output.get("calibration")) != ("Q4_K_M", "none"):
        problems.append(f"output quantization/calibration {output.get('quantization')!r}/{output.get('calibration')!r}")
    model = cache / "models" / str(output.get("file"))
    if not model.is_file():
        problems.append(f"output file {output.get('file')!r} is not in models/")
    elif sha256_file(model) != output.get("sha256"):
        problems.append(f"output file {output.get('file')!r} does not have the recorded SHA-256")
    if not HEX64.fullmatch(str(doc.get("chat_template_sha256"))):
        problems.append("chat_template_sha256 is not 64-hex")
    for key in ("started_utc", "ended_utc"):
        if _utc(doc.get(key)) is None:
            problems.append(f"{key} {doc.get(key)!r} is not a timestamp")
    return problems


def test_vs_rc_02_conversion_records_are_complete_and_verified():
    """VS-RC-02 · cache + repo · §12.5 conversion record; §12.2 publisher hashes compared locally; §12.1
    bf16 → Q4_K_M without calibration; brief objective 5 "conversion provenance".

    Every conversion record under `models/`: version "1"; `spec_sha256` of a
    committed spec; every source file's observed hash equals its expected
    hash; tool pins; a hashed bf16 intermediate; a Q4_K_M output with no
    calibration whose file is present and has the recorded SHA-256; a chat
    template hash; start and end timestamps.
    """
    cache = location("SLM_EVAL_TEST_EVIDENCE_CACHE", "the dataset cache holding converted models")
    records = conversion_records(cache)
    assert records, "no conversion record found under models/"
    spec_hashes = {sha256_bytes(raw) for _, raw, _ in committed_specs()}
    failures = {path.name: problems for path, _, doc in records if (problems := record_problems(cache, doc, spec_hashes))}
    assert not failures, "conversion records depart from §12.5:\n" + json.dumps(failures, indent=2)


def test_vs_rc_03_protocols_reference_their_conversion_records():
    """VS-RC-03 · cache + repo · v0.8.1 §4 "conversion_record_sha256 is a 64-hex SHA-256 for every artifact
    produced by model convert (§12.5). It is null only for a vendor-supplied artifact that has no conversion
    record"; §12.5 "The protocol's model.artifact.conversion_record_sha256 is this file's SHA-256"; §4
    `chat_template.sha256`.

    For every committed protocol: if a conversion record's output has the
    artifact's SHA-256, the protocol names that record's SHA-256 and its chat
    template hash equals the record's; a non-null value names a record whose
    output is this artifact; a null value only for an artifact no record
    produced.
    """
    cache = location("SLM_EVAL_TEST_EVIDENCE_CACHE", "the dataset cache holding converted models")
    records = conversion_records(cache)
    by_output = {dig(doc, "output", "sha256"): (sha256_bytes(raw), doc) for _, raw, doc in records}
    by_hash = {sha256_bytes(raw): doc for _, raw, doc in records}
    failures = []
    for path, _, data in committed_protocols():
        artifact = dig(data, "model", "artifact") or {}
        named = artifact.get("conversion_record_sha256")
        produced = by_output.get(artifact.get("sha256"))
        if produced is not None:
            record_hash, doc = produced
            if named != record_hash:
                failures.append(f"{path.name}: artifact was converted (record {record_hash}), "
                                f"but conversion_record_sha256 is {named!r}")
            template = dig(data, "model", "chat_template", "sha256")
            if template is not None and template != doc.get("chat_template_sha256"):
                failures.append(f"{path.name}: chat_template.sha256 differs from the conversion record's")
        elif named is not None:
            doc = by_hash.get(named)
            if doc is None:
                failures.append(f"{path.name}: conversion_record_sha256 {named} names no record under models/")
            elif dig(doc, "output", "sha256") != artifact.get("sha256"):
                failures.append(f"{path.name}: the named record produced a different artifact")
    assert not failures, "§4 conversion provenance:\n" + "\n".join(failures)


def _walk(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield None, value
            yield from _walk(value)


def test_vs_rc_04_every_conversion_has_a_passing_sanity_record():
    """VS-RC-04 · cache + repo · §12.4 `sanity-record.json` "beside the conversion record: the two
    perplexities, their ratio, the text hash and the pass/fail result"; brief objective 5 "a sanity result".

    For every conversion record, a JSON file in the same directory holds the
    spec's `perplexity_text_sha256` and a pass/fail result that is a pass (a
    true value under a key containing "pass", or the string "pass"), and no
    failing result. Field names are not fixed by §12.4, so they are found by
    value.
    """
    cache = location("SLM_EVAL_TEST_EVIDENCE_CACHE", "the dataset cache holding converted models")
    specs = {sha256_bytes(raw): data for _, raw, data in committed_specs()}
    failures = []
    for path, _, record in conversion_records(cache):
        text_hash = dig(specs.get(record.get("spec_sha256")), "sanity", "perplexity_text_sha256")
        if text_hash is None:
            failures.append(f"{path.name}: its spec is not committed")
            continue
        candidates = [read_json(p) for p in sorted(path.parent.glob("*.json")) if p != path]
        sanity = [d for d in candidates if text_hash in json.dumps(d)]
        if not sanity:
            failures.append(f"{path.name}: no sanity record beside it holds the text hash")
            continue
        values = [(str(k or "").lower(), v) for k, v in _walk(sanity[0])]
        passed = any(("pass" in k and v is True) or v == "pass" for k, v in values)
        failed = any(("pass" in k and v is False) or v == "fail" for k, v in values)
        if not passed or failed:
            failures.append(f"{path.name}: sanity result is not a pass")
    assert not failures, "\n".join(failures)


def target_protocols() -> list[tuple[Path, bytes, dict]]:
    return [(p, raw, d) for p, raw, d in committed_protocols() if d.get("role", "target") == "target"]


def test_vs_rc_05_every_target_protocol_has_both_verification_records():
    """VS-RC-05 · runs + repo · §3 verify-runtime and verify-artifact record locations; §10 "verify-runtime
    and verify-artifact run for every protocol"; brief objective 5 "runtime and artifact security
    verification".

    For every committed target protocol: `_verification/<SHA-256 of the
    protocol file>/` holds `runtime-verification.json` and
    `artifact-verification.json`, both JSON.
    """
    store = location("SLM_EVAL_TEST_EVIDENCE_RUNS", "the run store of the smoke and proof runs")
    missing = []
    for path, raw, _ in target_protocols():
        directory = store / "_verification" / sha256_bytes(raw)
        for name in ("runtime-verification.json", "artifact-verification.json"):
            if read_json(directory / name) is None:
                missing.append(f"{path.name}: {name}")
    assert not missing, "verification records missing or unreadable:\n" + "\n".join(missing)


def test_vs_rc_06_every_target_has_a_smoke_run_with_prompt_hashes():
    """VS-RC-06 · runs + repo · §12.4 "the smoke tier is run before any other tier"; §6.1 prompt_sha256 and
    the verification copies; brief objective 5 "prompt hashes at smoke".

    For every committed target protocol, a completed smoke run recorded
    against it (by `protocol_sha256`): every item record has a 64-hex
    `prompt_sha256`, one value per item across samples; both verification
    records are copied into the run directory.
    """
    store = location("SLM_EVAL_TEST_EVIDENCE_RUNS", "the run store of the smoke and proof runs")
    runs = [(d, read_json(d / "run.json")) for d in run_dirs(store)]
    failures = []
    for path, raw, _ in target_protocols():
        smoke = [d for d, doc in runs if dig(doc, "protocol_sha256") == sha256_bytes(raw)
                 and dig(doc, "tier") == "smoke" and dig(doc, "status") == "completed"]
        if not smoke:
            failures.append(f"{path.name}: no completed smoke run")
            continue
        run_dir = smoke[0]
        prompts: dict[str, set] = {}
        for record in items_of(run_dir):
            value = dig(record, "prompt_sha256")
            if not HEX64.fullmatch(str(value)):
                failures.append(f"{path.name}: {run_dir.name} has an item without a 64-hex prompt_sha256")
                break
            prompts.setdefault(dig(record, "item_id"), set()).add(value)
        if any(len(v) > 1 for v in prompts.values()):
            failures.append(f"{path.name}: {run_dir.name} has items whose samples differ in prompt_sha256")
        for name in ("runtime-verification.json", "artifact-verification.json"):
            if not (run_dir / name).is_file():
                failures.append(f"{path.name}: {run_dir.name} lacks its {name} copy")
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# objective 6 and §6, §13: proof-run records
# ---------------------------------------------------------------------------


def run_problems(run_dir: Path, subsets: set[str]) -> list[str]:
    doc = read_json(run_dir / "run.json") or {}
    problems = []
    for name, key in (("protocol.yaml", "protocol_sha256"), ("manifest.json", "manifest_sha256")):
        if not (run_dir / name).is_file() or sha256_file(run_dir / name) != doc.get(key):
            problems.append(f"{name} copy does not match run.json {key}")
    if doc.get("status") not in RUN_STATUSES:
        problems.append(f"status {doc.get('status')!r}")
    attempts = doc.get("attempts") or []
    if [a.get("n") for a in attempts] != list(range(1, len(attempts) + 1)):
        problems.append("attempts are not numbered 1..n in order")
    for attempt in attempts[1:]:
        if attempt.get("resumed_from") is None:
            problems.append(f"attempt {attempt.get('n')} has no resumed_from")
    if any(a.get("status") not in ATTEMPT_STATUSES for a in attempts):
        problems.append("an attempt status is outside §6.1")
    events = doc.get("events") or []
    if any(e.get("type") not in EVENT_TYPES for e in events):
        problems.append("an event type is outside §6.1")
    awake = [e.get("awake_s") for e in events if isinstance(e.get("awake_s"), (int, float))]
    if awake != sorted(awake):
        problems.append("event awake_s decreases")
    for before, after in zip(events, events[1:]):
        if before.get("type") == "sleep" and after.get("type") == "wake":
            start, end = _utc(before.get("at_utc")), _utc(after.get("at_utc"))
            if start is None or end is None:
                problems.append("a sleep or wake event has no parseable at_utc")
                continue
            awake_delta = (after.get("awake_s") or 0) - (before.get("awake_s") or 0)
            if awake_delta > (end - start).total_seconds() + 1:
                problems.append("the awake clock advanced more than wall time across a sleep")
    pairs, bad = set(), 0
    for record in items_of(run_dir):
        if not isinstance(record, dict):
            problems.append("items.jsonl has a line that is not JSON (partial write?)")
            continue
        pairs.add((record.get("item_id"), record.get("sample_index")))
        bad += sha256_bytes(str(record.get("raw_output", "")).encode()) != record.get("raw_output_sha256")
        bad += sha256_bytes(str(record.get("reference_answer", "")).encode()) != record.get("reference_sha256")
        state = record.get("thinking_state")
        if state is not None:
            if state not in THINKING_STATES:
                problems.append(f"thinking_state {state!r}")
            elif state in ("absent", "empty", "present") and record.get("thinking_present") != (state == "present"):
                problems.append(f"thinking_present {record.get('thinking_present')!r} with state {state}")
        if isinstance(record.get("awake_ms"), (int, float)) and isinstance(record.get("timing_ms"), (int, float)):
            if record["awake_ms"] > record["timing_ms"]:
                problems.append("awake_ms exceeds timing_ms")
        retries = record.get("infra_retries", 0)
        if not isinstance(retries, int) or not 0 <= retries <= 2:
            problems.append(f"infra_retries {retries!r} outside 0..2")
    if bad:
        problems.append(f"{bad} record hash mismatches")
    if len(pairs) != len(items_of(run_dir)):
        problems.append("items.jsonl repeats an item × sample pair")
    subset = doc.get("subset_sha256")
    if subset is not None and subset not in subsets:
        problems.append("subset_sha256 is not a committed subset")
    if doc.get("status") == "completed" and subset is None:
        manifest = read_json(run_dir / "manifest.json") or {}
        expected = len(dig(manifest, "tiers", doc.get("tier")) or []) * int(doc.get("k") or 1)
        if expected and len(pairs) != expected:
            problems.append(f"completed with {len(pairs)} pairs, expected {expected}")
    return sorted(set(problems))


def test_vs_rc_07_run_records_keep_the_section_6_invariants():
    """VS-RC-07 · runs + repo · §6.1 copies, record hashes and fields; "A partial generation is never written";
    §6.3 attempts and resumes; §13.5 awake clock and retries; §8 thinking_present; brief invariant 2 (a
    subset is frozen before use).

    For every run directory: copies match run.json; status, attempt and event
    values from §6.1; attempts numbered 1..n, later ones with `resumed_from`;
    `awake_s` never decreases and advances no more than wall time across a
    sleep; every items line is JSON with valid hashes, unique per item ×
    sample, `infra_retries` in 0..2, `awake_ms` ≤ `timing_ms`, and
    `thinking_present` true exactly when the state is `present` (among
    absent, empty and present); a `subset_sha256` names a committed subset;
    a completed run without a subset covers its tier × k.
    """
    store = location("SLM_EVAL_TEST_EVIDENCE_RUNS", "the run store of the smoke and proof runs")
    subsets = committed_subset_hashes()
    failures = {d.name: problems for d in run_dirs(store) if (problems := run_problems(d, subsets))}
    assert not failures, "run records depart from §6:\n" + json.dumps(failures, indent=2)


def test_vs_rc_08_proof_runs_show_interruption_and_pause_recovery():
    """VS-RC-08 · runs · brief objective 6 "A real interrupted-and-resumed run, a real pause-and-resume";
    §6.3; §13.4.

    The run store holds a completed run with an `interrupted` attempt and a
    later attempt, and a completed run with a `pause` and a later `resume`
    event (or a `paused` attempt and a later attempt). This is an acceptance
    check of the proof runs, so it fails while they are missing.
    """
    store = location("SLM_EVAL_TEST_EVIDENCE_RUNS", "the run store of the smoke and proof runs")
    interrupted = paused = False
    for run_dir in run_dirs(store):
        doc = read_json(run_dir / "run.json") or {}
        if doc.get("status") != "completed":
            continue
        statuses = [a.get("status") for a in doc.get("attempts") or []]
        types = [e.get("type") for e in doc.get("events") or []]
        interrupted |= "interrupted" in statuses[:-1]
        paused |= "paused" in statuses[:-1] or ("pause" in types and "resume" in types[types.index("pause"):])
    assert interrupted, "no completed run was interrupted and resumed"
    assert paused, "no completed run was paused and resumed"


def test_vs_rc_09_runner_state_keeps_its_hashes():
    """VS-RC-09 · runs · §13.3 status.json, stored copies and attempts.log.jsonl; v0.8.1 §13.3 hash rule
    (no line terminator; first prev_sha256 is 64 zeros).

    For every `_runner/<plan_sha256>/`: `status.json` names that plan hash and
    a §13.3 state; the stored plan and authorization have the SHA-256 values
    status.json records; the attempt log's chain holds by the v0.8.1 rule.
    """
    store = location("SLM_EVAL_TEST_EVIDENCE_RUNS", "the run store of the smoke and proof runs")
    states = sorted(p for p in (store / "_runner").iterdir() if p.is_dir()) if (store / "_runner").is_dir() else []
    if not states:
        pytest.skip("no runner state in the run store")
    failures = []
    for state in states:
        status = read_json(state / "status.json") or {}
        if status.get("plan_sha256") != state.name or status.get("state") not in RUNNER_STATES:
            failures.append(f"{state.name}: status.json plan_sha256/state {status.get('plan_sha256')!r}/"
                            f"{status.get('state')!r}")
        for name, key in (("plan.yaml", "plan_sha256"), ("authorization.json", "authorization_sha256")):
            if not (state / name).is_file() or sha256_file(state / name) != status.get(key):
                failures.append(f"{state.name}: stored {name} does not have status.json {key}")
        lines = (state / "attempts.log.jsonl").read_bytes().split(b"\n") if (state / "attempts.log.jsonl").is_file() else []
        lines = [line[:-1] if line.endswith(b"\r") else line for line in lines if line]
        previous = ZERO_HASH
        for n, line in enumerate(lines, start=1):
            try:
                record = json.loads(line)
            except ValueError:
                record = {}
            if not isinstance(record, dict) or not LOG_KEYS <= set(record) or record.get("prev_sha256") != previous:
                failures.append(f"{state.name}: attempts.log.jsonl line {n} breaks the chain")
                break
            previous = sha256_bytes(line)
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# invariant 1: no credential in evidence
# ---------------------------------------------------------------------------


def test_vs_rc_10_no_credential_in_evidence_logs_or_records():
    """VS-RC-10 · cache and/or runs · brief hard invariant 1 "No key, token or credential appears in any
    committed file, log or evidence"; §10 the API key is never written to disk, logs or artifacts; §13.3
    server.log; §12.5.

    No text file under the evidence run store or the evidence cache (files
    holding NUL bytes, such as model files, and files over 256 MiB are
    skipped) matches a credential pattern, an `--api-key` argument with a
    value, or an API-key field with a value. Hits are reported by file, line
    and pattern only.
    """
    roots = [Path(os.environ[v]).expanduser() for v in ("SLM_EVAL_TEST_EVIDENCE_RUNS", "SLM_EVAL_TEST_EVIDENCE_CACHE")
             if os.environ.get(v, "").strip()]
    if not roots:
        pytest.skip("needs SLM_EVAL_TEST_EVIDENCE_RUNS or SLM_EVAL_TEST_EVIDENCE_CACHE")
    hits = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            try:
                if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
                    continue
                with open(path, "rb") as fh:
                    if b"\0" in fh.read(8192):
                        continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                for label, pattern in SECRET_PATTERNS.items():
                    if pattern.search(line):
                        hits.append(f"{path.relative_to(root)}:{number} matches {label}")
    assert not hits, "possible credentials in evidence:\n" + "\n".join(hits[:50])
