"""Holdout — committed protocol and split manifest schemas (contract v0.3 §4, §5).

Read-only checks of the two contract files the repository commits. The files
are located through SLM_EVAL_HOLDOUT_PROTOCOL / SLM_EVAL_HOLDOUT_MANIFEST, or
discovered inside the repository (SLM_EVAL_HOLDOUT_REPO or the pytest working
directory). No CLI command, network, model or runtime is needed.

§4 (v0.3): the values shown in the contract are draft defaults and the
approved protocol file is authoritative, so these tests check structure,
types and provenance labels only, never the contract's example values.
"""

from __future__ import annotations

import re

import _ho_support as hs

SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-+]+)?")
COMMIT = re.compile(r"[0-9a-f]{7,64}")


def _protocol():
    _, _, data = hs.committed_protocol()
    return data


def _manifest():
    return hs.read_json(hs.manifest_path())


def test_ho_pf_01_protocol_structure():
    """HO-PF-01 · §4 protocol file: the committed protocol has every section
    and key the contract lists (model.artifact, runtime, prompt,
    reasoning_mode, sampling with all seven fields, extraction, scorers,
    dataset.source with files)."""
    p = _protocol()
    problems = []
    required = {
        ("protocol_version",): str,
        ("model", "artifact", "file"): str,
        ("model", "artifact", "sha256"): str,
        ("model", "artifact", "quantization"): str,
        ("runtime", "build"): str,
        ("runtime", "invocation"): str,
        ("prompt", "template"): str,
        ("prompt", "few_shot"): int,
        ("reasoning_mode", "value"): str,
        ("extraction", "id"): str,
        ("extraction", "version"): str,
        ("scorers", "primary", "id"): str,
        ("scorers", "primary", "pin"): str,
        ("scorers", "secondary", "id"): str,
        ("scorers", "secondary", "pin"): str,
        ("dataset", "source", "url"): str,
        ("dataset", "source", "commit"): str,
        ("dataset", "source", "files"): list,
    }
    for path, kind in required.items():
        value = hs.dig(p, *path)
        if not isinstance(value, kind) or isinstance(value, bool):
            problems.append(f"{'.'.join(path)} is {value!r}, expected {kind.__name__}")
    if not isinstance(p.get("prompt"), dict) or "system_prompt" not in p["prompt"]:
        problems.append("prompt.system_prompt is not declared")
    for name in hs.SAMPLING_FIELDS:
        entry = hs.dig(p, "sampling", name)
        if not isinstance(entry, dict) or "value" not in entry:
            problems.append(f"sampling.{name} is not a {{value, provenance}} mapping")
    for entry in hs.dig(p, "dataset", "source", "files", default=[]) or []:
        if not (isinstance(entry, dict) and isinstance(entry.get("path"), str) and "sha256" in entry):
            problems.append(f"dataset.source.files entry {entry!r} lacks path/sha256")
    assert not problems, "; ".join(problems)


def test_ho_pf_02_protocol_provenance():
    """HO-PF-02 · §4 provenance scope: model.artifact, runtime, prompt,
    reasoning_mode and every sampling field (including any extra runtime
    parameter declared there) carry a provenance label, and every label in
    the file is one of the five allowed values."""
    p = _protocol()
    problems = []
    for path in (("model", "artifact"), ("runtime",), ("prompt",), ("reasoning_mode",)):
        if not isinstance(hs.dig(p, *path), dict) or "provenance" not in hs.dig(p, *path):
            problems.append(f"{'.'.join(path)} has no provenance")
    for name, entry in (p.get("sampling") or {}).items():
        if not isinstance(entry, dict) or "provenance" not in entry:
            problems.append(f"sampling.{name} has no provenance")
    for path, value in hs.walk(p):
        if path and path[-1] == "provenance" and value not in hs.PROVENANCE_VALUES:
            problems.append(f"{'.'.join(map(str, path))} = {value!r} is not an allowed provenance")
    assert not problems, "; ".join(problems)


def test_ho_pf_03_protocol_values_are_concrete():
    """HO-PF-03 · §4 placeholders filled: model sha256 and dataset file
    sha256 are 64 lowercase hex, max_output_tokens is a positive integer,
    reasoning_mode is hybrid|forced_think|forced_non_think, the template
    contains {problem}, extraction.version is semver, both scorer pins are
    non-empty, and dataset.source.commit is hex."""
    p = _protocol()
    problems = []
    if not hs.is_hex64(hs.dig(p, "model", "artifact", "sha256")):
        problems.append("model.artifact.sha256 is not 64 lowercase hex")
    max_tokens = hs.dig(p, "sampling", "max_output_tokens", "value")
    if not (hs.is_int(max_tokens) and max_tokens > 0):
        problems.append(f"sampling.max_output_tokens.value {max_tokens!r} is not a positive integer")
    if hs.dig(p, "reasoning_mode", "value") not in hs.REASONING_MODES:
        problems.append(f"reasoning_mode.value {hs.dig(p, 'reasoning_mode', 'value')!r} not allowed")
    if "{problem}" not in str(hs.dig(p, "prompt", "template")):
        problems.append("prompt.template has no {problem} placeholder")
    if not SEMVER.fullmatch(str(hs.dig(p, "extraction", "version"))):
        problems.append(f"extraction.version {hs.dig(p, 'extraction', 'version')!r} is not semver")
    for role in ("primary", "secondary"):
        pin = hs.dig(p, "scorers", role, "pin")
        if not (isinstance(pin, str) and pin.strip() and not pin.startswith("<")):
            problems.append(f"scorers.{role}.pin {pin!r} is not a concrete pin")
    if not COMMIT.fullmatch(str(hs.dig(p, "dataset", "source", "commit"))):
        problems.append("dataset.source.commit is not hex")
    files = hs.dig(p, "dataset", "source", "files", default=[]) or []
    if not files or not all(isinstance(f, dict) and hs.is_hex64(f.get("sha256")) for f in files):
        problems.append("dataset.source.files is empty or has a non-hex sha256")
    assert not problems, "; ".join(problems)


