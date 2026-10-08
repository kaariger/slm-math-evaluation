"""Single keyed llama.cpp run with append-only raw item capture and safe resume."""

from __future__ import annotations

import json
import os
import platform
import secrets
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import data, runtime
from .data import DataError, read_json, sha256_bytes, write_json
from .evaluation import (EXTRACTOR_ID, EXTRACTOR_VERSION, PRIMARY_PIN,
                         SECONDARY_PIN, extract_raw, score_primary, score_secondary)
from .run_store import StoredRun, load_run


ADAPTER = {"id": "llama-cpp-chat-completions", "version": "1.0.0"}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verification_copies(protocol_sha: str, protocol: dict[str, Any]) -> dict[str, bytes]:
    directory = data.runs_root() / "_verification" / protocol_sha
    copies = {}
    for name in ("runtime-verification.json", "artifact-verification.json"):
        path = directory / name
        copies[name] = _verified_record(path, name, protocol_sha, protocol)
    return copies


def _verified_record(path: Path, name: str, protocol_sha: str,
                     protocol: dict[str, Any]) -> bytes:
    if not path.is_file():
        raise DataError(f"verification record missing: {path}", 3)
    content = path.read_bytes()
    try:
        record = json.loads(content)
    except json.JSONDecodeError as error:
        raise DataError(f"invalid verification record: {path}", 2) from error
    if (record.get("protocol_sha256") != protocol_sha
            or record.get("model_artifact_sha256") != protocol["model"]["artifact"]["sha256"]
            or record.get("runtime_build") != protocol["runtime"]["build"]
            or record.get("status") != "passed"
            or (name == "runtime-verification.json"
                and record.get("contract_version") != "v0.6")):
        raise DataError(f"verification record identity/status mismatch: {path}", 2)
    return content


def _identity(protocol: dict[str, Any], manifest: dict[str, Any], tier: str) -> list[str]:
    if manifest.get("source") != protocol.get("dataset", {}).get("source"):
        raise DataError("manifest source differs from protocol dataset source", 2)
    if tier == "test":
        ids = [item["id"] for item in manifest["items"] if item["role"] == "test"]
    else:
        ids = manifest["tiers"].get(tier)
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise DataError("tier has no unique item IDs", 3)
    indexed = {item["id"]: item for item in manifest["items"]}
    role = "test" if tier == "test" else "dev"
    if any(item_id not in indexed or indexed[item_id]["role"] != role for item_id in ids):
        raise DataError("tier IDs do not match manifest membership", 2)
    excluded = {entry["id"] for entry in manifest["exclusions"] if entry["verdict"] == "exclude"}
    if excluded.intersection(ids):
        raise DataError("tier contains excluded item", 2)
    return ids


def _source_rows(manifest: dict[str, Any], ids: list[str]) -> dict[str, dict[str, Any]]:
    test, train = data.load_rows()
    rows = {row["unique_id"]: row for row in test + train}
    indexed = {item["id"]: item for item in manifest["items"]}
    selected = {}
    for item_id in ids:
        row = rows.get(item_id)
        if row is None or data.content_hash(row["problem"]) != indexed[item_id]["content_sha256"]:
            raise DataError(f"manifest/source item mismatch: {item_id}", 2)
        selected[item_id] = row
    return selected


def _protocol_check(protocol: dict[str, Any]) -> None:
    if protocol.get("draft_status"):
        raise DataError("protocol remains a draft", 3)
    if (protocol.get("extraction") != {"id": EXTRACTOR_ID, "version": EXTRACTOR_VERSION}
            or protocol.get("scorers", {}).get("primary") != {
                "id": "prm800k-grader", "pin": PRIMARY_PIN}
            or protocol.get("scorers", {}).get("secondary") != {
                "id": "math-verify", "pin": SECONDARY_PIN}):
        raise DataError("extractor or scorer pin is unsupported", 3)
    seed_policy = protocol.get("sampling", {}).get("seed_policy", {}).get("value")
    if seed_policy != "seed = seed_base + sample_index":
        raise DataError("unsupported seed policy", 3)
    if protocol.get("prompt", {}).get("few_shot") != 0:
        raise DataError("few-shot examples are unsupported by this prompt renderer", 3)


