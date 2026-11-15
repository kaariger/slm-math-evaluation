"""Visible specification tests — each adapter's thinking split and extraction through `rescore`, recorded against adapter-valid fixture identities (contract v0.8.1 §3, §6.1, §6.2, §6.4, §8, §9).

Contract-only: written from the approved interface and the maintainer's
clarification of adapter version 1.0.0, with no implementation code seen.
Tests drive the implementation through the `slm-eval` CLI and the contract's
file schemas only.

Resources:
  hermetic  the `slm-eval` CLI only. Each test has its own temporary
            SLM_EVAL_CACHE, SLM_EVAL_RUNS, SLM_EVAL_SCRATCH and working
            directory, and needs no network, dataset, model or runtime.

Protocol identity. Every run is recorded against a `role: fixture` protocol
taken from the pinned fixture configuration embedded below (SHA-256
87b6781985ed22e93cce2a2ccbd79bb5bd4579772185852b2b713a39defd8359): its neutral
fixture alias, its own embedded chat-template hash, its artifact, extraction,
scorer and dataset bindings. Only `model.adapter` (version 1.0.0) and an
allowed `reasoning_mode` are changed. No target artifact pin is invented and no
Qwen or target alias is carried into another adapter's identity. A fixture
identity is evidence for the split rules only, never for target-model
compatibility.

Delimiters fixed by adapter version 1.0.0 (maintainer clarification):
  qwen3, phi4-reasoning   `<think>` and `</think>`
  gemma4                  `<|channel>thought` followed by an actual line feed,
                          and `<channel|>`; `<|think|>` is a thinking-mode
                          trigger, not an opening delimiter
None of the pinned evaluation templates leaves an opening delimiter unmatched,
so no output is read as starting inside a block: with no generated opening
delimiter, `final` is the whole output, even with a stray close (§8).

Each test rescores two runs of the same outputs: one whose stored verdicts are
the only correct ones (all must match), and a control whose stored verdicts
are all inverted (every record must report the correct recomputed verdict and
`match: false`). A rescore that copies stored verdicts cannot pass both.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable, and no variable whose name contains TOKEN, SECRET,
PASSWORD or API_KEY, is passed to the implementation.
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
EXIT_OK = 0
APPROVED_ADAPTER_VERSION = "1.0.0"
FIXTURE_CONFIG_SHA256 = "87b6781985ed22e93cce2a2ccbd79bb5bd4579772185852b2b713a39defd8359"

# The pinned fixture configuration, value for value. `dump()` of this mapping reproduces its exact bytes,
# which fixture_identity() checks against FIXTURE_CONFIG_SHA256 before use.
FIXTURE_CONFIG = {
    "protocol_version": "fixture-1-v1",
    "role": "fixture",
    "model": {
        "adapter": {"id": "qwen3", "version": "1.0.0"},
        "artifact": {
            "file": "fixture-1-Q4_K_M.gguf",
            "sha256": "a6da5bd7e3dd3d08d8da8e22449960829cd8d2729833e94420a9734e07fef8ca",
            "quantization": "Q4_K_M",
            "conversion_record_sha256": "ef76dbc7fa2ece871ee02753d89628f8bbaf5683445beb288853866556bbdcf8",
            "provenance": "local-choice",
        },
        "chat_template": {"source": "embedded",
                          "sha256": "872be49dbb638044ad01b60388f48d469ff2980e5f0dccdc22ec907db54d0788"},
    },
    "runtime": {
        "build": "b10412", "invocation": "keyed-run-scoped-server", "model_alias": "fixture-1",
        "host": "127.0.0.1", "port": "ephemeral", "ui": False, "context_size": 2048, "threads": -1,
        "threads_batch": -1, "batch_size": 2048, "ubatch_size": 512, "gpu_layers": 99, "flash_attention": True,
        "cache_type_k": "f16", "cache_type_v": "f16", "load_mode": "mmap", "context_shift": False,
        "parallel_slots": 1, "jinja": True, "reasoning_budget": -1, "reasoning_format": "none",
        "provenance": "local-choice",
    },
    "prompt": {"template": "{problem}\n\nPlease reason step by step, and put your final answer within \\boxed{}.",
               "system_prompt": None, "few_shot": 0, "provenance": "local-choice"},
    "reasoning_mode": {"value": "hybrid", "provenance": "local-choice"},
    "sampling": {
        "temperature": {"value": 0.6, "provenance": "local-choice"},
        "top_p": {"value": 0.95, "provenance": "local-choice"},
        "top_k": {"value": 20, "provenance": "local-choice"},
        "min_p": {"value": 0, "provenance": "local-choice"},
        "presence_penalty": {"value": 0, "provenance": "local-choice"},
        "repeat_penalty": {"value": 1, "provenance": "local-choice"},
        "max_output_tokens": {"value": 384, "provenance": "local-choice"},
        "context_length": {"value": 2048, "provenance": "local-choice"},
        "stop": {"value": [], "provenance": "local-choice"},
        "seed_policy": {"value": "seed = seed_base + sample_index", "provenance": "local-choice"},
    },
    "resources": {
        "max_memory_gb": {"value": 4, "provenance": "local-choice"},
        "min_free_memory_gb": {"value": 1, "provenance": "local-choice"},
        "stuck_after_awake_s": {"value": 60, "provenance": "local-choice"},
    },
    "extraction": {"id": "boxed-last-v1", "version": "1.0.0"},
    "scorers": {"primary": {"id": "prm800k-grader", "pin": "7ecc794703b2877f63226f2477a49b34f9b25163"},
                "secondary": {"id": "math-verify", "pin": "0.8.0"}},
    "dataset": {"source": {
        "url": "https://github.com/openai/prm800k",
        "commit": "7ecc794703b2877f63226f2477a49b34f9b25163",
        "files": [{"path": "prm800k/math_splits/test.jsonl",
                   "sha256": "35dc41080a3680858b27fa7e0533d2d547825316fc5dafe5d316f4ccc5a06132"},
                  {"path": "prm800k/math_splits/train.jsonl",
                   "sha256": "90d96daeac3fe343ebb1e22ce93dd99690f75983e957f88de42f87cffe1e8076"}]}},
}

DELIMITERS = {
    "qwen3": ("<think>", "</think>"),
    "phi4-reasoning": ("<think>", "</think>"),
    "gemma4": ("<|channel>thought\n", "<channel|>"),
}
# Every (adapter, reasoning_mode) pair the contract allows that the pinned templates were clarified for.
IDENTITIES = [("qwen3", "hybrid"), ("phi4-reasoning", "always_on"), ("gemma4", "thinking_on"),
              ("gemma4", "thinking_off")]
IDENTITY_IDS = [f"{a}-{m}" for a, m in IDENTITIES]

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
                              errors="replace", timeout=TIMEOUT, start_new_session=True)


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
# adapter-valid fixture identities
# ---------------------------------------------------------------------------


def fixture_identity(adapter_id: str, mode: str) -> tuple[bytes, dict]:
    """The pinned fixture protocol with only the adapter (version 1.0.0) and an allowed mode set."""
    data = copy.deepcopy(FIXTURE_CONFIG)
    if sha256_bytes(dump(data)) != FIXTURE_CONFIG_SHA256:
        pytest.fail("the embedded fixture configuration no longer has its pinned SHA-256", pytrace=False)
    if (adapter_id, mode) != (dig(data, "model", "adapter", "id"), dig(data, "reasoning_mode", "value")):
        data["model"]["adapter"] = {"id": adapter_id, "version": APPROVED_ADAPTER_VERSION}
        data["reasoning_mode"] = {"value": mode, "provenance": data["reasoning_mode"]["provenance"]}
    return dump(data), data


# ---------------------------------------------------------------------------
# golden cases (§8 split, §8 extract, §9 verdicts); the reference is always "7"
# ---------------------------------------------------------------------------


def _case(case: str, raw: str, *, truncated: bool = False, state: str, correct: bool) -> dict:
    return dict(case=case, raw=raw, truncated=truncated, state=state, correct=correct)


def golden_cases(adapter_id: str) -> list[dict]:
    """Outputs whose only correct verdict depends on the adapter's split and on extraction.

    Every incorrect case's correct `final` holds no digit at all, so no fallback
    extraction rule can recover the reference from it.
    """
    o, c = DELIMITERS[adapter_id]
    fo, fc = DELIMITERS["qwen3"] if adapter_id == "gemma4" else DELIMITERS["gemma4"]
    cases = [
        _case("absent", "The answer is \\boxed{7}.", state="absent", correct=True),
        _case("empty", f"{o}\n\n{c}\n\nThe answer is \\boxed{{7}}.", state="empty", correct=True),
        _case("present", f"{o}\nTry \\boxed{{3}} first; it fails.\n{c}\n\nThe answer is \\boxed{{7}}.",
              state="present", correct=True),
        _case("boxed-only-in-thinking", f"{o}\nIt must be \\boxed{{7}}.\n{c}\n\nI am not certain.",
              state="present", correct=False),
        _case("absent-final-answer", f"{o}\nIt must be \\boxed{{7}}.\n{c}\n", state="present", correct=False),
        _case("unterminated-truncated", f"{o}\nSo far \\boxed{{7}} looks right, and", truncated=True,
              state="unterminated", correct=False),
        _case("unterminated-not-truncated", f"{o}\nSo far \\boxed{{7}} looks right.", state="unterminated",
              correct=False),
        _case("extra-close-after-block", f"{o}\\boxed{{1}}{c}The answer is \\boxed{{7}}.{c}No box here.",
              state="present", correct=False),
        # No generated opening delimiter: the whole output is final despite the stray close.
        _case("stray-close-without-opening", f"The answer is \\boxed{{7}}.{c}No box here.", state="absent",
              correct=True),
        # Another adapter's delimiters are plain text for this adapter.
        _case("other-adapters-delimiters", f"{fo}It must be \\boxed{{7}}.{fc}\nI am not certain.", state="absent",
              correct=True),
        _case("last-box-after-close-wins", f"{o}\nTry \\boxed{{3}}.\n{c}\nFirst \\boxed{{2}}, finally \\boxed{{7}}.",
              state="present", correct=True),
        _case("unbalanced-trailing-box", f"{o}\nwork\n{c}\nSo \\boxed{{7}} then \\boxed{{8", state="present",
              correct=True),
    ]
    if adapter_id == "gemma4":
        cases += [
            _case("think-trigger-is-not-an-opening", "<|think|>\nIt must be \\boxed{7}.<channel|>\nI am not certain.",
                  state="absent", correct=True),
            _case("literal-backslash-n-is-not-an-opening",
                  "<|channel>thought\\nIt must be \\boxed{7}.<channel|>I am not certain.", state="absent",
                  correct=True),
        ]
    return cases


def reopened_cases(adapter_id: str) -> list[dict]:
    """A generated opening delimiter after the last close leaves an unterminated block and an empty final."""
    o, c = DELIMITERS[adapter_id]
    return [
        _case("reopened-never-closed-truncated", f"{o}\nwork\n{c}\nThe answer is \\boxed{{7}}.\n{o}\nre-check",
              truncated=True, state="unterminated", correct=False),
        _case("reopened-never-closed-not-truncated", f"{o}\nwork\n{c}\nThe answer is \\boxed{{7}}.\n{o}\nre-check",
              state="unterminated", correct=False),
        _case("stray-close-then-opening", f"The answer is \\boxed{{7}}.{c}\n{o}\nre-check", truncated=True,
              state="unterminated", correct=False),
    ]


def _stored_extraction(case: dict) -> dict:
    if case["correct"]:
        return {"answer": "7", "rule": "boxed_last", "status": "ok"}
    return {"answer": None, "rule": "none", "status": "truncated" if case["truncated"] else "no_answer"}


# ---------------------------------------------------------------------------
# run directories (§6.1) and rescoring (§3, §6.2, §6.4)
# ---------------------------------------------------------------------------


def fixture_manifest(item_ids: list[str], protocol: dict) -> bytes:
    return dump({
        "manifest_version": "1",
        "source": dig(protocol, "dataset", "source") or {},
        "membership_derivation": {"method": "unique_id", "independent_source": None},
        "normalization": {"version": "visible-1", "near_duplicate": {"method": "visible", "threshold": 0.85}},
        "items": [{"id": i, "role": "dev", "content_sha256": sha256_text(i), "subject": "Number Theory", "level": 2}
                  for i in item_ids],
        "exclusions": [],
        "tiers": {"seed": 7, "smoke": list(item_ids), "validation": []},
        "provenance_note": "visible-suite fixture manifest; IDs only",
    })


def write_run(env: Env, raw_protocol: bytes, data: dict, cases: list[dict], *, invert: bool) -> tuple[str, Path]:
    """A complete fixture run (k = 1, one item per case). Stored verdicts are the correct ones, or all inverted."""
    run_id = f"visible-{uuid.uuid4().hex[:12]}"
    run_dir = env.runs / run_id
    run_dir.mkdir()
    item_ids = [f"visible-{c['case']}" for c in cases]
    manifest = fixture_manifest(item_ids, data)
    seed = 4100
    with open(run_dir / "items.jsonl", "w", encoding="utf-8") as fh:
        for index, (item_id, case) in enumerate(zip(item_ids, cases)):
            stored = case["correct"] != invert
            fh.write(json.dumps({
                "item_id": item_id, "sample_index": 0, "seed": seed,
                "prompt_sha256": sha256_text("visible fixture prompt " + item_id),
                "raw_output": case["raw"], "raw_output_sha256": sha256_text(case["raw"]),
                "thinking_present": case["state"] in ("present", "unterminated"), "thinking_state": case["state"],
                "truncated": case["truncated"], "finish_reason": "length" if case["truncated"] else "stop",
                "tokens_generated": 40 + len(case["raw"]) // 4, "infra_retries": 0,
                "reference_answer": "7", "reference_sha256": sha256_text("7"),
                "extraction": _stored_extraction(case),
                "verdicts": {"primary": stored, "secondary": stored},
                "timing_ms": 500 + 11 * index, "awake_ms": 480 + 11 * index,
            }, ensure_ascii=False) + "\n")
    sampling = {k: v.get("value") for k, v in (data.get("sampling") or {}).items() if isinstance(v, dict)}
    run_json = {
        "run_id": run_id, "protocol_version": data.get("protocol_version"),
        "protocol_sha256": sha256_bytes(raw_protocol), "manifest_sha256": sha256_bytes(manifest),
        "tier": "smoke", "k": 1, "seeds": [seed],
        "model_artifact_sha256": dig(data, "model", "artifact", "sha256"),
        "runtime_build": dig(data, "runtime", "build"),
        "adapter": dict(dig(data, "model", "adapter")),
        "extractor": {"id": dig(data, "extraction", "id"), "version": dig(data, "extraction", "version")},
        "scorers": [{"id": dig(data, "scorers", r, "id"), "pin": dig(data, "scorers", r, "pin")}
                    for r in ("primary", "secondary")],
        "role": data.get("role"), "subset_sha256": None,
        "chat_template_sha256": dig(data, "model", "chat_template", "sha256"),
        "effective": {"reasoning": dig(data, "reasoning_mode", "value"), "template_args": {}, "sampling": sampling},
        "executor": "visible-suite fixture", "host": {"os": "Darwin", "arch": "arm64", "memory_gb": 32},
        "attempts": [{"n": 1, "started": "2026-11-05T09:00:00Z", "ended": "2026-11-05T09:05:00Z",
                      "status": "completed", "resumed_from": None}],
        "events": [], "status": "completed",
    }
    (run_dir / "protocol.yaml").write_bytes(raw_protocol)
    (run_dir / "manifest.json").write_bytes(manifest)
    (run_dir / "run.json").write_text(json.dumps(run_json, indent=2) + "\n", encoding="utf-8")
    for name in ("runtime-verification.json", "artifact-verification.json"):
        (run_dir / name).write_text(json.dumps({"fixture": "visible-suite", "passed": True}) + "\n", encoding="utf-8")
    return run_id, run_dir


def _files(run_dir: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(run_dir.iterdir()) if p.is_file() and p.name != "rescore.json"}


def rescore_problems(env: Env, raw_protocol: bytes, data: dict, cases: list[dict], *, invert: bool) -> list[str]:
    """Rescore one run and compare rescore.json with the only correct outcome (§3, §6.2, §6.4, §8, §9)."""
    label = "control (stored verdicts inverted)" if invert else "run"
    run_id, run_dir = write_run(env, raw_protocol, data, cases, invert=invert)
    before = _files(run_dir)
    proc = env.run("rescore", "--run", run_id)
    problems = []
    # The contract fixes exit 0 for a rescore that runs; it fixes no exit code for one that finds mismatches.
    if not invert and proc.returncode != EXIT_OK:
        return [f"{label}: rescore failed\n" + _explain(proc)]
    path = run_dir / "rescore.json"
    if not path.is_file():
        return [f"{label}: rescore wrote no rescore.json (§3, §6.4)\n" + _explain(proc)]
    after = _files(run_dir)
    if any(after.get(name) != data_bytes for name, data_bytes in before.items()):
        problems.append(f"{label}: rescore changed or removed a run-directory file that existed before it ran (§6.2)")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return problems + [f"{label}: rescore.json is not JSON"]
    if not isinstance(doc, dict):
        return problems + [f"{label}: rescore.json is not an object"]
    if doc.get("rescore_version") != "1" or doc.get("run_id") != run_id:
        problems.append(f"{label}: rescore_version/run_id {doc.get('rescore_version')!r}/{doc.get('run_id')!r}")
    records = [r for r in doc.get("records") or [] if isinstance(r, dict)]
    by_item = {}
    for record in records:
        by_item.setdefault(record.get("item_id"), []).append(record)
    if len(records) != len(cases):
        problems.append(f"{label}: {len(records)} records for {len(cases)} item × sample pairs")
    for case in cases:
        found = by_item.get(f"visible-{case['case']}") or []
        if len(found) != 1:
            problems.append(f"{label} {case['case']}: {len(found)} records")
            continue
        record, stored = found[0], case["correct"] != invert
        got_stored = (dig(record, "stored", "primary"), dig(record, "stored", "secondary"))
        got = (dig(record, "recomputed", "primary"), dig(record, "recomputed", "secondary"))
        if record.get("sample_index") != 0 or got_stored != (stored, stored):
            problems.append(f"{label} {case['case']}: sample_index/stored {record.get('sample_index')!r}/{got_stored}")
        if got != (case["correct"], case["correct"]):
            problems.append(f"{label} {case['case']}: recomputed {got}, expected both {case['correct']} "
                            f"(thinking_state {case['state']})")
        if record.get("match") is not (not invert):
            problems.append(f"{label} {case['case']}: match {record.get('match')!r}, expected {not invert}")
    if doc.get("all_match") is not (not invert):
        problems.append(f"{label}: all_match {doc.get('all_match')!r}, expected {not invert}")
    return problems


def round_trip(env: Env, adapter_id: str, mode: str, cases: list[dict]) -> list[str]:
    raw_protocol, data = fixture_identity(adapter_id, mode)
    assert dig(data, "model", "adapter") == {"id": adapter_id, "version": APPROVED_ADAPTER_VERSION}
    assert dig(data, "role") == "fixture" and dig(data, "runtime", "model_alias") == "fixture-1"
    return (rescore_problems(env, raw_protocol, data, cases, invert=False)
            + rescore_problems(env, raw_protocol, data, cases, invert=True))


# ---------------------------------------------------------------------------
# tests (hermetic)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_id,mode", IDENTITIES, ids=IDENTITY_IDS)
def test_vs_ad_08_rescore_reproduces_each_adapters_golden_split(env, adapter_id, mode):
    """VS-AD-08 · hermetic · §8 split_reasoning per adapter (v0.8.1: no opening delimiter → absent and the whole
    output is final, even with a stray close; the pinned templates of adapter version 1.0.0 leave no opening
    delimiter unmatched); §8 extract (last balanced box wins); §3 rescore; §6.2; §6.4; §9.

    Proposed replacement for the intent of VS-AD-01, with an adapter-valid fixture identity instead of an
    inherited alias. One run per adapter and allowed mode, one item per case: absent, empty, present, a box
    only inside the thinking, an absent final answer, unterminated (truncated and not), an extra close after
    a block, a stray close with no opening, another adapter's delimiters as plain text, the last box after the
    close, an unbalanced trailing box, and for gemma4 the `<|think|>` trigger and a literal backslash-n after
    `thought`, neither of which opens a block. `rescore` exits 0; rescore.json has version "1", the run id, one
    record per pair with the stored verdicts, the correct recomputed verdicts, `match: true` and
    `all_match: true`; no file that was already in the run directory changes. The inverted control reports the same
    recomputed verdicts with `match: false` and `all_match: false`.
    """
    problems = round_trip(env, adapter_id, mode, golden_cases(adapter_id))
    assert not problems, f"{adapter_id} ({mode}):\n" + "\n".join(problems)


@pytest.mark.parametrize("adapter_id,mode", IDENTITIES, ids=IDENTITY_IDS)
def test_vs_ad_09_an_opening_after_the_last_close_empties_final(env, adapter_id, mode):
    """VS-AD-09 · hermetic · v0.8.1 §8 "An opening delimiter after the last closing delimiter leaves an
    unterminated block and an empty final, even when an earlier block was closed"; §3 rescore; §6.4; §9.

    Proposed replacement for VS-AD-02, with an adapter-valid fixture identity. A closed block, a boxed answer,
    then a reopened block that never closes (truncated and not); and a stray close with no opening followed
    by a generated opening. Each final is empty, so each item is incorrect although a correct box precedes
    the last opening. Same rescore.json checks and inverted control as VS-AD-08.
    """
    problems = round_trip(env, adapter_id, mode, reopened_cases(adapter_id))
    assert not problems, f"{adapter_id} ({mode}):\n" + "\n".join(problems)
