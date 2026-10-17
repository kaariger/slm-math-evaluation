"""Visible specification tests — §3 data commands, §5 manifest and §5.1 flagged-pair invariants (contract v0.3).

Informed by implementation commit 18a67b75af9f1b1c670faa7291271033d97fefd0
(read-only archive), so these are visible tests by definition. They still
exercise the implementation only through the `slm-eval` CLI and the
contract's file and run-store schemas, with no imports from the package.

Resources, per test (stated in each docstring):
  hermetic   only the CLI; each test has its own temporary SLM_EVAL_CACHE,
             SLM_EVAL_RUNS and working directory
  repo       the committed split manifest (SLM_EVAL_TEST_MANIFEST, or found
             by content inside the repository that contains the pytest
             working directory)
  data       the pinned upstream dataset: SLM_EVAL_TEST_SEED_CACHE (a
             populated dataset cache, copied into the test's own cache; files
             over 256 MiB are symlinked) or SLM_EVAL_TEST_NETWORK=1 (the test
             runs `slm-eval data fetch`); skipped otherwise
  network    SLM_EVAL_TEST_NETWORK=1; skipped otherwise

SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No SLM_EVAL_TEST_*
variable is passed to the implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

CLI_TIMEOUT = 300
DATA_TIMEOUT = 1800
EXIT_OK, EXIT_INTEGRITY, EXIT_PRECONDITION = 0, 2, 3
LARGE_FILE = 256 * 1024 * 1024
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
TEXT_FIELDS = {"problem", "solution", "answer", "question", "text", "prompt"}

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


class Env:
    """A temporary dataset cache, run store and working directory for one test."""

    def __init__(self, root: Path):
        self.root = root
        self.cache = root / "cache"
        self.runs = root / "runs"
        self.work = root / "work"
        for directory in (self.cache, self.runs, self.work):
            directory.mkdir(parents=True)
        self.vars = {k: v for k, v in os.environ.items()
                     if k not in ("SLM_EVAL_CACHE", "SLM_EVAL_RUNS") and not k.startswith("SLM_EVAL_TEST_")}
        self.vars["SLM_EVAL_CACHE"] = str(self.cache)
        self.vars["SLM_EVAL_RUNS"] = str(self.runs)

    def run(self, *args: object, timeout: float = CLI_TIMEOUT, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            _command() + [str(a) for a in args],
            cwd=cwd or self.work, env=self.vars, stdin=subprocess.DEVNULL, capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )

    def files(self) -> dict[str, tuple[int, int]]:
        """Every file under the cache, run store and working directory, with size and mtime."""
        state = {}
        for directory in (self.cache, self.runs, self.work):
            for path in directory.rglob("*"):
                if path.is_file() or path.is_symlink():
                    st = path.lstat()
                    state[str(path)] = (st.st_size, st.st_mtime_ns)
        return state


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _explain(proc: subprocess.CompletedProcess) -> str:
    return (f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path


def printed(proc: subprocess.CompletedProcess, cwd: Path) -> set[Path]:
    out = set()
    for line in proc.stdout.splitlines():
        if line.strip():
            path = Path(line.strip())
            out.add((path if path.is_absolute() else cwd / path).resolve())
    return out


def normalize(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# repository files and the pinned dataset
# ---------------------------------------------------------------------------


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


def committed_manifest() -> Path:
    configured = os.environ.get("SLM_EVAL_TEST_MANIFEST", "").strip()
    if configured:
        return Path(configured).expanduser()
    root = _repo_root()
    if root is None:
        pytest.fail("repository not found from the pytest working directory; set SLM_EVAL_TEST_MANIFEST",
                    pytrace=False)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        if TEST_DIRS & set(Path(dirpath).relative_to(root).parts):
            continue
        for name in filenames:
            if name.endswith(".json"):
                path = Path(dirpath) / name
                try:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, UnicodeDecodeError):
                    continue
                if isinstance(doc, dict) and "manifest_version" in doc and "items" in doc:
                    found.append(path)
    if len(found) != 1:
        pytest.fail(f"expected one committed manifest, found {found}; set SLM_EVAL_TEST_MANIFEST", pytrace=False)
    return found[0]


def populate_cache(env: Env) -> None:
    """Fill the test's dataset cache from SLM_EVAL_TEST_SEED_CACHE, else by `data fetch`; skip if neither."""
    seed = os.environ.get("SLM_EVAL_TEST_SEED_CACHE", "").strip()
    if seed:
        source = Path(seed).expanduser()
        for dirpath, _, filenames in os.walk(source, followlinks=True):
            target = env.cache / Path(dirpath).relative_to(source)
            target.mkdir(parents=True, exist_ok=True)
            for name in filenames:
                real = (Path(dirpath) / name).resolve()
                if real.stat().st_size > LARGE_FILE:
                    os.symlink(real, target / name)
                else:
                    shutil.copy2(real, target / name)
        return
    if os.environ.get("SLM_EVAL_TEST_NETWORK", "").strip() == "1":
        proc = env.run("data", "fetch", timeout=DATA_TIMEOUT)
        assert proc.returncode == EXIT_OK, "data fetch failed\n" + _explain(proc)
        return
    pytest.skip("needs the pinned dataset: set SLM_EVAL_TEST_SEED_CACHE or SLM_EVAL_TEST_NETWORK=1")