def _host() -> dict[str, Any]:
    memory_gb = None
    try:
        import subprocess as sp
        if platform.system() == "Darwin":
            memory_gb = round(int(sp.check_output(["sysctl", "-n", "hw.memsize"], text=True)) / 2**30, 2)
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    return {"os": platform.system(), "arch": platform.machine(), "memory_gb": memory_gb}


def _run_metadata(run_id: str, protocol: dict[str, Any], protocol_sha: str,
                  manifest_sha: str, tier: str, k: int, seed_base: int) -> dict[str, Any]:
    return {"run_id": run_id, "protocol_version": protocol["protocol_version"],
            "protocol_sha256": protocol_sha, "manifest_sha256": manifest_sha,
            "tier": tier, "k": k, "seeds": [seed_base + sample for sample in range(k)],
            "model_artifact_sha256": protocol["model"]["artifact"]["sha256"],
            "runtime_build": protocol["runtime"]["build"], "adapter": ADAPTER,
            "extractor": protocol["extraction"],
            "scorers": [protocol["scorers"][role] for role in ("primary", "secondary")],
            "executor": "slm-eval native harness", "host": _host(),
            "attempts": [], "status": "interrupted"}


def start(protocol_path: Path, manifest_path: Path, tier: str, k: int, seed_base: int) -> str:
    if k < 1 or seed_base < 0:
        raise DataError("k must be positive and seed-base nonnegative", 3)
    if not protocol_path.is_file() or not manifest_path.is_file():
        raise DataError("protocol or manifest missing", 3)
    protocol_bytes, manifest_bytes = protocol_path.read_bytes(), manifest_path.read_bytes()
    try:
        protocol = yaml.safe_load(protocol_bytes)
    except yaml.YAMLError as error:
        raise DataError(f"invalid protocol YAML: {error}", 3) from error
    if not isinstance(protocol, dict):
        raise DataError("protocol must be a mapping", 3)
    protocol_sha = sha256_bytes(protocol_bytes)
    manifest = read_json(manifest_path)
    ids = _identity(protocol, manifest, tier)
    _protocol_check(protocol)
    verification = _verification_copies(protocol_sha, protocol)
    _source_rows(manifest, ids)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(5)
    directory = data.runs_root() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "protocol.yaml").write_bytes(protocol_bytes)
    (directory / "manifest.json").write_bytes(manifest_bytes)
    for name, content in verification.items():
        (directory / name).write_bytes(content)
    metadata = _run_metadata(run_id, protocol, protocol_sha, sha256_bytes(manifest_bytes), tier, k, seed_base)
    write_json(directory / "run.json", metadata)
    (directory / "items.jsonl").touch()
    # Expose the ID before generation so an interrupted run can be resumed.
    print(run_id, flush=True)
    _generate(directory, metadata, protocol, manifest, ids, set())
    return run_id


def resume(run_id: str) -> str:
    run = load_run(run_id)
    if run.metadata.get("status") == "completed":
        raise DataError("completed run is immutable", 3)
    _protocol_check(run.protocol)
    for name in ("runtime-verification.json", "artifact-verification.json"):
        _verified_record(run.directory / name, name, run.metadata["protocol_sha256"],
                         run.protocol)
    if (run.metadata["model_artifact_sha256"] != run.protocol["model"]["artifact"]["sha256"]
            or run.metadata["runtime_build"] != run.protocol["runtime"]["build"]
            or run.metadata["protocol_version"] != run.protocol["protocol_version"]):
        raise DataError("run model/runtime/protocol identity mismatch", 2)
    ids = _identity(run.protocol, run.manifest, run.metadata["tier"])
    existing = {(row["item_id"], row["sample_index"]) for row in run.items}
    required = {(item_id, sample) for item_id in ids for sample in range(run.metadata["k"])}
    if not existing <= required:
        raise DataError("stored item/sample pair outside run tier", 2)
    if not required - existing:
        raise DataError("no missing item/sample pairs to resume", 3)
    _source_rows(run.manifest, ids)
    _generate(run.directory, run.metadata, run.protocol, run.manifest, ids, existing)
    return run_id


