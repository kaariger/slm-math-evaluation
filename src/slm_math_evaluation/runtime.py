"""The pinned, keyed llama.cpp server boundary for the MATH-500 path."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from typing import Any

import yaml

from .data import cache_root, runs_root


BUILD = "b10412"
COMMIT = "0d0bfcd4fd8828e3e7906b6fc4561725b534511e"
ALIAS = "qwen3-8b"
DIAGNOSTIC_LOG_VERBOSITY = 4
ROUTES = tuple(
    tuple(line.split(" ", 1))
    for line in """GET /health
GET /v1/health
GET /metrics
GET /props
POST /props
GET /models
GET /v1/models
POST /completion
POST /completions
POST /v1/completions
POST /chat/completions
POST /v1/chat/completions
POST /v1/chat/completions/control
POST /v1/responses
POST /responses
POST /v1/audio/transcriptions
POST /audio/transcriptions
POST /v1/messages
POST /infill
POST /embedding
POST /embeddings
POST /v1/embeddings
POST /rerank
POST /reranking
POST /v1/rerank
POST /v1/reranking
POST /tokenize
POST /detokenize
POST /apply-template
POST /chat/completions/input_tokens
POST /v1/chat/completions/input_tokens
POST /responses/input_tokens
POST /v1/responses/input_tokens
POST /v1/messages/count_tokens
GET /lora-adapters
POST /lora-adapters
GET /slots
POST /slots/0
GET /v1/stream
POST /v1/streams/lookup
DELETE /v1/stream
GET /cors-proxy
POST /cors-proxy
GET /tools
POST /tools""".splitlines()
)
EXEMPT = {("GET", path) for path in ("/health", "/v1/health", "/models", "/v1/models")}


class RuntimeErrorWithCode(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_protocol(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise RuntimeErrorWithCode(f"protocol missing: {path}", 3)
    try:
        document = yaml.safe_load(path.read_bytes())
    except yaml.YAMLError as error:
        raise RuntimeErrorWithCode(f"invalid protocol YAML: {error}", 3) from error
    if not isinstance(document, dict):
        raise RuntimeErrorWithCode("protocol must be a mapping", 3)
    return document, sha256_file(path)


def pinned_paths(protocol: dict[str, Any]) -> tuple[Path, Path]:
    try:
        runtime = protocol["runtime"]
        artifact = protocol["model"]["artifact"]
        if runtime["build"] != BUILD or runtime["model_alias"] != ALIAS:
            raise ValueError("runtime build or model alias differs from the pin")
        name = artifact["file"]
        if name != Path(name).name:
            raise ValueError("artifact file must be a base name")
        if len(artifact["sha256"]) != 64:
            raise ValueError("artifact SHA-256 is malformed")
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeErrorWithCode(f"invalid runtime protocol: {error}", 3) from error
    root = cache_root() / "models"
    server = root / f"llama-{BUILD}" / "llama-server"
    model = root / name
    if not server.is_file() or not model.is_file():
        raise RuntimeErrorWithCode("pinned runtime or model artifact is missing", 3)
    return server, model


def request(port: int, method: str, path: str, key: str | None = None,
            payload: dict[str, Any] | None = None) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["Authorization"] = "Bearer " + key
    body = b"{" if method == "POST" and payload is None else None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def server_arguments(server: Path, model: Path, port: int,
                     protocol: dict[str, Any]) -> list[str]:
    runtime = protocol["runtime"]
    if runtime.get("host") != "127.0.0.1" or runtime.get("port") != "ephemeral":
        raise RuntimeErrorWithCode("runtime must use loopback and an ephemeral port", 3)
    if runtime.get("ui") is not False or runtime.get("reasoning_format") != "none":
        raise RuntimeErrorWithCode("runtime must disable the UI and reasoning parsing", 3)
    try:
        context = int(runtime["context_size"])
        threads = int(runtime["threads"])
        threads_batch = int(runtime["threads_batch"])
        batch_size = int(runtime["batch_size"])
        ubatch_size = int(runtime["ubatch_size"])
        gpu_layers = int(runtime["gpu_layers"])
        parallel = int(runtime["parallel_slots"])
        flash = "on" if runtime["flash_attention"] is True else "off"
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeErrorWithCode(f"invalid runtime setting: {error}", 3) from error
    if context <= 0 or gpu_layers < 0 or parallel <= 0 or batch_size <= 0 or ubatch_size <= 0:
        raise RuntimeErrorWithCode("invalid runtime setting range", 3)
    if (runtime.get("cache_type_k") != "f16" or runtime.get("cache_type_v") != "f16"
            or runtime.get("load_mode") != "mmap" or runtime.get("context_shift") is not False
            or runtime.get("jinja") is not True or runtime.get("reasoning_budget") != -1):
        raise RuntimeErrorWithCode("unsupported runtime settings in protocol", 3)
    reasoning = protocol.get("reasoning_mode", {}).get("value")
    if reasoning not in ("hybrid", "forced_think", "forced_non_think"):
        raise RuntimeErrorWithCode("invalid reasoning mode", 3)
    reasoning_arg = {"hybrid": "auto", "forced_think": "on", "forced_non_think": "off"}[reasoning]
    return [str(server), "--model", str(model), "--alias", ALIAS,
            # Trace startup settings are consumed in memory by the runner.
            "--log-verbosity", str(DIAGNOSTIC_LOG_VERBOSITY),
            "--host", "127.0.0.1", "--port", str(port),
            "--threads", str(threads), "--threads-batch", str(threads_batch),
            "--batch-size", str(batch_size), "--ubatch-size", str(ubatch_size),
            "--ctx-size", str(context), "--n-gpu-layers", str(gpu_layers),
            "--flash-attn", flash, "--parallel", str(parallel),
            "--cache-type-k", "f16", "--cache-type-v", "f16",
            "--load-mode", "mmap", "--no-context-shift", "--jinja",
            "--reasoning", reasoning_arg, "--reasoning-budget", "-1",
            "--reasoning-format", "none", "--no-webui"]


def _check_exempt_body(path: str, body: bytes, model: Path) -> bool:
    content = body.decode("utf-8", errors="replace").lower()
    if any(marker in content for marker in
           (str(model).lower(), "/private/", "/users/", "prompt", "output",
            "slot", "property", "generation")):
        return False
    if path in ("/models", "/v1/models"):
        try:
            response = json.loads(body)
            entries = response["data"]
            return bool(entries) and all(item["id"] == ALIAS for item in entries)
        except (ValueError, KeyError, TypeError):
            return False
    return True


def _stream_content(body: bytes) -> str:
    pieces: list[str] = []
    for line in body.splitlines():
        if not line.startswith(b"data: ") or line == b"data: [DONE]":
            continue
        event = json.loads(line[6:])
        for choice in event.get("choices", []):
            content = choice.get("delta", {}).get("content")
            if isinstance(content, str):
                pieces.append(content)
    return "".join(pieces)


def _fixture_checks(port: int, key: str, protocol: dict[str, Any]) -> dict[str, Any]:
    """Check request mapping and byte-exact content capture with a tiny fixture."""
    try:
        configured = {name: protocol["sampling"][name]["value"] for name in
                      ("temperature", "top_p", "top_k", "min_p",
                       "presence_penalty", "repeat_penalty")}
        configured["max_tokens"] = protocol["sampling"]["max_output_tokens"]["value"]
    except (KeyError, TypeError) as error:
        raise RuntimeErrorWithCode(f"incomplete sampling protocol: {error}", 3) from error
    fixture_parameters = dict(configured)
    fixture_parameters.update(temperature=0, max_tokens=16)
    payload: dict[str, Any] = {
        "model": ALIAS, "messages": [{"role": "user", "content": "Reply with 391."}],
        "seed": 42, **fixture_parameters,
    }
    status, body = request(port, "POST", "/v1/chat/completions", key,
                           {**payload, "stream": False})
    if status != 200:
        raise RuntimeErrorWithCode(f"fixture generation returned HTTP {status}", 4)
    response = json.loads(body)
    if response.get("model") != ALIAS:
        raise RuntimeErrorWithCode("fixture response model alias mismatch", 4)
    content = response["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content:
        raise RuntimeErrorWithCode("fixture returned no raw content", 4)
    stream_status, stream_body = request(port, "POST", "/v1/chat/completions", key,
                                         {**payload, "stream": True})
    if stream_status != 200:
        raise RuntimeErrorWithCode(f"fixture stream returned HTTP {stream_status}", 4)
    streamed = _stream_content(stream_body)
    match = content == streamed
    return {
        "parameter_equivalence": {
            "configured": configured,
            "submitted": fixture_parameters,
            "fixture_overrides": {"temperature": 0, "max_tokens": 16},
            "request_mapping_checked": all(
                payload[name] == fixture_parameters[name] for name in fixture_parameters
            ),
        },
        "raw_capture_fidelity": {
            "nonstream_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "stream_sha256": hashlib.sha256(streamed.encode("utf-8")).hexdigest(),
            "nonstream_length": len(content), "stream_length": len(streamed),
            "match": match,
        },
    }


def verify_runtime(protocol_path: Path) -> Path:
    protocol, protocol_sha = read_protocol(protocol_path)
    server, model = pinned_paths(protocol)
    if sha256_file(model) != protocol["model"]["artifact"]["sha256"]:
        raise RuntimeErrorWithCode("model artifact SHA-256 mismatch", 2)
    version = subprocess.run([str(server), "--version"], capture_output=True,
                             text=True, timeout=15, check=False)
    if version.returncode or COMMIT[:9] not in version.stdout + version.stderr:
        raise RuntimeErrorWithCode("runtime build mismatch", 2)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    arguments = server_arguments(server, model, port, protocol)
    key = secrets.token_urlsafe(32)
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith("LLAMA_ARG_") and name != "LLAMA_API_KEY"}
    environment["LLAMA_API_KEY"] = key
    record: dict[str, Any] = {
        "verification_version": "1", "contract_version": "v0.5",
        "protocol_sha256": protocol_sha, "runtime_build": BUILD,
        "runtime_commit": COMMIT, "model_artifact_sha256": protocol["model"]["artifact"]["sha256"],
        "model_alias": ALIAS, "bind": "127.0.0.1", "ephemeral_port": port,
        "routes": [], "options_probes": [], "unexpected_open_routes": [],
        "status": "incomplete",
    }
    process = subprocess.Popen(arguments, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    output = runs_root() / "_verification" / protocol_sha / "runtime-verification.json"
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeErrorWithCode("runtime exited during startup", 3)
            try:
                if request(port, "GET", "/health", key)[0] == 200:
                    break
            except (OSError, TimeoutError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeErrorWithCode("runtime did not become healthy", 3)
        for method, path in ROUTES:
            without, body = request(port, method, path)
            try:
                with_key, _ = request(port, method, path, key)
            except (OSError, TimeoutError):
                with_key = None
            exempt = (method, path) in EXEMPT
            row = {"method": method, "path": path, "without_key": without,
                   "with_key": with_key,
                   "classification": "exempt" if exempt else "protected"}
            if exempt:
                row["no_sensitive_data"] = _check_exempt_body(path, body, model)
                if without != 200 or with_key != 200 or not row["no_sensitive_data"]:
                    record["unexpected_open_routes"].append({"method": method, "path": path})
            elif without != 401:
                record["unexpected_open_routes"].append({"method": method, "path": path,
                                                         "without_key": without})
            record["routes"].append(row)
            options_without, options_body = request(port, "OPTIONS", path)
            options_with, options_body_with = request(port, "OPTIONS", path, key)
            options = {"method": "OPTIONS", "path": path, "companion_method": method,
                       "companion_without_key": without,
                       "without_key": options_without,
                       "without_key_body_length": len(options_body),
                       "with_key": options_with,
                       "with_key_body_length": len(options_body_with),
                       "classification": "cors_preflight"}
            record["options_probes"].append(options)
            if options_body:
                record["unexpected_open_routes"].append({"method": "OPTIONS", "path": path,
                                                         "without_key_body_length": len(options_body)})
        record["security_passed"] = not record["unexpected_open_routes"]
        if record["security_passed"]:
            props_status, props_body = request(port, "GET", "/props", key)
            if props_status != 200:
                raise RuntimeErrorWithCode("keyed properties endpoint unavailable", 4)
            props = json.loads(props_body)
            settings = {
                "context_size": props["default_generation_settings"]["n_ctx"],
                "parallel_slots": props["total_slots"],
                "model_alias": props["model_alias"],
                "ui": props["ui"],
            }
            expected = {"context_size": int(protocol["runtime"]["context_size"]),
                        "parallel_slots": int(protocol["runtime"]["parallel_slots"]),
                        "model_alias": ALIAS, "ui": False}
            record["server_parameter_equivalence"] = {
                "observed": settings, "expected": expected, "match": settings == expected,
            }
            if settings != expected or props.get("model_path") != str(model):
                raise RuntimeErrorWithCode("server settings differ from protocol", 4)
            record.update(_fixture_checks(port, key, protocol))
            if not record["raw_capture_fidelity"]["match"]:
                raise RuntimeErrorWithCode("raw capture differs from streamed content", 4)
        record["status"] = "passed" if record["security_passed"] else "failed"
    except Exception:
        record["status"] = "failed"
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        record["cleanup"] = process.poll() is not None
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not record.get("security_passed"):
        raise RuntimeErrorWithCode("runtime security verification failed", 4)
    return output


def verify_artifact(protocol_path: Path) -> Path:
    protocol, protocol_sha = read_protocol(protocol_path)
    server, model = pinned_paths(protocol)
    expected_sha = protocol["model"]["artifact"]["sha256"]
    actual_sha = sha256_file(model)
    if actual_sha != expected_sha:
        raise RuntimeErrorWithCode("model artifact SHA-256 mismatch", 2)
    version = subprocess.run([str(server), "--version"], capture_output=True,
                             text=True, timeout=15, check=False)
    if version.returncode or COMMIT[:9] not in version.stdout + version.stderr:
        raise RuntimeErrorWithCode("runtime build mismatch", 2)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    key = secrets.token_urlsafe(32)
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith("LLAMA_ARG_") and name != "LLAMA_API_KEY"}
    environment["LLAMA_API_KEY"] = key
    arguments = server_arguments(server, model, port, protocol)
    output = runs_root() / "_verification" / protocol_sha / "artifact-verification.json"
    record: dict[str, Any] = {
        "verification_version": "1", "protocol_sha256": protocol_sha,
        "model_artifact_sha256": actual_sha, "runtime_build": BUILD,
        "runtime_commit": COMMIT, "model_alias": ALIAS, "status": "incomplete",
    }
    process = subprocess.Popen(arguments, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeErrorWithCode("runtime exited during artifact check", 3)
            try:
                if request(port, "GET", "/health", key)[0] == 200:
                    break
            except (OSError, TimeoutError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeErrorWithCode("runtime did not become healthy", 3)
        model_status, model_body = request(port, "GET", "/v1/models")
        if model_status != 200 or not _check_exempt_body("/v1/models", model_body, model):
            raise RuntimeErrorWithCode("model listing did not expose only the alias", 4)
        record.update(_fixture_checks(port, key, protocol))
        record["compatible"] = record["raw_capture_fidelity"]["match"]
        record["status"] = "passed" if record["compatible"] else "failed"
    except Exception:
        record["status"] = "failed"
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        record["cleanup"] = process.poll() is not None
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not record.get("compatible"):
        raise RuntimeErrorWithCode("model artifact is not compatible", 4)
    return output