def cached_problems(env: Env) -> dict[str, str]:
    """unique_id -> problem text, from JSONL files in the test's dataset cache."""
    problems = {}
    for path in env.cache.rglob("*.jsonl"):
        if path.stat().st_size > LARGE_FILE:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("unique_id"), str) and isinstance(row.get("problem"), str):
                problems.setdefault(row["unique_id"], row["problem"])
    return problems


def build(env: Env, out_name: str = "manifest.json", *extra: object, cwd: Path | None = None):
    out = env.work / out_name
    proc = env.run("data", "build-manifest", "--out", out, *extra, timeout=DATA_TIMEOUT, cwd=cwd)
    assert proc.returncode == EXIT_OK, "build-manifest failed\n" + _explain(proc)
    return out, proc


def flagged_in_cache(env: Env) -> Path:
    found = []
    for path in env.cache.rglob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(doc, dict) and doc.get("flagged_version") == "1":
            found.append(path)
    assert len(found) == 1, f"expected one flagged file in the dataset cache, found {found}"
    return found[0]


def verdicts_file(env: Env, flagged: Path, choose) -> Path:
    pairs = json.loads(flagged.read_text(encoding="utf-8"))["pairs"]
    doc = {"verdicts_version": "1", "flagged_sha256": sha256_file(flagged),
           "verdicts": [{"pair": p["pair"], "verdict": choose(p)} for p in pairs]}
    return write_json(env.root / "review" / "verdicts.json", doc)


def apply(env: Env, manifest: Path, verdicts: Path) -> subprocess.CompletedProcess:
    return env.run("data", "apply-verdicts", "--manifest", manifest, "--verdicts", verdicts, timeout=DATA_TIMEOUT)


def roles(manifest: dict) -> tuple[set[str], set[str]]:
    test = {i["id"] for i in manifest["items"] if i.get("role") == "test"}
    dev = {i["id"] for i in manifest["items"] if i.get("role") == "dev"}
    return test, dev


