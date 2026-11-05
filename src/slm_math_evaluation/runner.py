"""Single keyed llama.cpp run with append-only raw item capture and safe resume."""

from __future__ import annotations

import ctypes
import http.client
import json
import math
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

from . import data, model_adapters, runtime
from .data import DataError, read_json, sha256_bytes, write_json
from .evaluation import (EXTRACTOR_ID, EXTRACTOR_VERSION, PRIMARY_PIN,
                         SECONDARY_PIN, extract_raw, extract_raw_with_state,
                         score_primary, score_secondary)
from .run_store import StoredRun, load_run


ADAPTER = {"id": "llama-cpp-chat-completions", "version": "1.0.0"}
PROVENANCE_LABELS = {"source-known-paper", "source-known-blog", "vendor-filled",
                     "vendor-template", "inferred", "local-choice"}


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
                and record.get("contract_version") !=
                ("v0.8.1" if protocol.get("model", {}).get("adapter") else "v0.6"))):
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
    try:
        adapter = model_adapters.from_protocol(protocol)
    except (AttributeError, TypeError, ValueError) as error:
        raise DataError(f"invalid model adapter or reasoning mode: {error}", 3) from error
    if protocol.get("role", "target") not in ("target", "fixture"):
        raise DataError("invalid model role", 3)
    new_protocol = protocol.get("model", {}).get("adapter") is not None
    if not new_protocol:
        return  # Preserve v0.7 protocol behavior and its regression fixtures.
    settings_with_provenance = [protocol["model"].get("artifact"),
                                protocol.get("runtime"), protocol.get("prompt"),
                                protocol.get("reasoning_mode")]
    settings_with_provenance.extend(protocol.get("sampling", {}).values())
    settings_with_provenance.extend(protocol.get("resources", {}).values())
    if any(not isinstance(setting, dict) or setting.get("provenance") not in PROVENANCE_LABELS
           for setting in settings_with_provenance):
        raise DataError("model protocol has a missing or invalid provenance label", 3)
    if not runtime.template_pin_valid(protocol, adapter):
        raise DataError("model chat template pin is missing or unsupported", 3)
    if adapter.id != "qwen3":
        conversion_hash = protocol["model"]["artifact"].get("conversion_record_sha256")
        if conversion_hash is not None and (not isinstance(conversion_hash, str)
                                            or len(conversion_hash) != 64
                                            or any(c not in "0123456789abcdef" for c in conversion_hash)):
            raise DataError("invalid conversion record SHA-256", 3)
    sampling = protocol.get("sampling", {})
    try:
        context = sampling["context_length"]["value"]
        stop = sampling["stop"]["value"]
        top_k = sampling["top_k"]["value"]
        min_p = sampling["min_p"]["value"]
        resources = {key: protocol["resources"][key]["value"] for key in
                     ("max_memory_gb", "min_free_memory_gb", "stuck_after_awake_s")}
    except (KeyError, TypeError) as error:
        raise DataError(f"incomplete model protocol: {error}", 3) from error
    if (type(context) is not int or context < 1
            or context != protocol.get("runtime", {}).get("context_size")
            or type(top_k) is not int or top_k < 0
            or not isinstance(min_p, (int, float)) or isinstance(min_p, bool)
            or not 0 <= min_p <= 1
            or not isinstance(stop, list) or any(not isinstance(item, str) or not item for item in stop)
            or not isinstance(resources["max_memory_gb"], (int, float))
            or isinstance(resources["max_memory_gb"], bool)
            or not math.isfinite(resources["max_memory_gb"])
            or resources["max_memory_gb"] <= 0
            or not isinstance(resources["min_free_memory_gb"], (int, float))
            or isinstance(resources["min_free_memory_gb"], bool)
            or not math.isfinite(resources["min_free_memory_gb"])
            or resources["min_free_memory_gb"] < 0
            or type(resources["stuck_after_awake_s"]) is not int
            or resources["stuck_after_awake_s"] < 1):
        raise DataError("invalid context, sampling, stop, or resource value", 3)


