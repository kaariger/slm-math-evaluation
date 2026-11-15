"""Visible specification tests — usage errors and missing inputs for the v0.8 commands (contract v0.8 §3).

Contract-only: written before any v0.8 implementation commit was supplied,
with no implementation code seen. Hermetic: every test runs `slm-eval` with
its own temporary SLM_EVAL_CACHE, SLM_EVAL_RUNS, SLM_EVAL_SCRATCH and working
directory, and needs no network, dataset, model or runtime.

Contract v0.8 §3, for every command: "usage errors exit with 3, never 2. A
usage error is an unknown command or option, or a missing or malformed
argument"; exit 3 also covers "a precondition is unmet (for example a missing
file, tool, or run)"; each written path goes to stdout and logs to stderr.
The files these commands name do not exist, so a missing-file precondition
would also give exit 3: the tests pin the exit code §3 requires without
depending on the order of checks.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable is passed to the implementation.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

USAGE_ERROR = 3
TIMEOUT = 120

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

    def written(self) -> list[str]:
        return [str(p.relative_to(self.root)) for base in (self.cache, self.runs, self.scratch, self.work)
                for p in sorted(base.rglob("*"))]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _explain(proc: subprocess.CompletedProcess) -> str:
    return (f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}")


def _assert_exit_3(env: Env, proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == USAGE_ERROR, "expected exit 3 (§3)\n" + _explain(proc)
    assert env.written() == [], f"a rejected command wrote files: {env.written()}"
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)


SUBSET = ("data", "subset", "--manifest", "manifest.json", "--tier", "validation")

USAGE_CASES = {
    "subset-missing-out": SUBSET + ("--size", "10", "--stratify", "subject", "--seed", "1"),
    "subset-malformed-size": SUBSET + ("--size", "ten", "--stratify", "subject", "--seed", "1", "--out", "s.json"),
    "subset-malformed-seed": SUBSET + ("--size", "10", "--stratify", "subject", "--seed", "one", "--out", "s.json"),
    "model-without-subcommand": ("model",),
    "convert-missing-spec": ("model", "convert"),
    "convert-unknown-option": ("model", "convert", "--spec", "spec.yaml", "--definitely-not-an-option"),
    "sanity-missing-spec": ("model", "sanity"),
    "runner-without-subcommand": ("runner",),
    "start-missing-authorization": ("runner", "start", "--plan", "plan.yaml"),
    "pause-unknown-option": ("runner", "pause", "--definitely-not-an-option"),
    "status-unknown-option": ("runner", "status", "--definitely-not-an-option"),
    "run-subset-without-value": ("run", "--protocol", "protocol.yaml", "--manifest", "manifest.json", "--tier",
                                 "validation", "--subset"),
}


@pytest.mark.parametrize("case", sorted(USAGE_CASES))
def test_vs_cu_01_usage_errors_exit_3(env, case):
    """VS-CU-01 · hermetic · §3 usage errors (unknown command or option, missing or malformed argument)
    for `data subset`, `model convert|sanity`, `runner start|pause|status` and `run --subset`.

    Each → exit 3; nothing written; empty stdout.
    """
    _assert_exit_3(env, env.run(*USAGE_CASES[case]))


MISSING_INPUT_CASES = {
    "convert-missing-spec-file": ("model", "convert", "--spec", "missing-spec.yaml"),
    "sanity-missing-spec-file": ("model", "sanity", "--spec", "missing-spec.yaml"),
    "start-missing-plan-files": ("runner", "start", "--plan", "missing-plan.yaml", "--authorization",
                                 "missing-authorization.json"),
    "subset-missing-manifest": ("data", "subset", "--manifest", "missing-manifest.json", "--tier", "validation",
                                "--size", "10", "--stratify", "subject", "--seed", "1", "--out", "subset.json"),
}


@pytest.mark.parametrize("case", sorted(MISSING_INPUT_CASES))
def test_vs_cu_02_missing_input_files_exit_3(env, case):
    """VS-CU-02 · hermetic · §3 exit 3 "a precondition is unmet (for example a missing file …)".

    A well-formed command naming an input file that does not exist → exit 3;
    nothing written (in particular no `--out` file and no runner state);
    empty stdout.
    """
    _assert_exit_3(env, env.run(*MISSING_INPUT_CASES[case]))
