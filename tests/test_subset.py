"""Deterministic, ID-only validation-subset command fixtures."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from slm_math_evaluation import cli, data


def _manifest(path: Path, subjects: list[tuple[str, str]]) -> None:
    path.write_bytes(data.canonical_json({
        "items": [{"id": item_id, "role": "dev", "subject": subject}
                  for item_id, subject in subjects],
        "tiers": {"validation": [item_id for item_id, _ in subjects]},
    }))


class SubsetTests(unittest.TestCase):
    def test_cli_fresh_generator_per_subject_and_tier_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            _manifest(manifest, [(f"{subject}{index}", subject)
                                 for index in range(4) for subject in ("A", "B")])
            first, second = root / "first.json", root / "second.json"
            for out in (first, second):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    status = cli.main(["data", "subset", "--manifest", str(manifest),
                                       "--tier", "validation", "--size", "4",
                                       "--stratify", "subject", "--seed", "17",
                                       "--out", str(out)])
                self.assertEqual(status, 0)
                self.assertEqual(stdout.getvalue(), str(out) + "\n")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            subset = json.loads(first.read_bytes())
            self.assertEqual(subset["ids"], ["A1", "B1", "A3", "B3"])
            self.assertEqual(subset["manifest_sha256"], data.sha256_file(manifest))
            self.assertEqual(subset["size"], 4)
            self.assertEqual(set(subset), {"subset_version", "manifest_sha256", "tier",
                                           "size", "stratify", "method", "seed", "ids"})
            self.assertNotIn(b"problem", first.read_bytes())

    def test_largest_remainder_tie_goes_to_subject_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, out = root / "manifest.json", root / "subset.json"
            _manifest(manifest, [("A0", "A"), ("B0", "B"), ("C0", "C"),
                                 ("A1", "A"), ("B1", "B")])
            data.build_subset(manifest, "validation", 1, "subject", 17, out)
            self.assertEqual(json.loads(out.read_bytes())["ids"], ["A1"])

    def test_rejects_oversize_and_preserves_existing_subset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, out = root / "manifest.json", root / "subset.json"
            _manifest(manifest, [("A0", "A"), ("B0", "B")])
            with self.assertRaises(data.DataError) as caught:
                data.build_subset(manifest, "validation", 3, "subject", 1, out)
            self.assertEqual(caught.exception.code, 3)
            self.assertFalse(out.exists())
            data.build_subset(manifest, "validation", 1, "subject", 1, out)
            original = out.read_bytes()
            self.assertEqual(data.build_subset(manifest, "validation", 1, "subject", 1, out), out)
            with self.assertRaises(data.DataError) as caught:
                data.build_subset(manifest, "validation", 2, "subject", 1, out)
            self.assertEqual(caught.exception.code, 3)
            self.assertEqual(out.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