def _darwin_memory_bytes() -> int | None:
    """Use the macOS kernel API when POSIX physical-page counts are absent."""
    try:
        query = ctypes.CDLL(None).sysctlbyname
        query.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        query.restype = ctypes.c_int
        value = ctypes.c_uint64()
        length = ctypes.c_size_t(ctypes.sizeof(value))
        if query(b"hw.memsize", ctypes.byref(value), ctypes.byref(length), None, 0) == 0:
            if length.value == ctypes.sizeof(value) and value.value > 0:
                return value.value
    except (AttributeError, OSError):
        pass
    return None


def _host() -> dict[str, Any]:
    os_name, arch = platform.system(), platform.machine()
    if not os_name or not arch:
        raise DataError("host OS or architecture unavailable", 3)
    memory_bytes = None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        if page_size > 0 and pages > 0:
            memory_bytes = page_size * pages
    except (AttributeError, OSError, ValueError):
        pass
    if memory_bytes is None and os_name == "Darwin":
        memory_bytes = _darwin_memory_bytes()
    if memory_bytes is None or memory_bytes <= 0:
        raise DataError("host physical memory unavailable", 3)
    return {"os": os_name, "arch": arch,
            "memory_gb": round(memory_bytes / 2**30, 2)}


def _resource_host_check(protocol: dict[str, Any], host: dict[str, Any]) -> None:
    if protocol.get("model", {}).get("adapter") is None:
        return
    required = protocol["resources"]["max_memory_gb"]["value"]
    if host["memory_gb"] < required:
        raise DataError("host memory is below the model resource requirement", 3)


def _run_metadata(run_id: str, protocol: dict[str, Any], protocol_sha: str,
                  manifest_sha: str, tier: str, k: int, seed_base: int,
                  host: dict[str, Any], subset_sha: str | None = None) -> dict[str, Any]:
    document = {"run_id": run_id, "protocol_version": protocol["protocol_version"],
            "protocol_sha256": protocol_sha, "manifest_sha256": manifest_sha,
            "tier": tier, "k": k, "seeds": [seed_base + sample for sample in range(k)],
            "model_artifact_sha256": protocol["model"]["artifact"]["sha256"],
            "runtime_build": protocol["runtime"]["build"], "adapter": ADAPTER,
            "extractor": protocol["extraction"],
            "scorers": [protocol["scorers"][role] for role in ("primary", "secondary")],
            "executor": "slm-eval native harness", "host": host,
            "attempts": [], "status": "interrupted"}
    if protocol.get("model", {}).get("adapter") is not None:
        adapter = model_adapters.from_protocol(protocol)
        document.update(adapter={"id": adapter.id, "version": model_adapters.ADAPTER_VERSION},
                        role=protocol.get("role", "target"), subset_sha256=subset_sha,
                        chat_template_sha256=protocol["model"]["chat_template"]["sha256"],
                        events=[])
        document["model_source_revision"] = (None if document["role"] == "fixture" else
                                              getattr(adapter, "source_revision", None))
    elif subset_sha is not None:
        # A legacy subset run binds its subset copy; a no-subset legacy run.json is unchanged.
        document["subset_sha256"] = subset_sha
    return document