def invariant_problems(manifest: dict) -> list[str]:
    """§5 schema shape and §5.1 invariants (independent_source is checked separately)."""
    problems = []
    for key in ("manifest_version", "source", "membership_derivation", "normalization", "items", "exclusions",
                "tiers", "provenance_note"):
        if key not in manifest:
            problems.append(f"missing {key}")
    if problems:
        return problems
    if manifest["manifest_version"] != "1":
        problems.append("manifest_version is not '1'")
    ids = [i.get("id") for i in manifest["items"]]
    if len(ids) != len(set(ids)):
        problems.append("item ids are not unique")
    for entry in manifest["items"]:
        if entry.get("role") not in ("test", "dev") or not isinstance(entry.get("subject"), str) \
                or not isinstance(entry.get("level"), int) or isinstance(entry.get("level"), bool) \
                or not (isinstance(entry.get("content_sha256"), str) and len(entry["content_sha256"]) == 64):
            problems.append(f"malformed item {entry.get('id')!r}")
        if TEXT_FIELDS & set(entry):
            problems.append(f"item {entry.get('id')!r} carries text fields")
    test, dev = roles(manifest)
    if len(test) != 500:
        problems.append(f"{len(test)} test items, expected 500")
    excluded = set()
    for entry in manifest["exclusions"]:
        if entry.get("id") not in dev:
            problems.append(f"exclusion {entry.get('id')!r} is not a dev item")
        if entry.get("verdict") not in ("exclude", "keep") or entry.get("verdict_source") != "maintainer" \
                or entry.get("match") not in ("exact", "near"):
            problems.append(f"malformed exclusion {entry!r}"[:200])
        if entry.get("verdict") == "exclude":
            excluded.add(entry.get("id"))
    for tier in ("smoke", "validation"):
        members = manifest["tiers"].get(tier)
        if not isinstance(members, list) or not set(members) <= dev or set(members) & excluded:
            problems.append(f"tier {tier} is not a list of non-excluded dev ids")
    if not isinstance(manifest["tiers"].get("seed"), int):
        problems.append("tiers.seed is not an integer")
    for path, value in _strings(manifest):
        if len(value) > 256 and path != ("provenance_note",):
            problems.append(f"long string at {'.'.join(map(str, path))}")
    return problems


def _strings(obj, path=()):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _strings(value, path + (key,))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _strings(value, path + (index,))


def run_using_tier(env: Env, manifest_bytes: bytes, tier: str) -> Path:
    """A §6.1 run directory (interrupted at start) that has used `tier` of this manifest."""
    run_dir = env.runs / "visible-used-tier-run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_bytes(manifest_bytes)
    protocol = b'{"protocol_version": "visible-fixture"}\n'
    (run_dir / "protocol.yaml").write_bytes(protocol)
    write_json(run_dir / "run.json", {
        "run_id": run_dir.name, "protocol_version": "visible-fixture", "protocol_sha256": sha256_bytes(protocol),
        "manifest_sha256": sha256_bytes(manifest_bytes), "tier": tier, "k": 1, "seeds": [0],
        "model_artifact_sha256": "a" * 64, "runtime_build": "b10412",
        "adapter": {"id": "visible-fixture", "version": "0"}, "extractor": {"id": "visible-fixture", "version": "0"},
        "scorers": [{"id": "prm800k-grader", "pin": "0"}, {"id": "math-verify", "pin": "0"}],
        "executor": "visible-suite fixture", "host": {"os": "Darwin", "arch": "arm64", "memory_gb": 32},
        "attempts": [{"n": 1, "started": "2026-10-06T12:00:00Z", "ended": "2026-10-06T12:00:05Z",
                      "status": "interrupted", "resumed_from": None}],
        "status": "interrupted",
    })
    (run_dir / "items.jsonl").write_text("", encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# hermetic
# ---------------------------------------------------------------------------


def test_vs_dt_01_apply_verdicts_to_foreign_manifest_exits_2(env):
    """VS-DT-01 · hermetic · §3 exit 2 ("a ... manifest check failed") +
    apply-verdicts "Fails without changing anything": a manifest whose
    source is not the pinned upstream source is refused with exit 2 and
    left byte-identical."""
    manifest = write_json(env.work / "manifest.json", {
        "manifest_version": "1",
        "source": {"url": "https://example.invalid/other-dataset", "commit": "0" * 40,
                   "files": [{"path": "test.jsonl", "sha256": "1" * 64}]},
        "membership_derivation": {"method": "unique_id", "independent_source": None},
        "normalization": {"version": "x", "near_duplicate": {"method": "x", "threshold": 0.9}},
        "items": [], "exclusions": [], "tiers": {"seed": 0, "smoke": [], "validation": []},
        "provenance_note": "foreign",
    })
    original = manifest.read_bytes()
    verdicts = write_json(env.work / "verdicts.json",
                          {"verdicts_version": "1", "flagged_sha256": "2" * 64, "verdicts": []})
    proc = apply(env, manifest, verdicts)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)
    assert manifest.read_bytes() == original, "the refused manifest was modified"


