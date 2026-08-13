#!/usr/bin/env python3

"""Detect obvious local-path and credential leakage in public files."""

import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional, Pattern, Sequence


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    rule: str


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: Pattern[str]


def rules() -> Sequence[Rule]:
    return (
        Rule(
            "absolute local home path",
            re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^/\s]+(?:/[^\s'\"<>]*)?"),
        ),
        Rule(
            "absolute Windows user path",
            re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:\\Users\\[^\\\s]+"),
        ),
        Rule(
            "private key material",
            re.compile(r"-{5}BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-{5}"),
        ),
        Rule("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
        Rule("GitHub access token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
        Rule("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
        Rule("API secret token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    )


def candidate_paths(root: Path) -> Iterator[Path]:
    """Yield tracked and unignored untracked files, or a filesystem fallback."""

    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        for raw_path in sorted(item for item in result.stdout.split(b"\0") if item):
            yield root / os.fsdecode(raw_path)
        return

    for path in sorted(root.rglob("*")):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.is_file() or path.is_symlink():
            yield path


def scan_text(relative_path: Path, text: str, scan_rules: Iterable[Rule]) -> Iterator[Finding]:
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule in scan_rules:
            if rule.pattern.search(line):
                yield Finding(relative_path, line_number, rule.name)


def read_candidate(path: Path) -> Optional[str]:
    if path.is_symlink():
        return os.readlink(path)
    try:
        content = path.read_bytes()
    except OSError:
        return None
    if b"\0" in content:
        return None
    return content.decode("utf-8", errors="replace")


def validate(root: Path) -> Sequence[Finding]:
    root = root.resolve()
    scan_rules = rules()
    findings = []
    for path in candidate_paths(root):
        text = read_candidate(path)
        if text is None:
            continue
        try:
            relative_path = path.relative_to(root)
        except ValueError:
            relative_path = path
        findings.extend(scan_text(relative_path, text, scan_rules))
    return tuple(findings)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    findings = validate(args.root)
    if findings:
        print("Public repository leakage validation FAILED:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line}: {finding.rule}")
        return 1

    print("Public repository leakage validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
