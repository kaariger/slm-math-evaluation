import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY_ROOT / "scripts" / "validate_runtime_feasibility.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_runtime_feasibility", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_validator()

BANNER = (
    "Loading model...\n\n"
    "build      : b10412-0d0bfcd4f\n"
    "model      : models/391/Qwen3-8B-Q4_K_M.gguf\n\n"
)
TIMINGS = "\n[ Prompt: 66.2 t/s | Generation: 23.3 t/s ]\n\n\nExiting...\n"


def smoke_stdout(response: str, timings: str = TIMINGS) -> str:
    return f"{BANNER}> {validator.SMOKE_PROMPT}\n{response}{timings}"


def time_stderr(resident: int = 5738217472, swaps: int = 0) -> str:
    return (
        "        2.05 real         0.24 user         0.71 sys\n"
        f"          {resident}  maximum resident set size\n"
        f"                   {swaps}  swaps\n"
    )


def benchmark_records(**prompt_fields) -> list:
    prompt = {
        "n_prompt": 64,
        "n_gen": 0,
        "backends": "MTL,BLAS",
        "flash_attn": 1,
        "n_gpu_layers": 99,
        "build_number": 10412,
        "build_commit": "0d0bfcd4f",
        "avg_ts": 201.867,
        "gpu_info": "Apple M1 Pro",
    }
    generation = dict(prompt, n_prompt=0, n_gen=32, avg_ts=23.832)
    prompt.update(prompt_fields)
    return [prompt, generation]


def process_is_running(pid: int) -> bool:
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


class SmokeOutputTests(unittest.TestCase):
    def test_accepts_answer_in_response(self) -> None:
        result = validator.check_smoke(smoke_stdout("391\n"), time_stderr())
        self.assertEqual(result["answer"], "391")
        self.assertEqual(result["maximum_resident_set_bytes"], 5738217472)
        self.assertEqual(result["swaps"], 0)
        self.assertEqual(result["prompt_tokens_per_second"], 66.2)
        self.assertEqual(result["generation_tokens_per_second"], 23.3)

    def test_ignores_answer_in_banner_and_timings(self) -> None:
        stdout = smoke_stdout(
            "390\n", "\n[ Prompt: 391.0 t/s | Generation: 23.3 t/s ]\n"
        )
        with self.assertRaisesRegex(validator.ValidationError, "expected answer"):
            validator.check_smoke(stdout, time_stderr())

    def test_ignores_answer_in_reasoning_trace(self) -> None:
        response = "[Start thinking]\n17 * 23 = 391\n[End thinking]\n\n390\n"
        with self.assertRaisesRegex(validator.ValidationError, "expected answer"):
            validator.check_smoke(smoke_stdout(response), time_stderr())

    def test_accepts_answer_after_reasoning_trace(self) -> None:
        response = "[Start thinking]\nworking\n[End thinking]\n\n391\n"
        result = validator.check_smoke(smoke_stdout(response), time_stderr())
        self.assertEqual(result["answer"], "391")

    def test_unfinished_reasoning_trace_has_no_answer(self) -> None:
        response = "[Start thinking]\n17 * 23 = 391\n"
        with self.assertRaisesRegex(validator.ValidationError, "expected answer"):
            validator.check_smoke(smoke_stdout(response), time_stderr())

    def test_requires_prompt_echo(self) -> None:
        stdout = BANNER + "391\n" + TIMINGS
        with self.assertRaisesRegex(validator.ValidationError, "echo"):
            validator.check_smoke(stdout, time_stderr())

    def test_requires_timings(self) -> None:
        with self.assertRaisesRegex(validator.ValidationError, "timings"):
            validator.check_smoke(smoke_stdout("391\n", "\nExiting...\n"), time_stderr())

    def test_rejects_swap(self) -> None:
        with self.assertRaisesRegex(validator.ValidationError, "swap"):
            validator.check_smoke(smoke_stdout("391\n"), time_stderr(swaps=3))

    def test_rejects_resident_memory_above_ceiling(self) -> None:
        with self.assertRaisesRegex(validator.ValidationError, "16 GiB"):
            validator.check_smoke(
                smoke_stdout("391\n"), time_stderr(resident=16 * 1024**3 + 1)
            )

    def test_rejects_throughput_below_floor(self) -> None:
        stdout = smoke_stdout("391\n", "\n[ Prompt: 9.9 t/s | Generation: 23.3 t/s ]\n")
        with self.assertRaisesRegex(validator.ValidationError, "floor"):
            validator.check_smoke(stdout, time_stderr())

    def test_requires_memory_and_swap_evidence(self) -> None:
        with self.assertRaisesRegex(validator.ValidationError, "memory and swap"):
            validator.check_smoke(smoke_stdout("391\n"), "")