def test_vs_dt_02_show_pair_rejects_flagged_file_from_other_inputs(env):
    """VS-DT-02 · hermetic · §5.1 manifest_inputs_sha256 + §3 exit 2 (identity
    check): a flagged file whose manifest_inputs_sha256 does not match the
    pinned inputs makes show-pair exit 2, print nothing to stdout and write
    nothing."""
    flagged = write_json(env.root / "review" / "flagged.json", {
        "flagged_version": "1", "manifest_inputs_sha256": "0" * 64,
        "pairs": [{"pair": 1, "id": "train/algebra/1.json", "matched_id": "test/algebra/1.json",
                   "match": "near", "similarity": 0.9}],
    })
    before = env.files()
    proc = env.run("data", "show-pair", "--flagged", flagged, "--pair", "1")
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)
    assert proc.stdout.strip() == "", "show-pair printed output although it refused the flagged file"
    assert env.files() == before, "show-pair wrote files"


def test_vs_dt_03_build_without_pinned_files_writes_nothing(env):
    """VS-DT-03 · hermetic · §3 build-manifest + exit 3 (missing file): with an
    empty dataset cache, build-manifest with both --out and --flagged-out
    exits 3 and writes neither file."""
    out = env.work / "manifest.json"
    flagged = env.root / "review" / "flagged.json"
    proc = env.run("data", "build-manifest", "--out", out, "--flagged-out", flagged)
    assert proc.returncode == EXIT_PRECONDITION, _explain(proc)
    assert not out.exists() and not flagged.exists(), "build-manifest wrote output without its pinned inputs"


# ---------------------------------------------------------------------------
# committed manifest (repo)
# ---------------------------------------------------------------------------


def test_vs_dt_04_committed_manifest_invariants():
    """VS-DT-04 · repo · §5 schema + §5.1 invariants + §2 no dataset text: the
    committed manifest has every §5 key and version "1"; 500 test items with
    unique ids and well-formed role, content hash, subject and level; any
    exclusions are dev items with a maintainer verdict; both tiers are lists
    of non-excluded dev ids; no item carries a text field and no string but
    provenance_note exceeds 256 characters."""
    manifest = json.loads(committed_manifest().read_text(encoding="utf-8"))
    problems = invariant_problems(manifest)
    assert not problems, "; ".join(problems[:15])


def test_vs_dt_05_committed_manifest_independent_source():
    """VS-DT-05 · repo · §5 membership_derivation as clarified in contract
    v0.4: method is unique_id or content-hash; independent_source is null
    when method is unique_id, and an object with url, revision and a 64-hex
    sha256 when method is content-hash."""
    derivation = json.loads(committed_manifest().read_text(encoding="utf-8")).get("membership_derivation") or {}
    method = derivation.get("method")
    assert method in ("unique_id", "content-hash"), f"method {method!r}"
    assert "independent_source" in derivation, "membership_derivation has no independent_source key"
    independent = derivation["independent_source"]
    if method == "unique_id":
        assert independent is None, f"independent_source is {independent!r}; v0.4 §5 requires null for unique_id"
        return
    assert isinstance(independent, dict), f"independent_source is {independent!r}; v0.4 §5 requires an object"
    assert isinstance(independent.get("url"), str) and independent["url"], "independent_source.url missing"
    assert isinstance(independent.get("revision"), str) and independent["revision"], "independent_source.revision missing"
    sha = independent.get("sha256")
    assert isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), \
        "independent_source.sha256 is not 64 lowercase hex"


