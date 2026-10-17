"""Visible specification tests — §4 protocol-file structure and provenance scope (contract v0.5; §4 unchanged since v0.3).

Contract-only. Read-only checks of the committed protocol file, located by
SLM_EVAL_TEST_PROTOCOL or as the protocol.yaml inside the repository that
contains the pytest working directory (paths under tests/ or fixtures/ are
ignored unless nothing else is found). No CLI command, network, model or
runtime is needed.

§4: the values shown in the contract are v1 draft defaults and "Tests
must read values from the protocol file under test", so these tests check
structure, types, placeholders and provenance labels, never a contract value.

The file is parsed with PyYAML or ruamel.yaml when importable; a file that
is plain JSON (a YAML subset) is parsed without them; otherwise these tests
are skipped with that reason.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

PROVENANCE = {"source-known-paper", "source-known-blog", "vendor-filled", "inferred", "local-choice"}
NAMED_SAMPLING = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "max_output_tokens", "seed_policy")
REASONING_MODES = ("hybrid", "forced_think", "forced_non_think")
HEX64 = re.compile(r"[0-9a-f]{64}")
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}


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


def protocol_path() -> Path:
    configured = os.environ.get("SLM_EVAL_TEST_PROTOCOL", "").strip()
    if configured:
        return Path(configured).expanduser()
    root = _repo_root()
    if root is None:
        pytest.fail("repository not found from the pytest working directory; set SLM_EVAL_TEST_PROTOCOL",
                    pytrace=False)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        if "protocol.yaml" in filenames:
            found.append(Path(dirpath) / "protocol.yaml")
    preferred = [p for p in found if not TEST_DIRS & set(p.relative_to(root).parts)] or found
    if len(preferred) != 1:
        pytest.fail(f"expected one committed protocol.yaml, found {preferred}; set SLM_EVAL_TEST_PROTOCOL",
                    pytrace=False)
    return preferred[0]


def load_protocol() -> dict:
    text = protocol_path().read_text(encoding="utf-8")
    data = None
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except ImportError:
        try:
            from ruamel.yaml import YAML  # type: ignore

            data = YAML(typ="safe", pure=True).load(text)
        except ImportError:
            try:
                data = json.loads(text)
            except ValueError:
                pytest.skip("no YAML parser importable and the protocol file is not plain JSON")
    assert isinstance(data, dict), "the protocol file does not parse to a mapping"
    return data


def dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def walk(obj, path=()):
    yield path, obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk(value, path + (str(key),))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk(value, path + (index,))


def dotted(path) -> str:
    return ".".join(str(p) for p in path)


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def nonempty_str(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def test_vs_pf_01_sections_and_keys():
    """VS-PF-01 · §4 structure: the protocol has protocol_version and the
    sections model.artifact {file, sha256, quantization}, runtime {build,
    invocation}, prompt {template, system_prompt, few_shot}, reasoning_mode
    {value}, sampling, extraction {id, version}, scorers {primary {id, pin},
    secondary {id, pin}} and dataset.source {url, commit, files}."""
    p = load_protocol()
    required = [
        ("protocol_version",), ("model", "artifact", "file"), ("model", "artifact", "sha256"),
        ("model", "artifact", "quantization"), ("runtime", "build"), ("runtime", "invocation"),
        ("prompt", "template"), ("prompt", "few_shot"), ("reasoning_mode", "value"), ("sampling",),
        ("extraction", "id"), ("extraction", "version"), ("scorers", "primary", "id"), ("scorers", "primary", "pin"),
        ("scorers", "secondary", "id"), ("scorers", "secondary", "pin"), ("dataset", "source", "url"),
        ("dataset", "source", "commit"), ("dataset", "source", "files"),
    ]
    missing = [dotted(path) for path in required if dig(p, *path) is None]
    if not (isinstance(p.get("prompt"), dict) and "system_prompt" in p["prompt"]):
        missing.append("prompt.system_prompt")
    assert not missing, f"protocol lacks {missing}"


def test_vs_pf_02_sampling_fields_are_value_provenance_pairs():
    """VS-PF-02 · §4 sampling: the seven named fields (temperature, top_p,
    top_k, min_p, presence_penalty, max_output_tokens, seed_policy) are
    declared, and every sampling entry, including any other runtime
    parameter declared there, is a mapping with a value and a provenance."""
    sampling = load_protocol().get("sampling")
    assert isinstance(sampling, dict), "sampling is not a mapping"
    missing = [name for name in NAMED_SAMPLING if name not in sampling]
    malformed = [name for name, entry in sampling.items()
                 if not (isinstance(entry, dict) and "value" in entry and "provenance" in entry)]
    assert not missing, f"sampling lacks {missing}"
    assert not malformed, f"sampling entries without {{value, provenance}}: {malformed}"


def test_vs_pf_03_provenance_where_required():
    """VS-PF-03 · §4 provenance scope: a provenance label is present on
    model.artifact, runtime, prompt, reasoning_mode and every sampling
    field."""
    p = load_protocol()
    required = [("model", "artifact"), ("runtime",), ("prompt",), ("reasoning_mode",)]
    required += [("sampling", name) for name in (p.get("sampling") or {})]
    absent = [dotted(path) for path in required if not nonempty_str(dig(p, *path, "provenance"))]
    assert not absent, f"provenance missing on {absent}"


def test_vs_pf_04_provenance_vocabulary():
    """VS-PF-04 · §4 "Each provenance label is one of": every provenance label
    anywhere in the file (including any optional label on extraction,
    scorers or dataset) is one of source-known-paper, source-known-blog,
    vendor-filled, inferred or local-choice."""
    bad = {dotted(path): value for path, value in walk(load_protocol())
           if path and path[-1] == "provenance" and value not in PROVENANCE}
    assert not bad, f"labels outside the §4 set: {bad}"


def test_vs_pf_05_model_artifact_is_a_cache_file_name():
    """VS-PF-05 · §4 model.artifact + §2 "Model files live under models/ in
    this cache": file is a bare file name (no directory part, not absolute,
    no ~), sha256 is 64 lowercase hex, and quantization is a non-empty
    string."""
    artifact = dig(load_protocol(), "model", "artifact") or {}
    name = artifact.get("file")
    assert nonempty_str(name), f"model.artifact.file is {name!r}"
    assert "/" not in name and "\\" not in name and not name.startswith("~") and name not in (".", ".."), (
        f"model.artifact.file {name!r} is not a bare file name under $SLM_EVAL_CACHE/models/")
    assert isinstance(artifact.get("sha256"), str) and HEX64.fullmatch(artifact["sha256"]), \
        "model.artifact.sha256 is not 64 lowercase hex"
    assert nonempty_str(artifact.get("quantization")), "model.artifact.quantization is empty"


def test_vs_pf_06_identified_sections():
    """VS-PF-06 · §4 "extraction, scorers, or dataset ... are identified by
    their id, pin, and hashes": extraction has a non-empty id and a semver
    version; each scorer has a non-empty id and pin; dataset.source has a
    url, a hex commit and a non-empty files list whose entries have a path
    and a 64-hex sha256."""
    p = load_protocol()
    problems = []
    if not nonempty_str(dig(p, "extraction", "id")):
        problems.append("extraction.id")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-+]+)?", str(dig(p, "extraction", "version"))):
        problems.append(f"extraction.version {dig(p, 'extraction', 'version')!r} is not semver")
    for role in ("primary", "secondary"):
        for key in ("id", "pin"):
            if not nonempty_str(dig(p, "scorers", role, key)):
                problems.append(f"scorers.{role}.{key}")
    if not nonempty_str(dig(p, "dataset", "source", "url")):
        problems.append("dataset.source.url")
    if not re.fullmatch(r"[0-9a-f]{7,64}", str(dig(p, "dataset", "source", "commit"))):
        problems.append("dataset.source.commit is not hex")
    files = dig(p, "dataset", "source", "files")
    if not (isinstance(files, list) and files and all(
            isinstance(f, dict) and nonempty_str(f.get("path")) and isinstance(f.get("sha256"), str)
            and HEX64.fullmatch(f["sha256"]) for f in files)):
        problems.append("dataset.source.files is empty or has an entry without path / 64-hex sha256")
    assert not problems, "; ".join(problems)


def test_vs_pf_07_values_are_concrete():
    """VS-PF-07 · §4 placeholders filled: protocol_version is a non-empty
    string; reasoning_mode.value is hybrid, forced_think or forced_non_think;
    sampling.max_output_tokens.value is a positive integer ("declared before
    smoke"); prompt.template is a string containing {problem};
    prompt.system_prompt is null or a string; prompt.few_shot is a
    non-negative integer; and no string value is still a <placeholder>."""
    p = load_protocol()
    problems = []
    if not nonempty_str(p.get("protocol_version")):
        problems.append("protocol_version")
    if dig(p, "reasoning_mode", "value") not in REASONING_MODES:
        problems.append(f"reasoning_mode.value {dig(p, 'reasoning_mode', 'value')!r}")
    max_tokens = dig(p, "sampling", "max_output_tokens", "value")
    if not (is_int(max_tokens) and max_tokens > 0):
        problems.append(f"sampling.max_output_tokens.value {max_tokens!r}")
    template = dig(p, "prompt", "template")
    if not (isinstance(template, str) and "{problem}" in template):
        problems.append("prompt.template has no {problem}")
    system_prompt = dig(p, "prompt", "system_prompt")
    if system_prompt is not None and not isinstance(system_prompt, str):
        problems.append("prompt.system_prompt is neither null nor a string")
    few_shot = dig(p, "prompt", "few_shot")
    if not (is_int(few_shot) and few_shot >= 0):
        problems.append(f"prompt.few_shot {few_shot!r}")
    placeholders = [dotted(path) for path, value in walk(p)
                    if isinstance(value, str) and re.fullmatch(r"<[^<>]+>", value.strip())]
    if placeholders:
        problems.append(f"unfilled placeholders at {placeholders}")
    assert not problems, "; ".join(problems)
