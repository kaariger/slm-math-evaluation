"""Behavioral checks for pinned-data integrity and cross-split review."""

import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from slm_math_evaluation import data


def row(item_id: str, problem: str) -> dict:
    return {
        "unique_id": item_id,
        "problem": problem,
        "answer": "0",
        "subject": "Algebra",
        "level": 1,
    }


class DuplicateChecks(unittest.TestCase):
    def test_normalized_exact_and_near_are_flagged_without_problem_text(self) -> None:
        tests = [row("test/a/1.json", "A  long\nmathematics problem with x^2 + y^2."),
                 row("test/a/2.json", "Completely unrelated exercise.")]
        dev = [row("train/a/1.json", "A long mathematics problem with x^2 + y^2."),
               row("train/a/2.json", "A long mathematics problem with x^2 + y^3."),
               row("train/a/3.json", "Another unrelated exercise.")]
        pairs = data.flagged_pairs(tests, dev)
        self.assertEqual([(pair["id"], pair["match"]) for pair in pairs], [
            ("train/a/1.json", "exact"), ("train/a/2.json", "near")
        ])
        self.assertTrue(all("problem" not in pair for pair in pairs))

    def test_prefix_filter_matches_exhaustive_jaccard(self) -> None:
        generator = random.Random(4500)
        tests = [row(f"test/a/{i}.json", "".join(generator.choices("abcd efgh", k=80))) for i in range(25)]
        dev = []
        for i in range(25):
            original = tests[i]["problem"]
            position = generator.randrange(10, 70)
            changed = original[:position] + "Z" + original[position + 1:]
            dev.append(row(f"train/a/{i}.json", changed))
        actual = {(pair["id"], pair["matched_id"]) for pair in data.flagged_pairs(tests, dev)}
        expected = set()
        for left in dev:
            left_grams = data._grams(left["problem"])
            for right in tests:
                right_grams = data._grams(right["problem"])
                overlap = len(left_grams & right_grams)
                similarity = overlap / len(left_grams | right_grams)
                if similarity >= data.NORMALIZATION["near_duplicate"]["threshold"]:
                    expected.add((left["unique_id"], right["unique_id"]))
        self.assertEqual(actual, expected)

    def test_duplicate_retained_id_is_integrity_error(self) -> None:
        with self.assertRaises(data.DataError) as result:
            data.flagged_pairs([row("test/a/1.json", "first"), row("test/a/1.json", "second")], [])
        self.assertEqual(result.exception.code, 2)


class ManifestChecks(unittest.TestCase):
    def test_build_is_byte_identical_and_keeps_flagged_outside_repo(self) -> None:
        tests = [row("test/a/1.json", "Long problem about apples and pears 123")]
        dev = [row(f"train/a/{i}.json", f"Independent problem number {i} and quantity {i * 3}") for i in range(160)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            flagged = root / "flagged.json"
            with mock.patch.object(data, "load_rows", return_value=(tests, dev)):
                data.build_manifest(manifest, flagged)
                first = manifest.read_bytes()
                data.build_manifest(manifest, flagged)
            self.assertEqual(manifest.read_bytes(), first)
            parsed = json.loads(first)
            self.assertEqual(len(parsed["tiers"]["smoke"]), data.SMOKE_SIZE)
            self.assertEqual(len(parsed["tiers"]["validation"]), data.VALIDATION_SIZE)
            self.assertFalse(set(parsed["tiers"]["smoke"]) & set(parsed["tiers"]["validation"]))

    def test_bad_cached_file_is_integrity_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(data, "cache_root", return_value=Path(directory)):
                path = data.source_path(data.SOURCE["files"][0])
                path.parent.mkdir(parents=True)
                path.write_bytes(b"tampered")
                with self.assertRaises(data.DataError) as result:
                    data.verified_source_path(data.SOURCE["files"][0])
        self.assertEqual(result.exception.code, 2)

    def test_verdict_hash_and_used_tier_guard_leave_manifest_unchanged(self) -> None:
        dev = [row(f"train/a/{i}.json", f"Distinct exercise {i}: {i * 17} + {i * 23} = ?") for i in range(8)]
        with mock.patch.object(data, "SMOKE_SIZE", 1), mock.patch.object(data, "VALIDATION_SIZE", 1):
            chosen = random.Random(data.TIER_SEED).sample(sorted(item["unique_id"] for item in dev), 2)[0]
            test_problem = next(item["problem"] for item in dev if item["unique_id"] == chosen)
            tests = [row("test/a/1.json", test_problem)]
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = root / "manifest.json"
                flagged = root / "flagged.json"
                verdicts_path = root / "verdicts.json"
                with mock.patch.object(data, "load_rows", return_value=(tests, dev)):
                    data.build_manifest(manifest, flagged)
                    original = manifest.read_bytes()
                    pair_number = json.loads(flagged.read_text())["pairs"][0]["pair"]
                    verdicts = {"verdicts_version": "1", "flagged_sha256": "0" * 64,
                                "verdicts": [{"pair": pair_number, "verdict": "exclude"}]}
                    data.write_json(verdicts_path, verdicts)
                    with self.assertRaises(data.DataError) as result:
                        data.apply_verdicts(manifest, verdicts_path, flagged)
                    self.assertEqual(result.exception.code, 2)
                    self.assertEqual(manifest.read_bytes(), original)

                    verdicts["flagged_sha256"] = data.sha256_file(flagged)
                    data.write_json(verdicts_path, verdicts)
                    used_smoke = json.loads(original)["tiers"]["smoke"]
                    with mock.patch.object(data, "_used_tier_snapshots", return_value=[("smoke", used_smoke)]):
                        with self.assertRaises(data.DataError) as result:
                            data.apply_verdicts(manifest, verdicts_path, flagged)
                    self.assertEqual(result.exception.code, 3)
                    self.assertEqual(manifest.read_bytes(), original)

                    with mock.patch.object(data, "_used_tier_snapshots", return_value=[]):
                        data.apply_verdicts(manifest, verdicts_path, flagged)
                    updated = json.loads(manifest.read_text())
                    self.assertNotEqual(updated["tiers"]["smoke"], json.loads(original)["tiers"]["smoke"])
                    self.assertEqual(updated["exclusions"][0]["verdict"], "exclude")


if __name__ == "__main__":
    unittest.main()
