"""Pinned MATH-500 data, duplicate review, and deterministic dev tiers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import tempfile
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SOURCE = {
    "url": "https://github.com/openai/prm800k",
    "commit": "7ecc794703b2877f63226f2477a49b34f9b25163",
    "files": [
        {
            "path": "prm800k/math_splits/test.jsonl",
            "sha256": "35dc41080a3680858b27fa7e0533d2d547825316fc5dafe5d316f4ccc5a06132",
        },
        {
            "path": "prm800k/math_splits/train.jsonl",
            "sha256": "90d96daeac3fe343ebb1e22ce93dd99690f75983e957f88de42f87cffe1e8076",
        },
    ],
}
NORMALIZATION = {
    "version": "unicode-nfc-whitespace-v1",
    "near_duplicate": {"method": "character-5-gram-jaccard-v1", "threshold": 0.85},
}
TIER_SEED = 20261006
SMOKE_SIZE = 16
VALIDATION_SIZE = 128
PROVENANCE_NOTE = (
    "The pinned PRM800K split has 7,499 train-origin items; one of the expected "
    "7,500 MATH-train items is absent upstream. Its 4,501 test-origin rows are "
    "outside the dev pool; one test-origin unique_id is duplicated with identical "
    "problem and answer content, although its source solution field differs. "
    "Original-split membership uses the unique_id prefix, per maintainer "
    "ruling. PRM800K republishes MATH problems; upstream licensing and provenance "
    "remain subject to the upstream records."
)


class DataError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def cache_root() -> Path:
    return Path(os.environ.get("SLM_EVAL_CACHE", "~/.cache/slm-eval/data")).expanduser()


def runs_root() -> Path:
    return Path(os.environ.get("SLM_EVAL_RUNS", "~/.cache/slm-eval/runs")).expanduser()


def source_path(file_record: dict[str, str]) -> Path:
    return cache_root() / "prm800k" / SOURCE["commit"] / file_record["path"]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DataError(f"missing file: {path}", 3) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DataError(f"cannot read JSON: {path}: {error}", 3) from error


def verified_source_path(file_record: dict[str, str]) -> Path:
    path = source_path(file_record)
    if not path.is_file():
        raise DataError(f"missing pinned dataset file: {path}", 3)
    if sha256_file(path) != file_record["sha256"]:
        raise DataError(f"pinned dataset SHA-256 mismatch: {path}", 2)
    return path


def fetch() -> list[Path]:
    paths = []
    for file_record in SOURCE["files"]:
        destination = source_path(file_record)
        if destination.exists():
            verified_source_path(file_record)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            url = (
                "https://media.githubusercontent.com/media/openai/prm800k/"
                f"{SOURCE['commit']}/{file_record['path']}"
            )
            try:
                with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    with urllib.request.urlopen(url, timeout=120) as response:
                        while chunk := response.read(1024 * 1024):
                            stream.write(chunk)
                if sha256_file(temporary) != file_record["sha256"]:
                    raise DataError(f"downloaded dataset SHA-256 mismatch: {url}", 2)
                temporary.replace(destination)
            except BaseException:
                if "temporary" in locals():
                    temporary.unlink(missing_ok=True)
                raise
        paths.append(destination)
    return paths


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not all(
                    isinstance(row.get(key), str) for key in ("unique_id", "problem", "answer", "subject")
                ) or type(row.get("level")) is not int:
                    raise ValueError("missing or malformed required field")
                rows.append(row)
            except (ValueError, json.JSONDecodeError) as error:
                raise DataError(f"malformed pinned dataset row: {path}:{line_number}: {error}", 2) from error
    return rows


def load_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    test_path, train_path = (verified_source_path(record) for record in SOURCE["files"])
    test_rows = _read_rows(test_path)
    train_rows = _read_rows(train_path)
    if len(test_rows) != 500 or len(train_rows) != 12000:
        raise DataError("pinned split row count differs from audited source", 2)
    if any(not row["unique_id"].startswith("test/") for row in test_rows):
        raise DataError("pinned test set has a non-test unique_id", 2)
    original_train = [row for row in train_rows if row["unique_id"].startswith("train/")]
    other = [row for row in train_rows if row["unique_id"].startswith("test/")]
    if len(original_train) != 7499 or len(other) != 4501:
        raise DataError("pinned train-origin membership differs from maintainer ruling", 2)
    repeated = Counter(row["unique_id"] for row in other)
    duplicate_ids = [item_id for item_id, count in repeated.items() if count != 1]
    if len(duplicate_ids) != 1 or repeated[duplicate_ids[0]] != 2:
        raise DataError("pinned test-origin duplicate audit differs from maintainer ruling", 2)
    duplicate_rows = [row for row in other if row["unique_id"] == duplicate_ids[0]]
    evaluation_fields = ("unique_id", "problem", "answer", "subject", "level")
    if any(duplicate_rows[0][field] != duplicate_rows[1][field] for field in evaluation_fields):
        raise DataError("duplicated test-origin ID has different evaluation content", 2)
    return test_rows, original_train


def normalize(problem: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", problem)).strip()


def content_hash(problem: str) -> str:
    return sha256_bytes(normalize(problem).encode("utf-8"))


def _grams(problem: str) -> frozenset[str]:
    normalized = normalize(problem)
    if len(normalized) < 5:
        return frozenset((normalized,))
    return frozenset(normalized[index : index + 5] for index in range(len(normalized) - 4))


def _prefix(grams: frozenset[str], frequency: Counter[str], threshold: float) -> list[str]:
    # Any pair above the Jaccard threshold must share a globally ordered prefix gram.
    # This filters candidates without approximating the final similarity check.
    count = len(grams) - math.ceil(threshold * len(grams)) + 1
    return sorted(grams, key=lambda gram: (frequency[gram], gram))[:count]


def flagged_pairs(test_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find all cross-split pairs meeting the versioned Jaccard threshold."""
    test_ids = [row["unique_id"] for row in test_rows]
    dev_ids = [row["unique_id"] for row in dev_rows]
    if len(set(test_ids)) != len(test_ids) or len(set(dev_ids)) != len(dev_ids):
        raise DataError("duplicate unique_id inside a retained split", 2)
    if set(test_ids) & set(dev_ids):
        raise DataError("unique_id occurs in both test and dev", 2)

    test_grams = [_grams(row["problem"]) for row in test_rows]
    dev_grams = [_grams(row["problem"]) for row in dev_rows]
    frequency: Counter[str] = Counter()
    for grams in test_grams + dev_grams:
        frequency.update(grams)
    threshold = NORMALIZATION["near_duplicate"]["threshold"]
    index: dict[str, list[int]] = defaultdict(list)
    for index_number, grams in enumerate(test_grams):
        for gram in _prefix(grams, frequency, threshold):
            index[gram].append(index_number)

    found: list[dict[str, Any]] = []
    for dev_row, dev_set in zip(dev_rows, dev_grams):
        candidate_numbers: set[int] = set()
        for gram in _prefix(dev_set, frequency, threshold):
            candidate_numbers.update(index.get(gram, ()))
        dev_normalized = normalize(dev_row["problem"])
        for number in sorted(candidate_numbers):
            test_normalized = normalize(test_rows[number]["problem"])
            if dev_normalized == test_normalized:
                similarity = 1.0
                match = "exact"
            else:
                overlap = len(dev_set & test_grams[number])
                similarity = overlap / (len(dev_set) + len(test_grams[number]) - overlap)
                if similarity < threshold:
                    continue
                match = "near"
            found.append(
                {
                    "id": dev_row["unique_id"],
                    "matched_id": test_rows[number]["unique_id"],
                    "match": match,
                    "similarity": round(similarity, 6),
                }
            )
    found.sort(key=lambda pair: (pair["id"], pair["matched_id"]))
    return [{"pair": number, **pair} for number, pair in enumerate(found, 1)]


