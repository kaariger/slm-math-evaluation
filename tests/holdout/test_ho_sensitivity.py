"""Holdout — `slm-eval sensitivity` (contract v0.3 §3, §6.1, §7.1).

Hermetic: base and variant runs are run-store fixtures in a temporary
SLM_EVAL_RUNS. Base runs are recorded against the committed protocol when
it can be located (else the §4 v1 draft defaults); each variant is a new
protocol version that differs from the base in the stated factor only.
Factors are compared on the run-directory copies `protocol.yaml` and
`manifest.json`, whose SHA-256 values are the recorded protocol_sha256 and
manifest_sha256 (§6.1, §7.1).

§7.1 names a factor "by its dotted path" yet its examples omit the final
`.value` (e.g. `reasoning_mode`), so both spellings are accepted.
"""

from __future__ import annotations

import _ho_support as hs

MODE_NAMES = {"reasoning_mode", "reasoning_mode.value"}
TEMPERATURE_NAMES = {"sampling.temperature", "sampling.temperature.value"}
ARTIFACT_NAMES = {"model.artifact"}
ANY = object()
N = 20


def _other_mode(ctx):
    current = hs.dig(ctx.data, "reasoning_mode", "value")
    return current, next(m for m in hs.REASONING_MODES if m != current)


def _other_temperature(ctx):
    current = hs.dig(ctx.data, "sampling", "temperature", "value")
    try:
        return current, round(float(current) + 0.1, 4)
    except (TypeError, ValueError):
        return current, 0.7


def _lines(correct, n=N):
    return hs.pattern_lines(n, range(correct), range(correct))


def _base(sandbox, ctx, correct=10, **kwargs):
    return hs.write_run(sandbox.runs, _lines(correct), ctx=ctx, **kwargs)


def _variant(sandbox, ctx, changes, suffix, correct, lines=None, **kwargs):
    raw, data = ctx.variant(changes, suffix)
    return hs.write_run(sandbox.runs, lines or _lines(correct), ctx=ctx, protocol_bytes=raw,
                        protocol_data=data, **kwargs)


def _mode_variant(sandbox, ctx, correct=14, **kwargs):
    base_value, value = _other_mode(ctx)
    run_id, run_dir = _variant(sandbox, ctx, {("reasoning_mode", "value"): value}, "mode", correct, **kwargs)
    return run_id, base_value, value


def _sensitivity(sandbox, base_id, *variant_ids, out_name="sensitivity.json"):
    out = sandbox.work / out_name
    args = ["sensitivity", "--base", base_id]
    for variant_id in variant_ids:
        args += ["--variant", variant_id]
    proc = sandbox.cli(*args, "--out", out)
    return proc, out


def _comparison_problems(c, variant_id, names, base_value, variant_value, base_acc, variant_acc):
    problems = []
    if c.get("variant_run") != variant_id:
        problems.append(f"variant_run {c.get('variant_run')!r} != {variant_id!r}")
    if c.get("factor") not in names:
        problems.append(f"factor {c.get('factor')!r} not in {sorted(names)}")
    if base_value is not ANY and not hs.loose_equal(c.get("base_value"), base_value):
        problems.append(f"base_value {c.get('base_value')!r} != {base_value!r}")
    if variant_value is not ANY and not hs.loose_equal(c.get("variant_value"), variant_value):
        problems.append(f"variant_value {c.get('variant_value')!r} != {variant_value!r}")
    if c.get("paired_items") != N:
        problems.append(f"paired_items {c.get('paired_items')!r} != {N}")
    for key, expected in (("base_accuracy", base_acc), ("variant_accuracy", variant_acc),
                          ("effect", variant_acc - base_acc)):
        value = c.get(key)
        if not hs.is_number(value) or abs(value - expected) > 1e-9:
            problems.append(f"{key} {value!r} != {expected}")
    interval = c.get("effect_interval_95")
    effect = c.get("effect")
    if not (isinstance(interval, list) and len(interval) == 2 and all(hs.is_number(x) for x in interval)):
        problems.append(f"effect_interval_95 {interval!r} is not [lo, hi]")
    elif not hs.is_number(effect) or not (-1.0 <= interval[0] <= effect <= interval[1] <= 1.0):
        problems.append(f"effect_interval_95 {interval!r} does not bracket effect {effect!r} within [-1, 1]")
    if c.get("method") != hs.SENSITIVITY_METHOD:
        problems.append(f"method {c.get('method')!r} != {hs.SENSITIVITY_METHOD!r}")
    if not hs.is_int(c.get("bootstrap_seed")):
        problems.append(f"bootstrap_seed {c.get('bootstrap_seed')!r} is not an integer")
    if not (hs.is_int(c.get("bootstrap_resamples")) and c.get("bootstrap_resamples") > 0):
        problems.append(f"bootstrap_resamples {c.get('bootstrap_resamples')!r} is not a positive integer")
    return problems