def _prompt(protocol: dict[str, Any], problem: str) -> tuple[str, list[dict[str, str]]]:
    template = protocol["prompt"]["template"]
    rendered = template.replace("{problem}", problem)
    messages = []
    if protocol["prompt"].get("system_prompt"):
        messages.append({"role": "system", "content": protocol["prompt"]["system_prompt"]})
    messages.append({"role": "user", "content": rendered})
    return rendered, messages


def _check_runtime_version(server: Path) -> None:
    version = subprocess.run([str(server), "--version"], capture_output=True,
                             text=True, timeout=15, check=False)
    if version.returncode or runtime.COMMIT[:9] not in version.stdout + version.stderr:
        raise DataError("runtime build identity mismatch", 2)


def _effective_settings(protocol: dict[str, Any], port: int, key: str,
                        arguments: list[str]) -> dict[str, Any]:
    def argument(name: str) -> int:
        return int(arguments[arguments.index(name) + 1])

    status, body = runtime.request(port, "GET", "/props", key)
    if status != 200:
        raise DataError("keyed runtime properties unavailable", 3)
    try:
        props = json.loads(body)
        prop_values = {"context_size": props["default_generation_settings"]["n_ctx"],
                       "parallel_slots": props["total_slots"], "model_alias": props["model_alias"],
                       "ui": props["ui"]}
    except (ValueError, KeyError, TypeError) as error:
        raise DataError("keyed runtime properties incomplete", 3) from error
    configured = protocol["runtime"]
    selected = {"threads": argument("--threads"),
                "threads_batch": argument("--threads-batch"),
                "http_threads": argument("--threads-http")}
    if min(selected.values()) < 1:
        raise DataError("invalid explicit runtime thread count", 2)
    for name, value in prop_values.items():
        if configured[name] != value:
            raise DataError(f"runtime properties differ: {name}", 2)
    effective = {name: value for name, value in configured.items() if name != "provenance"}
    effective.update(selected)
    effective["port"] = port
    effective["trace_logging"] = False
    effective["reasoning_mode"] = protocol["reasoning_mode"]["value"]
    effective["request_sampling"] = {
        name: setting["value"] for name, setting in protocol["sampling"].items()
        if name != "seed_policy"
    }
    effective["generation_endpoint"] = "/v1/chat/completions"
    effective["stream"] = False
    effective["evidence"] = {"selected_threads": "explicit server arguments",
                             "context_and_slots": "keyed /props",
                             "other_settings": "explicit server arguments"}
    return effective


def _generation_response(port: int, key: str, payload: dict[str, Any],
                         timeout: int) -> tuple[int, bytes]:
    try:
        return runtime.request(port, "POST", "/v1/chat/completions", key,
                               payload, timeout=timeout)
    except TimeoutError as error:
        raise DataError(f"generation response timed out after {timeout} seconds", 1) from error


