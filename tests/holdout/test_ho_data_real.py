"""Holdout — `slm-eval data` commands on the real pinned dataset (contract v0.3 §3, §5, §5.1).

REQUIRES the pinned upstream dataset: either SLM_EVAL_HOLDOUT_SEED_CACHE (a
populated dataset cache, copied into each test's temporary SLM_EVAL_CACHE)
or SLM_EVAL_HOLDOUT_NETWORK=1 (each test runs `slm-eval data fetch` into its
temporary cache). HO-DR-01 always fetches and so requires the network.
No model or runtime is needed. Every manifest, flagged file and verdicts
file is written inside the test's temporary directories.
"""

from __future__ import annotations

import pytest

import _ho_support as hs

pytestmark = pytest.mark.requires_data

BUILD_TIMEOUT = 1800


def _build(sandbox, out, *extra, cwd=None):
    proc = sandbox.cli("data", "build-manifest", "--out", out, *extra, timeout=BUILD_TIMEOUT, cwd=cwd)
    assert proc.returncode == hs.EXIT_OK, "build-manifest failed\n" + hs.describe(proc)
    return proc


def _pinned_hashes():
    return [f["sha256"] for f in hs.pinned_source().get("files") or [] if isinstance(f, dict)]


def _built_with_flagged(sandbox):
    """Build a manifest with the default flagged location and return (manifest path, flagged path, pairs)."""
    hs.populate_cache(sandbox)
    manifest = sandbox.work / "manifest.json"
    _build(sandbox, manifest)
    flagged = hs.find_flagged_file(sandbox.cache)
    assert flagged is not None, "no flagged file (flagged_version) in the dataset cache after build-manifest"
    pairs = hs.read_json(flagged).get("pairs") or []
    return manifest, flagged, pairs


@pytest.mark.requires_network
def test_ho_dr_01_fetch_stores_files_matching_pinned_hashes(sandbox):
    """HO-DR-01 · §3 `data fetch` + §4 dataset.source: REQUIRES network.
    Fetching into an empty cache exits 0 and leaves, for every pinned file, a
    file in the cache whose SHA-256 equals the pinned hash."""
    proc = sandbox.cli("data", "fetch", timeout=3600)
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc)
    wanted = _pinned_hashes()
    assert wanted, "no pinned dataset files found in the protocol or manifest"
    found = hs.files_by_sha(sandbox.cache, wanted)
    missing = [h for h in wanted if h not in found]
    assert not missing, f"no cached file matches pinned sha256 {missing}"


def test_ho_dr_02_build_manifest_is_byte_identical(sandbox):
    """HO-DR-02 · §3 build-manifest "Deterministic: the same pinned inputs and
    seed always produce a byte-identical manifest": two builds, the second
    from a different working directory, give byte-identical files."""
    hs.populate_cache(sandbox)
    first = sandbox.work / "first.json"
    other_cwd = sandbox.root / "elsewhere"
    other_cwd.mkdir()
    second = sandbox.work / "second.json"
    _build(sandbox, first)
    _build(sandbox, second, cwd=other_cwd)
    assert first.read_bytes() == second.read_bytes(), "two builds from the same pinned inputs differ"


def test_ho_dr_03_built_manifest_satisfies_the_contract(sandbox):
    """HO-DR-03 · §5 schema and invariants, §5 "contains no dataset text":
    the built manifest is well formed, has 500 test items, exclusions on dev
    items only, tiers drawn from non-excluded dev items, a source equal to
    the pinned source, and no cached problem text anywhere in it."""
    hs.populate_cache(sandbox)
    out = sandbox.work / "manifest.json"
    _build(sandbox, out)
    manifest = hs.read_json(out)
    problems = hs.manifest_problems(manifest, strict_verdict_source=False)
    assert not problems, "; ".join(problems[:20])
    test, _ = hs.manifest_roles(manifest)
    assert len(test) == 500, f"{len(test)} test items, expected 500"
    pinned = hs.pinned_source()
    source = manifest.get("source") or {}
    assert source.get("commit") == pinned.get("commit") and source.get("url") == pinned.get("url"), \
        "manifest source differs from the pinned source"
    assert sorted(str(f.get("sha256")) for f in source.get("files") or [] if isinstance(f, dict)) \
        == sorted(_pinned_hashes()), \
        "manifest source files differ from the pinned files"
    leaks = hs.leaked_problems([out.read_text(encoding="utf-8")], hs.cached_records(sandbox.cache))
    assert not leaks, f"{len(leaks)} problem texts appear in the manifest"


