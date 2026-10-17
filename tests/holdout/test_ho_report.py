"""Holdout — `slm-eval report` and the comparison reading (contract v0.3 §3, §7).

Hermetic: k=1 and k=2 run-store fixtures in a temporary SLM_EVAL_RUNS, with
stored verdicts chosen so that every expected aggregate is known exactly.

Reports are read from the run directory (the default --out-dir, §3).
Accuracies, rates and V are fractions in [0, 1] (§3). The contract fixes the
report's contents but not its field names, so these tests look for values
under keyword-matched keys rather than for exact names.
"""

from __future__ import annotations

import json
import uuid

import _ho_support as hs

FACTOR = "reasoning_mode"
OTHER_FACTOR = "sampling.temperature"


def _run(sandbox, ctx, n, primary_correct, secondary_correct, **kwargs):
    lines = hs.pattern_lines(n, range(primary_correct), range(secondary_correct), **kwargs)
    return hs.write_run(sandbox.runs, lines, ctx=ctx)


def _count_or_rate(values, count, n):
    return any(v == count for v in values) or hs.has_close(values, count / n)


def _reading(sandbox, ctx, primary_correct, secondary_correct, value, comparisons=None, n=20):
    run_id, _ = _run(sandbox, ctx, n, primary_correct, secondary_correct)
    extra = ["--compare-published", value]
    if comparisons is not None:
        doc = hs.sensitivity_doc(run_id, ctx.sha256, comparisons)
        extra += ["--sensitivity", hs.write_json(sandbox.work / "sensitivity.json", doc)]
    _, report, md = hs.run_report(sandbox, run_id, *extra)
    return report, md


def _assert_reading(report, md, expected):
    labels = hs.labels_in_json(report)
    assert labels == {expected}, f"expected reading {expected}, report.json carries {sorted(labels) or 'none'}"
    assert hs.label_in_text(expected, md), f"report.md does not state the reading {expected}"


def test_ho_rp_01_accuracy_and_wilson_interval_per_scorer(sandbox, ctx):
    """HO-RP-01 · §7 "per scorer: accuracy, with a Wilson 95% interval for
    k=1": 20 items, primary 13 correct and secondary 11 correct; report.json
    carries 0.65 and 0.55 and the Wilson intervals of 13/20 and 11/20, and
    both report.json and report.md are written."""
    run_id, _ = _run(sandbox, ctx, 20, 13, 11)
    _, report, md = hs.run_report(sandbox, run_id)
    values = hs.numbers(report)
    assert hs.has_close(values, 0.65), "primary accuracy 0.65 not in report.json"
    assert hs.has_close(values, 0.55), "secondary accuracy 0.55 not in report.json"
    for successes in (13, 11):
        lo, hi = hs.wilson(successes, 20)
        assert hs.has_interval(report, lo, hi), f"Wilson 95% interval [{lo:.4f}, {hi:.4f}] for {successes}/20 missing"
    assert md.strip(), "report.md is empty"


def test_ho_rp_02_format_failure_thinking_and_disagreement_counts(sandbox, ctx):
    """HO-RP-02 · §7 counts and their definitions: 20 items, none truncated,
    3 with extraction status no_answer (format failures), 15 with
    thinking_present true, 2 with differing primary/secondary verdicts;
    report.json carries format failures 3, thinking rate 0.75 and grader
    disagreements 2 (counts, or rates as fractions)."""
    lines = []
    for i in range(17):
        reference = str(100 + i)
        primary = i <= 10 and i != 11
        secondary = i <= 9 or i == 11
        right = primary or secondary
        lines.append(hs.boxed_line(hs.item_id(i), 0, reference, reference if right else str(500 + i),
                                   primary=primary, secondary=secondary, with_think=i < 15))
    for i in range(17, 20):
        lines.append(hs.no_answer_line(hs.item_id(i), 0, str(100 + i), with_think=False))
    run_id, _ = hs.write_run(sandbox.runs, lines, ctx=ctx)
    _, report, _ = hs.run_report(sandbox, run_id)
    assert _count_or_rate(hs.numbers_under(report, "format"), 3, 20), "format failures (3) not reported"
    assert _count_or_rate(hs.numbers_under(report, "think"), 15, 20), "thinking rate (0.75) not reported"
    assert _count_or_rate(hs.numbers_under(report, "disagree"), 2, 20), "grader disagreements (2) not reported"