# ---------------------------------------------------------------------------
# data commands on the pinned dataset (data / network)
# ---------------------------------------------------------------------------


def test_vs_dt_06_fetch_prints_files_matching_pinned_hashes(env):
    """VS-DT-06 · network + repo · §3 data fetch: fetching into an empty cache
    exits 0 and prints the cached file paths; every printed path is a file
    inside SLM_EVAL_CACHE, and their SHA-256 values are exactly the pinned
    file hashes recorded in the committed manifest's source."""
    if os.environ.get("SLM_EVAL_TEST_NETWORK", "").strip() != "1":
        pytest.skip("needs network access: set SLM_EVAL_TEST_NETWORK=1")
    pinned = {f["sha256"] for f in json.loads(committed_manifest().read_text(encoding="utf-8"))["source"]["files"]}
    proc = env.run("data", "fetch", timeout=DATA_TIMEOUT)
    assert proc.returncode == EXIT_OK, _explain(proc)
    paths = printed(proc, env.work)
    assert paths and all(p.is_file() and p.is_relative_to(env.cache.resolve()) for p in paths), (
        "fetch did not print cached file paths inside SLM_EVAL_CACHE\n" + _explain(proc))
    assert {sha256_file(p) for p in paths} == pinned, "fetched files do not match the pinned hashes"


def test_vs_dt_07_fetch_over_altered_cached_file_exits_2(env):
    """VS-DT-07 · data · §3 data fetch "verifies their SHA-256 ... A mismatch
    exits 2": after one byte is appended to a cached pinned file, fetch
    exits 2."""
    populate_cache(env)
    out, _ = build(env)
    pinned = {f["sha256"] for f in json.loads(out.read_text(encoding="utf-8"))["source"]["files"]}
    target = next(p for p in env.cache.rglob("*") if p.is_file() and not p.is_symlink()
                  and p.stat().st_size <= LARGE_FILE and sha256_file(p) in pinned)
    target.write_bytes(target.read_bytes() + b"\n")
    proc = env.run("data", "fetch", timeout=DATA_TIMEOUT)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)


def test_vs_dt_08_build_prints_paths_and_defaults_flagged_to_cache(env):
    """VS-DT-08 · data · §3 build-manifest "prints each written path" and
    flagged output "default location is the dataset cache": without
    --flagged-out, exit 0; stdout lists the --out manifest and a flagged
    file that lies inside SLM_EVAL_CACHE; nothing lands in the working
    directory except the manifest."""
    populate_cache(env)
    out, proc = build(env)
    paths = printed(proc, env.work)
    assert out.resolve() in paths, "manifest path not printed\n" + _explain(proc)
    flagged = [p for p in paths if p != out.resolve()]
    assert len(flagged) == 1 and flagged[0].is_relative_to(env.cache.resolve()), (
        "expected one printed flagged path inside SLM_EVAL_CACHE\n" + _explain(proc))
    assert json.loads(flagged[0].read_text(encoding="utf-8")).get("flagged_version") == "1"
    assert sorted(p.name for p in env.work.iterdir()) == [out.name], "unexpected files in the working directory"


def test_vs_dt_09_manifest_bytes_do_not_depend_on_flagged_location(env):
    """VS-DT-09 · data · §3 build-manifest "Deterministic ... byte-identical
    manifest": two builds with different --flagged-out locations, run from
    different working directories, give byte-identical manifests."""
    populate_cache(env)
    elsewhere = env.root / "elsewhere"
    elsewhere.mkdir()
    first, _ = build(env, "first.json", "--flagged-out", env.root / "review-a" / "flagged.json")
    second, _ = build(env, "second.json", "--flagged-out", env.root / "review-b" / "pairs.json", cwd=elsewhere)
    assert first.read_bytes() == second.read_bytes(), "manifest bytes differ between builds"