def test_ho_dr_04_flagged_file_schema(sandbox):
    """HO-DR-04 · §3 `--flagged-out` + §5.1 flagged schema: the file written to
    `--flagged-out` has flagged_version "1", a 64-hex manifest_inputs_sha256,
    and pairs with exactly pair / id / matched_id / match / similarity, where
    the flagged id is a dev item, match is exact|near and similarity is in
    [0, 1]; it contains no problem text."""
    hs.populate_cache(sandbox)
    out = sandbox.work / "manifest.json"
    flagged_path = sandbox.root / "outside" / "flagged.json"
    flagged_path.parent.mkdir()
    _build(sandbox, out, "--flagged-out", flagged_path)
    assert flagged_path.is_file(), "no flagged file at --flagged-out"
    flagged = hs.read_json(flagged_path)
    problems = []
    if set(flagged) != {"flagged_version", "manifest_inputs_sha256", "pairs"}:
        problems.append(f"top-level keys {sorted(flagged)}")
    if flagged.get("flagged_version") != "1":
        problems.append(f"flagged_version {flagged.get('flagged_version')!r}")
    if not hs.is_hex64(flagged.get("manifest_inputs_sha256")):
        problems.append("manifest_inputs_sha256 is not 64 lowercase hex")
    _, dev = hs.manifest_roles(hs.read_json(out))
    numbers = []
    for pair in flagged.get("pairs") or []:
        if not isinstance(pair, dict) or set(pair) != {"pair", "id", "matched_id", "match", "similarity"}:
            problems.append(f"pair entry keys {sorted(pair) if isinstance(pair, dict) else pair!r}")
            continue
        numbers.append(pair["pair"])
        if not hs.is_int(pair["pair"]):
            problems.append(f"pair number {pair['pair']!r} is not an integer")
        if pair["id"] not in dev:
            problems.append(f"flagged id {pair['id']!r} is not a dev item")
        if pair["match"] not in ("exact", "near"):
            problems.append(f"match {pair['match']!r}")
        if not hs.is_number(pair["similarity"]) or not 0.0 <= pair["similarity"] <= 1.0:
            problems.append(f"similarity {pair['similarity']!r}")
    if len(numbers) != len(set(numbers)):
        problems.append("pair numbers are not unique")
    assert not problems, "; ".join(problems[:20])
    leaks = hs.leaked_problems([flagged_path.read_text(encoding="utf-8")], hs.cached_records(sandbox.cache))
    assert not leaks, f"{len(leaks)} problem texts appear in the flagged file"


def test_ho_dr_05_flagged_file_defaults_to_the_cache(sandbox):
    """HO-DR-05 · §3 build-manifest "The default location is the dataset
    cache, outside the repository": without --flagged-out the flagged file
    is written under SLM_EVAL_CACHE and not into the working directory."""
    hs.populate_cache(sandbox)
    _build(sandbox, sandbox.work / "manifest.json")
    assert hs.find_flagged_file(sandbox.cache) is not None, "no flagged file under SLM_EVAL_CACHE"
    assert hs.find_flagged_file(sandbox.work) is None, "a flagged file was written into the working directory"


def test_ho_dr_06_show_pair_prints_texts_and_writes_nothing(sandbox):
    """HO-DR-06 · §3 `data show-pair`: for flagged pair n it exits 0, prints
    both problem texts to stdout, and writes nothing to the cache, the run
    store or the working directory. Skipped when no pair is flagged."""
    _, flagged, pairs = _built_with_flagged(sandbox)
    if not pairs:
        pytest.skip("the pinned dataset produced no flagged pairs")
    pair = pairs[0]
    before = hs.snapshot(sandbox.cache, sandbox.runs, sandbox.work)
    proc = sandbox.cli("data", "show-pair", "--flagged", flagged, "--pair", pair["pair"])
    after = hs.snapshot(sandbox.cache, sandbox.runs, sandbox.work)
    assert proc.returncode == hs.EXIT_OK, hs.describe(proc)
    assert before == after, "show-pair modified files: " + ", ".join(sorted(set(before) ^ set(after)))[:1000]
    records = hs.records_by_id(hs.cached_records(sandbox.cache))
    shown = hs.normalize_ws(proc.stdout)
    for key in ("id", "matched_id"):
        record = records.get(pair[key])
        if record is not None:
            assert hs.normalize_ws(record["problem"])[:120] in shown, f"problem text of {key} not printed"
    assert len(shown) > 20, "show-pair printed no problem text"