def test_ho_rp_03_truncation_count(sandbox, ctx):
    """HO-RP-03 · §7 truncation ("truncated: true"): 4 of 20 outputs are
    truncated; report.json reports truncations 4 (count, or rate 0.2)."""
    lines = [hs.truncated_line(hs.item_id(i), 0, str(100 + i)) for i in range(4)]
    lines += [hs.boxed_line(hs.item_id(i), 0, str(100 + i), str(100 + i)) for i in range(4, 20)]
    run_id, _ = hs.write_run(sandbox.runs, lines, ctx=ctx)
    _, report, _ = hs.run_report(sandbox, run_id)
    assert _count_or_rate(hs.numbers_under(report, "truncat"), 4, 20), "truncations (4) not reported"


def test_ho_rp_04_provenance_table_and_deviations(sandbox, ctx):
    """HO-RP-04 · §7 "the protocol provenance table and the list of
    deviations" with §4 provenance: every provenance label and every
    sampling field of the run's protocol file appear in report.json, every
    provenance label appears in report.md, and report.json has a deviations
    entry (possibly empty)."""
    run_id, _ = _run(sandbox, ctx, 20, 10, 10)
    _, report, md = hs.run_report(sandbox, run_id)
    used = {v for p, v in hs.walk(ctx.data) if p and p[-1] == "provenance" and isinstance(v, str)}
    assert used, "fixture protocol carries no provenance values\n" + ctx.note
    text = hs.text_of(report)
    missing_json = sorted(v for v in used if v not in text)
    missing_md = sorted(v for v in used if v not in md)
    assert not missing_json, f"provenance values missing from report.json: {missing_json}\n{ctx.note}"
    assert not missing_md, f"provenance values missing from report.md: {missing_md}\n{ctx.note}"
    absent_fields = [f for f in (ctx.data.get("sampling") or {}) if f not in text]
    assert not absent_fields, f"provenance table lacks sampling fields {absent_fields}"
    assert any("deviation" in k.lower() for k in hs.all_keys(report)), "report.json has no deviations entry"


def test_ho_rp_05_multi_seed_aggregates(sandbox, ctx):
    """HO-RP-05 · §7 "for k>1: per-item seed mean, an item-bootstrap interval,
    and the spread across seeds" and "per-seed results": 10 items, k=2,
    seed 0 accuracy 0.7 and seed 1 accuracy 0.4; report.json carries both
    per-seed accuracies, the seed mean 0.55, a bootstrap interval containing
    0.55, and a spread entry."""
    lines = hs.pattern_lines(10, range(7), range(7), sample_index=0)
    lines += hs.pattern_lines(10, range(4), range(4), sample_index=1)
    run_id, _ = hs.write_run(sandbox.runs, lines, ctx=ctx)
    _, report, _ = hs.run_report(sandbox, run_id)
    values = hs.numbers(report)
    for expected in (0.7, 0.4, 0.55):
        assert hs.has_close(values, expected), f"{expected} not in report.json"
    boot = [iv for tree in hs.subtrees_mentioning(report, "bootstrap") for iv in hs.intervals(tree)]
    assert any(0.0 <= lo <= 0.55 <= hi <= 1.0 for lo, hi in boot), (
        f"no item-bootstrap interval containing the seed mean 0.55; intervals near 'bootstrap': {boot}"
    )
    spread_keys = [k for k in hs.all_keys(report)
                   if any(t in k.lower() for t in ("spread", "std", "range", "variance"))]
    assert spread_keys, "report.json has no spread-across-seeds entry"