def test_vs_dt_10_built_manifest_and_flagged_file_invariants(env):
    """VS-DT-10 · data · §5 / §5.1 invariants and "Neither file contains
    problem text": the built manifest satisfies the §5 schema and §5.1
    invariants (500 test items, dev-only exclusions, tiers of non-excluded
    dev ids); the flagged file has exactly the §5.1 keys, each pair has
    exactly pair / id / matched_id / match / similarity with a dev id and a
    test matched_id; no cached problem text occurs in either file."""
    populate_cache(env)
    flagged_path = env.root / "review" / "flagged.json"
    out, _ = build(env, "manifest.json", "--flagged-out", flagged_path)
    manifest = json.loads(out.read_text(encoding="utf-8"))
    problems = invariant_problems(manifest)
    flagged = json.loads(flagged_path.read_text(encoding="utf-8"))
    if set(flagged) != {"flagged_version", "manifest_inputs_sha256", "pairs"}:
        problems.append(f"flagged keys {sorted(flagged)}")
    test, dev = roles(manifest)
    for pair in flagged.get("pairs", []):
        if set(pair) != {"pair", "id", "matched_id", "match", "similarity"}:
            problems.append(f"pair keys {sorted(pair)}")
        elif pair["id"] not in dev or pair["matched_id"] not in test or pair["match"] not in ("exact", "near") \
                or not 0.0 <= pair["similarity"] <= 1.0:
            problems.append(f"malformed pair {pair!r}"[:200])
    texts = [normalize(p) for p in cached_problems(env).values() if len(normalize(p)) >= 40]
    for path in (out, flagged_path):
        content = normalize(path.read_text(encoding="utf-8"))
        leaked = sum(1 for text in texts if text[:100] in content)
        if leaked:
            problems.append(f"{leaked} problem texts in {path.name}")
    assert not problems, "; ".join(problems[:15])


def test_vs_dt_11_altered_pinned_file_build_exits_2_writes_nothing(env):
    """VS-DT-11 · data · §3 build-manifest "If a cached pinned file doesn't
    match its pinned SHA-256, it exits 2 and writes nothing": with one byte
    appended to every cached pinned file, build-manifest exits 2 and writes
    neither the manifest nor the flagged file."""
    populate_cache(env)
    reference, _ = build(env, "reference.json", "--flagged-out", env.root / "review" / "reference-flagged.json")
    pinned = {f["sha256"] for f in json.loads(reference.read_text(encoding="utf-8"))["source"]["files"]}
    for path in [p for p in env.cache.rglob("*") if p.is_file() and not p.is_symlink()]:
        if path.stat().st_size <= LARGE_FILE and sha256_file(path) in pinned:
            path.write_bytes(path.read_bytes() + b"\n")
    out = env.work / "manifest.json"
    flagged = env.root / "review" / "flagged.json"
    proc = env.run("data", "build-manifest", "--out", out, "--flagged-out", flagged, timeout=DATA_TIMEOUT)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)
    assert not out.exists() and not flagged.exists(), "build-manifest wrote output despite the hash mismatch"


def test_vs_dt_12_show_pair_prints_both_texts_and_writes_nothing(env):
    """VS-DT-12 · data · §3 data show-pair: for the first flagged pair it
    exits 0, prints both problem texts (dev id and matched test id) to
    stdout, and writes nothing to the cache, run store or working directory.
    Skips if no pair is flagged."""
    populate_cache(env)
    _, _ = build(env)
    flagged = flagged_in_cache(env)
    pairs = json.loads(flagged.read_text(encoding="utf-8"))["pairs"]
    if not pairs:
        pytest.skip("the pinned dataset produced no flagged pairs")
    before = env.files()
    proc = env.run("data", "show-pair", "--flagged", flagged, "--pair", pairs[0]["pair"], timeout=DATA_TIMEOUT)
    assert proc.returncode == EXIT_OK, _explain(proc)
    assert env.files() == before, "show-pair wrote files"
    problems = cached_problems(env)
    shown = normalize(proc.stdout)
    for key in ("id", "matched_id"):
        assert normalize(problems[pairs[0][key]]) in shown, f"problem text of {key} not printed"