def test_ho_dr_07_apply_verdicts_rebuilds_deterministically(sandbox):
    """HO-DR-07 · §3 `data apply-verdicts` + §5.1 invariants: applying
    maintainer verdicts (alternating exclude / keep per flagged id) exits 0;
    test items are unchanged; every exclusion is a dev item recorded with
    its verdict and verdict_source "maintainer"; excluded ids are in no tier;
    and applying the same verdicts to a byte copy of the original manifest
    gives a byte-identical result."""
    manifest, flagged, pairs = _built_with_flagged(sandbox)
    copy_path = sandbox.work / "manifest-copy.json"
    copy_path.write_bytes(manifest.read_bytes())
    before = hs.read_json(manifest)
    by_id = hs.per_id_verdicts(pairs)
    verdicts = hs.write_json(sandbox.root / "outside" / "verdicts.json", hs.verdicts_doc(flagged, pairs, by_id))
    for target in (manifest, copy_path):
        proc = sandbox.cli("data", "apply-verdicts", "--manifest", target, "--verdicts", verdicts,
                           timeout=BUILD_TIMEOUT)
        assert proc.returncode == hs.EXIT_OK, hs.describe(proc)
    assert manifest.read_bytes() == copy_path.read_bytes(), "apply-verdicts is not deterministic"
    after = hs.read_json(manifest)
    problems = hs.manifest_problems(after, strict_verdict_source=True)
    assert not problems, "; ".join(problems[:20])

    def tests_of(m):
        return sorted((e for e in m["items"] if e.get("role") == "test"), key=lambda e: e["id"])

    assert tests_of(after) == tests_of(before), "test items were altered by apply-verdicts"
    recorded = {e.get("id"): e.get("verdict") for e in after.get("exclusions") or []}
    wrong = {iid: (verdict, recorded.get(iid)) for iid, verdict in by_id.items() if recorded.get(iid) != verdict}
    assert not wrong, f"verdicts not reflected in exclusions (expected, recorded): {dict(list(wrong.items())[:5])}"


def test_ho_dr_08_verdicts_for_another_flagged_file_exit_2(sandbox):
    """HO-DR-08 · §3 apply-verdicts "flagged_sha256 doesn't match the flagged
    file: exits 2", failing "without changing anything": the manifest is
    left byte-identical."""
    manifest, flagged, pairs = _built_with_flagged(sandbox)
    original = manifest.read_bytes()
    doc = hs.verdicts_doc(flagged, pairs, hs.per_id_verdicts(pairs))
    doc["flagged_sha256"] = "0" * 64
    verdicts = hs.write_json(sandbox.root / "outside" / "verdicts.json", doc)
    proc = sandbox.cli("data", "apply-verdicts", "--manifest", manifest, "--verdicts", verdicts,
                       timeout=BUILD_TIMEOUT)
    assert proc.returncode == hs.EXIT_INTEGRITY, hs.describe(proc)
    assert manifest.read_bytes() == original, "the manifest changed although the verdicts were refused"


def test_ho_dr_09_tampered_pinned_file_exits_2_and_writes_nothing(sandbox):
    """HO-DR-09 · §3 build-manifest "If a cached pinned file doesn't match its
    pinned SHA-256, it exits 2 and writes nothing": after one byte is
    appended to a cached pinned file, build-manifest exits 2 and writes
    neither a manifest nor a flagged file."""
    hs.populate_cache(sandbox)
    found = hs.files_by_sha(sandbox.cache, _pinned_hashes())
    assert found, "no cached file matches any pinned hash"
    target = next(iter(found.values()))
    data = target.read_bytes()
    target.unlink()
    target.write_bytes(data + b"\n")
    out = sandbox.work / "manifest.json"
    flagged = sandbox.work / "flagged.json"
    proc = sandbox.cli("data", "build-manifest", "--out", out, "--flagged-out", flagged, timeout=BUILD_TIMEOUT)
    assert proc.returncode == hs.EXIT_INTEGRITY, hs.describe(proc)
    assert not out.exists() and not flagged.exists(), "build-manifest wrote output despite the hash mismatch"