def start(protocol_path: Path, manifest_path: Path, tier: str, k: int, seed_base: int,
          control: Any = None, subset_path: Path | None = None) -> str:
    if k < 1 or seed_base < 0:
        raise DataError("k must be positive and seed-base nonnegative", 3)
    if not protocol_path.is_file() or not manifest_path.is_file():
        raise DataError("protocol or manifest missing", 3)
    if subset_path is not None and not subset_path.is_file():
        raise DataError("subset missing", 3)
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
    subset_bytes = None
    if subset_path is not None:
        # A legacy protocol may also use --subset; its extraction and scoring are unchanged.
        subset_bytes = subset_path.read_bytes()
        try:
            subset = json.loads(subset_bytes)
        except ValueError as error:
            raise DataError(f"invalid subset JSON: {error}", 3) from error
        ids = data.subset_ids(subset, sha256_bytes(manifest_bytes), tier, ids)
    _protocol_check(protocol)
    verification = _verification_copies(protocol_sha, protocol)
    _source_rows(manifest, ids)
    host = _host()
    _resource_host_check(protocol, host)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(5)
    directory = data.runs_root() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "protocol.yaml").write_bytes(protocol_bytes)
    (directory / "manifest.json").write_bytes(manifest_bytes)
    if subset_bytes is not None:
        (directory / "subset.json").write_bytes(subset_bytes)
    for name, content in verification.items():
        (directory / name).write_bytes(content)
    metadata = _run_metadata(run_id, protocol, protocol_sha, sha256_bytes(manifest_bytes),
                             tier, k, seed_base, host,
                             sha256_bytes(subset_bytes) if subset_bytes is not None else None)
    write_json(directory / "run.json", metadata)
    (directory / "items.jsonl").touch()
    # Expose the ID before generation so an interrupted run can be resumed.
    print(run_id, flush=True)
    if control is not None:
        control.run_created(run_id)
    _generate(directory, metadata, protocol, manifest, ids, set(), control)
    return run_id


def resume(run_id: str, control: Any = None) -> str:
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
    if run.subset is not None:  # The run-start copy, already checked against run.json.
        ids = data.subset_ids(run.subset, run.metadata["manifest_sha256"],
                              run.metadata["tier"], ids)
    existing = {(row["item_id"], row["sample_index"]) for row in run.items}
    required = {(item_id, sample) for item_id in ids for sample in range(run.metadata["k"])}
    if not existing <= required:
        raise DataError("stored item/sample pair outside run tier", 2)
    remaining = required - existing
    if control is not None:
        remaining = {pair for pair in remaining if not control.exhausted(*pair, run.metadata)}
        if not remaining:
            # The last pair was recorded before the crash that preceded this resume.
            run.metadata["status"] = "completed"
            write_json(run.directory / "run.json", run.metadata)
            return run_id
    if not remaining:
        raise DataError("no missing item/sample pairs to resume", 3)
    _source_rows(run.manifest, ids)
    _resource_host_check(run.protocol, _host())
    attempts = run.metadata["attempts"]
    if (control is None and run.metadata.get("events") is not None
            and attempts and attempts[-1].get("ended") is None
            and not interruption_recorded(run.metadata, attempts[-1]["n"])):
        # The previous direct attempt ended without a checkpoint: a crash, kill or
        # power loss. Nothing observed its item, so the event names none. The
        # runner records the same event for its steps.
        run.metadata["events"].append(_interrupted_event(
            {"reason": "previous_attempt_ended", "attempt": attempts[-1]["n"]}, 0.0))
    _generate(run.directory, run.metadata, run.protocol, run.manifest, ids, existing, control)
    return run_id


def _interrupted_event(detail: dict[str, Any], awake_s: float) -> dict[str, Any]:
    return {"type": "interrupted", "at_utc": _utc(), "awake_s": round(awake_s, 3), "detail": detail}


def interruption_recorded(metadata: dict[str, Any], attempt: Any) -> bool:
    """Whether an `interrupted` event already names this attempt; recovery adds no second."""
    return any(event.get("type") == "interrupted"
               and (event.get("detail") or {}).get("attempt") == attempt
               for event in metadata.get("events", []))


def _returned_output(body: bytes) -> str | None:
    """The text a generation response returned, without keeping or logging it."""
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


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
                             text=True, timeout=15, check=False,
                             env=runtime.llama_environment())
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
    effective = {name: value for name, value in configured.items()
                 if name not in ("provenance", "gpu_layers")}
    effective.update(selected)
    effective["requested_gpu_layers"] = argument("--n-gpu-layers")
    effective["port"] = port
    effective["log_verbosity"] = argument("--log-verbosity")
    effective["trace_logging"] = False
    if (effective["requested_gpu_layers"] != configured["gpu_layers"]
            or effective["log_verbosity"] != runtime.SERVER_LOG_VERBOSITY):
        raise DataError("runtime arguments differ from approved settings", 2)
    effective["reasoning_mode"] = protocol["reasoning_mode"]["value"]
    effective["request_sampling"] = {
        name: setting["value"] for name, setting in protocol["sampling"].items()
        if name != "seed_policy"
    }
    effective["generation_endpoint"] = "/v1/chat/completions"
    effective["stream"] = False
    effective["evidence"] = {"selected_threads": "explicit server arguments",
                             "context_and_slots": "keyed /props",
                             "other_settings": "explicit server arguments",
                             "actual_gpu_offload": "not exposed by keyed API at INFO verbosity"}
    if protocol.get("model", {}).get("adapter") is not None:
        effective["resources"] = {name: setting["value"] for name, setting in
                                  protocol["resources"].items()}
    return effective


