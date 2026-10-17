"""Visible specification tests — `slm-eval rescore` on run-store fixtures (contract v0.3 §3, §6.1, §6.2, §6.4, §8, §9).

Contract-only: written before any implementation commit was supplied, with
no implementation code seen. Hermetic: each test writes a complete §6.1 run
directory into its own temporary SLM_EVAL_RUNS, leaves SLM_EVAL_CACHE empty
(rescore works "from the stored raw outputs only"), and needs no network,
dataset, model or runtime.

Fixture runs are recorded against the committed protocol.yaml when it can be
found (SLM_EVAL_TEST_PROTOCOL, or a protocol.yaml inside the repository that
contains the pytest working directory) and parsed; otherwise against the
contract §4 draft defaults. Stored verdicts are ones every correct
implementation must reproduce: an identical integer answer is correct, a
different integer is incorrect, and `answer = None` is incorrect (§9).

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split);
SLM_EVAL_TEST_PROTOCOL names the protocol file used for fixtures.
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
EXIT_OK, EXIT_INTEGRITY = 0, 2
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


def fixture_manifest(item_ids, protocol_data: dict, *, tier: str = "smoke",
                     note: str = "visible-suite fixture manifest") -> bytes:
    ids = sorted(set(item_ids))
    doc = {
        "manifest_version": "1",
        "source": dig(protocol_data, "dataset", "source") or {},
        "membership_derivation": {"method": "unique_id", "independent_source": {
            "url": "https://example.invalid/visible-suite-index", "revision": "e" * 40, "sha256": "f" * 64}},
        "normalization": {"version": "visible-1", "near_duplicate": {"method": "visible", "threshold": 0.85}},
        "items": [{"id": i, "role": "dev", "content_sha256": sha256_text(i), "subject": "Number Theory", "level": 2}
                  for i in ids],
        "exclusions": [],
        "tiers": {"seed": 7, "smoke": ids if tier == "smoke" else [], "validation": ids if tier == "validation" else []},
        "provenance_note": note,
    }
    return dump(doc)


def item(index: int) -> str:
    return f"visible-item-{index:03d}"


def record(item_id: str, sample: int, raw: str, reference: str, *, answer, rule: str, status: str,
           primary: bool, secondary: bool, truncated: bool = False, thinking: bool | None = None) -> dict:
    return {
        "item_id": item_id, "sample_index": sample, "raw_output": raw, "reference_answer": reference,
        "extraction": {"answer": answer, "rule": rule, "status": status},
        "verdicts": {"primary": primary, "secondary": secondary},
        "truncated": truncated,
        "thinking_present": ("</think>" in raw or raw.startswith("<think>")) if thinking is None else thinking,
    }


def boxed_record(item_id: str, sample: int, reference: str, answer: str, *, think: bool = True) -> dict:
    final = "Therefore the answer is \\boxed{" + answer + "}."
    raw = ("<think>\nCheck the small cases first, then generalise.\n</think>\n\n" + final) if think else final
    correct = answer == reference
    return record(item_id, sample, raw, reference, answer=answer, rule="boxed_last", status="ok",
                  primary=correct, secondary=correct)


def write_run(env: Env, records: list[dict], *, protocol: tuple[bytes, dict, str], tier: str = "smoke",
              seed_base: int = 3100, manifest_bytes: bytes | None = None) -> tuple[str, Path]:
    """A complete §6.1 run directory; returns (run_id, run_dir)."""
    raw_protocol, data, _ = protocol
    run_id = f"visible-{uuid.uuid4().hex[:12]}"
    run_dir = env.runs / run_id
    run_dir.mkdir()
    if manifest_bytes is None:
        manifest_bytes = fixture_manifest((r["item_id"] for r in records), data, tier=tier)
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
            "tokens_generated": 50 + len(r["raw_output"]) // 4,
            "reference_answer": r["reference_answer"],
            "reference_sha256": sha256_text(r["reference_answer"]),
            "extraction": dict(r["extraction"]),
            "verdicts": dict(r["verdicts"]),
            "timing_ms": 400 + 7 * index,
        })
    scorers = [{"id": dig(data, "scorers", role, "id"), "pin": dig(data, "scorers", role, "pin")}
               for role in ("primary", "secondary")]
    run_json = {
        "run_id": run_id,
        "protocol_version": data.get("protocol_version"),
        "protocol_sha256": sha256_bytes(raw_protocol),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "tier": tier,
        "k": k,
        "seeds": [seed_base + s for s in range(k)],
        "model_artifact_sha256": dig(data, "model", "artifact", "sha256"),
        "runtime_build": dig(data, "runtime", "build"),
        "adapter": {"id": "visible-suite-adapter", "version": "0.1.0"},
        "extractor": {"id": dig(data, "extraction", "id"), "version": dig(data, "extraction", "version")},
        "scorers": scorers,
        "executor": "visible-suite fixture",
        "host": {"os": "Darwin", "arch": "arm64", "memory_gb": 32},
        "attempts": [{"n": 1, "started": "2026-10-06T09:00:00Z", "ended": "2026-10-06T09:20:00Z",
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


def snapshot(run_dir: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in run_dir.iterdir() if p.is_file()}


# ---------------------------------------------------------------------------
# rescore helpers
# ---------------------------------------------------------------------------


def rescore(env: Env, run_id: str) -> tuple[subprocess.CompletedProcess, Path]:
    return env.run("rescore", "--run", run_id), env.runs / run_id / "rescore.json"


def schema_problems(doc, run_id: str, pairs: set) -> list[str]:
    """Departures from the §6.4 schema, or records not covering each pair exactly once."""
    if not isinstance(doc, dict):
        return ["rescore.json is not a JSON object"]
    problems = []
    if doc.get("rescore_version") != "1":
        problems.append(f"rescore_version is {doc.get('rescore_version')!r}, expected '1'")
    if doc.get("run_id") != run_id:
        problems.append(f"run_id is {doc.get('run_id')!r}, expected {run_id!r}")
    if not isinstance(doc.get("all_match"), bool):
        problems.append("all_match is not a boolean")
    records = doc.get("records")
    if not isinstance(records, list):
        return problems + ["records is not a list"]
    seen = []
    for r in records:
        keys = {"item_id", "sample_index", "stored", "recomputed", "match"}
        if not isinstance(r, dict) or not keys <= set(r):
            problems.append(f"record without {sorted(keys)}: {r!r}"[:200])
            continue
        for side in ("stored", "recomputed"):
            v = r[side]
            if not (isinstance(v, dict) and isinstance(v.get("primary"), bool) and isinstance(v.get("secondary"), bool)):
                problems.append(f"{r['item_id']}#{r['sample_index']}: {side} is not {{primary: bool, secondary: bool}}")
        if not isinstance(r["match"], bool):
            problems.append(f"{r['item_id']}#{r['sample_index']}: match is not a boolean")
        seen.append((r["item_id"], r["sample_index"]))
    if len(seen) != len(set(seen)) or set(seen) != pairs:
        problems.append(f"records cover {len(set(seen))} distinct pairs ({len(seen)} records); expected {len(pairs)}")
    return problems


def pairs_of(records: list[dict]) -> set:
    return {(r["item_id"], r["sample_index"]) for r in records}


def by_pair(doc: dict) -> dict:
    return {(r["item_id"], r["sample_index"]): r for r in doc["records"]}


def assert_reproduced(env: Env, records: list[dict]) -> dict:
    protocol = fixture_protocol()
    run_id, _ = write_run(env, records, protocol=protocol)
    proc, path = rescore(env, run_id)
    assert proc.returncode == EXIT_OK, _explain(proc) + "\n" + protocol[2]
    doc = json.loads(path.read_text(encoding="utf-8"))
    problems = schema_problems(doc, run_id, pairs_of(records))
    assert not problems, "rescore.json departs from §6.4: " + "; ".join(problems)
    mismatched = [pair for pair, r in by_pair(doc).items() if r["match"] is not True]
    assert doc["all_match"] is True and not mismatched, (
        f"stored verdicts not reproduced; mismatched pairs {mismatched}\n{protocol[2]}")
    return doc


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def _three_by_three() -> list[dict]:
    records = []
    for sample in range(3):
        records.append(boxed_record(item(1), sample, "12", "12"))
        records.append(boxed_record(item(2), sample, "305", "350"))
        records.append(boxed_record(item(3), sample, "9", "9", think=False))
    return records


def test_vs_rs_01_rescore_json_follows_the_schema(env):
    """VS-RS-01 · §3 rescore + §6.4: a k=3 run of three unambiguous items
    rescored with an empty dataset cache exits 0 and writes
    `<run_dir>/rescore.json` with rescore_version "1", the run_id,
    all_match true and nine records (one per item x sample), each with
    stored, recomputed and match true."""
    doc = assert_reproduced(env, _three_by_three())
    assert all(r["stored"] == r["recomputed"] for r in doc["records"]), "stored and recomputed differ"


def test_vs_rs_02_written_path_is_printed(env):
    """VS-RS-02 · §3 "prints each written path to stdout": rescore prints the
    path of the rescore.json it wrote."""
    run_id, run_dir = write_run(env, _three_by_three(), protocol=fixture_protocol())
    proc, path = rescore(env, run_id)
    assert proc.returncode == EXIT_OK, _explain(proc)
    printed = [Path(line.strip()) for line in proc.stdout.splitlines() if line.strip()]
    resolved = {(p if p.is_absolute() else env.work / p).resolve() for p in printed}
    assert path.resolve() in resolved, "the rescore.json path is not printed on stdout\n" + _explain(proc)


def test_vs_rs_03_only_rescore_json_is_added(env):
    """VS-RS-03 · §6.2 "A completed run is never rewritten" + "rescore writes
    rescore.json beside the original": after rescore, every original file
    is byte-identical and rescore.json is the only new file."""
    run_id, run_dir = write_run(env, _three_by_three(), protocol=fixture_protocol())
    before = snapshot(run_dir)
    proc, _ = rescore(env, run_id)
    assert proc.returncode == EXIT_OK, _explain(proc)
    after = snapshot(run_dir)
    changed = sorted(name for name in before if after.get(name) != before[name])
    assert not changed, f"original run files changed or removed: {changed}"
    assert sorted(set(after) - set(before)) == ["rescore.json"], f"new files: {sorted(set(after) - set(before))}"


def test_vs_rs_04_secondary_mismatch_is_recorded(env):
    """VS-RS-04 · §3 rescore + §6.4: a stored secondary verdict of true on a
    wrong answer is not reproduced; that record has stored.secondary true,
    recomputed.secondary false and match false, all other records match,
    and all_match is false."""
    records = _three_by_three()
    tampered = (item(2), 1)
    for r in records:
        if (r["item_id"], r["sample_index"]) == tampered:
            r["verdicts"]["secondary"] = True
    run_id, _ = write_run(env, records, protocol=fixture_protocol())
    proc, path = rescore(env, run_id)
    assert path.is_file(), "rescore.json was not written\n" + _explain(proc)
    doc = json.loads(path.read_text(encoding="utf-8"))
    problems = schema_problems(doc, run_id, pairs_of(records))
    assert not problems, "rescore.json departs from §6.4: " + "; ".join(problems)
    rec = by_pair(doc)[tampered]
    assert rec["stored"]["secondary"] is True and rec["recomputed"]["secondary"] is False, rec
    assert rec["match"] is False, rec
    assert all(r["match"] is True for pair, r in by_pair(doc).items() if pair != tampered)
    assert doc["all_match"] is False


def test_vs_rs_05_unclosed_think_gives_no_answer(env):
    """VS-RS-05 · §8 "the empty string if a <think> is never closed" + §9: a
    boxed answer inside a <think> that is never closed is not extracted, so
    the output is scored incorrect even though the boxed value equals the
    reference."""
    raw = "<think>\nThe sum telescopes, so it should be \\boxed{64}, but let me double-check the"
    records = [
        record(item(1), 0, raw, "64", answer=None, rule="none", status="truncated",
               primary=False, secondary=False, truncated=True, thinking=True),
        boxed_record(item(2), 0, "64", "64"),
    ]
    assert_reproduced(env, records)


def test_vs_rs_06_output_without_think_is_all_final(env):
    """VS-RS-06 · §8 "the whole output if there is no <think>": with no think
    block, the boxed answer anywhere in the output is extracted (the last
    one wins), so these outputs are scored as their last boxed value says."""
    records = [
        record(item(1), 0, "We get \\boxed{27}. As a check, 3^3 = 27.", "27", answer="27", rule="boxed_last",
               status="ok", primary=True, secondary=True),
        record(item(2), 0, "First \\boxed{8}, then after fixing an error, \\boxed{6}.", "6", answer="6",
               rule="boxed_last", status="ok", primary=True, secondary=True),
        record(item(3), 0, "First \\boxed{6}, then after a second look, \\boxed{8}.", "6", answer="8",
               rule="boxed_last", status="ok", primary=False, secondary=False),
    ]
    assert_reproduced(env, records)


def test_vs_rs_07_rescore_is_repeatable(env):
    """VS-RS-07 · §8/§9 "The same inputs always give the same output" + §6.4:
    rescoring the same run twice gives the same all_match and records."""
    run_id, _ = write_run(env, _three_by_three(), protocol=fixture_protocol())
    first, path = rescore(env, run_id)
    assert first.returncode == EXIT_OK, _explain(first)
    doc_a = json.loads(path.read_text(encoding="utf-8"))
    second, _ = rescore(env, run_id)
    assert second.returncode == EXIT_OK, _explain(second)
    doc_b = json.loads(path.read_text(encoding="utf-8"))
    key = lambda r: (r["item_id"], r["sample_index"])  # noqa: E731
    assert doc_a["all_match"] == doc_b["all_match"]
    assert sorted(doc_a["records"], key=key) == sorted(doc_b["records"], key=key)


def test_vs_rs_08_wrong_recorded_raw_hash_exits_2(env):
    """VS-RS-08 · §6.1 "Every command that reads items.jsonl checks each
    record's raw_output_sha256 ... and exits 2 on any mismatch": a record
    whose raw_output_sha256 field is wrong (text unchanged) makes rescore
    exit 2."""
    run_id, run_dir = write_run(env, _three_by_three(), protocol=fixture_protocol())
    path = run_dir / "items.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    lines[4]["raw_output_sha256"] = sha256_text("not the raw output")
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    proc, _ = rescore(env, run_id)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)


@pytest.mark.parametrize("copy_name", ["protocol.yaml", "manifest.json"],
                         ids=["VS-RS-09a-protocol-copy", "VS-RS-09b-manifest-copy"])
def test_vs_rs_09_altered_run_copy_exits_2(env, copy_name):
    """VS-RS-09a..b · §6.1 run-directory copies: rescore "read[s] only these
    copies, and exit[s] 2 if a copy's hash fails". Appending to the
    protocol.yaml or manifest.json copy makes rescore exit 2."""
    run_id, run_dir = write_run(env, _three_by_three(), protocol=fixture_protocol())
    target = run_dir / copy_name
    target.write_bytes(target.read_bytes() + b"\n")
    proc, _ = rescore(env, run_id)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)