class BenchmarkOutputTests(unittest.TestCase):
    def test_summarizes_valid_records(self) -> None:
        result = validator.check_benchmark(json.dumps(benchmark_records()))
        self.assertEqual(
            result,
            {
                "backends": "MTL,BLAS",
                "generation_tokens_per_second": 23.832,
                "gpu": "Apple M1 Pro",
                "prompt_tokens_per_second": 201.867,
            },
        )

    def test_rejects_invalid_json(self) -> None:
        with self.assertRaisesRegex(validator.ValidationError, "valid JSON"):
            validator.check_benchmark("not json")

    def test_rejects_non_list_json(self) -> None:
        with self.assertRaisesRegex(validator.ValidationError, "list of benchmark records"):
            validator.check_benchmark(json.dumps({"n_prompt": 64}))

    def test_rejects_missing_field(self) -> None:
        records = benchmark_records()
        del records[0]["backends"]
        with self.assertRaisesRegex(validator.ValidationError, "missing or malformed"):
            validator.check_benchmark(json.dumps(records))

    def test_rejects_malformed_field(self) -> None:
        records = benchmark_records(backends=None)
        with self.assertRaisesRegex(validator.ValidationError, "missing or malformed"):
            validator.check_benchmark(json.dumps(records))

    def test_rejects_missing_record(self) -> None:
        records = benchmark_records()[:1]
        with self.assertRaisesRegex(validator.ValidationError, "omitted"):
            validator.check_benchmark(json.dumps(records))

    def test_rejects_inactive_metal_backend(self) -> None:
        records = benchmark_records(backends="CPU")
        with self.assertRaisesRegex(validator.ValidationError, "Metal backend"):
            validator.check_benchmark(json.dumps(records))

    def test_rejects_changed_offload_settings(self) -> None:
        records = benchmark_records(flash_attn=0)
        with self.assertRaisesRegex(validator.ValidationError, "settings changed"):
            validator.check_benchmark(json.dumps(records))

    def test_rejects_other_build(self) -> None:
        records = benchmark_records(build_number=10413)
        with self.assertRaisesRegex(validator.ValidationError, "pinned runtime"):
            validator.check_benchmark(json.dumps(records))

    def test_rejects_throughput_below_floor(self) -> None:
        records = benchmark_records(avg_ts=9.5)
        with self.assertRaisesRegex(validator.ValidationError, "floor"):
            validator.check_benchmark(json.dumps(records))


class CommandTests(unittest.TestCase):
    def test_timeout_kills_process_group(self) -> None:
        parent = (
            "import subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "open(sys.argv[1], 'w').write(str(child.pid))\n"
            "child.wait()\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "grandchild.pid"
            with self.assertRaisesRegex(validator.ValidationError, "timed out"):
                validator.run_checked(
                    [sys.executable, "-c", parent, str(pid_file)], timeout=2
                )
            grandchild = int(pid_file.read_text())

        deadline = time.monotonic() + 5
        while process_is_running(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(process_is_running(grandchild))

    def test_strips_llama_arg_environment(self) -> None:
        overrides = {
            "LLAMA_ARG_REASONING": "on",
            "LLAMA_ARG_CHAT_TEMPLATE": "chatml",
            "KEEP_THIS_VARIABLE": "1",
        }
        with mock.patch.dict(os.environ, overrides):
            completed = validator.run_checked(
                [sys.executable, "-c", "import json, os; print(json.dumps(sorted(os.environ)))"],
                timeout=30,
            )
        names = json.loads(completed.stdout)
        self.assertIn("KEEP_THIS_VARIABLE", names)
        self.assertEqual([name for name in names if name.startswith("LLAMA_ARG_")], [])

    def test_reports_nonzero_exit(self) -> None:
        command = [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
        with self.assertRaisesRegex(validator.ValidationError, "exit code 3(.|\n)*boom"):
            validator.run_checked(command, timeout=30)

    def test_checks_runtime_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            llama_cli = Path(directory) / "llama-cli"
            llama_cli.write_text(
                "#!/bin/sh\necho 'version: 0.1.0-dev (build 10412, commit 0d0bfcd4f)'\n",
                encoding="utf-8",
            )
            llama_cli.chmod(0o755)
            self.assertIn("build 10412", validator.validate_runtime_version(llama_cli))

            llama_cli.write_text(
                "#!/bin/sh\necho 'version: 0.1.0-dev (build 10413, commit 1234567ab)'\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(validator.ValidationError, "unexpected llama.cpp version"):
                validator.validate_runtime_version(llama_cli)


class ArtifactTests(unittest.TestCase):
    def test_reports_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(validator.ValidationError, "llama-cli is missing"):
                validator.require_file(Path(directory) / "llama-cli", "llama-cli")

    def test_checks_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "model.gguf"
            artifact.write_bytes(b"model bytes")
            expected = hashlib.sha256(b"model bytes").hexdigest()
            validator.require_digest(artifact, expected, "model")
            with self.assertRaisesRegex(validator.ValidationError, "SHA-256 mismatch"):
                validator.require_digest(artifact, "0" * 64, "model")


if __name__ == "__main__":
    unittest.main()