def test_ho_rp_06_consistent_takes_precedence(sandbox, ctx):
    """HO-RP-06 · §7 rule order: primary 10/20 (Wilson [0.299, 0.701]),
    secondary 16/20, V = 0.6, and a sensitivity comparison whose interval
    also covers d = 0.1. V is inside the primary interval, so the reading is
    CONSISTENT although both PROTOCOL-SENSITIVE conditions also hold."""
    comparisons = [{"factor": FACTOR, "base_accuracy": 0.5, "variant_accuracy": 0.65, "interval": [0.05, 0.30]}]
    report, md = _reading(sandbox, ctx, 10, 16, "0.6", comparisons)
    _assert_reading(report, md, "CONSISTENT")


def test_ho_rp_07_grader_range_is_protocol_sensitive(sandbox, ctx):
    """HO-RP-07 · §7 rule 2, grader-sensitivity range: primary 0.5,
    secondary 0.8, V = 0.75 lies outside the primary interval and inside
    [0.5, 0.8], so the reading is PROTOCOL-SENSITIVE."""
    report, md = _reading(sandbox, ctx, 10, 16, "0.75")
    _assert_reading(report, md, "PROTOCOL-SENSITIVE")


def test_ho_rp_08_grader_range_is_closed(sandbox, ctx):
    """HO-RP-08 · §7 rule 2, "the closed interval": V = 0.8 equals the
    secondary accuracy exactly, so the reading is PROTOCOL-SENSITIVE."""
    report, md = _reading(sandbox, ctx, 10, 16, "0.8")
    _assert_reading(report, md, "PROTOCOL-SENSITIVE")


def test_ho_rp_09_grader_range_when_secondary_is_lower(sandbox, ctx):
    """HO-RP-09 · §7 rule 2, "between the primary and secondary accuracies":
    primary 0.5, secondary 0.2, V = 0.25 is outside the primary interval
    and inside [0.2, 0.5], so the reading is PROTOCOL-SENSITIVE."""
    report, md = _reading(sandbox, ctx, 10, 4, "0.25")
    _assert_reading(report, md, "PROTOCOL-SENSITIVE")


def test_ho_rp_10_unexplained_without_sensitivity(sandbox, ctx):
    """HO-RP-10 · §7 rule 3: primary 0.5, secondary 0.8, V = 0.9 is outside
    the primary interval and the grader range, with no sensitivity file, so
    the reading is UNEXPLAINED."""
    report, md = _reading(sandbox, ctx, 10, 16, "0.9")
    _assert_reading(report, md, "UNEXPLAINED")


def test_ho_rp_11_sensitivity_interval_extended_to_zero(sandbox, ctx):
    """HO-RP-11 · §7 rule 2, d in [min(0, lo), max(0, hi)]: primary 0.5,
    secondary 0.55, V = 0.75 so d = 0.25. The reasoning_mode comparison's
    interval [0.30, 0.40] does not contain d but [0, 0.40] does, so the
    reading is PROTOCOL-SENSITIVE and names reasoning_mode."""
    comparisons = [
        {"factor": FACTOR, "base_accuracy": 0.5, "variant_accuracy": 0.85, "interval": [0.30, 0.40]},
        {"factor": OTHER_FACTOR, "base_accuracy": 0.5, "variant_accuracy": 0.42, "interval": [-0.10, -0.05]},
    ]
    report, md = _reading(sandbox, ctx, 10, 11, "0.75", comparisons)
    _assert_reading(report, md, "PROTOCOL-SENSITIVE")
    assert FACTOR in hs.text_of(report) and FACTOR in md, "the satisfying factor reasoning_mode is not named"