def test_ho_se_01_single_factor_comparison(sandbox, ctx):
    """HO-SE-01 · §7.1 schema and rules: base 10/20 correct, variant differing
    only in reasoning_mode with 14/20 correct on the same items; exit 0 and
    a sensitivity file with version "1", the base run and its protocol hash,
    and one comparison: factor reasoning_mode, base/variant values, 20 paired
    items, accuracies 0.5 / 0.7, effect 0.2 bracketed by its 95% interval,
    the stated method, and a recorded bootstrap seed and resample count."""
    base_id, _ = _base(sandbox, ctx)
    variant_id, base_value, value = _mode_variant(sandbox, ctx)
    proc, out = _sensitivity(sandbox, base_id, variant_id)
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc) + "\n" + ctx.note
    doc = hs.read_json(out)
    assert doc.get("sensitivity_version") == "1", f"sensitivity_version {doc.get('sensitivity_version')!r}"
    assert doc.get("base_run") == base_id
    assert doc.get("base_protocol_sha256") == ctx.sha256, "base_protocol_sha256 is not the base run's protocol hash"
    comparisons = doc.get("comparisons")
    assert isinstance(comparisons, list) and len(comparisons) == 1, f"comparisons: {comparisons!r}"
    problems = _comparison_problems(comparisons[0], variant_id, MODE_NAMES, base_value, value, 0.5, 0.7)
    assert not problems, "; ".join(problems)


def test_ho_se_02_sensitivity_is_deterministic(sandbox, ctx):
    """HO-SE-02 · §3 "Deterministic" + §7.1 "recorded, so the output is
    reproducible": two invocations over the same runs give identical JSON."""
    base_id, _ = _base(sandbox, ctx)
    variant_id, _, _ = _mode_variant(sandbox, ctx)
    first, out_a = _sensitivity(sandbox, base_id, variant_id, out_name="a.json")
    second, out_b = _sensitivity(sandbox, base_id, variant_id, out_name="b.json")
    assert first.returncode == hs.EXIT_OK, hs.describe(first)
    assert second.returncode == hs.EXIT_OK, hs.describe(second)
    assert hs.read_json(out_a) == hs.read_json(out_b), "two sensitivity runs over the same inputs differ"


def test_ho_se_03_several_variants(sandbox, ctx):
    """HO-SE-03 · §3 `--variant <run_id> [--variant <run_id> ...]` + §7.1:
    two single-factor variants (reasoning_mode at 14/20, temperature at 8/20)
    give one comparison each, with the right factor, values and effects."""
    base_id, _ = _base(sandbox, ctx)
    mode_id, mode_base, mode_value = _mode_variant(sandbox, ctx)
    temp_base, temp_value = _other_temperature(ctx)
    temp_id, _ = _variant(sandbox, ctx, {("sampling", "temperature", "value"): temp_value}, "temp", 8)
    proc, out = _sensitivity(sandbox, base_id, mode_id, temp_id)
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc) + "\n" + ctx.note
    comparisons = hs.read_json(out).get("comparisons") or []
    by_variant = {c.get("variant_run"): c for c in comparisons if isinstance(c, dict)}
    assert set(by_variant) == {mode_id, temp_id}, f"comparisons for {sorted(by_variant)}"
    problems = _comparison_problems(by_variant[mode_id], mode_id, MODE_NAMES, mode_base, mode_value, 0.5, 0.7)
    problems += _comparison_problems(by_variant[temp_id], temp_id, TEMPERATURE_NAMES, temp_base, temp_value,
                                     0.5, 0.4)
    assert not problems, "; ".join(problems)


def test_ho_se_04_two_differing_fields_exit_3(sandbox, ctx):
    """HO-SE-04 · §7.1 "more than one [factor differs]" exits 3 and writes no
    output: a variant changing reasoning_mode and temperature exits 3 and
    writes no sensitivity file."""
    base_id, _ = _base(sandbox, ctx)
    _, mode = _other_mode(ctx)
    _, temperature = _other_temperature(ctx)
    variant_id, _ = _variant(sandbox, ctx, {("reasoning_mode", "value"): mode,
                                            ("sampling", "temperature", "value"): temperature}, "two", 14)
    proc, out = _sensitivity(sandbox, base_id, variant_id)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not out.exists(), "a sensitivity file was written for a variant differing in two fields"


def test_ho_se_05_one_bad_variant_fails_the_command(sandbox, ctx):
    """HO-SE-05 · §7.1 exit 3 and no output: with one valid single-factor
    variant and one variant differing in two factors, the command exits 3
    and writes no sensitivity file."""
    base_id, _ = _base(sandbox, ctx)
    good_id, _, _ = _mode_variant(sandbox, ctx)
    _, mode = _other_mode(ctx)
    _, temperature = _other_temperature(ctx)
    bad_id, _ = _variant(sandbox, ctx, {("reasoning_mode", "value"): mode,
                                        ("sampling", "temperature", "value"): temperature}, "two", 12)
    proc, out = _sensitivity(sandbox, base_id, good_id, bad_id)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not out.exists(), "a sensitivity file was written although one variant differs in two factors"


