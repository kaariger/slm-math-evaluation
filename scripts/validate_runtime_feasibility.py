#!/usr/bin/env python3
"""Reproduce the accepted local Qwen3-8B runtime feasibility check."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence


RUNTIME_BUILD = 10412
RUNTIME_COMMIT = "0d0bfcd4fd8828e3e7906b6fc4561725b534511e"
RUNTIME_COMMIT_SHORT = RUNTIME_COMMIT[:9]
RUNTIME_ARCHIVE_FILENAME = f"llama-b{RUNTIME_BUILD}-bin-macos-arm64.tar.gz"
RUNTIME_ARCHIVE_SHA256 = (
    "94f48fad6d22117f0c85524cd8b76dab482902aedde30f628f91bc7c4ac9135c"
)
MODEL_REPOSITORY = "Qwen/Qwen3-8B-GGUF"
MODEL_REVISION = "7c41481f57cb95916b40956ab2f0b139b296d974"
MODEL_FILENAME = "Qwen3-8B-Q4_K_M.gguf"
MODEL_SHA256 = "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"
SMOKE_PROMPT = "What is 17 multiplied by 23? Return only the final integer. /no_think"
EXPECTED_ANSWER = "391"


class ValidationError(RuntimeError):
    """Raised when the pinned feasibility contract is not satisfied."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise ValidationError(f"{label} is missing: {path}")