def test_vs_dt_13_show_pair_unknown_pair_exits_3(env):
    """VS-DT-13 · data · §3 data show-pair + exit 3 (precondition): a pair
    number absent from a valid flagged file exits 3 and prints nothing to
    stdout."""
    populate_cache(env)
    build(env)
    flagged = flagged_in_cache(env)
    numbers = [p["pair"] for p in json.loads(flagged.read_text(encoding="utf-8"))["pairs"]]
    proc = env.run("data", "show-pair", "--flagged", flagged, "--pair", max(numbers, default=0) + 1000,
                   timeout=DATA_TIMEOUT)
    assert proc.returncode == EXIT_PRECONDITION, _explain(proc)
    assert proc.stdout.strip() == ""


def test_vs_dt_14_keep_verdicts_record_pairs_and_leave_tiers(env):
    """VS-DT-14 · data · §3 apply-verdicts + §5 exclusions: "keep" for every
    flagged pair exits 0 and prints the manifest path; every pair is
    recorded in exclusions with verdict keep and verdict_source maintainer;
    test items and both tiers are unchanged."""
    populate_cache(env)
    out, _ = build(env)
    before = json.loads(out.read_text(encoding="utf-8"))
    flagged = flagged_in_cache(env)
    pairs = json.loads(flagged.read_text(encoding="utf-8"))["pairs"]
    proc = apply(env, out, verdicts_file(env, flagged, lambda pair: "keep"))
    assert proc.returncode == EXIT_OK, _explain(proc)
    assert out.resolve() in printed(proc, env.work), "manifest path not printed\n" + _explain(proc)
    after = json.loads(out.read_text(encoding="utf-8"))
    recorded = {(e["id"], e["matched_id"], e["verdict"], e["verdict_source"]) for e in after["exclusions"]}
    assert recorded == {(p["id"], p["matched_id"], "keep", "maintainer") for p in pairs}
    assert after["tiers"] == before["tiers"], "keep verdicts changed the tiers"
    tests = lambda m: sorted((i for i in m["items"] if i["role"] == "test"), key=lambda i: i["id"])  # noqa: E731
    assert tests(after) == tests(before), "test items changed"


def test_vs_dt_15_exclude_verdicts_rebuild_tiers_deterministically(env):
    """VS-DT-15 · data · §3 apply-verdicts "deterministically rebuilds the
    exclusions and tiers" + §5.1 invariants: "exclude" for every flagged pair
    applied to a manifest and to a byte copy of it gives byte-identical
    results; every flagged dev id is excluded and absent from both tiers;
    test items are unchanged; the result satisfies the §5 / §5.1 checks."""
    populate_cache(env)
    out, _ = build(env)
    twin = env.work / "manifest-twin.json"
    twin.write_bytes(out.read_bytes())
    before = json.loads(out.read_text(encoding="utf-8"))
    flagged = flagged_in_cache(env)
    pairs = json.loads(flagged.read_text(encoding="utf-8"))["pairs"]
    verdicts = verdicts_file(env, flagged, lambda pair: "exclude")
    for target in (out, twin):
        proc = apply(env, target, verdicts)
        assert proc.returncode == EXIT_OK, _explain(proc)
    assert out.read_bytes() == twin.read_bytes(), "apply-verdicts is not deterministic"
    after = json.loads(out.read_text(encoding="utf-8"))
    excluded = {p["id"] for p in pairs}
    tiers = set(after["tiers"]["smoke"]) | set(after["tiers"]["validation"])
    assert not excluded & tiers, "an excluded id is still in a tier"
    assert {e["id"] for e in after["exclusions"] if e["verdict"] == "exclude"} == excluded
    tests = lambda m: sorted((i for i in m["items"] if i["role"] == "test"), key=lambda i: i["id"])  # noqa: E731
    assert tests(after) == tests(before), "test items changed"
    problems = invariant_problems(after)
    assert not problems, "; ".join(problems[:15])


