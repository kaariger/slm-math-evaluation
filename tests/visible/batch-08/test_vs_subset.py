"""Visible specification tests — the validation subset and the frozen split manifest (contract v0.8 §3 `data subset`, §5, §5.2).

Contract-only: written before any v0.8 implementation commit was supplied,
with no implementation code seen. Tests use only the `slm-eval` CLI and the
contract's file schemas.

Resources (stated in each docstring):
  hermetic  the `slm-eval` CLI only, on synthetic manifests that hold IDs and
            subjects but no dataset text. Each test has its own temporary
            SLM_EVAL_CACHE, SLM_EVAL_RUNS, SLM_EVAL_SCRATCH and working
            directory.
  repo      the committed split manifest and, once one exists, the committed
            subset file, found by content in the repository that contains the
            pytest working directory, or through SLM_EVAL_TEST_MANIFEST and
            SLM_EVAL_TEST_SUBSET.

Synthetic manifests list each validation tier in `items` order, so the two
possible readings of "manifest order" in §5.2 (the order of `items`, or the
order of the `tiers.validation` list) coincide. The exact seeded draw is
checked independently only for a single-subject tier, where every reading of
§5.2 "within each subject, random.Random(seed).sample" gives the same IDs.

Configuration: SLM_EVAL_TEST_CMD overrides the CLI command (shell-split). No
SLM_EVAL_TEST_* variable is passed to the implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shlex
import shutil
import subprocess
from collections import Counter
from fractions import Fraction
from pathlib import Path

import pytest

TIMEOUT = 300
EXIT_OK, EXIT_PRECONDITION = 0, 3
SKIP_DIRS = {"node_modules", "site-packages", "__pycache__"}
TEST_DIRS = {"tests", "test", "fixtures"}
# SHA-256 of configs/datasets/math500-manifest-v1.json at public commit
# 3d2da1f49a0a217251add9717acea60d669becd7, computed with `shasum -a 256`.
PINNED_MANIFEST_SHA256 = "a10c02e7ac60b563a4d112cad92c2540ce58d6c0a49fa73f844091df66e48c71"
SUBSET_SIZE = 50  # the test-author brief's "50-ID validation subset"; the contract fixes no size
SUBSET_METHOD = "largest-remainder allocation by subject, then a seeded draw within each subject"
SUBSET_KEYS = {"subset_version", "manifest_sha256", "tier", "size", "stratify", "method", "seed", "ids"}
HEX64 = re.compile(r"[0-9a-f]{64}")

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
    """A temporary cache, run store, scratch and working directory for one test; inputs live apart."""

    def __init__(self, root: Path):
        self.root = root
        self.cache, self.runs, self.scratch, self.work = (root / n for n in ("cache", "runs", "scratch", "work"))
        self.inputs = root / "inputs"
        for directory in (self.cache, self.runs, self.scratch, self.work, self.inputs):
            directory.mkdir(parents=True)
        self.vars = {k: v for k, v in os.environ.items() if _passed_on(k)}
        self.vars.update(SLM_EVAL_CACHE=str(self.cache), SLM_EVAL_RUNS=str(self.runs),
                         SLM_EVAL_SCRATCH=str(self.scratch))

    def run(self, *args: object, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(_command() + [str(a) for a in args], cwd=cwd or self.work, env=self.vars,
                              stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT)

    def written(self) -> list[str]:
        """Every path created under the cache, run store, scratch or working directory."""
        return [str(p.relative_to(self.root)) for base in (self.cache, self.runs, self.scratch, self.work)
                for p in sorted(base.rglob("*"))]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _explain(proc: subprocess.CompletedProcess) -> str:
    return (f"command: {proc.args!r}\nexit code: {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def printed(proc: subprocess.CompletedProcess, path: Path, cwd: Path) -> bool:
    """§3: the written path appears as a line of stdout (absolute, or relative to the working directory)."""
    target = path.resolve()
    for line in proc.stdout.splitlines():
        text = line.strip()
        if text and (cwd / Path(text).expanduser()).resolve() == target:
            return True
    return False


# ---------------------------------------------------------------------------
# synthetic manifests (§5) and the §5.2 allocation
# ---------------------------------------------------------------------------


def _slug(subject: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")


def synthetic_manifest(subject_counts: list[tuple[str, int]]) -> tuple[bytes, dict]:
    """A §5 manifest whose validation tier is every dev item, listed in `items` order, subjects interleaved."""
    queues = [[(subject, n) for n in range(count)] for subject, count in subject_counts]
    validation = []
    while any(queues):
        for queue in queues:
            if queue:
                subject, n = queue.pop(0)
                validation.append({"id": f"dev/visible/{_slug(subject)}/{n:03d}.json", "role": "dev",
                                   "content_sha256": sha256_bytes(f"{subject}/{n}".encode()),
                                   "subject": subject, "level": 1 + n % 5})
    smoke = [{"id": f"dev/visible/smoke/{n:03d}.json", "role": "dev",
              "content_sha256": sha256_bytes(f"smoke/{n}".encode()), "subject": "Algebra", "level": 2}
             for n in range(2)]
    tests = [{"id": f"test/visible/{n:03d}.json", "role": "test",
              "content_sha256": sha256_bytes(f"test/{n}".encode()), "subject": "Algebra", "level": 3}
             for n in range(3)]
    doc = {
        "manifest_version": "1",
        "source": {"url": "https://example.invalid/visible-suite-dataset", "commit": "c" * 40,
                   "files": [{"path": "test.jsonl", "sha256": "d" * 64}]},
        "membership_derivation": {"method": "unique_id", "independent_source": None},
        "normalization": {"version": "visible-1", "near_duplicate": {"method": "visible", "threshold": 0.85}},
        "items": tests + smoke + validation,
        "exclusions": [],
        "tiers": {"seed": 7, "smoke": [i["id"] for i in smoke], "validation": [i["id"] for i in validation]},
        "provenance_note": "visible-suite synthetic manifest; IDs and subjects only",
    }
    return (json.dumps(doc, indent=2) + "\n").encode("utf-8"), doc


def largest_remainder(counts: dict[str, int], size: int) -> dict[str, int]:
    """§5.2 allocation, in exact arithmetic: proportional shares, largest remainders first, ties by name."""
    total = sum(counts.values())
    quotas = {s: Fraction(size * n, total) for s, n in counts.items()}
    allocation = {s: q.numerator // q.denominator for s, q in quotas.items()}
    remaining = size - sum(allocation.values())
    for subject in sorted(counts, key=lambda s: (-(quotas[s] - allocation[s]), s))[:remaining]:
        allocation[subject] += 1
    return allocation


def subject_of(manifest: dict) -> dict[str, str]:
    return {item["id"]: item.get("subject") for item in manifest.get("items", [])}


def subset_problems(doc, manifest_bytes: bytes, manifest: dict, *, size: int, seed: int | None) -> list[str]:
    """Departures from the §5.2 schema and rules that hold under every reading of "manifest order"."""
    if not isinstance(doc, dict):
        return ["the subset file is not a JSON object"]
    problems = [f"missing key {k!r}" for k in sorted(SUBSET_KEYS - set(doc))]
    expected = {"subset_version": "1", "manifest_sha256": sha256_bytes(manifest_bytes), "tier": "validation",
                "size": size, "stratify": "subject", "method": SUBSET_METHOD}
    if seed is not None:
        expected["seed"] = seed
    for key, value in expected.items():
        if key in doc and doc[key] != value:
            problems.append(f"{key} is {doc[key]!r}, expected {value!r}")
    if not isinstance(doc.get("seed"), int) or isinstance(doc.get("seed"), bool):
        problems.append(f"seed {doc.get('seed')!r} is not an integer")
    ids = doc.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return problems + ["ids is not a list of strings"]
    tier = list(manifest["tiers"]["validation"])
    if len(ids) != size or len(set(ids)) != len(ids):
        problems.append(f"ids holds {len(ids)} entries ({len(set(ids))} distinct); expected {size} distinct")
    outside = [i for i in ids if i not in set(tier)]
    if outside:
        problems.append(f"{len(outside)} ids are not in the validation tier, e.g. {outside[:3]}")
    subjects = subject_of(manifest)
    want = largest_remainder(Counter(subjects[i] for i in tier), size)
    got = Counter(subjects.get(i) for i in ids)
    if {s: n for s, n in want.items() if n} != dict(got):
        problems.append(f"per-subject counts {dict(sorted(got.items()))} differ from the largest-remainder "
                        f"allocation {dict(sorted((s, n) for s, n in want.items() if n))}")
    item_position = {item["id"]: n for n, item in enumerate(manifest["items"])}
    tier_position = {i: n for n, i in enumerate(tier)}
    in_order = [sorted(ids, key=lambda i: position.get(i, -1)) == ids for position in (item_position, tier_position)]
    if not any(in_order):
        problems.append("ids are listed neither in `items` order nor in `tiers.validation` order")
    return problems


def subset(env: Env, manifest: Path, size: int, seed: int, out: Path, *, cwd: Path | None = None):
    return env.run("data", "subset", "--manifest", manifest, "--tier", "validation", "--size", size,
                   "--stratify", "subject", "--seed", seed, "--out", out, cwd=cwd)


def written_subset(proc: subprocess.CompletedProcess, out: Path) -> bytes:
    assert proc.returncode == EXIT_OK, "data subset failed\n" + _explain(proc)
    assert out.is_file(), "data subset exited 0 without writing --out\n" + _explain(proc)
    return out.read_bytes()


def write_manifest(env: Env, subject_counts: list[tuple[str, int]]) -> tuple[Path, bytes, dict]:
    raw, doc = synthetic_manifest(subject_counts)
    path = env.inputs / "manifest.json"
    path.write_bytes(raw)
    return path, raw, doc


# ---------------------------------------------------------------------------
# tests: `data subset` on synthetic manifests (hermetic)
# ---------------------------------------------------------------------------

SUBJECTS = [("Algebra", 7), ("Geometry", 5), ("Number Theory", 5), ("Prealgebra", 6)]


def test_vs_sb_01_subset_schema_and_written_path(env):
    """VS-SB-01 · hermetic · §3 data subset; written path printed; §5.2 schema and rules.

    A 23-item validation tier over four subjects, size 10, seed 11 → exit 0;
    the `--out` path is printed on stdout; the file has `subset_version` "1",
    the manifest file's SHA-256, tier `validation`, size 10, stratify
    `subject`, the contract's method text, seed 11, and 10 distinct tier ids
    in manifest order; the manifest is unchanged.
    """
    manifest, raw, doc = write_manifest(env, SUBJECTS)
    out = env.work / "subset.json"
    proc = subset(env, manifest, 10, 11, out)
    assert proc.returncode == EXIT_OK, "data subset failed\n" + _explain(proc)
    assert out.is_file(), "data subset exited 0 without writing --out\n" + _explain(proc)
    assert printed(proc, out, env.work), "the written subset path is not on stdout\n" + _explain(proc)
    problems = subset_problems(json.loads(out.read_text(encoding="utf-8")), raw, doc, size=10, seed=11)
    assert not problems, "subset departs from §5.2:\n" + "\n".join(problems)
    assert manifest.read_bytes() == raw, "data subset changed the manifest"


def test_vs_sb_02_subset_is_byte_identical_across_runs(env):
    """VS-SB-02 · hermetic · §3 data subset "Deterministic: the same manifest, size and seed always
    produce a byte-identical file".

    The same manifest, size and seed, run from two working directories with
    two `--out` names → byte-identical files.
    """
    manifest, _, _ = write_manifest(env, SUBJECTS)
    elsewhere = env.work / "elsewhere"
    elsewhere.mkdir()
    first, second = env.work / "subset-a.json", elsewhere / "subset-b.json"
    outputs = [written_subset(subset(env, manifest, 10, 20261027, out, cwd=cwd), out)
               for out, cwd in ((first, env.work), (second, elsewhere))]
    assert outputs[0] == outputs[1], "two subsets from the same inputs differ"


ALLOCATION_CASES = {
    "largest-remainder": (SUBJECTS, 10),
    "tie-goes-to-name-order": ([("Number Theory", 3), ("Counting & Probability", 4), ("Geometry", 3)], 5),
    "all-ties-in-name-order": ([("Precalculus", 1), ("Prealgebra", 1), ("Geometry", 1), ("Algebra", 1)], 2),
}


@pytest.mark.parametrize("case", sorted(ALLOCATION_CASES))
def test_vs_sb_03_allocation_by_largest_remainder_ties_by_name(env, case):
    """VS-SB-03 · hermetic · §5.2 allocation: "each subject's share of size is proportional to its count
    in the tier, rounded by largest remainder. Ties go to subjects in name order."

    Per-subject counts in the subset equal the exact largest-remainder
    allocation. In the tie cases, manifest order and name order disagree, so
    a tie broken by manifest order fails.
    """
    subject_counts, size = ALLOCATION_CASES[case]
    manifest, raw, doc = write_manifest(env, subject_counts)
    out = env.work / "subset.json"
    written = written_subset(subset(env, manifest, size, 5, out), out)
    problems = subset_problems(json.loads(written.decode("utf-8")), raw, doc, size=size, seed=5)
    assert not problems, f"{case}:\n" + "\n".join(problems)


def test_vs_sb_04_single_subject_draw_rebuilds_independently(env):
    """VS-SB-04 · hermetic · §5.2 draw: "within each subject, random.Random(seed).sample over the
    subject's tier IDs in manifest order"; ids "listed in manifest order"; brief objective 7.

    One subject of 12 ids, size 5, seed 20261027 → `ids` equal
    `random.Random(seed).sample(tier_ids, 5)` listed in manifest order. With
    one subject, every reading of the draw gives these ids.
    """
    manifest, _, doc = write_manifest(env, [("Geometry", 12)])
    tier = doc["tiers"]["validation"]
    seed = 20261027
    drawn = set(random.Random(seed).sample(tier, 5))
    expected = [i for i in tier if i in drawn]
    out = env.work / "subset.json"
    got = json.loads(written_subset(subset(env, manifest, 5, seed, out), out).decode("utf-8")).get("ids")
    assert got == expected, f"independent rebuild gives {expected}, the CLI wrote {got}"


def test_vs_sb_05_size_larger_than_the_tier_exits_3(env):
    """VS-SB-05 · hermetic · §3 data subset "A size larger than the tier exits 3"; nothing written.

    A 23-item tier with `--size 24` → exit 3; no `--out` file and nothing
    else written; empty stdout.
    """
    manifest, _, _ = write_manifest(env, SUBJECTS)
    out = env.work / "subset.json"
    proc = subset(env, manifest, 24, 11, out)
    assert proc.returncode == EXIT_PRECONDITION, "expected exit 3\n" + _explain(proc)
    assert env.written() == [], f"a rejected subset wrote: {env.written()}"
    assert proc.stdout.strip() == "", "a failing command printed to stdout\n" + _explain(proc)


# ---------------------------------------------------------------------------
# committed manifest and subset (repo)
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


def _json_files(accept, configured_var: str) -> list[Path]:
    configured = os.environ.get(configured_var, "").strip()
    if configured:
        return [Path(configured).expanduser()]
    root = _repo_root()
    if root is None:
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix != ".json":
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if isinstance(doc, dict) and accept(doc):
                found.append(path)
    return [p for p in found if not TEST_DIRS & set(p.relative_to(root).parts)] or found


def committed_manifests() -> list[Path]:
    return _json_files(lambda d: "manifest_version" in d and "items" in d, "SLM_EVAL_TEST_MANIFEST")


def committed_subsets() -> list[Path]:
    return _json_files(lambda d: "subset_version" in d and "ids" in d, "SLM_EVAL_TEST_SUBSET")


def pinned_manifest() -> tuple[Path, bytes, dict]:
    manifests = committed_manifests()
    if not manifests:
        pytest.skip("no committed split manifest found; set SLM_EVAL_TEST_MANIFEST")
    for path in manifests:
        raw = path.read_bytes()
        if sha256_bytes(raw) == PINNED_MANIFEST_SHA256:
            return path, raw, json.loads(raw.decode("utf-8"))
    hashes = {str(p): sha256_bytes(p.read_bytes()) for p in manifests}
    pytest.fail(f"no committed manifest has the pinned SHA-256 {PINNED_MANIFEST_SHA256}: {hashes}", pytrace=False)


def test_vs_sb_06_pinned_split_manifest_is_byte_identical():
    """VS-SB-06 · repo · §5 "A tier is immutable once it is used"; brief hard invariant "the pinned split
    manifest remains byte-identical".

    A committed split manifest has exactly the SHA-256 recorded at public
    commit 3d2da1f (`configs/datasets/math500-manifest-v1.json`).
    """
    path, _, doc = pinned_manifest()
    assert doc.get("manifest_version") == "1", f"{path} is not a version-1 manifest"


def _committed_subset_docs() -> list[tuple[Path, bytes, dict]]:
    subsets = committed_subsets()
    if not subsets:
        pytest.skip("no committed subset file yet (§5.2); set SLM_EVAL_TEST_SUBSET once one exists")
    return [(p, p.read_bytes(), json.loads(p.read_text(encoding="utf-8"))) for p in subsets]


def test_vs_sb_07_committed_subset_follows_the_rules():
    """VS-SB-07 · repo · §5.2 schema and rules; brief "the 50-ID validation subset references its
    SHA-256" (of the pinned manifest).

    Each committed subset: `manifest_sha256` is the pinned manifest's; size
    50 and 50 distinct ids from its validation tier; per-subject counts equal
    the largest-remainder allocation; ids listed in manifest order (either
    reading); the contract's method text; no other content.
    """
    _, raw, manifest = pinned_manifest()
    failures = {}
    for path, _, doc in _committed_subset_docs():
        problems = subset_problems(doc, raw, manifest, size=SUBSET_SIZE, seed=None)
        extra = sorted(set(doc) - SUBSET_KEYS) if isinstance(doc, dict) else []
        if extra:
            problems.append(f"keys outside §5.2: {extra}")
        if problems:
            failures[str(path)] = problems
    assert not failures, "committed subset departs from §5.2:\n" + json.dumps(failures, indent=2)


def test_vs_sb_08_rebuilding_the_committed_subset_is_byte_identical(env):
    """VS-SB-08 · repo + hermetic · §3 data subset deterministic; brief objective 7 "independently
    rebuilding the validation-subset file yields byte-identical output".

    `data subset` on the pinned manifest with the committed subset's size and
    seed → exit 0 and a file byte-identical to the committed subset.
    """
    manifest_path, _, _ = pinned_manifest()
    for path, committed, doc in _committed_subset_docs():
        out = env.work / f"rebuilt-{path.name}"
        rebuilt = written_subset(subset(env, manifest_path, doc.get("size"), doc.get("seed"), out), out)
        assert rebuilt == committed, f"rebuilt subset differs from the committed {path}"