def test_ho_rp_12_sensitivity_negative_side(sandbox, ctx):
    """HO-RP-12 · §7 rule 2 with a negative discrepancy: primary 0.5,
    secondary 0.55, V = 0.25 so d = -0.25, inside [min(0, -0.40), max(0,
    -0.30)] = [-0.40, 0]; the reading is PROTOCOL-SENSITIVE and names
    reasoning_mode."""
    comparisons = [{"factor": FACTOR, "base_accuracy": 0.5, "variant_accuracy": 0.15, "interval": [-0.40, -0.30]}]
    report, md = _reading(sandbox, ctx, 10, 11, "0.25", comparisons)
    _assert_reading(report, md, "PROTOCOL-SENSITIVE")
    assert FACTOR in hs.text_of(report) and FACTOR in md, "the satisfying factor reasoning_mode is not named"


def test_ho_rp_13_unexplained_despite_sensitivity_file(sandbox, ctx):
    """HO-RP-13 · §7 rule 3: primary 0.5, secondary 0.55, V = 0.95 so
    d = 0.45, outside [0, 0.40] for the only comparison; the reading is
    UNEXPLAINED."""
    comparisons = [{"factor": FACTOR, "base_accuracy": 0.5, "variant_accuracy": 0.85, "interval": [0.30, 0.40]}]
    report, md = _reading(sandbox, ctx, 10, 11, "0.95", comparisons)
    _assert_reading(report, md, "UNEXPLAINED")


def test_ho_rp_14_every_satisfying_factor_is_named(sandbox, ctx):
    """HO-RP-14 · §7 "every factor that satisfies a condition is named":
    primary 0.5, secondary 0.8, V = 0.75 satisfies the grader range and the
    reasoning_mode comparison ([0.20, 0.30], d = 0.25); the reading is
    PROTOCOL-SENSITIVE and reasoning_mode is named."""
    comparisons = [{"factor": FACTOR, "base_accuracy": 0.5, "variant_accuracy": 0.75, "interval": [0.20, 0.30]}]
    report, md = _reading(sandbox, ctx, 10, 16, "0.75", comparisons)
    _assert_reading(report, md, "PROTOCOL-SENSITIVE")
    assert FACTOR in hs.text_of(report) and FACTOR in md, "the satisfying factor reasoning_mode is not named"


def test_ho_rp_15_no_reading_without_compare_published(sandbox, ctx):
    """HO-RP-15 · §7 "With --compare-published V, the report adds a
    comparison reading": without the option, report.json carries none of
    the three reading labels."""
    run_id, _ = _run(sandbox, ctx, 20, 10, 16)
    _, report, _ = hs.run_report(sandbox, run_id)
    assert hs.labels_in_json(report) == set(), "a comparison reading was added without --compare-published"


def test_ho_rp_16_report_contains_aggregates_only(sandbox, ctx):
    """HO-RP-16 · §2 data policy + §7 "aggregates only": raw outputs,
    extracted answers and reference answers carry unique sentinel tokens;
    none of them appears in report.json or report.md."""
    lines, tokens = [], []
    for i in range(6):
        raw_token, answer_token, ref_token = (uuid.uuid4().hex for _ in range(3))
        tokens += [raw_token, answer_token, ref_token]
        answer = "HOLDOUTANS" + answer_token
        reference = "HOLDOUTREF" + ref_token
        raw = hs.think("The answer is " + hs.boxed(answer) + ".", thought="Scratch work HOLDOUTRAW" + raw_token)
        lines.append(hs.make_line(hs.item_id(i), 0, raw, reference, answer=answer, rule="boxed_last",
                                  status="ok", primary=i % 2 == 0, secondary=i % 3 == 0))
    run_id, _ = hs.write_run(sandbox.runs, lines, ctx=ctx)
    _, report, md = hs.run_report(sandbox, run_id, "--compare-published", "0.5")
    blob = json.dumps(report, ensure_ascii=False) + "\n" + md
    leaked = [t for t in tokens if t in blob]
    assert not leaked, f"{len(leaked)} per-item text sentinels leaked into the committed report"