def test_vs_dt_16_verdicts_for_other_flagged_content_exit_2(env):
    """VS-DT-16 · data · §3 apply-verdicts "flagged_sha256 doesn't match the
    flagged file: exits 2", failing without changing anything."""
    populate_cache(env)
    out, _ = build(env)
    original = out.read_bytes()
    verdicts = verdicts_file(env, flagged_in_cache(env), lambda pair: "exclude")
    doc = json.loads(verdicts.read_text(encoding="utf-8"))
    doc["flagged_sha256"] = sha256_bytes(b"some other flagged file")
    write_json(verdicts, doc)
    proc = apply(env, out, verdicts)
    assert proc.returncode == EXIT_INTEGRITY, _explain(proc)
    assert out.read_bytes() == original, "the manifest changed although the verdicts were refused"


def test_vs_dt_17_tier_used_by_an_existing_run_is_not_changed(env):
    """VS-DT-17 · data · §3 apply-verdicts "the result would change a tier that
    any existing run has already used: exits 3" + §5.1 "A tier is immutable
    once it is used": with a run directory in SLM_EVAL_RUNS whose
    manifest.json copy is this manifest and whose tier is smoke, exclude
    verdicts either exit 3 leaving the manifest byte-identical or exit 0
    leaving the smoke tier unchanged. Skips if no pair is flagged."""
    populate_cache(env)
    out, _ = build(env)
    flagged = flagged_in_cache(env)
    if not json.loads(flagged.read_text(encoding="utf-8"))["pairs"]:
        pytest.skip("the pinned dataset produced no flagged pairs")
    original = out.read_bytes()
    run_using_tier(env, original, "smoke")
    proc = apply(env, out, verdicts_file(env, flagged, lambda pair: "exclude"))
    if proc.returncode == EXIT_PRECONDITION:
        assert out.read_bytes() == original, "apply-verdicts exited 3 but changed the manifest"
    else:
        assert proc.returncode == EXIT_OK, _explain(proc)
        assert json.loads(out.read_text(encoding="utf-8"))["tiers"]["smoke"] == \
            json.loads(original.decode("utf-8"))["tiers"]["smoke"], "a used smoke tier was changed"


def test_vs_dt_18_incomplete_verdicts_are_refused(env):
    """VS-DT-18 · data · §3 apply-verdicts / §5.1 verdicts (implementation-
    informed: the commit requires one verdict per flagged pair): a verdicts
    file omitting one flagged pair is refused with exit 3 and the manifest
    is left byte-identical. Skips if no pair is flagged."""
    populate_cache(env)
    out, _ = build(env)
    flagged = flagged_in_cache(env)
    if not json.loads(flagged.read_text(encoding="utf-8"))["pairs"]:
        pytest.skip("the pinned dataset produced no flagged pairs")
    original = out.read_bytes()
    verdicts = verdicts_file(env, flagged, lambda pair: "keep")
    doc = json.loads(verdicts.read_text(encoding="utf-8"))
    doc["verdicts"] = doc["verdicts"][1:]
    write_json(verdicts, doc)
    proc = apply(env, out, verdicts)
    assert proc.returncode == EXIT_PRECONDITION, _explain(proc)
    assert out.read_bytes() == original, "the manifest changed although the verdicts were refused"


def test_vs_dt_19_committed_manifest_is_reproducible(env):
    """VS-DT-19 · data + repo · §3 build-manifest deterministic + §5
    committed manifest: while the committed manifest records no verdicts
    (empty exclusions), a fresh build from the pinned inputs is byte-identical
    to it. Skips once verdicts have been applied."""
    committed = committed_manifest()
    if json.loads(committed.read_text(encoding="utf-8")).get("exclusions"):
        pytest.skip("the committed manifest carries maintainer verdicts; a fresh build cannot reproduce them")
    populate_cache(env)
    out, _ = build(env)
    assert out.read_bytes() == committed.read_bytes(), f"a fresh build differs from {committed}"
