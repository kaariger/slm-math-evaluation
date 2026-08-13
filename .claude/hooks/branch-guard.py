#!/usr/bin/env python3

"""Block Claude Code's built-in file writes on protected Git branches."""

import json
import subprocess
import sys
from typing import Optional


PROTECTED_BRANCHES = {"main", "master", "develop", "production"}
WRITE_TOOLS = {"Edit", "Write"}


def decision(value: str, reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": value,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def git_output(cwd: str, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def is_protected_branch(branch: str) -> bool:
    return branch in PROTECTED_BRANCHES or branch.startswith("release/")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, TypeError):
        decision(
            "ask",
            "Branch guard could not parse hook input. Confirm repository state before editing.",
        )
        return 0

    if payload.get("tool_name") not in WRITE_TOOLS:
        return 0

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        decision("ask", "Branch guard could not determine the working directory.")
        return 0

    repo_root = git_output(cwd, "rev-parse", "--show-toplevel")
    if not repo_root:
        decision(
            "ask",
            "Branch guard could not confirm that the edit is inside a Git repository.",
        )
        return 0

    branch = git_output(repo_root, "symbolic-ref", "--short", "-q", "HEAD")
    if not branch:
        decision(
            "ask",
            "Repository is detached or its branch state is unknown. Confirm before editing.",
        )
        return 0

    if is_protected_branch(branch):
        if git_output(repo_root, "rev-parse", "--verify", "HEAD") is None:
            decision(
                "ask",
                f"Repository genesis on protected branch '{branch}' requires explicit authorization.",
            )
            return 0

        decision(
            "deny",
            f"Edits are blocked on protected branch '{branch}'. Switch to a working branch first.",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
