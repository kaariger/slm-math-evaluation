import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY_ROOT / "scripts" / "validate_public_repo.py"


class PublicRepositoryValidatorTests(unittest.TestCase):
    def run_validator(self, files: dict[str, str]) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path, content in files.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(VALIDATOR), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_accepts_public_content(self) -> None:
        result = self.run_validator({"README.md": "A reproducible evaluation project.\n"})
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_detects_absolute_home_path(self) -> None:
        local_path = "/" + "Users" + "/example/work/repository"
        result = self.run_validator({"notes.txt": local_path})
        self.assertEqual(result.returncode, 1)
        self.assertIn("absolute local home path", result.stdout)

    def test_detects_windows_home_path(self) -> None:
        local_path = "C:" + "\\Users\\example\\work\\repository"
        result = self.run_validator({"notes.txt": local_path})
        self.assertEqual(result.returncode, 1)
        self.assertIn("absolute Windows user path", result.stdout)

    def test_detects_private_key_material(self) -> None:
        key_header = "-" * 5 + "BEGIN PRIVATE KEY" + "-" * 5
        result = self.run_validator({"key.pem": key_header})
        self.assertEqual(result.returncode, 1)
        self.assertIn("private key material", result.stdout)

    def test_detects_github_access_token(self) -> None:
        token = "gh" + "p_" + "a" * 30
        result = self.run_validator({"notes.txt": token})
        self.assertEqual(result.returncode, 1)
        self.assertIn("GitHub access token", result.stdout)


if __name__ == "__main__":
    unittest.main()