def require_digest(path: Path, expected: str, label: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise ValidationError(
            f"{label} SHA-256 mismatch: expected {expected}, observed {actual}"
        )


def run_checked(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    # llama.cpp reads LLAMA_ARG_* settings from the environment; drop them so
    # only the explicit arguments configure the pinned runtime.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("LLAMA_ARG_")
    }
    # A new session lets a timeout stop grandchildren too, such as llama-cli
    # running under /usr/bin/time with its ephemeral loopback server.
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise ValidationError(
            f"command timed out after {timeout} seconds: {' '.join(command)}"
        ) from error
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        if "failed to get a free port" in detail:
            detail += (
                "\nThe pinned llama-cli uses an ephemeral loopback server; allow local "
                "loopback sockets when running inside a restricted sandbox."
            )
        raise ValidationError(
            f"command failed with exit code {completed.returncode}: "
            f"{' '.join(command)}\n{detail}"
        )
    return completed


def validate_runtime_version(llama_cli: Path) -> str:
    completed = run_checked([str(llama_cli), "--version"], timeout=30)
    version = (completed.stdout + completed.stderr).strip()
    expected_build = f"build {RUNTIME_BUILD}"
    if expected_build not in version or RUNTIME_COMMIT_SHORT not in version:
        raise ValidationError(
            f"unexpected llama.cpp version; expected {expected_build} and "
            f"{RUNTIME_COMMIT_SHORT}, observed: {version}"
        )
    return version


def run_smoke(llama_cli: Path, model: Path) -> dict[str, Any]:
    time_executable = Path("/usr/bin/time")
    require_file(time_executable, "macOS time executable")
    command = [
        str(time_executable),
        "-l",
        str(llama_cli),
        "--model",
        str(model),
        "--offline",
        "--ctx-size",
        "4096",
        "--gpu-layers",
        "99",
        "--flash-attn",
        "on",
        "--fit",
        "off",
        "--threads",
        "8",
        "--threads-batch",
        "8",
        "--seed",
        "1",
        "--temp",
        "0",
        "--top-k",
        "1",
        "--top-p",
        "1",
        "--min-p",
        "0",
        "--predict",
        "64",
        "--prompt",
        SMOKE_PROMPT,
        "--single-turn",
        "--simple-io",
        "--no-display-prompt",
        "--color",
        "off",
        "--reasoning",
        "off",
        "--show-timings",
        "--log-verbosity",
        "3",
    ]

    started = time.perf_counter()
    completed = run_checked(command, timeout=240)
    elapsed = time.perf_counter() - started

    result = check_smoke(completed.stdout, completed.stderr)
    result["process_elapsed_seconds"] = round(elapsed, 3)
    return result


def smoke_response(stdout: str) -> str:
    """Return the generated response between the prompt echo and the timings."""
    echo = f"> {SMOKE_PROMPT}"
    start = stdout.find(echo)
    if start == -1:
        raise ValidationError("llama-cli output did not echo the smoke prompt")
    start += len(echo)
    end = stdout.find("[ Prompt:", start)
    if end == -1:
        raise ValidationError("llama-cli did not report prompt/generation timings")
    response = stdout[start:end]
    # A reasoning trace is not part of the answer; an unfinished trace leaves none.
    if "[Start thinking]" in response:
        response = response.partition("[End thinking]")[2]
    return response


def check_smoke(stdout: str, stderr: str) -> dict[str, Any]:
    if not re.search(rf"(?<!\d){EXPECTED_ANSWER}(?!\d)", smoke_response(stdout)):
        raise ValidationError(
            f"smoke response did not contain the expected answer {EXPECTED_ANSWER}"
        )

    timing = re.search(
        r"Prompt:\s*([0-9.]+)\s*t/s\s*\|\s*Generation:\s*([0-9.]+)\s*t/s",
        stdout,
    )
    if timing is None:
        raise ValidationError("llama-cli did not report prompt/generation timings")

    resident = re.search(
        r"^\s*(\d+)\s+maximum resident set size\s*$",
        stderr,
        flags=re.MULTILINE,
    )
    swaps = re.search(r"^\s*(\d+)\s+swaps\s*$", stderr, flags=re.MULTILINE)
    if resident is None or swaps is None:
        raise ValidationError("macOS time did not report memory and swap evidence")

    swaps_count = int(swaps.group(1))
    maximum_resident_set_bytes = int(resident.group(1))
    prompt_tokens_per_second = float(timing.group(1))
    generation_tokens_per_second = float(timing.group(2))
    if swaps_count != 0:
        raise ValidationError(f"smoke run used swap: {swaps_count} swap operations")
    if maximum_resident_set_bytes > 16 * 1024**3:
        raise ValidationError(
            "smoke run exceeded the 16 GiB feasibility ceiling: "
            f"{maximum_resident_set_bytes} bytes"
        )
    if prompt_tokens_per_second < 10 or generation_tokens_per_second < 10:
        raise ValidationError(
            "smoke throughput fell below the 10 token/second feasibility floor"
        )

    return {
        "answer": EXPECTED_ANSWER,
        "generation_tokens_per_second": generation_tokens_per_second,
        "maximum_resident_set_bytes": maximum_resident_set_bytes,
        "prompt_tokens_per_second": prompt_tokens_per_second,
        "swaps": swaps_count,
    }


def run_benchmark(llama_bench: Path, model: Path) -> dict[str, Any]:
    command = [
        str(llama_bench),
        "--model",
        str(model),
        "--offline",
        "--n-prompt",
        "64",
        "--n-gen",
        "32",
        "--repetitions",
        "1",
        "--n-gpu-layers",
        "99",
        "--flash-attn",
        "on",
        "--threads",
        "8",
        "--output",
        "json",
    ]
    return check_benchmark(run_checked(command, timeout=240).stdout)


def check_benchmark(stdout: str) -> dict[str, Any]:
    try:
        records = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise ValidationError("llama-bench did not emit valid JSON") from error
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise ValidationError("llama-bench JSON is not a list of benchmark records")
    try:
        return summarize_benchmark(records)
    except (AttributeError, KeyError, TypeError) as error:
        raise ValidationError(
            f"llama-bench JSON has a missing or malformed field: {error!r}"
        ) from error


def summarize_benchmark(records: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = next((record for record in records if record["n_prompt"] == 64), None)
    generation = next((record for record in records if record["n_gen"] == 32), None)
    if prompt is None or generation is None:
        raise ValidationError("llama-bench omitted a requested benchmark record")

    backends = {item.strip() for item in prompt["backends"].split(",")}
    if "MTL" not in backends:
        raise ValidationError(
            f"Metal backend was not active; observed backends: {prompt['backends']}"
        )
    if prompt["flash_attn"] != 1 or prompt["n_gpu_layers"] != 99:
        raise ValidationError("requested Metal offload/flash-attention settings changed")
    if any(
        record["build_number"] != RUNTIME_BUILD
        or record["build_commit"] != RUNTIME_COMMIT_SHORT
        for record in records
    ):
        raise ValidationError("llama-bench build does not match the pinned runtime")
    if prompt["avg_ts"] < 10 or generation["avg_ts"] < 10:
        raise ValidationError(
            "benchmark throughput fell below the 10 token/second feasibility floor"
        )

    return {
        "backends": prompt["backends"],
        "generation_tokens_per_second": round(generation["avg_ts"], 3),
        "gpu": prompt["gpu_info"],
        "prompt_tokens_per_second": round(prompt["avg_ts"], 3),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--runtime-archive",
        type=Path,
        default=Path(f"models/{RUNTIME_ARCHIVE_FILENAME}"),
        help="downloaded llama.cpp release archive",
    )
    parser.add_argument(
        "--runtime-directory",
        type=Path,
        default=Path(f"models/llama-b{RUNTIME_BUILD}"),
        help="directory extracted from the release archive",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=Path(f"models/{MODEL_FILENAME}"),
        help="model file downloaded from the pinned revision",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValidationError(
            "this feasibility record targets a Darwin arm64 (Apple silicon) host"
        )

    llama_cli = args.runtime_directory / "llama-cli"
    llama_bench = args.runtime_directory / "llama-bench"
    require_file(args.runtime_archive, "llama.cpp release archive")
    require_file(llama_cli, "llama-cli")
    require_file(llama_bench, "llama-bench")
    require_file(args.model, "Qwen3-8B model")

    require_digest(
        args.runtime_archive, RUNTIME_ARCHIVE_SHA256, "llama.cpp release archive"
    )
    require_digest(args.model, MODEL_SHA256, "Qwen3-8B model")

    result = {
        "model": {
            "filename": MODEL_FILENAME,
            "repository": MODEL_REPOSITORY,
            "revision": MODEL_REVISION,
            "sha256": MODEL_SHA256,
        },
        "platform": {
            "machine": platform.machine(),
            "system": platform.system(),
        },
        "runtime": {
            "archive_sha256": RUNTIME_ARCHIVE_SHA256,
            "build": RUNTIME_BUILD,
            "commit": RUNTIME_COMMIT,
            "version_output": validate_runtime_version(llama_cli),
        },
        "smoke": run_smoke(llama_cli, args.model),
        "benchmark": run_benchmark(llama_bench, args.model),
    }
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as error:
        print(f"runtime feasibility validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