def _generation_response(port: int, key: str, payload: dict[str, Any],
                         timeout: int | None) -> tuple[int, bytes]:
    try:
        return runtime.request(port, "POST", "/v1/chat/completions", key,
                               payload, timeout=timeout)
    except TimeoutError as error:
        raise DataError(f"generation response timed out after {timeout} seconds", 1) from error


class RunPaused(Exception):
    """Raised through a controlled run when the plan runner pauses it."""

    def __init__(self, now: bool) -> None:
        super().__init__("run paused immediately" if now else "run paused after item")
        self.now = now


class RunStopped(Exception):
    """Raised through a controlled run when a stop rule ends it for the maintainer."""


class InfraFailure(Exception):
    """An infrastructure failure of one item: server error, awake crash, or stuck item."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Interruption(Exception):
    """An item lost to sleep or a similar interruption; it never counts as a retry."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Server:
    """One keyed, loopback, ephemeral-port server process; restartable with a new key."""

    def __init__(self, server: Path, model: Path, protocol: dict[str, Any],
                 log_path: Path | None = None) -> None:
        self.server, self.model, self.protocol, self.log_path = server, model, protocol, log_path
        self.process: Any = None
        self.port = 0
        self.key = ""
        self.arguments: list[str] = []
        self.environment: dict[str, str] = {}

    def prepare(self) -> None:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.key = secrets.token_urlsafe(32)
        self.environment = runtime.llama_environment(self.key)
        self.arguments = runtime.server_arguments(self.server, self.model, self.port, self.protocol)

    def launch(self) -> None:
        output = self.log_path.open("ab") if self.log_path is not None else subprocess.DEVNULL
        try:
            self.process = subprocess.Popen(self.arguments, env=self.environment,
                                            stdin=subprocess.DEVNULL, stdout=output, stderr=output)
        finally:
            if self.log_path is not None:
                output.close()

    def wait_healthy(self) -> None:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise DataError("runtime exited during startup", 3)
            try:
                if runtime.request(self.port, "GET", "/health", self.key)[0] == 200:
                    return
            except (OSError, TimeoutError):
                pass
            time.sleep(.5)
        raise DataError("runtime did not become healthy", 3)

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        # A busy b10412 server answers the first SIGTERM with a graceful shutdown
        # that can wait on an in-flight request, so escalate to SIGKILL.
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)