def manifest_inputs_hash() -> str:
    inputs = {
        "source": SOURCE,
        "membership_derivation": "unique_id-prefix-v1",
        "normalization": NORMALIZATION,
        "tiers": {"seed": TIER_SEED, "smoke_size": SMOKE_SIZE, "validation_size": VALIDATION_SIZE},
    }
    return sha256_bytes(canonical_json(inputs))


def make_manifest(
    test_rows: list[dict[str, Any]],
    dev_rows: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    flagged_sha256: str,
    verdicts: dict[int, str] | None = None,
) -> dict[str, Any]:
    verdicts = verdicts or {}
    exclusions = [
        {
            "id": pair["id"],
            "matched_id": pair["matched_id"],
            "match": pair["match"],
            "similarity": pair["similarity"],
            "verdict": verdicts[pair["pair"]],
            "verdict_source": "maintainer",
        }
        for pair in pairs
        if pair["pair"] in verdicts
    ]
    excluded = {entry["id"] for entry in exclusions if entry["verdict"] == "exclude"}
    eligible_ids = sorted(row["unique_id"] for row in dev_rows if row["unique_id"] not in excluded)
    required = SMOKE_SIZE + VALIDATION_SIZE
    if len(eligible_ids) < required:
        raise DataError("not enough eligible dev items to draw tiers", 3)
    draw = random.Random(TIER_SEED).sample(eligible_ids, required)
    items = [
        {
            "id": row["unique_id"],
            "role": role,
            "content_sha256": content_hash(row["problem"]),
            "subject": row["subject"],
            "level": row["level"],
        }
        for role, rows in (("test", test_rows), ("dev", dev_rows))
        for row in rows
    ]
    items.sort(key=lambda item: item["id"])
    return {
        "manifest_version": "1",
        "source": SOURCE,
        "membership_derivation": {"method": "unique_id", "independent_source": None},
        "normalization": NORMALIZATION,
        "items": items,
        "exclusions": exclusions,
        "tiers": {"seed": TIER_SEED, "smoke": draw[:SMOKE_SIZE], "validation": draw[SMOKE_SIZE:]},
        "flagged_sha256": flagged_sha256,
        "flagged_pairs_count": len(pairs),
        "review_status": "complete" if len(verdicts) == len(pairs) else "pending",
        "provenance_note": PROVENANCE_NOTE,
    }


