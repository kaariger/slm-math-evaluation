"""The pinned, keyed llama.cpp server boundary for the MATH-500 path."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
from threading import current_thread, main_thread
import time
from typing import Any

import yaml

from .data import cache_root, runs_root


BUILD = "b10412"
COMMIT = "0d0bfcd4fd8828e3e7906b6fc4561725b534511e"
ALIAS = "qwen3-8b"
SERVER_SHA256 = "d3bce60d45758268a90e0fca82ce5a22d5c35ecb92a06d1c544ed68ec2efa769"
SERVER_IMPL_SHA256 = "e969ffd4ba1973700cec7b870527f6fc981db27f215e6db22b68afc57977ea3d"
SERVER_LOG_VERBOSITY = 3  # INFO; TRACE (4) prints an API-key suffix.
# The feasibility gate is 10 generated tokens/s; the observed Q4 rate was
# about 23 tokens/s. Leave 120 s for prompt evaluation and runtime variance.
MIN_GENERATION_TOKENS_PER_SECOND = 10
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

# Asset names extracted from the pinned b10412 server library. Its auth
# middleware treats every embedded UI path as public even with --no-webui;
# disabled UI must therefore return only the same fixed not-found body.
WEB_UI_PATHS = ("/",
    "/index.html",
    "/_app/immutable/assets/bundle.Dnj2pKqv.css",
    "/_app/immutable/bundle.DHRVET94.js",
    "/_app/version.json",
    "/apple-splash-landscape-1136x640.png",
    "/apple-splash-landscape-1334x750.png",
    "/apple-splash-landscape-2266x1488.png",
    "/apple-splash-landscape-2360x1640.png",
    "/apple-splash-landscape-2388x1668.png",
    "/apple-splash-landscape-2532x1170.png",
    "/apple-splash-landscape-2556x1179.png",
    "/apple-splash-landscape-2622x1206.png",
    "/apple-splash-landscape-2732x2048.png",
    "/apple-splash-landscape-2778x1284.png",
    "/apple-splash-landscape-2796x1290.png",
    "/apple-splash-landscape-2868x1320.png",
    "/apple-splash-landscape-dark-1136x640.png",
    "/apple-splash-landscape-dark-1334x750.png",
    "/apple-splash-landscape-dark-2266x1488.png",
    "/apple-splash-landscape-dark-2360x1640.png",
    "/apple-splash-landscape-dark-2388x1668.png",
    "/apple-splash-landscape-dark-2532x1170.png",
    "/apple-splash-landscape-dark-2556x1179.png",
    "/apple-splash-landscape-dark-2622x1206.png",
    "/apple-splash-landscape-dark-2732x2048.png",
    "/apple-splash-landscape-dark-2778x1284.png",
    "/apple-splash-landscape-dark-2796x1290.png",
    "/apple-splash-landscape-dark-2868x1320.png",
    "/apple-splash-portrait-1170x2532.png",
    "/apple-splash-portrait-1179x2556.png",
    "/apple-splash-portrait-1206x2622.png",
    "/apple-splash-portrait-1284x2778.png",
    "/apple-splash-portrait-1290x2796.png",
    "/apple-splash-portrait-1320x2868.png",
    "/apple-splash-portrait-1488x2266.png",
    "/apple-splash-portrait-1640x2360.png",
    "/apple-splash-portrait-1668x2388.png",
    "/apple-splash-portrait-2048x2732.png",
    "/apple-splash-portrait-640x1136.png",
    "/apple-splash-portrait-750x1334.png",
    "/apple-splash-portrait-dark-1170x2532.png",
    "/apple-splash-portrait-dark-1179x2556.png",
    "/apple-splash-portrait-dark-1206x2622.png",
    "/apple-splash-portrait-dark-1284x2778.png",
    "/apple-splash-portrait-dark-1290x2796.png",
    "/apple-splash-portrait-dark-1320x2868.png",
    "/apple-splash-portrait-dark-1488x2266.png",
    "/apple-splash-portrait-dark-1640x2360.png",
    "/apple-splash-portrait-dark-1668x2388.png",
    "/apple-splash-portrait-dark-2048x2732.png",
    "/apple-splash-portrait-dark-640x1136.png",
    "/apple-splash-portrait-dark-750x1334.png",
    "/apple-touch-icon-180x180.png",
    "/build.json",
    "/favicon-dark.ico",
    "/favicon-dark.svg",
    "/favicon.ico",
    "/favicon.svg",
    "/manifest.webmanifest",
    "/maskable-icon-512x512.png",
    "/pwa-192x192.png",
    "/pwa-512x512.png",
    "/pwa-64x64.png",
    "/recommended-mcp/context7.png",
    "/recommended-mcp/exa.ico",
    "/recommended-mcp/github-dark.png",
    "/recommended-mcp/github-light.png",
    "/recommended-mcp/huggingface.ico",
    "/sw.js",
    "/workbox-b3c04f83.js",
)


class RuntimeErrorWithCode(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def install_cleanup_signals() -> dict[signal.Signals, Any]:
    """Turn process termination into an exception so server finally runs."""
    if current_thread() is not main_thread():
        return {}
    previous = {}

    def interrupt(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            sig = getattr(signal, name)
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, interrupt)
    return previous


def restore_cleanup_signals(previous: dict[signal.Signals, Any]) -> None:
    for sig, handler in previous.items():
        signal.signal(sig, handler)


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
    server_impl = server.parent / "libllama-server-impl.dylib"
    model = root / name
    if not server.is_file() or not server_impl.is_file() or not model.is_file():
        raise RuntimeErrorWithCode("pinned runtime or model artifact is missing", 3)
    if sha256_file(server) != SERVER_SHA256 or sha256_file(server_impl) != SERVER_IMPL_SHA256:
        raise RuntimeErrorWithCode("pinned runtime binary SHA-256 mismatch", 2)
    return server, model


def generation_timeout(max_tokens: int) -> int:
    if not isinstance(max_tokens, int) or max_tokens < 1:
        raise RuntimeErrorWithCode("max output tokens must be positive", 3)
    return 120 + math.ceil(max_tokens / MIN_GENERATION_TOKENS_PER_SECOND)


def request(port: int, method: str, path: str, key: str | None = None,
            payload: dict[str, Any] | None = None, *, timeout: float = 30) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["Authorization"] = "Bearer " + key
    body = b"{" if method == "POST" and payload is None else None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


SAMPLING_FIELDS = ("temperature", "top_p", "top_k", "min_p",
                   "presence_penalty", "repeat_penalty", "max_tokens", "seed")


def confirm_sampling(port: int, key: str, requested: dict[str, Any]) -> dict[str, Any]:
    """Read the server's actual last-request parameters from its keyed slot."""
    status, body = request(port, "GET", "/slots", key)
    if status != 200:
        raise RuntimeErrorWithCode("server did not expose keyed slot parameters", 4)
    try:
        slots = json.loads(body)
        if not isinstance(slots, list) or len(slots) != 1:
            raise ValueError("expected exactly one slot")
        params = slots[0]["params"]
        confirmed = {name: params[name] for name in SAMPLING_FIELDS}
        if params["n_predict"] != requested["max_tokens"]:
            raise ValueError("n_predict differs")
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeErrorWithCode(f"server sampling confirmation incomplete: {error}", 4) from error
    for name, expected in requested.items():
        actual = confirmed[name]
        match = (math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-8)
                 if isinstance(expected, float) else actual == expected)
        if not match:
            raise RuntimeErrorWithCode(f"server sampling differs: {name}", 4)
    return confirmed


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
    logical_cores = os.cpu_count()
    if not logical_cores or logical_cores < 1:
        raise RuntimeErrorWithCode("host CPU count unavailable", 3)
    # Resolve approved -1 auto settings before launch, then pass the exact
    # values. This makes them observable without enabling the server's trace
    # logger, which prints a fragment of the API key.
    threads = logical_cores if threads == -1 else threads
    threads_batch = threads if threads_batch == -1 else threads_batch
    if threads < 1 or threads_batch < 1:
        raise RuntimeErrorWithCode("invalid resolved thread count", 3)
    http_threads = max(parallel + 4, logical_cores - 1)
    if (runtime.get("cache_type_k") != "f16" or runtime.get("cache_type_v") != "f16"
            or runtime.get("load_mode") != "mmap" or runtime.get("context_shift") is not False
            or runtime.get("jinja") is not True or runtime.get("reasoning_budget") != -1):
        raise RuntimeErrorWithCode("unsupported runtime settings in protocol", 3)
    reasoning = protocol.get("reasoning_mode", {}).get("value")
    if reasoning not in ("hybrid", "forced_think", "forced_non_think"):
        raise RuntimeErrorWithCode("invalid reasoning mode", 3)
    reasoning_arg = {"hybrid": "auto", "forced_think": "on", "forced_non_think": "off"}[reasoning]
    return [str(server), "--model", str(model), "--alias", ALIAS,
            "--log-verbosity", str(SERVER_LOG_VERBOSITY),
            "--host", "127.0.0.1", "--port", str(port),
            "--threads", str(threads), "--threads-batch", str(threads_batch),
            "--threads-http", str(http_threads),
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


def _fixed_not_found(body: bytes) -> bool:
    try:
        return json.loads(body) == {"error": {"message": "File Not Found",
                                               "type": "not_found_error", "code": 404}}
    except ValueError:
        return False


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
    confirmed = confirm_sampling(port, key, {"seed": 42, **fixture_parameters})
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
    stream_confirmed = confirm_sampling(port, key, {"seed": 42, **fixture_parameters})
    streamed = _stream_content(stream_body)
    match = content == streamed
    return {
        "parameter_equivalence": {
            "configured": configured,
            "submitted": fixture_parameters,
            "fixture_overrides": {"temperature": 0, "max_tokens": 16},
            "server_confirmed": confirmed,
            "stream_server_confirmed": stream_confirmed,
            "request_mapping_checked": True,
            "match": True,
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
        "verification_version": "1", "contract_version": "v0.6",
        "protocol_sha256": protocol_sha, "runtime_build": BUILD,
        "runtime_commit": COMMIT, "model_artifact_sha256": protocol["model"]["artifact"]["sha256"],
        "model_alias": ALIAS, "bind": "127.0.0.1", "ephemeral_port": port,
        "routes": [], "web_ui_paths": [], "options_probes": [], "unexpected_open_routes": [],
        "status": "incomplete",
    }
    process = subprocess.Popen(arguments, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    previous_signals = install_cleanup_signals()
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
        fixed_not_found_body: bytes | None = None
        for method, path in ROUTES + tuple(("GET", path) for path in WEB_UI_PATHS):
            without, body = request(port, method, path)
            try:
                with_key, _ = request(port, method, path, key)
            except (OSError, TimeoutError):
                with_key = None
            ui_path = path in WEB_UI_PATHS
            exempt = (method, path) in EXEMPT
            fixed_not_found = (ui_path and without == 404 and with_key == 404
                               and _fixed_not_found(body))
            if fixed_not_found:
                if fixed_not_found_body is None:
                    fixed_not_found_body = body
                fixed_not_found = body == fixed_not_found_body
            row = {"method": method, "path": path, "without_key": without,
                   "with_key": with_key,
                   "classification": ("fixed_not_found" if fixed_not_found else
                                      "exempt" if exempt else "protected")}
            if ui_path:
                row["body"] = (body.decode("utf-8") if fixed_not_found
                               else "<unexpected body omitted>")
                row["without_key_body"] = row["body"]
                row["without_key_body_length"] = len(body)
                record["web_ui_paths"].append(dict(row))
            if exempt:
                row["no_sensitive_data"] = _check_exempt_body(path, body, model)
                if without != 200 or with_key != 200 or not row["no_sensitive_data"]:
                    record["unexpected_open_routes"].append({"method": method, "path": path})
            elif fixed_not_found:
                pass
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
                       "classification": "exempt" if ui_path else "cors_preflight"}
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
        restore_cleanup_signals(previous_signals)
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
    previous_signals = install_cleanup_signals()
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
        restore_cleanup_signals(previous_signals)
        record["cleanup"] = process.poll() is not None
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not record.get("compatible"):
        raise RuntimeErrorWithCode("model artifact is not compatible", 4)
    return output
