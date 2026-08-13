import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPOSITORY_ROOT / ".claude" / "hooks" / "branch-guard.py"


class BranchGuardTests(unittest.TestCase):
    def run_hook(self, cwd: Path) -> subprocess.CompletedProcess:
        payload = json.dumps({"tool_name": "Write", "cwd": str(cwd)})
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=payload,
            check=False,
            capture_output=True,
            text=True,
        )

    def make_repository(self, root: Path, branch: str) -> None:
        subprocess.run(
            ["git", "init", "-q", "-b", branch, str(root)],
            check=True,
            capture_output=True,
            text=True,
        )

    def make_initial_commit(self, root: Path) -> None:
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Branch Guard Test",
                "-c",
                "user.email=branch-guard@example.invalid",
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                "Initial commit",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_asks_for_authorization_on_unborn_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root, "main")
            result = self.run_hook(root)

        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        decision = output["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "ask")

    def test_denies_write_on_established_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root, "main")
            self.make_initial_commit(root)
            result = self.run_hook(root)

        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        decision = output["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "deny")

    def test_denies_write_on_established_release_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root, "release/1.0")
            self.make_initial_commit(root)
            result = self.run_hook(root)

        output = json.loads(result.stdout)
        decision = output["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "deny")

    def test_allows_write_on_working_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root, "test/bootstrap-controls")
            result = self.run_hook(root)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_asks_when_repository_state_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_hook(Path(directory))

        output = json.loads(result.stdout)
        decision = output["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "ask")


if __name__ == "__main__":
    unittest.main()