def _outside_repository(path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    if path.resolve().is_relative_to(repository):
        raise DataError("flagged pairs must be stored outside the repository", 3)


def default_flagged_path() -> Path:
    return cache_root() / "flagged" / f"{manifest_inputs_hash()}.json"


def build_manifest(out: Path, flagged_out: Path | None = None) -> list[Path]:
    flagged_out = flagged_out or default_flagged_path()
    _outside_repository(flagged_out)
    test_rows, dev_rows = load_rows()
    pairs = flagged_pairs(test_rows, dev_rows)
    flagged = {"flagged_version": "1", "manifest_inputs_sha256": manifest_inputs_hash(), "pairs": pairs}
    manifest = make_manifest(test_rows, dev_rows, pairs, sha256_bytes(canonical_json(flagged)))
    write_json(flagged_out, flagged)
    write_json(out, manifest)
    return [flagged_out, out]


def show_pair(flagged_path: Path, pair_number: int) -> tuple[str, str]:
    flagged = read_json(flagged_path)
    if flagged.get("manifest_inputs_sha256") != manifest_inputs_hash():
        raise DataError("flagged pair inputs do not match pinned source", 2)
    if not isinstance(flagged.get("pairs"), list):
        raise DataError("malformed flagged file", 3)
    pair = next((pair for pair in flagged["pairs"] if pair.get("pair") == pair_number), None)
    if pair is None:
        raise DataError(f"flagged pair {pair_number} not found", 3)
    test_rows, dev_rows = load_rows()
    rows = {row["unique_id"]: row for row in test_rows + dev_rows}
    try:
        return rows[pair["id"]]["problem"], rows[pair["matched_id"]]["problem"]
    except KeyError as error:
        raise DataError("flagged pair ID not found in pinned source", 2) from error


def _find_flagged(verdicts_path: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    default = default_flagged_path()
    if default.is_file():
        return default
    candidates = []
    for path in verdicts_path.parent.glob("*.json"):
        if path == verdicts_path:
            continue
        try:
            candidate = read_json(path)
        except DataError:
            continue
        if isinstance(candidate, dict) and candidate.get("manifest_inputs_sha256") == manifest_inputs_hash():
            candidates.append(path)
    if len(candidates) != 1:
        raise DataError("cannot locate the flagged file; pass --flagged", 3)
    return candidates[0]


def _used_tier_snapshots() -> list[tuple[str, list[str]]]:
    """Read tier membership from every retained run of this pinned source."""
    snapshots = []
    root = runs_root()
    if not root.exists():
        return snapshots
    for path in root.glob("*/run.json"):
        run = read_json(path)
        run_manifest_path = path.parent / "manifest.json"
        run_manifest = read_json(run_manifest_path)
        if sha256_file(run_manifest_path) != run.get("manifest_sha256"):
            raise DataError(f"run manifest SHA-256 mismatch: {run_manifest_path}", 2)
        tier = run.get("tier")
        if run_manifest.get("source") == SOURCE and tier in ("smoke", "validation"):
            snapshots.append((tier, run_manifest["tiers"][tier]))
    return snapshots


def apply_verdicts(manifest_path: Path, verdicts_path: Path, flagged_path: Path | None = None) -> Path:
    original = read_json(manifest_path)
    if original.get("source") != SOURCE:
        raise DataError("manifest source differs from the pinned source", 2)
    flagged_path = _find_flagged(verdicts_path, flagged_path)
    flagged = read_json(flagged_path)
    if flagged.get("manifest_inputs_sha256") != manifest_inputs_hash():
        raise DataError("flagged inputs differ from pinned source", 2)
    flagged_sha256 = sha256_file(flagged_path)
    if original.get("flagged_sha256") != flagged_sha256:
        raise DataError("flagged file hash differs from manifest", 2)
    verdict_file = read_json(verdicts_path)
    if verdict_file.get("flagged_sha256") != flagged_sha256:
        raise DataError("verdicts flagged_sha256 mismatch", 2)
    pairs = flagged["pairs"]
    if not isinstance(pairs, list):
        raise DataError("malformed flagged file", 3)
    entries = verdict_file.get("verdicts")
    if verdict_file.get("verdicts_version") != "1" or not isinstance(entries, list):
        raise DataError("malformed verdicts file", 3)
    verdicts = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("verdict") not in ("exclude", "keep") or entry.get("pair") in verdicts:
            raise DataError("invalid or repeated verdict", 3)
        verdicts[entry["pair"]] = entry["verdict"]
    if set(verdicts) != {pair["pair"] for pair in pairs}:
        raise DataError("verdicts must cover every flagged pair", 3)
    test_rows, dev_rows = load_rows()
    if pairs != flagged_pairs(test_rows, dev_rows):
        raise DataError("flagged pairs differ from recomputed pinned source", 2)
    updated = make_manifest(test_rows, dev_rows, pairs, flagged_sha256, verdicts)
    for tier, used_ids in _used_tier_snapshots():
        if updated["tiers"][tier] != used_ids:
            raise DataError(f"cannot change already-used {tier} tier", 3)
    write_json(manifest_path, updated)
    return manifest_path
