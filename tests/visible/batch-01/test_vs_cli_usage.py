"""Visible specification tests — CLI usage errors and stdout discipline (contract v0.3 §3).

Contract-only: written before any implementation commit was supplied, with
no implementation code seen. Hermetic: every test runs `slm-eval` with its
own temporary SLM_EVAL_CACHE, SLM_EVAL_RUNS and working directory, and needs
no network, dataset, model or runtime.

Contract v0.3 §3, for every command:
  - "usage errors exit with 3, never 2. A usage error is an unknown command
    or option, or a missing or malformed argument."
  - writes machine-readable output to the named paths, "prints each written
    path to stdout", and "writes logs to stderr".

Configuration: SLM_EVAL_TEST_CMD overrides the command used to invoke the CLI
(shell-split); the default is `slm-eval` on PATH.
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
                     if k not in ("SLM_EVAL_CACHE", "SLM_EVAL_RUNS", "SLM_EVAL_TEST_CMD")}
        self.vars["SLM_EVAL_CACHE"] = str(self.cache)
        self.vars["SLM_EVAL_RUNS"] = str(self.runs)

    def run(self, *args: object) -> subprocess.CompletedProcess:
        return subprocess.run(
            _command() + [str(a) for a in args],
            cwd=self.work,
            env=self.vars,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
        )

    def written(self) -> list[str]:
        """Every file and directory created under the cache, run store or working directory."""
        found = []
        for root in (self.cache, self.runs, self.work):
            for path in sorted(root.rglob("*")):
                found.append(str(path.relative_to(root.parent)))
        return found


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _explain(proc: subprocess.CompletedProcess) -> str:
    return (f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}")


def _assert_usage_error(env: Env, proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == USAGE_ERROR, "expected usage-error exit 3 (§3)\n" + _explain(proc)
    assert env.written() == [], f"a rejected command wrote files: {env.written()}"


def test_vs_cli_01_no_command(env):
    """VS-CLI-01 · §3 usage errors (missing argument): `slm-eval` with no
    command exits 3 and writes nothing."""
    _assert_usage_error(env, env.run())


def test_vs_cli_02_data_without_subcommand(env):
    """VS-CLI-02 · §3 usage errors (missing argument): `slm-eval data` with
    no data subcommand exits 3 and writes nothing."""
    _assert_usage_error(env, env.run("data"))


def test_vs_cli_03_unknown_option_is_rejected_before_any_work(env):
    """VS-CLI-03 · §3 usage errors (unknown option): `slm-eval data fetch`
    with an unknown option exits 3 without fetching anything (the dataset
    cache stays empty)."""
    _assert_usage_error(env, env.run("data", "fetch", "--definitely-not-an-option"))


def test_vs_cli_04_missing_required_option(env):
    """VS-CLI-04 · §3 usage errors (missing argument): `sensitivity` without
    its required `--out` exits 3 and writes nothing."""
    _assert_usage_error(env, env.run("sensitivity", "--base", "run-a", "--variant", "run-b"))


def test_vs_cli_05_malformed_integer(env):
    """VS-CLI-05 · §3 usage errors (malformed argument): `run --k two` exits
    3 and creates no run directory."""
    proc = env.run("run", "--protocol", "protocol.yaml", "--manifest", "manifest.json",
                   "--tier", "smoke", "--k", "two")
    _assert_usage_error(env, proc)


def test_vs_cli_06_tier_outside_the_allowed_choices(env):
    """VS-CLI-06 · §3 `--tier {smoke,validation,test}` + usage errors: a tier
    outside those three choices exits 3 and creates no run directory."""
    proc = env.run("run", "--protocol", "protocol.yaml", "--manifest", "manifest.json", "--tier", "gold")
    _assert_usage_error(env, proc)


def test_vs_cli_07_non_numeric_published_value(env):
    """VS-CLI-07 · §3 report `<value>` is a fraction + usage errors
    (malformed argument): `--compare-published high` exits 3 and writes
    nothing."""
    _assert_usage_error(env, env.run("report", "--run", "run-a", "--compare-published", "high"))


def test_vs_cli_08_non_integer_pair_number(env):
    """VS-CLI-08 · §3 `data show-pair --pair <n>` + usage errors (malformed
    argument): a non-integer pair number exits 3 and writes nothing."""
    _assert_usage_error(env, env.run("data", "show-pair", "--flagged", "flagged.json", "--pair", "first"))


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("rescore", "--run", "run-that-does-not-exist"),
        ("data", "show-pair", "--flagged", "flagged-that-does-not-exist.json", "--pair", "1"),
    ],
    ids=["VS-CLI-09a-no-command", "VS-CLI-09b-missing-run", "VS-CLI-09c-missing-flagged-file"],
)
def test_vs_cli_09_failures_keep_stdout_empty(env, args):
    """VS-CLI-09a..c · §3 "prints each written path to stdout" and "writes
    logs to stderr": a command that fails and writes nothing prints nothing
    to stdout; its diagnostics, including any usage text, go to stderr."""
    proc = env.run(*args)
    assert proc.returncode != 0, "the command was expected to fail\n" + _explain(proc)
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)
