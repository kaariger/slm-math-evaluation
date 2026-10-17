"""Integrity-checked readers for immutable evaluation run directories."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .data import DataError, read_json, runs_root, sha256_bytes


@dataclass(frozen=True)
class StoredRun:
    directory: Path
    metadata: dict[str, Any]
    protocol: dict[str, Any]
    manifest: dict[str, Any]
    items: list[dict[str, Any]]


def load_run(run_id: str) -> StoredRun:
    if not run_id or run_id != Path(run_id).name or run_id in (".", "..") or run_id.startswith("_"):
        raise DataError("invalid run ID", 3)
    directory = runs_root() / run_id
    if not directory.is_dir():
        raise DataError(f"run missing: {run_id}", 3)
    metadata = read_json(directory / "run.json")
    if not isinstance(metadata, dict) or metadata.get("run_id") != run_id:
        raise DataError("run identity mismatch", 2)
    copies = []
    for name, key in (("protocol.yaml", "protocol_sha256"), ("manifest.json", "manifest_sha256")):
        try:
            content = (directory / name).read_bytes()
        except FileNotFoundError as error:
            raise DataError(f"run copy missing: {name}", 3) from error
        if sha256_bytes(content) != metadata.get(key):
            raise DataError(f"run copy SHA-256 mismatch: {name}", 2)
        copies.append(content)
    try:
        protocol = yaml.safe_load(copies[0])
        manifest = json.loads(copies[1])
    except (yaml.YAMLError, ValueError) as error:
        raise DataError(f"invalid run copy: {error}", 2) from error
    if not isinstance(protocol, dict) or not isinstance(manifest, dict):
        raise DataError("invalid run copy shape", 2)
    items = []
    try:
        with (directory / "items.jsonl").open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("record is not an object")
                for field, hash_field in (("raw_output", "raw_output_sha256"),
                                          ("reference_answer", "reference_sha256")):
                    value = row.get(field)
                    if not isinstance(value, str) or sha256_bytes(value.encode("utf-8")) != row.get(hash_field):
                        raise DataError(f"{hash_field} mismatch at item line {number}", 2)
                items.append(row)
    except FileNotFoundError as error:
        raise DataError("items.jsonl missing", 3) from error
    except (UnicodeError, ValueError) as error:
        raise DataError(f"malformed items.jsonl: {error}", 2) from error
    pairs = [(row.get("item_id"), row.get("sample_index")) for row in items]
    if len(set(pairs)) != len(pairs):
        raise DataError("duplicate item/sample pair", 2)
    return StoredRun(directory, metadata, protocol, manifest, items)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
