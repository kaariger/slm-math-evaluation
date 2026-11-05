"""Pinned local HF-to-GGUF conversion and fixture-scale sanity checks (§12)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from typing import Any, Iterator
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import data, runner, runtime
from .gguf_metadata import chat_template


SOURCE_COMMIT = "0d0bfcd4fd8828e3e7906b6fc4561725b534511e"
SOURCE_ARCHIVE_SHA256 = "ce8b09b263886ffcf011b632a2e62d04136f8cfdcd7f7f417d77e962d0dcc977"
SCRIPT_SHA256 = "e38975e1c68d98ac1664dfd530616eb35c72294382a4dd873d4746b23f27779f"
GIB = 2 ** 30
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*\Z")
LOCK = Path(__file__).resolve().parents[2] / "tooling/conversion/poetry.lock"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def scratch_root() -> Path:
    return Path(os.environ.get("SLM_EVAL_SCRATCH", "~/.cache/slm-eval/scratch")).expanduser()


def _digest(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _relative_file(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in (".", "..", "") for part in path.parts)


def read_spec(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        spec = yaml.safe_load(raw)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise data.DataError(f"invalid model spec: {error}", 3) from error
    try:
        name, source, conversion, sizes, sanity = (spec["name"], spec["source"],
                                                   spec["conversion"], spec["expected_sizes_gb"],
                                                   spec["sanity"])
        files = source["files"]
        tool = conversion["tool"]
        reference = sanity["reference"]
        valid = (
            spec["spec_version"] == "1" and isinstance(name, str) and NAME.fullmatch(name)
            and spec["role"] in ("target", "fixture")
            and isinstance(source["repo"], str) and re.fullmatch(r"[\w.-]+/[\w.-]+", source["repo"])
            and HEX40.fullmatch(source["commit"])
            and isinstance(files, list) and bool(files)
            and all(_relative_file(row["path"]) and _digest(row["sha256"]) for row in files)
            and len({row["path"] for row in files}) == len(files)
            and isinstance(spec["architecture"], str) and bool(spec["architecture"])
            and spec["text_only"] is True
            and type(spec["template"]["inserts_system_message"]) is bool
            and spec["template"]["thinking_control"] in ("none", "always", "template_arg", "runtime_flag")
            and tool["release"] == runtime.BUILD and tool["script_sha256"] == SCRIPT_SHA256
            and _digest(tool["python_lock_sha256"])
            and conversion["intermediate"] == "bf16"
            and conversion["quantization"] == "Q4_K_M"
            and conversion["calibration"] == "none"
            and all(isinstance(sizes[key], (int, float)) and not isinstance(sizes[key], bool)
                    and math.isfinite(sizes[key]) and sizes[key] > 0
                    for key in ("source", "intermediate", "output"))
            and (reference is None or (isinstance(reference["repo"], str)
                 and re.fullmatch(r"[\w.-]+/[\w.-]+", reference["repo"])
                 and HEX40.fullmatch(reference["commit"])
                 and _relative_file(reference["file"]) and _digest(reference["sha256"])))
            and _digest(sanity["perplexity_text_sha256"])
            and isinstance(sanity["max_perplexity_ratio"], (int, float))
            and not isinstance(sanity["max_perplexity_ratio"], bool)
            and math.isfinite(sanity["max_perplexity_ratio"])
            and sanity["max_perplexity_ratio"] > 0
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise data.DataError("model spec does not match contract §12.1", 3)
    if spec["conversion"]["tool"]["python_lock_sha256"] != data.sha256_file(LOCK):
        raise data.DataError("conversion Python lock SHA-256 mismatch", 2)
    return spec, data.sha256_bytes(raw)


def _floor(name: str, default: float) -> int:
    try:
        value = float(os.environ.get(name, default))
    except ValueError as error:
        raise data.DataError(f"invalid {name}", 3) from error
    if not math.isfinite(value) or value < 0:
        raise data.DataError(f"invalid {name}", 3)
    return math.ceil(value * GIB)


def _space_check(scratch: Path, models: Path, scratch_peak: float = 0,
                 cache_peak: float = 0) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    models.mkdir(parents=True, exist_ok=True)
    scratch_floor = _floor("SLM_EVAL_SCRATCH_FLOOR_GB", 10)
    cache_floor = _floor("SLM_EVAL_FLOOR_GB", 20)
    scratch_need = math.ceil(scratch_peak * GIB)
    cache_need = math.ceil(cache_peak * GIB)
    if scratch.stat().st_dev == models.stat().st_dev:
        free = shutil.disk_usage(scratch).free
        if free - scratch_need - cache_need <= max(scratch_floor, cache_floor):
            raise data.DataError("model conversion disk floor would be breached", 3)
    elif (shutil.disk_usage(scratch).free - scratch_need <= scratch_floor
          or shutil.disk_usage(models).free - cache_need <= cache_floor):
        raise data.DataError("model conversion disk floor would be breached", 3)


def _large_file_check(scratch: Path) -> None:
    try:
        bits = os.pathconf(scratch, "PC_FILESIZEBITS")
    except (OSError, ValueError) as error:
        raise data.DataError("scratch maximum file size is unavailable", 3) from error
    if bits <= 32:
        raise data.DataError("scratch cannot hold files larger than 4 GB", 3)


def _download(url: str, path: Path, expected: str, scratch: Path, models: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise data.DataError("publisher destination is a symbolic link", 3)
    if path.exists():
        if data.sha256_file(path) != expected:
            raise data.DataError("completed publisher file SHA-256 mismatch", 2)
        return path
    partial = path.with_name(path.name + ".partial")
    if partial.is_symlink():
        raise data.DataError("partial publisher destination is a symbolic link", 3)
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    token = os.environ.get("HF_TOKEN")
    if token and url.startswith("https://huggingface.co/"):
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = response.status
            if offset and status == 206:
                content_range = response.headers.get("Content-Range", "")
                if not content_range.startswith(f"bytes {offset}-"):
                    raise data.DataError("resumed download range differs", 2)
            elif offset and status != 200:
                raise data.DataError("resumed download was not accepted", 1)
            with partial.open("ab" if offset and status == 206 else "wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
                    _space_check(scratch, models)
                stream.flush()
                os.fsync(stream.fileno())
    except (urllib.error.URLError, TimeoutError) as error:
        raise data.DataError("pinned download failed; partial file retained", 1) from error
    if data.sha256_file(partial) != expected:
        raise data.DataError("downloaded publisher file SHA-256 mismatch", 2)
    partial.replace(path)
    return path


def _archive(scratch: Path, models: Path) -> Path:
    path = Path(os.environ.get("SLM_EVAL_LLAMA_SOURCE_ARCHIVE",
                               scratch / "tooling/llama-b10412-source.tar.gz"))
    _space_check(scratch, models, scratch_peak=0.1)
    return _download(f"https://codeload.github.com/ggml-org/llama.cpp/tar.gz/{SOURCE_COMMIT}",
                     path, SOURCE_ARCHIVE_SHA256, scratch, models)


@contextmanager
def _source_tree(archive: Path, scratch: Path) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(dir=scratch, prefix="llama-source-") as directory:
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(directory, filter="data")
        root = Path(directory) / f"llama.cpp-{SOURCE_COMMIT}"
        script = root / "convert_hf_to_gguf.py"
        if not script.is_file() or data.sha256_file(script) != SCRIPT_SHA256:
            raise data.DataError("pinned converter script SHA-256 mismatch", 2)
        yield root


def _tools(scratch: Path, models: Path) -> tuple[Path, Path, Path]:
    python_value = os.environ.get("SLM_EVAL_CONVERTER_PYTHON")
    if not python_value:
        raise data.DataError("SLM_EVAL_CONVERTER_PYTHON is required", 3)
    python = Path(python_value)
    binary_dir = Path(os.environ.get("SLM_EVAL_LLAMA_BIN", models / f"llama-{runtime.BUILD}"))
    if not python.is_file() or not os.access(python, os.X_OK):
        raise data.DataError("separate conversion Python is unavailable", 3)
    _verify_python(python, scratch)
    try:
        runtime.verify_runtime_files(binary_dir)
    except runtime.RuntimeErrorWithCode as error:
        raise data.DataError(str(error), error.code) from error
    return python, binary_dir / "llama-quantize", binary_dir / "llama-perplexity"


def _verify_python(python: Path, scratch: Path) -> None:
    """Check the selected interpreter against every applicable locked package."""
    check = """