def _generate(directory: Path, metadata: dict[str, Any], protocol: dict[str, Any],
              manifest: dict[str, Any], ids: list[str], existing: set[tuple[str, int]]) -> None:
    server, model = runtime.pinned_paths(protocol)
    if runtime.sha256_file(model) != protocol["model"]["artifact"]["sha256"]:
        raise DataError("model artifact SHA-256 mismatch", 2)
    _check_runtime_version(server)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    key = secrets.token_urlsafe(32)
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith("LLAMA_ARG_") and name != "LLAMA_API_KEY"}
    environment["LLAMA_API_KEY"] = key
    arguments = runtime.server_arguments(server, model, port, protocol)
    attempt = {"n": len(metadata["attempts"]) + 1, "started": _utc(), "ended": None,
               "status": "interrupted", "resumed_from": metadata["attempts"][-1]["n"] if metadata["attempts"] else None}
    metadata["attempts"].append(attempt)
    write_json(directory / "run.json", metadata)
    process = subprocess.Popen(arguments, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    previous_signals = runtime.install_cleanup_signals()
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise DataError("runtime exited during startup", 3)
            try:
                if runtime.request(port, "GET", "/health", key)[0] == 200:
                    break
            except (OSError, TimeoutError):
                pass
            time.sleep(.5)
        else:
            raise DataError("runtime did not become healthy", 3)
        effective = _effective_settings(protocol, port, key, arguments)
        attempt["effective_runtime_settings"] = effective
        metadata["effective_runtime_settings"] = effective
        write_json(directory / "run.json", metadata)
        rows = _source_rows(manifest, ids)
        settings = {name: protocol["sampling"][name]["value"] for name in
                    ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "repeat_penalty")}
        settings["max_tokens"] = protocol["sampling"]["max_output_tokens"]["value"]
        timeout = runtime.generation_timeout(settings["max_tokens"])
        attempt["generation_timeout_seconds"] = timeout
        write_json(directory / "run.json", metadata)
        with (directory / "items.jsonl").open("a", encoding="utf-8") as stream:
            for item_id in ids:
                source = rows[item_id]
                prompt, messages = _prompt(protocol, source["problem"])
                for sample in range(metadata["k"]):
                    if (item_id, sample) in existing:
                        continue
                    seed = metadata["seeds"][sample]
                    started = time.monotonic()
                    status, body = _generation_response(
                        port, key, {"model": runtime.ALIAS, "messages": messages,
                                    "seed": seed, "stream": False, **settings}, timeout)
                    if status != 200:
                        raise DataError(f"generation returned HTTP {status}", 1)
                    try:
                        confirmed = runtime.confirm_sampling(port, key, {"seed": seed, **settings})
                    except runtime.RuntimeErrorWithCode as error:
                        raise DataError(str(error), error.code) from error
                    attempt["confirmed_request_sampling"] = confirmed
                    attempt["confirmed_request_count"] = attempt.get("confirmed_request_count", 0) + 1
                    metadata["confirmed_request_sampling"] = confirmed
                    write_json(directory / "run.json", metadata)
                    response = json.loads(body)
                    choice = response["choices"][0]
                    raw = choice["message"]["content"]
                    if not isinstance(raw, str):
                        raise DataError("generation content is not text", 1)
                    reason = choice.get("finish_reason")
                    truncated = reason == "length"
                    thinking, extraction = extract_raw(raw, truncated)
                    answer, reference = extraction["answer"], source["answer"]
                    verdicts = {
                        "primary": score_primary(answer, reference)["correct"],
                        "secondary": score_secondary(answer, reference)["correct"],
                    }
                    record = {"item_id": item_id, "sample_index": sample, "seed": seed,
                              "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                              "raw_output": raw, "raw_output_sha256": sha256_bytes(raw.encode("utf-8")),
                              "thinking_present": thinking, "truncated": truncated,
                              "finish_reason": reason, "tokens_generated": response.get("usage", {}).get("completion_tokens"),
                              "reference_answer": reference, "reference_sha256": sha256_bytes(reference.encode("utf-8")),
                              "extraction": extraction, "verdicts": verdicts,
                              "timing_ms": round((time.monotonic() - started) * 1000)}
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
        metadata["status"] = "completed"
        attempt["status"] = "completed"
    except BaseException:
        metadata["status"] = "interrupted"
        attempt["status"] = "interrupted"
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        runtime.restore_cleanup_signals(previous_signals)
        attempt["ended"] = _utc()
        write_json(directory / "run.json", metadata)