def test_ho_se_06_different_manifest_exits_3(sandbox, ctx):
    """HO-SE-06 · §7.1 "the manifest differs" exits 3 and writes no output: a
    single-factor variant whose manifest copy differs from the base's."""
    base_id, _ = _base(sandbox, ctx)
    other = hs.fixture_manifest((hs.item_id(i) for i in range(N)), ctx.data, note="another holdout manifest")
    variant_id, _, _ = _mode_variant(sandbox, ctx, manifest_bytes=other)
    proc, out = _sensitivity(sandbox, base_id, variant_id)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not out.exists(), "a sensitivity file was written for runs on different manifests"


def test_ho_se_07_different_tier_exits_3(sandbox, ctx):
    """HO-SE-07 · §7.1 "the tier differs" exits 3 and writes no output: base
    on smoke and a single-factor variant on validation, both with the same
    manifest (whose smoke and validation tiers both hold the items)."""
    both = hs.fixture_manifest((hs.item_id(i) for i in range(N)), ctx.data, tiers=("smoke", "validation"))
    base_id, _ = _base(sandbox, ctx, manifest_bytes=both)
    variant_id, _, _ = _mode_variant(sandbox, ctx, tier="validation", manifest_bytes=both)
    proc, out = _sensitivity(sandbox, base_id, variant_id)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not out.exists(), "a sensitivity file was written for runs on different tiers"


def test_ho_se_08_different_item_set_exits_3(sandbox, ctx):
    """HO-SE-08 · §7.1 "the item set differs" exits 3 and writes no output: a
    single-factor variant with the same manifest copy and tier but one item
    missing from items.jsonl."""
    base_id, base_dir = _base(sandbox, ctx)
    _, value = _other_mode(ctx)
    variant_id, _ = _variant(sandbox, ctx, {("reasoning_mode", "value"): value}, "fewer", 14,
                             lines=hs.pattern_lines(N - 1, range(14), range(14)),
                             manifest_bytes=(base_dir / "manifest.json").read_bytes())
    proc, out = _sensitivity(sandbox, base_id, variant_id)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not out.exists(), "a sensitivity file was written for runs with different item sets"


def test_ho_se_09_identical_protocol_exits_3(sandbox, ctx):
    """HO-SE-09 · §7.1 "zero factors differ" exits 3 and writes no output: a
    second run of the identical protocol."""
    base_id, _ = _base(sandbox, ctx)
    replicate_id, _ = _base(sandbox, ctx, correct=12)
    proc, out = _sensitivity(sandbox, base_id, replicate_id)
    assert proc.returncode == hs.EXIT_PRECONDITION, hs.describe(proc)
    assert not out.exists(), "a sensitivity file was written although no factor differs"


def test_ho_se_10_model_artifact_is_one_factor(sandbox, ctx):
    """HO-SE-10 · §7.1 "model.artifact (file, sha256, quantization together)
    counts as one factor": a variant using another quantization (so its
    artifact file name and hash change with it) gives exit 0 and one
    comparison with factor model.artifact, differing base and variant
    values, and accuracies 0.5 / 0.45."""
    base_id, _ = _base(sandbox, ctx)
    artifact = hs.dig(ctx.data, "model", "artifact") or {}
    base_quant = artifact.get("quantization")
    quant = "Q8_0" if base_quant != "Q8_0" else "Q4_K_M"
    file_name = str(artifact.get("file") or "model.gguf")
    new_file = file_name.replace(str(base_quant), quant) if base_quant and str(base_quant) in file_name \
        else f"holdout-{quant}.gguf"
    changes = {
        ("model", "artifact", "quantization"): quant,
        ("model", "artifact", "file"): new_file,
        ("model", "artifact", "sha256"): hs.sha256_text("holdout variant artifact " + quant),
    }
    variant_id, _ = _variant(sandbox, ctx, changes, "quant", 9)
    proc, out = _sensitivity(sandbox, base_id, variant_id)
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc) + "\n" + ctx.note
    comparisons = hs.read_json(out).get("comparisons") or []
    assert len(comparisons) == 1, f"comparisons: {comparisons!r}"
    problems = _comparison_problems(comparisons[0], variant_id, ARTIFACT_NAMES, ANY, ANY, 0.5, 0.45)
    if hs.loose_equal(comparisons[0].get("base_value"), comparisons[0].get("variant_value")):
        problems.append("base_value and variant_value are equal for a differing model.artifact")
    assert not problems, "; ".join(problems)
