"""Holdout — CLI entry point and exit codes (contract v0.3 §3).

Hermetic: no network, model or runtime is needed. Each test uses its own
temporary SLM_EVAL_CACHE, SLM_EVAL_RUNS and working directory.

§3 exit codes: 0 success, 1 other error, 2 integrity mismatch (only), 3
precondition unmet (a missing file, tool or run) or usage error, 4 runtime
security failure.
"""

from __future__ import annotations

from pathlib import Path

import _ho_support as hs


def _missing(sandbox: hs.Sandbox, name: str) -> str:
    return str(sandbox.work / "does-not-exist" / name)


def test_ho_ex_01_entry_point_resolves():
    """HO-EX-01 · §3 entry point: `slm-eval` (a Poetry script) is resolvable in
    the test environment."""
    command = hs.cli_command()
    assert command, "no command resolved for slm-eval"


def test_ho_ex_02_unknown_command_is_a_usage_error(sandbox):
    """HO-EX-02 · §3 "usage errors exit with 3, never 2": an unknown
    subcommand exits 3."""
    proc = sandbox.cli("holdout-no-such-command")
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)


def test_ho_ex_03_missing_required_option_is_a_usage_error(sandbox):
    """HO-EX-03 · §3 "usage errors exit with 3, never 2": `rescore` without
    its required `--run` exits 3."""
    proc = sandbox.cli("rescore")
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)


def test_ho_ex_04_rescore_unknown_run_is_precondition(sandbox):
    """HO-EX-04 · §3 rescore + exit 3 (missing run): rescoring a run ID with no
    run directory exits 3 and creates nothing in the run store."""
    run_id = hs.new_run_id("missing")
    proc = sandbox.cli("rescore", "--run", run_id)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not (sandbox.runs / run_id).exists(), "rescore created a directory for a run that does not exist"


def test_ho_ex_05_rescore_run_without_items_is_precondition(sandbox, ctx):
    """HO-EX-05 · §3 rescore + §6.1 + exit 3: a run directory whose
    items.jsonl is missing cannot be rescored; exit 3."""
    lines = [hs.boxed_line(hs.item_id(0), 0, "42", "42")]
    run_id, run_dir = hs.write_run(sandbox.runs, lines, ctx=ctx)
    (run_dir / "items.jsonl").unlink()
    proc = sandbox.cli("rescore", "--run", run_id)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)


def test_ho_ex_06_report_unknown_run_is_precondition(sandbox):
    """HO-EX-06 · §3 report + exit 3: reporting on a run ID with no run
    directory exits 3."""
    proc = sandbox.cli("report", "--run", hs.new_run_id("missing"))
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)


def test_ho_ex_07_report_missing_sensitivity_file_is_precondition(sandbox, ctx):
    """HO-EX-07 · §3 report `--sensitivity` + exit 3: a sensitivity path that
    does not exist exits 3 even though the run itself is valid."""
    lines = hs.pattern_lines(20, range(10), range(10))
    run_id, _ = hs.write_run(sandbox.runs, lines, ctx=ctx)
    proc = sandbox.cli("report", "--run", run_id, "--compare-published", "0.5",
                       "--sensitivity", _missing(sandbox, "sensitivity.json"))
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)


def test_ho_ex_08_sensitivity_unknown_base_is_precondition(sandbox, ctx):
    """HO-EX-08 · §3 sensitivity + exit 3: an unknown base run exits 3 and no
    sensitivity file is written."""
    lines = hs.pattern_lines(20, range(10), range(10))
    variant_id, _ = hs.write_run(sandbox.runs, lines, ctx=ctx)
    out = sandbox.work / "sensitivity.json"
    proc = sandbox.cli("sensitivity", "--base", hs.new_run_id("missing"), "--variant", variant_id, "--out", out)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not out.exists(), "sensitivity wrote an output although its base run does not exist"


def test_ho_ex_09_sensitivity_unknown_variant_is_precondition(sandbox, ctx):
    """HO-EX-09 · §3 sensitivity + exit 3: an unknown variant run exits 3 and
    no sensitivity file is written."""
    lines = hs.pattern_lines(20, range(10), range(10))
    base_id, _ = hs.write_run(sandbox.runs, lines, ctx=ctx)
    out = sandbox.work / "sensitivity.json"
    proc = sandbox.cli("sensitivity", "--base", base_id, "--variant", hs.new_run_id("missing"), "--out", out)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not out.exists(), "sensitivity wrote an output although its variant run does not exist"


def test_ho_ex_10_resume_unknown_run_is_precondition(sandbox):
    """HO-EX-10 · §3 `run --resume` + exit 3: resuming a run ID with no run
    directory exits 3."""
    proc = sandbox.cli("run", "--resume", hs.new_run_id("missing"))
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)


def test_ho_ex_11_run_missing_protocol_and_manifest_is_precondition(sandbox):
    """HO-EX-11 · §3 run + exit 3: starting a run from protocol and manifest
    paths that do not exist exits 3 and records no run."""
    proc = sandbox.cli("run", "--protocol", _missing(sandbox, "protocol.yaml"),
                       "--manifest", _missing(sandbox, "manifest.json"), "--tier", "smoke")
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not any((d / "items.jsonl").exists() for d in sandbox.run_dirs()), "a run produced items"


def test_ho_ex_12_verify_runtime_missing_protocol_is_precondition(sandbox):
    """HO-EX-12 · §3 verify-runtime + exit 3: a protocol path that does not
    exist exits 3 (the missing-file precondition is checked before any
    runtime is started)."""
    proc = sandbox.cli("verify-runtime", "--protocol", _missing(sandbox, "protocol.yaml"))
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)


def test_ho_ex_13_verify_artifact_missing_protocol_is_precondition(sandbox):
    """HO-EX-13 · §3 verify-artifact + exit 3: a protocol path that does not
    exist exits 3."""
    proc = sandbox.cli("verify-artifact", "--protocol", _missing(sandbox, "protocol.yaml"))
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)


def test_ho_ex_14_show_pair_missing_flagged_file_is_precondition(sandbox):
    """HO-EX-14 · §3 `data show-pair` + exit 3: a flagged file that does not
    exist exits 3."""
    proc = sandbox.cli("data", "show-pair", "--flagged", _missing(sandbox, "flagged.json"), "--pair", "1")
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)


def test_ho_ex_15_apply_verdicts_missing_files_is_precondition(sandbox):
    """HO-EX-15 · §3 `data apply-verdicts` + exit 3: manifest and verdicts paths
    that do not exist exit 3 and create no manifest."""
    manifest = _missing(sandbox, "manifest.json")
    proc = sandbox.cli("data", "apply-verdicts", "--manifest", manifest,
                       "--verdicts", _missing(sandbox, "verdicts.json"))
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not Path(manifest).exists(), "apply-verdicts created a manifest"


def test_ho_ex_16_build_manifest_with_empty_cache_is_precondition(sandbox):
    """HO-EX-16 · §3 `data build-manifest` + exit 3 (missing file): with an
    empty dataset cache (nothing fetched) the pinned inputs are missing, not
    mismatched, so the build exits 3 and writes no manifest."""
    out = sandbox.work / "manifest.json"
    proc = sandbox.cli("data", "build-manifest", "--out", out)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not out.exists(), "build-manifest wrote a manifest without its pinned inputs"