import importlib.metadata, sys, tomllib
from packaging.markers import Marker
assert sys.prefix != sys.base_prefix, "a separate virtual environment is required"
assert (3, 11) <= sys.version_info[:2] < (3, 14), "unsupported conversion Python"
with open(sys.argv[1], "rb") as stream:
    lock = tomllib.load(stream)
for package in lock["package"]:
    marker = package.get("markers")
    if marker is None or Marker(marker).evaluate():
        assert importlib.metadata.version(package["name"]) == package["version"], package["name"]
"""
    result = subprocess.run([str(python), "-I", "-c", check, str(LOCK)],
                            env=_environment(scratch), capture_output=True, check=False)
    if result.returncode:
        raise data.DataError("conversion Python environment differs from the lock", 2)


def _run_monitored(command: list[str], scratch: Path, models: Path,
                   environment: dict[str, str]) -> None:
    process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while process.poll() is None:
            _space_check(scratch, models)
            time.sleep(0.5)
        _space_check(scratch, models)
        if process.returncode:
            raise data.DataError("pinned conversion step failed", 1)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        raise


def _environment(scratch: Path) -> dict[str, str]:
    home = scratch / "huggingface"
    temp = scratch / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    # The tools run offline from verified local files, so no credential reaches them.
    environment = {key: value for key, value in runtime.credential_free_environment().items()
                   if key not in ("NO_LOCAL_GGUF", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
                   and not key.startswith("LLAMA_ARG_")}
    environment.update(HF_HOME=str(home), HF_HUB_CACHE=str(home / "hub"),
                       TMPDIR=str(temp), TORCH_HOME=str(scratch / "torch"),
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return environment


def _state(path: Path, spec_sha: str) -> dict[str, Any]:
    if path.exists():
        state = data.read_json(path)
        if state.get("spec_sha256") != spec_sha:
            raise data.DataError("conversion checkpoint spec SHA-256 mismatch", 2)
        return state
    return {"spec_sha256": spec_sha, "started_utc": _utc()}


def _verified_step(path: Path, state: dict[str, Any], name: str, cache: bool) -> str | None:
    expected = state.get(name)
    if path.exists():
        if _digest(expected) and data.sha256_file(path) == expected:
            return expected
        if cache:
            raise data.DataError("existing model output does not match the checkpoint", 2)
        path.unlink()  # Scratch output is rebuildable from verified publisher files.
    partial = path.with_name(path.name + ".partial")
    partial.unlink(missing_ok=True)
    return None


def convert(spec_path: Path, clean_scratch: bool = False) -> list[Path]:
    spec, spec_sha = read_spec(spec_path)
    scratch = scratch_root()
    models = data.cache_root() / "models"
    _space_check(scratch, models)
    python, quantize, _ = _tools(scratch, models)
    work = scratch / "conversion" / f"{spec['name']}-{spec_sha[:16]}"
    output = models / f"{spec['name']}-Q4_K_M.gguf"
    record_path = models / f"{spec['name']}-conversion-record.json"
    if record_path.exists():
        record = data.read_json(record_path)
        expected_files = [{"path": row["path"], "sha256_expected": row["sha256"],
                           "sha256_observed": row["sha256"]} for row in spec["source"]["files"]]
        if (not output.is_file() or record.get("spec_sha256") != spec_sha
                or record.get("source") != {"repo": spec["source"]["repo"],
                                            "commit": spec["source"]["commit"],
                                            "files": expected_files}
                or record.get("tool") != spec["conversion"]["tool"]
                or record.get("output", {}).get("sha256") != data.sha256_file(output)):
            raise data.DataError("existing conversion record does not match model output", 2)
        if clean_scratch and work.exists():
            shutil.rmtree(work)
        return [output, record_path]
    _space_check(scratch, models, scratch_peak=spec["expected_sizes_gb"]["source"])
    _large_file_check(scratch)
    archive = _archive(scratch, models)
    work.mkdir(parents=True, exist_ok=True)
    state_path = work / "steps.json"
    state = _state(state_path, spec_sha)
    source_dir = work / "source"
    source_dir.mkdir(exist_ok=True)
    observed = []
    for row in spec["source"]["files"]:
        _space_check(scratch, models, scratch_peak=spec["expected_sizes_gb"]["source"])
        destination = source_dir / row["path"]
        url = (f"https://huggingface.co/{spec['source']['repo']}/resolve/"
               f"{spec['source']['commit']}/{urllib.parse.quote(row['path'], safe='/')}")
        _download(url, destination, row["sha256"], scratch, models)
        observed.append({"path": row["path"], "sha256_expected": row["sha256"],
                         "sha256_observed": data.sha256_file(destination)})
    try:
        architecture = json.loads((source_dir / "config.json").read_text())["architectures"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        raise data.DataError("source config architecture is unavailable", 2) from error
    if architecture != spec["architecture"]:
        raise data.DataError("source architecture differs from model spec", 2)
    intermediate = work / f"{spec['name']}-bf16.gguf"
    with _source_tree(archive, scratch) as source_root:
        if _verified_step(intermediate, state, "intermediate_sha256", cache=False) is None:
            _space_check(scratch, models, scratch_peak=spec["expected_sizes_gb"]["intermediate"])
            partial = intermediate.with_name(intermediate.name + ".partial")
            _run_monitored([str(python), str(source_root / "convert_hf_to_gguf.py"),
                            str(source_dir), "--outtype", "bf16", "--outfile", str(partial)],
                           scratch, models, _environment(scratch))
            if not partial.is_file():
                raise data.DataError("converter did not write the BF16 intermediate", 1)
            state["intermediate_sha256"] = data.sha256_file(partial)
            data.write_json(state_path, state)
            partial.replace(intermediate)
    if _verified_step(output, state, "output_sha256", cache=True) is None:
        _space_check(scratch, models, cache_peak=spec["expected_sizes_gb"]["output"])
        partial = output.with_name(output.name + ".partial")
        _run_monitored([str(quantize), str(intermediate), str(partial), "Q4_K_M"],
                       scratch, models, _environment(scratch))
        if not partial.is_file():
            raise data.DataError("quantizer did not write the Q4_K_M output", 1)
        state["output_sha256"] = data.sha256_file(partial)
        data.write_json(state_path, state)
        partial.replace(output)
    try:
        template_sha = data.sha256_bytes(chat_template(output).encode("utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        raise data.DataError("converted output has no readable chat template", 2) from error
    record = {"record_version": "1", "spec_sha256": spec_sha,
              "source": {"repo": spec["source"]["repo"], "commit": spec["source"]["commit"],
                         "files": observed},
              "tool": spec["conversion"]["tool"],
              "intermediate": {"type": "bf16", "sha256": state["intermediate_sha256"]},
              "output": {"file": output.name, "sha256": state["output_sha256"],
                         "quantization": "Q4_K_M", "calibration": "none"},
              "chat_template_sha256": template_sha,
              "started_utc": state["started_utc"], "ended_utc": _utc(),
              "host": runner._host()}
    data.write_json(record_path, record)
    if clean_scratch:
        shutil.rmtree(work)
    return [output, record_path]


def _perplexity(binary: Path, model: Path, text_path: Path) -> float:
    result = subprocess.run([str(binary), "--model", str(model), "--file", str(text_path),
                             "--ctx-size", "128", "--device", "none", "--fit", "off",
                             "--n-gpu-layers", "0", "--no-warmup"],
                            capture_output=True, text=True, env=runtime.llama_environment(),
                            check=False)
    output = result.stdout + result.stderr
    match = re.search(r"Final estimate: PPL = ([0-9]+(?:\.[0-9]+)?)", output)
    if result.returncode or match is None:
        raise data.DataError("pinned perplexity check failed", 1)
    value = float(match.group(1))
    if not math.isfinite(value) or value <= 0:
        raise data.DataError("pinned perplexity result is invalid", 1)
    return value


def sanity(spec_path: Path) -> Path:
    spec, spec_sha = read_spec(spec_path)
    scratch = scratch_root()
    models = data.cache_root() / "models"
    _space_check(scratch, models)
    _, _, perplexity = _tools(scratch, models)
    output = models / f"{spec['name']}-Q4_K_M.gguf"
    conversion_record = models / f"{spec['name']}-conversion-record.json"
    record = data.read_json(conversion_record)
    if (record.get("spec_sha256") != spec_sha or not output.is_file()
            or record.get("output", {}).get("sha256") != data.sha256_file(output)):
        raise data.DataError("sanity input does not match the conversion record", 2)
    text_value = os.environ.get("SLM_EVAL_SANITY_TEXT")
    if not text_value:
        raise data.DataError("SLM_EVAL_SANITY_TEXT is required", 3)
    text_path = Path(text_value)
    if not text_path.is_file() or data.sha256_file(text_path) != spec["sanity"]["perplexity_text_sha256"]:
        raise data.DataError("fixed sanity text SHA-256 mismatch", 2)
    converted = _perplexity(perplexity, output, text_path)
    reference_spec = spec["sanity"]["reference"]
    reference_value = None
    ratio = None
    if reference_spec is not None:
        _space_check(scratch, models, scratch_peak=max(spec["expected_sizes_gb"]["source"],
                                                        spec["expected_sizes_gb"]["intermediate"]))
        reference_path = scratch / "reference" / spec["name"] / reference_spec["file"]
        url = (f"https://huggingface.co/{reference_spec['repo']}/resolve/"
               f"{reference_spec['commit']}/{urllib.parse.quote(reference_spec['file'], safe='/')}")
        _download(url, reference_path, reference_spec["sha256"], scratch, models)
        reference_value = _perplexity(perplexity, reference_path, text_path)
        ratio = converted / reference_value
    passed = ratio is None or ratio <= spec["sanity"]["max_perplexity_ratio"]
    sanity_record = {"record_version": "1", "spec_sha256": spec_sha,
                     "conversion_record_sha256": data.sha256_file(conversion_record),
                     "output_sha256": data.sha256_file(output),
                     "perplexity_text_sha256": data.sha256_file(text_path),
                     "converted_perplexity": converted,
                     "reference_perplexity": reference_value, "ratio": ratio,
                     "max_perplexity_ratio": spec["sanity"]["max_perplexity_ratio"],
                     "smoke_tier_required": True, "status": "passed" if passed else "failed"}
    destination = models / f"{spec['name']}-sanity-record.json"
    data.write_json(destination, sanity_record)
    if not passed:
        raise data.DataError("reference perplexity ratio exceeds the spec limit", 1)
    return destination
