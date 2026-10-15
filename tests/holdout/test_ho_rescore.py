"""Holdout — `slm-eval rescore` (contract v0.3 §3, §6.1, §6.2, §6.4, §8, §9).

Hermetic: run-store fixtures (with their protocol.yaml and manifest.json
copies, §6.1) are written into a temporary SLM_EVAL_RUNS and the dataset
cache (SLM_EVAL_CACHE) is an empty temporary directory, because rescore
works "from the stored raw outputs only".

Stored verdicts in these fixtures are the ones any correct implementation of
the §8 extraction rules and of both scorers must produce: identical integer
or identical LaTeX answers are correct, different integers are incorrect,
and `answer = None` is incorrect (§9). A rescore that disagrees with them
therefore reports `all_match: false`.
"""

from __future__ import annotations

import _ho_support as hs


def _rescore(sandbox, run_id):
    proc = sandbox.cli("rescore", "--run", run_id)
    path = sandbox.runs / run_id / "rescore.json"
    return proc, path


def _assert_all_match(sandbox, ctx, lines):
    run_id, run_dir = hs.write_run(sandbox.runs, lines, ctx=ctx)
    before = hs.file_bytes(run_dir)
    proc, path = _rescore(sandbox, run_id)
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc) + "\n" + ctx.note
    assert path.is_file(), "rescore.json was not written into the run directory (§3, §6.2)\n" + hs.describe(proc)
    doc = hs.read_json(path)
    problems = hs.rescore_problems(doc, run_id, hs.pairs_of(lines))
    assert not problems, "rescore.json violates §6.4: " + "; ".join(problems[:10])
    unmatched = [pair for pair, record in hs.rescore_by_pair(doc).items() if record["match"] is not True]
    assert doc["all_match"] is True and not unmatched, (
        f"stored verdicts were not reproduced; records with match false: {unmatched}\n{ctx.note}"
    )
    after = hs.file_bytes(run_dir)
    for name, data in before.items():
        assert after.get(name) == data, f"rescore modified or removed original run file {name} (§6.2)"
    return doc


def test_ho_rs_01_rescore_reproduces_stored_verdicts(sandbox, ctx):
    """HO-RS-01 · §3 rescore, §6.2, §6.4: on a k=2 run of unambiguous items,
    with an empty dataset cache, rescore exits 0 and writes
    `<run>/rescore.json` following §6.4 (rescore_version "1", run_id,
    all_match true, one record per item x sample with stored, recomputed and
    match true), leaving every original run file byte-identical."""
    lines = []
    for sample in (0, 1):
        lines.append(hs.boxed_line(hs.item_id(0), sample, "42", "42"))
        lines.append(hs.boxed_line(hs.item_id(1), sample, "42", "17"))
        lines.append(hs.boxed_line(hs.item_id(2), sample, "7", "7", with_think=False))
        lines.append(hs.boxed_line(hs.item_id(3), sample, "7", "8", with_think=False))
    _assert_all_match(sandbox, ctx, lines)


def test_ho_rs_02_rescore_flags_a_changed_verdict(sandbox, ctx):
    """HO-RS-02 · §3 rescore, §6.4: when one stored verdict differs from what
    extraction and scoring produce, rescore.json has all_match false; that
    record has stored.primary false, recomputed.primary true and match
    false; every other record has match true; original files are untouched."""
    lines = [hs.boxed_line(hs.item_id(i), 0, str(10 + i), str(10 + i)) for i in range(4)]
    lines[1]["verdicts"]["primary"] = False
    run_id, run_dir = hs.write_run(sandbox.runs, lines, ctx=ctx)
    before = hs.file_bytes(run_dir)
    proc, path = _rescore(sandbox, run_id)
    assert path.is_file(), "rescore.json was not written into the run directory (§3, §6.2)\n" + hs.describe(proc)
    doc = hs.read_json(path)
    problems = hs.rescore_problems(doc, run_id, hs.pairs_of(lines))
    assert not problems, "rescore.json violates §6.4: " + "; ".join(problems[:10])
    assert doc["all_match"] is False, "all_match should be false"
    records = hs.rescore_by_pair(doc)
    changed = records[(hs.item_id(1), 0)]
    assert changed["stored"]["primary"] is False and changed["recomputed"]["primary"] is True, changed
    assert changed["match"] is False, f"changed pair not reported as a mismatch: {changed!r}"
    others = [pair for pair, record in records.items() if pair != (hs.item_id(1), 0) and record["match"] is not True]
    assert not others, f"unchanged pairs reported as mismatches: {others}"
    after = hs.file_bytes(run_dir)
    for name, data in before.items():
        assert after.get(name) == data, f"rescore modified or removed original run file {name} (§6.2)"


def test_ho_rs_03_last_boxed_answer_wins(sandbox, ctx):
    """HO-RS-03 · §8 "the last balanced \\boxed{...} wins": with two boxed
    answers in the final text, the verdict follows the last one."""
    raw_a = hs.think("A first guess is " + hs.boxed("41") + ". Correcting that, the answer is " + hs.boxed("42") + ".")
    raw_b = hs.think("The answer is " + hs.boxed("42") + ". On reflection it is " + hs.boxed("41") + ".")
    lines = [
        hs.make_line(hs.item_id(0), 0, raw_a, "42", answer="42", rule="boxed_last", status="ok",
                     primary=True, secondary=True),
        hs.make_line(hs.item_id(1), 0, raw_b, "42", answer="41", rule="boxed_last", status="ok",
                     primary=False, secondary=False),
    ]
    _assert_all_match(sandbox, ctx, lines)


