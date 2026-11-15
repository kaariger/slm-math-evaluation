"""Visible specification tests — model adapters, reasoning modes and the per-adapter thinking split (contract v0.8 §4, §6.1, §8, §9).

Contract-only: written before any v0.8 implementation commit was supplied,
with no implementation code seen. Tests drive the implementation through the
`slm-eval` CLI and the contract's file schemas and, only where a named
capability binds them, through the §8 functions themselves.

Resources (stated in each docstring):
  hermetic  the `slm-eval` CLI only. Each test has its own temporary
            SLM_EVAL_CACHE, SLM_EVAL_RUNS, SLM_EVAL_SCRATCH and working
            directory, and needs no network, dataset, model or runtime.
  repo      committed protocol files, found by content in the repository that
            contains the pytest working directory and parsed with PyYAML
            (skipped if PyYAML is missing or no protocol is found).
  gemma4    SLM_EVAL_TEST_GEMMA4_DELIMITERS: a JSON list [open, close] giving
            the thought-channel delimiters that the approved gemma4 adapter
            version fixes (§8). The contract does not state them, so gemma4
            cases are skipped until they are supplied.
  callable  SLM_EVAL_TEST_SPLIT_CALLABLE and SLM_EVAL_TEST_EXTRACT_CALLABLE:
            "module:attribute" bindings of the §8 `split_reasoning` and
            `extract` functions. Module layout is outside the contract, so the
            golden-fixture tests are skipped until the bindings are supplied.
            They are imported in the pytest process, which must then run in
            the project environment.

Synthetic runs are recorded against a committed protocol naming the adapter
when one is found, and otherwise against the committed (or §4 draft) protocol
with the v0.8 adapter fields added. Stored verdicts are ones every correct
implementation must reproduce: an identical integer is correct, and a missing
or different answer is incorrect (§9).

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
import re
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
ADAPTER_MODES = {
    "qwen3": ("hybrid", "forced_think", "forced_non_think"),
    "phi4-reasoning": ("always_on",),
    "gemma4": ("thinking_on", "thinking_off"),
}
PROVENANCE_LABELS = {"source-known-paper", "source-known-blog", "vendor-filled", "vendor-template", "inferred",
                     "local-choice"}
V08_PROTOCOL_KEYS = ("role",)
V08_MODEL_KEYS = ("adapter", "chat_template")
HEX64 = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?")
THINK = ("<think>", "</think>")

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

    def written(self) -> list[str]:
        """Every path created under the cache, run store, scratch or working directory."""
        return [str(p.relative_to(self.root)) for base in (self.cache, self.runs, self.scratch, self.work)
                for p in sorted(base.rglob("*"))]


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
# committed protocols (§4)
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


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    return yaml


def _parse(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    yaml = _yaml()
    return yaml.safe_load(text) if yaml is not None else json.loads(text)


def committed_protocols() -> list[tuple[Path, bytes, dict]]:
    """(path, raw bytes, parsed) for every protocol file in the repository, test directories last."""
    root = _repo_root()
    if root is None:
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix not in (".yaml", ".yml", ".json"):
                continue
            try:
                raw = path.read_bytes()
                data = _parse(path)
            except Exception:
                continue
            if isinstance(data, dict) and "protocol_version" in data and isinstance(data.get("model"), dict):
                found.append((path, raw, data))
    return sorted(found, key=lambda f: (bool(TEST_DIRS & set(f[0].relative_to(root).parts)), str(f[0])))


def adapter_of(data: dict) -> str:
    """§4 compatibility: a protocol without `model.adapter` uses adapter qwen3."""
    return dig(data, "model", "adapter", "id") or "qwen3"


def _v07_base() -> tuple[dict, str]:
    for path, _, data in committed_protocols():
        if adapter_of(data) == "qwen3":
            return copy.deepcopy(data), f"derived from committed {path.name}"
    return copy.deepcopy(DRAFT_PROTOCOL), "contract §4 draft defaults (no committed protocol found)"


def v07_protocol() -> tuple[bytes, dict, str]:
    """A protocol in v0.7 form: every v0.8 field removed (§4 compatibility)."""
    data, note = _v07_base()
    for key in ("role", "resources"):
        data.pop(key, None)
    model = data.setdefault("model", {})
    for key in V08_MODEL_KEYS:
        model.pop(key, None)
    if isinstance(model.get("artifact"), dict):
        model["artifact"].pop("conversion_record_sha256", None)
    for key in ("context_length", "stop"):
        (data.get("sampling") or {}).pop(key, None)
    if dig(data, "reasoning_mode", "value") not in ADAPTER_MODES["qwen3"]:
        data["reasoning_mode"] = {"value": "hybrid", "provenance": "inferred"}
    return dump(data), data, "v0.7-form protocol " + note


def adapter_protocol(adapter_id: str, *, mode: str | None = None) -> tuple[bytes, dict, str]:
    """A v0.8 protocol for the adapter: a committed one verbatim if it exists, else a synthesized one."""
    if mode is None:
        for path, raw, data in committed_protocols():
            if dig(data, "model", "adapter", "id") == adapter_id:
                return raw, data, f"committed {path.name}"
    data, note = _v07_base()
    data["role"] = "target"
    model = data.setdefault("model", {})
    model["adapter"] = {"id": adapter_id, "version": "1.0.0"}
    if isinstance(model.get("artifact"), dict):
        model["artifact"].setdefault("conversion_record_sha256", None)
    model["chat_template"] = {"source": "embedded", "sha256": sha256_text("visible-suite chat template " + adapter_id)}
    data["reasoning_mode"] = {"value": mode or ADAPTER_MODES[adapter_id][0],
                              "provenance": "vendor-template" if adapter_id != "qwen3" else "inferred"}
    sampling = data.setdefault("sampling", {})
    sampling.setdefault("context_length", {"value": 16384, "provenance": "local-choice"})
    sampling.setdefault("stop", {"value": [], "provenance": "local-choice"})
    data.setdefault("resources", {
        "max_memory_gb": {"value": 24, "provenance": "local-choice"},
        "min_free_memory_gb": {"value": 4, "provenance": "local-choice"},
        "stuck_after_awake_s": {"value": 600, "provenance": "local-choice"},
    })
    return dump(data), data, f"synthesized {adapter_id} protocol, {note}"


# ---------------------------------------------------------------------------
# thinking-split cases (§8) and stored verdicts (§9)
# ---------------------------------------------------------------------------


def gemma4_delimiters() -> tuple[str, str] | None:
    configured = os.environ.get("SLM_EVAL_TEST_GEMMA4_DELIMITERS", "").strip()
    if not configured:
        return None
    pair = json.loads(configured)
    if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(s, str) and s for s in pair)):
        pytest.fail("SLM_EVAL_TEST_GEMMA4_DELIMITERS must be a JSON list of two non-empty strings", pytrace=False)
    return pair[0], pair[1]


def delimiters(adapter_id: str) -> tuple[str, str]:
    if adapter_id != "gemma4":
        return THINK
    pair = gemma4_delimiters()
    if pair is None:
        pytest.skip("gemma4 thought-channel delimiters are fixed by the adapter version, not the contract (§8): "
                    "set SLM_EVAL_TEST_GEMMA4_DELIMITERS")
    return pair


def split_cases(o: str, c: str) -> list[dict]:
    """Raw outputs whose verdict depends on the §8 split. Reference is always "7".

    `final` is the expected split result; `correct` is the only verdict a
    correct implementation can store for it.
    """
    return [
        dict(case="absent", raw="The answer is \\boxed{7}.", truncated=False, state="absent",
             final="The answer is \\boxed{7}.", correct=True),
        dict(case="empty", raw=f"{o}\n\n{c}\n\nThe answer is \\boxed{{7}}.", truncated=False, state="empty",
             final="The answer is \\boxed{7}.", correct=True),
        dict(case="present", raw=f"{o}\nTry \\boxed{{3}} first; it fails.\n{c}\n\nThe answer is \\boxed{{7}}.",
             truncated=False, state="present", final="The answer is \\boxed{7}.", correct=True),
        dict(case="boxed-only-in-thinking", raw=f"{o}\nIt must be \\boxed{{7}}.\n{c}\n\nI am not certain.",
             truncated=False, state="present", final="I am not certain.", correct=False),
        dict(case="absent-final-answer", raw=f"{o}\nIt must be \\boxed{{7}}.\n{c}\n", truncated=False,
             state="present", final="", correct=False),
        dict(case="unterminated-truncated", raw=f"{o}\nSo far \\boxed{{7}} looks right, and", truncated=True,
             state="unterminated", final="", correct=False),
        dict(case="unterminated-not-truncated", raw=f"{o}\nSo far \\boxed{{7}} looks right.", truncated=False,
             state="unterminated", final="", correct=False),
        dict(case="malformed-extra-close", raw=f"{o}\\boxed{{1}}{c}The answer is \\boxed{{7}}.{c}No box here.",
             truncated=False, state="present", final="No box here.", correct=False),
    ]


def _stored_extraction(case: dict) -> dict:
    if case["correct"]:
        return {"answer": "7", "rule": "boxed_last", "status": "ok"}
    status = "truncated" if case["truncated"] and not case["final"] else "no_answer"
    return {"answer": None, "rule": "none", "status": status}


def fixture_manifest(item_ids, protocol: dict, *, tier: str = "smoke") -> bytes:
    ids = list(dict.fromkeys(item_ids))
    doc = {
        "manifest_version": "1",
        "source": dig(protocol, "dataset", "source") or {},
        "membership_derivation": {"method": "unique_id", "independent_source": None},
        "normalization": {"version": "visible-1", "near_duplicate": {"method": "visible", "threshold": 0.85}},
        "items": [{"id": i, "role": "dev", "content_sha256": sha256_text(i), "subject": "Number Theory", "level": 2}
                  for i in ids],
        "exclusions": [],
        "tiers": {"seed": 7, "smoke": ids if tier == "smoke" else [], "validation": ids if tier == "validation" else []},
        "provenance_note": "visible-suite fixture manifest",
    }
    return dump(doc)


def write_run(env: Env, protocol: tuple[bytes, dict, str], cases: list[dict], *, v08: bool = True,
              seed_base: int = 4100) -> tuple[str, Path]:
    """A complete §6.1 run directory (k = 1, one item per case); returns (run_id, run_dir)."""
    raw_protocol, data, _ = protocol
    run_id = f"visible-{uuid.uuid4().hex[:12]}"
    run_dir = env.runs / run_id
    run_dir.mkdir()
    item_ids = [f"visible-{c['case']}" for c in cases]
    manifest = fixture_manifest(item_ids, data)
    lines = []
    for index, (item_id, case) in enumerate(zip(item_ids, cases)):
        stored = case["correct"]
        line = {
            "item_id": item_id, "sample_index": 0, "seed": seed_base,
            "prompt_sha256": sha256_text("visible fixture prompt " + item_id),
            "raw_output": case["raw"], "raw_output_sha256": sha256_text(case["raw"]),
            "thinking_present": case["state"] in ("present", "unterminated"),
            "truncated": case["truncated"], "finish_reason": "length" if case["truncated"] else "stop",
            "tokens_generated": 40 + len(case["raw"]) // 4,
            "reference_answer": "7", "reference_sha256": sha256_text("7"),
            "extraction": _stored_extraction(case),
            "verdicts": {"primary": stored, "secondary": stored},
            "timing_ms": 500 + 11 * index,
        }
        if v08:
            line.update(thinking_state=case["state"], infra_retries=0, awake_ms=480 + 11 * index)
        lines.append(line)
    scorers = [{"id": dig(data, "scorers", r, "id"), "pin": dig(data, "scorers", r, "pin")}
               for r in ("primary", "secondary")]
    run_json = {
        "run_id": run_id, "protocol_version": data.get("protocol_version"),
        "protocol_sha256": sha256_bytes(raw_protocol), "manifest_sha256": sha256_bytes(manifest),
        "tier": "smoke", "k": 1, "seeds": [seed_base],
        "model_artifact_sha256": dig(data, "model", "artifact", "sha256"),
        "runtime_build": dig(data, "runtime", "build"),
        "adapter": {"id": adapter_of(data), "version": dig(data, "model", "adapter", "version") or "1.0.0"},
        "extractor": {"id": dig(data, "extraction", "id"), "version": dig(data, "extraction", "version")},
        "scorers": scorers,
        "executor": "visible-suite fixture",
        "host": {"os": "Darwin", "arch": "arm64", "memory_gb": 32},
        "attempts": [{"n": 1, "started": "2026-10-27T09:00:00Z", "ended": "2026-10-27T09:20:00Z",
                      "status": "completed", "resumed_from": None}],
        "status": "completed",
    }
    if v08:
        sampling = {k: v.get("value") for k, v in (data.get("sampling") or {}).items() if isinstance(v, dict)}
        run_json.update(
            role=data.get("role", "target"), subset_sha256=None,
            chat_template_sha256=dig(data, "model", "chat_template", "sha256"),
            effective={"reasoning": dig(data, "reasoning_mode", "value"), "template_args": {}, "sampling": sampling},
            events=[],
        )
    (run_dir / "protocol.yaml").write_bytes(raw_protocol)
    (run_dir / "manifest.json").write_bytes(manifest)
    (run_dir / "run.json").write_text(json.dumps(run_json, indent=2) + "\n", encoding="utf-8")
    with open(run_dir / "items.jsonl", "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    for name in ("runtime-verification.json", "artifact-verification.json"):
        (run_dir / name).write_text(json.dumps({"fixture": "visible-suite", "passed": True}) + "\n", encoding="utf-8")
    return run_id, run_dir


def rescore_doc(env: Env, run_id: str) -> tuple[subprocess.CompletedProcess, dict | None]:
    proc = env.run("rescore", "--run", run_id)
    path = env.runs / run_id / "rescore.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    return proc, doc


def mismatches(doc: dict, cases: list[dict]) -> list[str]:
    """Cases whose recomputed verdicts differ from the only correct stored verdict."""
    by_item = {r.get("item_id"): r for r in doc.get("records", []) if isinstance(r, dict)}
    problems = []
    for case in cases:
        record = by_item.get(f"visible-{case['case']}")
        if record is None:
            problems.append(f"{case['case']}: no rescore record")
            continue
        recomputed = record.get("recomputed") or {}
        if (recomputed.get("primary"), recomputed.get("secondary"), record.get("match")) != (
                case["correct"], case["correct"], True):
            problems.append(f"{case['case']}: expected both verdicts {case['correct']} (final {case['final']!r}), "
                            f"got recomputed {recomputed!r}, match {record.get('match')!r}")
    return problems


# ---------------------------------------------------------------------------
# tests: per-adapter split through `rescore` (hermetic)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_id", ["qwen3", "phi4-reasoning", "gemma4"])
def test_vs_ad_01_rescore_applies_the_adapters_thinking_split(env, adapter_id):
    """VS-AD-01 · hermetic (gemma4 also needs SLM_EVAL_TEST_GEMMA4_DELIMITERS) · §8 split_reasoning per
    adapter; §3 rescore re-runs extraction and scoring from raw outputs; §6.4; §9.

    One run per adapter, one item per thinking case: absent, empty, present,
    a boxed answer only inside the thinking, an absent final answer,
    unterminated (truncated and not), and an extra closing delimiter (text
    after the LAST close is final). Rescoring must reproduce every stored
    verdict: `all_match: true`, and each record matches.
    """
    o, c = delimiters(adapter_id)
    cases = split_cases(o, c)
    protocol = adapter_protocol(adapter_id)
    assert adapter_of(protocol[1]) == adapter_id
    run_id, _ = write_run(env, protocol, cases)
    proc, doc = rescore_doc(env, run_id)
    assert proc.returncode == EXIT_OK, f"rescore failed ({protocol[2]})\n" + _explain(proc)
    assert doc is not None, "rescore exited 0 without writing rescore.json\n" + _explain(proc)
    problems = mismatches(doc, cases)
    assert not problems, f"{adapter_id} split departs from §8 ({protocol[2]}):\n" + "\n".join(problems)
    assert doc.get("all_match") is True, f"all_match is {doc.get('all_match')!r} with every record matching"


@pytest.mark.parametrize("adapter_id", ["qwen3", "phi4-reasoning", "gemma4"])
def test_vs_ad_02_an_open_never_closed_after_a_closed_block_empties_final(env, adapter_id):
    """VS-AD-02 · hermetic (gemma4 also needs SLM_EVAL_TEST_GEMMA4_DELIMITERS) · §8 "the empty string if a
    <think> is never closed"; `unterminated` means `final` is empty.

    A closed block, a boxed answer, then a second opening delimiter that never
    closes. The second opening is never closed, so `final` is empty and the
    item is incorrect, although the text after the last close holds the
    right boxed answer.
    """
    o, c = delimiters(adapter_id)
    cases = [dict(case="reopened-never-closed", raw=f"{o}\nwork\n{c}\nThe answer is \\boxed{{7}}.\n{o}\nre-check",
                  truncated=True, state="unterminated", final="", correct=False)]
    protocol = adapter_protocol(adapter_id)
    run_id, _ = write_run(env, protocol, cases)
    proc, doc = rescore_doc(env, run_id)
    assert proc.returncode == EXIT_OK, f"rescore failed ({protocol[2]})\n" + _explain(proc)
    assert doc is not None, "rescore exited 0 without writing rescore.json\n" + _explain(proc)
    problems = mismatches(doc, cases)
    assert not problems, f"{adapter_id}: " + "\n".join(problems)


def test_vs_ad_03_v07_form_runs_rescore_and_report_unchanged(env):
    """VS-AD-03 · hermetic · §4 compatibility: a protocol without the v0.8 fields takes v0.7 behaviour
    (adapter qwen3, role target); "re-scoring and reporting of runs made under v0.7 are unchanged".

    A run directory in v0.7 form (no `role`, `model.adapter`,
    `chat_template`, `resources`; no v0.8 run.json or items.jsonl fields) with
    the `<think>` cases → `rescore` exits 0 with `all_match: true`; `report`
    exits 0 and writes `report.json` and `report.md` in the run directory.
    """
    cases = split_cases(*THINK)
    protocol = v07_protocol()
    run_id, run_dir = write_run(env, protocol, cases, v08=False)
    proc, doc = rescore_doc(env, run_id)
    assert proc.returncode == EXIT_OK, f"rescore failed on a v0.7-form run ({protocol[2]})\n" + _explain(proc)
    assert doc is not None, "rescore exited 0 without writing rescore.json\n" + _explain(proc)
    problems = mismatches(doc, cases)
    assert not problems, "v0.7-form run rescored differently:\n" + "\n".join(problems)
    report = env.run("report", "--run", run_id)
    assert report.returncode == EXIT_OK, "report failed on a v0.7-form run\n" + _explain(report)
    for name in ("report.json", "report.md"):
        assert (run_dir / name).is_file(), f"report exited 0 without writing {name} in the run directory"


@pytest.mark.parametrize("adapter_id", ["llama3", "slm_math_evaluation.adapters:Qwen3Adapter"],
                         ids=["unknown-name", "module-path"])
def test_vs_ad_04_rescore_refuses_an_adapter_the_contract_does_not_name(env, adapter_id):
    """VS-AD-04 · hermetic · prohibited constructs (no registries, plug-in discovery or factories);
    allowed: an adapter "named explicitly by the protocol (§4) and selected by that name alone".

    A run whose protocol copy names an adapter other than qwen3,
    phi4-reasoning or gemma4 (an unknown name, or a module path) cannot be
    split by any contract adapter → `rescore` exits non-zero and writes no
    `rescore.json`. The contract fixes no exit code for this case.
    """
    raw, data, note = adapter_protocol("qwen3", mode="hybrid")
    data = copy.deepcopy(data)
    data["model"]["adapter"] = {"id": adapter_id, "version": "1.0.0"}
    run_id, run_dir = write_run(env, (dump(data), data, note), split_cases(*THINK)[:3])
    proc = env.run("rescore", "--run", run_id)
    assert proc.returncode != EXIT_OK, f"rescore accepted adapter {adapter_id!r}\n" + _explain(proc)
    assert not (run_dir / "rescore.json").exists(), "a refused rescore wrote rescore.json"


# ---------------------------------------------------------------------------
# tests: reasoning modes the adapter does not allow (hermetic)
# ---------------------------------------------------------------------------

DISALLOWED = [("phi4-reasoning", "thinking_off"), ("phi4-reasoning", "forced_non_think"),
              ("gemma4", "forced_think"), ("qwen3", "always_on")]


def _write_protocol(env: Env, adapter_id: str, mode: str) -> Path:
    raw, _, _ = adapter_protocol(adapter_id, mode=mode)
    path = env.inputs / f"protocol-{adapter_id}-{mode}.yaml"
    path.write_bytes(raw)
    return path


@pytest.mark.parametrize("adapter_id,mode", DISALLOWED, ids=[f"{a}-{m}" for a, m in DISALLOWED])
def test_vs_ad_05_verify_artifact_rejects_a_mode_the_adapter_does_not_allow(env, adapter_id, mode):
    """VS-AD-05 · hermetic · §4 "a reasoning_mode value the adapter doesn't allow makes verify-artifact
    … exit 3" (Phi thinking-off rejection included); §3 written paths.

    `verify-artifact` → exit 3; no verification record and nothing else
    written; empty stdout. The cache is empty, which is also a §3 exit-3
    precondition, so this pins the exit code without depending on the order
    of checks.
    """
    proc = env.run("verify-artifact", "--protocol", _write_protocol(env, adapter_id, mode))
    assert proc.returncode == EXIT_PRECONDITION, "expected exit 3 (§4)\n" + _explain(proc)
    assert env.written() == [], f"a rejected verify-artifact wrote: {env.written()}"
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)


@pytest.mark.parametrize("adapter_id,mode", DISALLOWED, ids=[f"{a}-{m}" for a, m in DISALLOWED])
def test_vs_ad_06_run_rejects_a_mode_the_adapter_does_not_allow(env, adapter_id, mode):
    """VS-AD-06 · hermetic · §4 "… makes verify-artifact and run exit 3"; §6 no run directory.

    `run --tier smoke` with that protocol → exit 3; no run directory and
    nothing else written; empty stdout. Verification records are also
    missing (§3 exit 3), so the order of checks does not matter.
    """
    protocol = _write_protocol(env, adapter_id, mode)
    manifest = env.inputs / "manifest.json"
    manifest.write_bytes(fixture_manifest(["visible-item-001"], adapter_protocol(adapter_id, mode=mode)[1]))
    proc = env.run("run", "--protocol", protocol, "--manifest", manifest, "--tier", "smoke")
    assert proc.returncode == EXIT_PRECONDITION, "expected exit 3 (§4)\n" + _explain(proc)
    assert env.written() == [], f"a rejected run wrote: {env.written()}"
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)


# ---------------------------------------------------------------------------
# tests: committed protocols in v0.8 form (repo)
# ---------------------------------------------------------------------------


def _provenance_problems(name: str, entry) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{name} is not a mapping"]
    label = entry.get("provenance")
    if not label:
        return [f"{name} has no provenance label"]
    if label not in PROVENANCE_LABELS:
        return [f"{name} provenance {label!r} is not one of {sorted(PROVENANCE_LABELS)}"]
    return []


def protocol_problems(data: dict) -> list[str]:
    problems = []
    role = data.get("role", "target")
    if role not in ("target", "fixture"):
        problems.append(f"role is {role!r}, not target or fixture")
    adapter = dig(data, "model", "adapter")
    if adapter is not None:
        if not isinstance(adapter, dict) or adapter.get("id") not in ADAPTER_MODES:
            problems.append(f"model.adapter {adapter!r} does not name qwen3, phi4-reasoning or gemma4")
        elif not SEMVER.fullmatch(str(adapter.get("version", ""))):
            problems.append(f"model.adapter.version {adapter.get('version')!r} is not a semantic version")
    adapter_id = adapter_of(data)
    mode = dig(data, "reasoning_mode", "value")
    if adapter_id in ADAPTER_MODES and mode not in ADAPTER_MODES[adapter_id]:
        problems.append(f"reasoning_mode {mode!r} is not allowed for adapter {adapter_id}")
    artifact = dig(data, "model", "artifact") or {}
    if "conversion_record_sha256" in artifact:
        value = artifact["conversion_record_sha256"]
        if value is not None and not HEX64.fullmatch(str(value)):
            problems.append(f"conversion_record_sha256 {value!r} is neither null nor 64 lowercase hex")
    template = dig(data, "model", "chat_template")
    if template is not None:
        if not (isinstance(template, dict) and template.get("source") and HEX64.fullmatch(str(template.get("sha256")))):
            problems.append(f"model.chat_template {template!r} lacks a source and a 64-hex sha256")
    for name in ("model.artifact", "runtime", "prompt", "reasoning_mode"):
        problems += _provenance_problems(name, dig(data, *name.split(".")))
    for section in ("sampling", "resources"):
        for key, entry in (data.get(section) or {}).items():
            problems += _provenance_problems(f"{section}.{key}", entry)
    resources = data.get("resources")
    if resources is not None:
        for key in ("max_memory_gb", "min_free_memory_gb", "stuck_after_awake_s"):
            value = dig(resources, key, "value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                problems.append(f"resources.{key}.value {value!r} is not a non-negative number")
    for key in ("top_k", "min_p"):
        value = dig(data, "sampling", key, "value")
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            problems.append(f"sampling.{key}.value {value!r} is not a non-negative number (0 disables it)")
    return problems


def test_vs_ad_07_committed_protocols_satisfy_the_v08_rules():
    """VS-AD-07 · repo · §4 v0.8 fields: role; adapter id and semver; reasoning modes by adapter (adapter
    qwen3 when absent); conversion_record_sha256; chat_template; provenance on model.artifact, runtime,
    prompt, reasoning_mode, every sampling field and every resources field, from the six labels.

    Every committed protocol file satisfies these rules. v0.7-form files pass
    when their remaining fields do (§4 compatibility).
    """
    if _yaml() is None:
        pytest.skip("PyYAML is needed to read committed protocol files")
    protocols = committed_protocols()
    if not protocols:
        pytest.skip("no committed protocol file found from the pytest working directory")
    root = _repo_root()
    failures = {}
    for path, _, data in protocols:
        problems = protocol_problems(data)
        if problems:
            failures[str(path.relative_to(root))] = problems
    assert not failures, "committed protocols depart from §4:\n" + json.dumps(failures, indent=2)


# ---------------------------------------------------------------------------
# tests: golden fixtures through the bound §8 functions (callable)
# ---------------------------------------------------------------------------


def _bound(variable: str):
    spec = os.environ.get(variable, "").strip()
    if not spec:
        pytest.skip(f"set {variable}=module:attribute to bind the §8 function (module layout is out of contract)")
    module_name, _, attribute = spec.partition(":")
    target = importlib.import_module(module_name)
    for part in attribute.split("."):
        target = getattr(target, part)
    return target


def _field(result, name: str):
    return result[name] if isinstance(result, dict) else getattr(result, name)


GOLDEN_ADAPTERS = ["qwen3", "phi4-reasoning", "gemma4"]


@pytest.mark.parametrize("adapter_id", GOLDEN_ADAPTERS)
def test_vs_sp_01_split_reasoning_golden_fixtures(adapter_id):
    """VS-SP-01 · callable (gemma4 also needs SLM_EVAL_TEST_GEMMA4_DELIMITERS) · §8 split_reasoning:
    "tested with golden fixtures for every thinking_state, including an absent final answer".

    For each case: `thinking_state` as defined; `final` equal to the text
    after the last close (compared with surrounding whitespace stripped),
    `""` exactly when unterminated, the whole output when absent; `thinking`
    is None when absent, whitespace-only when empty, and the block's text
    when present. Calling twice gives the same result.
    """
    split = _bound("SLM_EVAL_TEST_SPLIT_CALLABLE")
    o, c = delimiters(adapter_id)
    problems = []
    for case in split_cases(o, c):
        result = split(case["raw"], adapter_id)
        if split(case["raw"], adapter_id) != result:
            problems.append(f"{case['case']}: two calls gave different results")
        thinking, final, state = result
        if state != case["state"]:
            problems.append(f"{case['case']}: thinking_state {state!r}, expected {case['state']!r}")
        if case["state"] == "unterminated":
            if final != "":
                problems.append(f"{case['case']}: final {final!r}, expected the empty string")
        elif case["state"] == "absent":
            if final != case["raw"] or thinking is not None:
                problems.append(f"{case['case']}: expected (None, whole output), got ({thinking!r}, {final!r})")
        elif not isinstance(final, str) or final.strip() != case["final"]:
            problems.append(f"{case['case']}: final {final!r}, expected {case['final']!r}")
        if case["state"] == "empty" and not (isinstance(thinking, str) and thinking.strip() == ""):
            problems.append(f"{case['case']}: thinking {thinking!r} is not whitespace-only")
        if case["case"] == "present" and (not isinstance(thinking, str) or "Try \\boxed{3}" not in thinking):
            problems.append(f"{case['case']}: thinking {thinking!r} does not hold the block's text")
    assert not problems, f"{adapter_id}:\n" + "\n".join(problems)


EXTRACT_CASES = [
    ("The answer is \\boxed{7}.", False, "7", "boxed_last", "ok"),
    ("First \\boxed{2}, finally \\boxed{\\frac{1}{2}}.", False, "\\frac{1}{2}", "boxed_last", "ok"),
    ("So \\boxed{x^{2^{3}}} holds.", False, "x^{2^{3}}", "boxed_last", "ok"),
    ("\\boxed{7} then \\boxed{8", False, "7", "boxed_last", "ok"),
    ("", False, None, None, "no_answer"),
    ("", True, None, None, "truncated"),
]


@pytest.mark.parametrize("final,truncated,answer,rule,status", EXTRACT_CASES,
                         ids=["single", "nested-last-wins", "deep-nesting", "unbalanced-last-ignored",
                              "empty-not-truncated", "empty-truncated"])
def test_vs_sp_02_extract_golden_fixtures(final, truncated, answer, rule, status):
    """VS-SP-02 · callable · §8 extract: the last balanced `\\boxed{...}` wins, nested braces supported;
    an empty `final` gives `truncated` if the output was truncated, otherwise `no_answer`.

    Expected answer, status and (when given) rule; the same inputs give the
    same output.
    """
    extract = _bound("SLM_EVAL_TEST_EXTRACT_CALLABLE")
    result = extract(final, truncated)
    assert extract(final, truncated) == result, "two calls gave different results"
    got = (_field(result, "answer"), _field(result, "status"))
    assert got == (answer, status), f"extract({final!r}, {truncated}) → (answer, status) {got!r}"
    if rule is not None:
        assert _field(result, "rule") == rule, f"rule {_field(result, 'rule')!r}, expected {rule!r}"


@pytest.mark.parametrize("adapter_id", GOLDEN_ADAPTERS)
def test_vs_sp_03_absent_final_answer_extracts_nothing(adapter_id):
    """VS-SP-03 · callable (gemma4 also needs SLM_EVAL_TEST_GEMMA4_DELIMITERS) · §8 absent final answer.

    A closed thinking block holding the only boxed answer, then nothing:
    split → `present`; extracting that `final` gives answer None with status
    `no_answer` (not truncated) or `truncated` (truncated).
    """
    split, extract = _bound("SLM_EVAL_TEST_SPLIT_CALLABLE"), _bound("SLM_EVAL_TEST_EXTRACT_CALLABLE")
    o, c = delimiters(adapter_id)
    _, final, state = split(f"{o}\nIt must be \\boxed{{7}}.\n{c}", adapter_id)
    assert state == "present"
    for truncated, status in ((False, "no_answer"), (True, "truncated")):
        result = extract(final, truncated)
        got = (_field(result, "answer"), _field(result, "status"))
        assert got == (None, status), f"final {final!r}, truncated {truncated}: got {got!r}"