def test_ho_mf_01_manifest_schema_and_invariants():
    """HO-MF-01 · §5 schema + §5.1 invariants: the committed manifest has
    every top-level key, version "1", well-formed items, exclusions and
    tiers; exclusions name dev items only (never test items) and carry
    verdict_source "maintainer"; tiers hold dev ids only and no excluded id."""
    problems = hs.manifest_problems(_manifest(), strict_verdict_source=True)
    assert not problems, "; ".join(problems[:20])


def test_ho_mf_02_manifest_has_the_500_test_items():
    """HO-MF-02 · §5 items, MATH-500 scope: exactly 500 items have role
    test, with unique ids."""
    test, _ = hs.manifest_roles(_manifest())
    assert len(test) == 500, f"{len(test)} test items, expected 500"


def test_ho_mf_03_manifest_source_is_the_pinned_source():
    """HO-MF-03 · §5 source + §4 dataset.source + §3 run ("Exits 2 if the
    manifest source differs from the protocol's dataset.source"): the
    committed manifest's source names the same url, commit and files (path,
    sha256) as the committed protocol."""
    manifest_source = _manifest().get("source") or {}
    protocol_source = hs.dig(_protocol(), "dataset", "source") or {}

    def files(source):
        return sorted((str(f.get("path")), str(f.get("sha256"))) for f in source.get("files") or [] if isinstance(f, dict))

    assert manifest_source.get("url") == protocol_source.get("url"), "source.url differs from the protocol"
    assert manifest_source.get("commit") == protocol_source.get("commit"), "source.commit differs from the protocol"
    assert files(manifest_source) == files(protocol_source), "source.files differ from the protocol"


def test_ho_mf_04_manifest_derivation_and_normalization():
    """HO-MF-04 · §5 membership_derivation and normalization: the method is
    unique_id or content-hash, the independent source has url, revision and
    a 64-hex sha256, normalization has a version and a near-duplicate method
    with a numeric threshold, and provenance_note is non-empty."""
    m = _manifest()
    problems = []
    if hs.dig(m, "membership_derivation", "method") not in ("unique_id", "content-hash"):
        problems.append(f"membership_derivation.method {hs.dig(m, 'membership_derivation', 'method')!r}")
    independent = hs.dig(m, "membership_derivation", "independent_source") or {}
    for key in ("url", "revision"):
        if not isinstance(independent.get(key), str) or not independent.get(key):
            problems.append(f"independent_source.{key} missing")
    if not hs.is_hex64(independent.get("sha256")):
        problems.append("independent_source.sha256 is not 64 lowercase hex")
    if not isinstance(hs.dig(m, "normalization", "version"), str):
        problems.append("normalization.version missing")
    if not isinstance(hs.dig(m, "normalization", "near_duplicate", "method"), str):
        problems.append("normalization.near_duplicate.method missing")
    if not hs.is_number(hs.dig(m, "normalization", "near_duplicate", "threshold")):
        problems.append("normalization.near_duplicate.threshold is not a number")
    if not (isinstance(m.get("provenance_note"), str) and m["provenance_note"].strip()):
        problems.append("provenance_note is empty")
    assert not problems, "; ".join(problems)


def test_ho_mf_05_manifest_contains_no_dataset_text():
    """HO-MF-05 · §5 "contains no dataset text" + §2: no item or exclusion
    carries a text field (problem, solution, answer, ...), and no string
    other than provenance_note is longer than 256 characters."""
    m = _manifest()
    problems = []
    for section in ("items", "exclusions"):
        for entry in m.get(section) or []:
            if isinstance(entry, dict) and hs.TEXT_FIELD_NAMES & set(entry):
                problems.append(f"{section} entry {entry.get('id')!r} has {sorted(hs.TEXT_FIELD_NAMES & set(entry))}")
    for path, value in hs.long_strings(m, 256, skip_keys=("provenance_note",)):
        problems.append(f"long string at {'.'.join(map(str, path))} ({len(value)} chars)")
    assert not problems, "; ".join(problems[:20])