def test_ho_rs_04_boxed_with_nested_braces(sandbox, ctx):
    """HO-RS-04 · §8 "nested braces are supported": \\boxed{\\frac{1}{2}} yields
    the whole fraction, which both scorers accept against reference
    \\frac{1}{2}."""
    lines = [
        hs.make_line(hs.item_id(0), 0, hs.think("So the value is " + hs.boxed("\\frac{1}{2}") + "."), "\\frac{1}{2}",
                     answer="\\frac{1}{2}", rule="boxed_last", status="ok", primary=True, secondary=True),
        hs.make_line(hs.item_id(1), 0, hs.think("So the value is " + hs.boxed("\\frac{3}{4}") + "."), "\\frac{1}{2}",
                     answer="\\frac{3}{4}", rule="boxed_last", status="ok", primary=False, secondary=False),
    ]
    _assert_all_match(sandbox, ctx, lines)


def test_ho_rs_05_unbalanced_trailing_boxed_is_ignored(sandbox, ctx):
    """HO-RS-05 · §8 "the last *balanced* \\boxed{...} wins": an unterminated
    \\boxed{ after a complete one does not displace it."""
    raw = "The answer is " + hs.boxed("42") + ", and to double-check, \\boxed{43"
    lines = [hs.make_line(hs.item_id(0), 0, raw, "42", answer="42", rule="boxed_last", status="ok",
                          primary=True, secondary=True)]
    _assert_all_match(sandbox, ctx, lines)


def test_ho_rs_06_answer_comes_from_text_after_last_think_close(sandbox, ctx):
    """HO-RS-06 · §8 split_reasoning: `final` is the text after the LAST
    </think>. A boxed answer inside the thinking, or between two </think>
    tags, is not the final answer; a boxed answer after the last </think> is."""
    decoy_in_thinking = "<think>\nMaybe it is " + hs.boxed("42") + "?\n</think>\n\nI could not finish this problem."
    between_closes = "<think>\nworking\n</think>\n" + hs.boxed("42") + "\n</think>\n\nI could not finish this problem."
    control = "<think>\nPerhaps " + hs.boxed("41") + ".\n</think>\n\nThe answer is " + hs.boxed("42") + "."
    lines = [
        hs.make_line(hs.item_id(0), 0, decoy_in_thinking, "42", answer=None, rule="none", status="no_answer",
                     primary=False, secondary=False),
        hs.make_line(hs.item_id(1), 0, between_closes, "42", answer=None, rule="none", status="no_answer",
                     primary=False, secondary=False),
        hs.make_line(hs.item_id(2), 0, control, "42", answer="42", rule="boxed_last", status="ok",
                     primary=True, secondary=True),
    ]
    _assert_all_match(sandbox, ctx, lines)


def test_ho_rs_07_no_answer_scores_incorrect(sandbox, ctx):
    """HO-RS-07 · §9 "answer = None scores as incorrect": outputs with no
    extractable answer are incorrect under both scorers, including against a
    reference that is literally the text "None"."""
    lines = [
        hs.make_line(hs.item_id(0), 0, "I cannot determine this.", "None", answer=None, rule="none",
                     status="no_answer", primary=False, secondary=False),
        hs.make_line(hs.item_id(1), 0, "", "None", answer=None, rule="none", status="no_answer",
                     primary=False, secondary=False),
        hs.make_line(hs.item_id(2), 0, hs.think("I cannot determine this."), "None", answer=None, rule="none",
                     status="no_answer", primary=False, secondary=False),
    ]
    _assert_all_match(sandbox, ctx, lines)


def test_ho_rs_08_tampered_raw_output_is_integrity_mismatch(sandbox, ctx):
    """HO-RS-08 · §6.1 "Every command that reads items.jsonl checks each
    record's raw_output_sha256 ... and exits 2 on any mismatch": a stored raw
    output that no longer matches its recorded SHA-256 makes rescore exit 2."""
    lines = [hs.boxed_line(hs.item_id(i), 0, "42", "42") for i in range(3)]
    run_id, run_dir = hs.write_run(sandbox.runs, lines, ctx=ctx)

    def tamper(record):
        if record["item_id"] == hs.item_id(1):
            record["raw_output"] = record["raw_output"].replace("42", "43")

    hs.rewrite_items(run_dir, tamper)
    proc, _ = _rescore(sandbox, run_id)
    assert proc.returncode == hs.EXIT_INTEGRITY, hs.describe(proc)


def test_ho_rs_09_tampered_reference_is_integrity_mismatch(sandbox, ctx):
    """HO-RS-09 · §6.1 "Every command that reads items.jsonl checks each
    record's ... reference_sha256, and exits 2 on any mismatch": a stored
    reference answer that no longer matches its SHA-256 makes rescore exit 2."""
    lines = [hs.boxed_line(hs.item_id(i), 0, "42", "42") for i in range(3)]
    run_id, run_dir = hs.write_run(sandbox.runs, lines, ctx=ctx)

    def tamper(record):
        if record["item_id"] == hs.item_id(2):
            record["reference_answer"] = "43"

    hs.rewrite_items(run_dir, tamper)
    proc, _ = _rescore(sandbox, run_id)
    assert proc.returncode == hs.EXIT_INTEGRITY, hs.describe(proc)