def _generate(directory: Path, metadata: dict[str, Any], protocol: dict[str, Any],
              manifest: dict[str, Any], ids: list[str], existing: set[tuple[str, int]],
              control: Any = None) -> None:
    """Generate missing pairs. ``control`` is the plan runner's hook (§13); None is `run`."""
    adapter = model_adapters.from_protocol(protocol)
    new_protocol = protocol.get("model", {}).get("adapter") is not None
    server_path, model = runtime.pinned_paths(protocol)
    metadata["runtime_files_verified"] = {
        "archive_sha256": runtime.ARCHIVE_SHA256,
        "regular_files_checked": len(runtime.REGULAR_SHA256),
        "symlinks_checked": len(runtime.SYMLINK_TARGETS),
    }
    if runtime.sha256_file(model) != protocol["model"]["artifact"]["sha256"]:
        raise DataError("model artifact SHA-256 mismatch", 2)
    if new_protocol:
        runtime.verify_chat_template(protocol, model)
    _check_runtime_version(server_path)
    session = _Server(server_path, model, protocol,
                      control.server_log if control is not None else None)
    session.prepare()
    attempt = {"n": len(metadata["attempts"]) + 1, "started": _utc(), "ended": None,
               "status": "interrupted", "resumed_from": metadata["attempts"][-1]["n"] if metadata["attempts"] else None}
    metadata["attempts"].append(attempt)
    awake_start = time.monotonic()  # Does not advance during system sleep (macOS, Linux).
    # The pair in progress and the output its current attempt actually returned, if any.
    # Only its thinking state is ever persisted (§7 interruption states), never the text.
    observed: dict[str, Any] = {"pair": None, "raw": None}

    def observed_state() -> str | None:
        raw = observed["raw"]
        return adapter.split(raw)[2] if new_protocol and raw is not None else None
    if control is not None:
        control.bind(directory, metadata, attempt)
    write_json(directory / "run.json", metadata)
    session.launch()
    if control is not None:
        control.server_started(session)
    previous_signals = runtime.install_cleanup_signals()
    try:
        session.wait_healthy()
        effective = _effective_settings(protocol, session.port, session.key, session.arguments)
        attempt["effective_runtime_settings"] = effective
        metadata["effective_runtime_settings"] = effective
        write_json(directory / "run.json", metadata)
        rows = _source_rows(manifest, ids)
        settings = {name: protocol["sampling"][name]["value"] for name in
                    ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "repeat_penalty")}
        settings["max_tokens"] = protocol["sampling"]["max_output_tokens"]["value"]
        template_args = runtime.template_arguments(protocol) if new_protocol else {}
        stop = protocol["sampling"]["stop"]["value"] if new_protocol else None
        if new_protocol:
            reasoning_flag, _ = adapter.settings(protocol["reasoning_mode"]["value"])
            metadata["effective"] = {"reasoning": {"mode": protocol["reasoning_mode"]["value"],
                                                    "server_flag": reasoning_flag},
                                     "template_args": template_args,
                                     "sampling": {**settings, "stop": stop,
                                                  "context_length": protocol["sampling"]["context_length"]["value"]}}
            write_json(directory / "run.json", metadata)
        timeout = (None if new_protocol else runtime.generation_timeout(settings["max_tokens"]))
        attempt["generation_timeout_seconds"] = timeout
        metadata["generation_timeout_seconds"] = timeout
        write_json(directory / "run.json", metadata)

        def restart() -> None:
            session.stop()
            control.server_stopped(session)
            session.prepare()
            session.launch()
            control.server_started(session)
            session.wait_healthy()
            _effective_settings(protocol, session.port, session.key, session.arguments)
            control.event("server_restart", {"port": session.port})

        total = len(ids) * metadata["k"]
        with (directory / "items.jsonl").open("a", encoding="utf-8") as stream:
            for item_id in ids:
                source = rows[item_id]
                prompt, messages = _prompt(protocol, source["problem"])
                for sample in range(metadata["k"]):
                    if (item_id, sample) in existing:
                        continue
                    if control is not None and control.exhausted(item_id, sample):
                        continue
                    observed.update(pair=(item_id, sample), raw=None)
                    seed = metadata["seeds"][sample]
                    payload = {"model": adapter.alias, "messages": messages,
                               "seed": seed, "stream": False, **settings}
                    if new_protocol:
                        payload["add_generation_prompt"] = True
                        payload["chat_template_kwargs"] = template_args
                        payload["stop"] = stop

                    def generate_once() -> tuple[str, bytes, dict[str, Any]]:
                        observed["raw"] = None  # Each attempt is observed on its own.
                        if new_protocol:
                            try:
                                rendered = runtime.render_prompt(session.port, session.key,
                                                                 messages, protocol)
                            except runtime.RuntimeErrorWithCode as error:
                                raise DataError(str(error), error.code) from error
                        else:
                            rendered = prompt

                        def send() -> tuple[int, bytes]:
                            return _generation_response(session.port, session.key, payload, timeout)
                        status, body = send() if control is None else control.generate(session, send)
                        if status != 200:
                            raise DataError(f"generation returned HTTP {status}", 1)
                        observed["raw"] = _returned_output(body)
                        try:
                            confirmed = runtime.confirm_sampling(session.port, session.key,
                                                                 {"seed": seed, **settings})
                        except runtime.RuntimeErrorWithCode as error:
                            raise DataError(str(error), error.code) from error
                        return rendered, body, confirmed

                    retries = 0
                    if control is None:
                        started = time.monotonic()
                        rendered_prompt, body, confirmed = generate_once()
                        timing_ms = awake_ms = round((time.monotonic() - started) * 1000)
                    else:
                        control.before_item(item_id, sample)
                        retries = control.retries_used(item_id, sample)
                        while True:
                            awake_start, wall_start = control.clock.awake(), control.clock.continuous()
                            try:
                                rendered_prompt, body, confirmed = generate_once()
                                break
                            except (InfraFailure, Interruption, OSError,
                                    http.client.HTTPException) as failure:
                                classified = control.classify(failure, session)
                            if isinstance(classified, Interruption):
                                control.interrupted(item_id, sample, classified.reason,
                                                    observed_state())
                                restart()
                                continue
                            if not control.infra_failure(item_id, sample, classified.reason,
                                                         retries, total):
                                rendered_prompt = None
                                break
                            retries += 1
                            restart()
                        if rendered_prompt is None:
                            if not session.alive():
                                restart()
                            continue
                        timing_ms = round((control.clock.continuous() - wall_start) * 1000)
                        awake_ms = round((control.clock.awake() - awake_start) * 1000)
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
                    if new_protocol:
                        thinking, thinking_state, extraction = extract_raw_with_state(raw, truncated, adapter)
                    else:
                        thinking, extraction = extract_raw(raw, truncated)
                    answer, reference = extraction["answer"], source["answer"]
                    verdicts = {
                        "primary": score_primary(answer, reference)["correct"],
                        "secondary": score_secondary(answer, reference)["correct"],
                    }
                    record = {"item_id": item_id, "sample_index": sample, "seed": seed,
                              "prompt_sha256": sha256_bytes(rendered_prompt.encode("utf-8")),
                              "raw_output": raw, "raw_output_sha256": sha256_bytes(raw.encode("utf-8")),
                              "thinking_present": thinking, "truncated": truncated,
                              "finish_reason": reason, "tokens_generated": response.get("usage", {}).get("completion_tokens"),
                              "reference_answer": reference, "reference_sha256": sha256_bytes(reference.encode("utf-8")),
                              "extraction": extraction, "verdicts": verdicts,
                              "timing_ms": timing_ms}
                    if new_protocol:
                        record.update(thinking_state=thinking_state, infra_retries=retries,
                                      awake_ms=awake_ms)
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                    observed.update(pair=None, raw=None)
                    if control is not None:
                        control.item_done(item_id, sample)
        metadata["status"] = "completed"
        attempt["status"] = "completed"
    except RunPaused:
        metadata["status"] = "paused"
        attempt["status"] = "paused"
        raise
    except (RunStopped, DataError, runtime.RuntimeErrorWithCode):
        stopped = control is not None
        metadata["status"] = "stopped" if stopped else "interrupted"
        attempt["status"] = "aborted" if stopped else "interrupted"
        raise
    except BaseException as error:
        metadata["status"] = "interrupted"
        attempt["status"] = "interrupted"
        if new_protocol:
            # §6.1: the item in progress is never written; the interruption is an event,
            # bound to that item when there is one, with the state its attempt returned.
            detail = ({"reason": "signal"} if isinstance(error, KeyboardInterrupt) else
                      {"reason": "error", "error": type(error).__name__})
            detail["attempt"] = attempt["n"]
            if observed["pair"] is not None:
                detail.update(item_id=observed["pair"][0], sample_index=observed["pair"][1])
                state = observed_state()
                if state is not None:
                    detail["thinking_state"] = state
            metadata["events"].append(_interrupted_event(detail, time.monotonic() - awake_start))
        raise
    finally:
        session.stop()
        if control is not None:
            control.server_stopped(session)
        runtime.restore_cleanup_signals(previous_signals)
        attempt["ended"] = _utc()
        write_json(directory / "run.json", metadata)
