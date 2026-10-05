# 0001: Use llama.cpp for the first local Qwen runtime

- Status: Accepted
- Date: 2026-10-05
- Feasibility evidence: initial run 2026-08-13; re-run 2026-10-05

## Context

The first model path needs to run Qwen3-8B locally on an Apple M1 Pro with
32 GB of unified memory. The runtime must expose the model and runtime
revisions, prompt formatting, generation controls, raw output, and enough
performance to make the first MATH-500 path practical.

The focused candidate set was llama.cpp, MLX-LM, Transformers with MPS, and
Ollama. Only the leading candidate needed an experiment; this decision does not
claim a comprehensive performance comparison.

## Decision

Use a pinned llama.cpp runtime with Qwen's official Q4_K_M GGUF as the first
local Qwen3-8B runtime:

- llama.cpp release: `b10412`
- llama.cpp commit: `0d0bfcd4fd8828e3e7906b6fc4561725b534511e`
- macOS ARM64 archive SHA-256:
  `94f48fad6d22117f0c85524cd8b76dab482902aedde30f628f91bc7c4ac9135c`
- model repository: `Qwen/Qwen3-8B-GGUF`
- model repository revision:
  `7c41481f57cb95916b40956ab2f0b139b296d974`
- model file: `Qwen3-8B-Q4_K_M.gguf`
- model file SHA-256:
  `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`

The runtime build, model repository revision, model file digest, quantization,
chat-template behavior, reasoning mode, and generation options are part of run
provenance. Q4_K_M is an intentional quantized representation and must not be
reported as interchangeable with an unquantized checkpoint. The GGUF metadata
names its source model `Qwen3 8B Awq Compatible Instruct`.

This decision selects the runtime family and first tested artifacts. It does
not yet choose whether the production adapter will invoke a process or a local
server.

## Feasibility evidence

The pinned artifacts were first tested on 2026-08-13 and re-run on 2026-10-05
on an ARM64 Apple M1 Pro with 32 GB of unified memory. Both local artifacts
matched their published SHA-256 digests. On 2026-10-05 those digests were also
confirmed against the GitHub release asset digest and the Git LFS object ID at
the pinned model revision.

The deterministic smoke prompt ran with a 4,096-token context, all model layers
requested for GPU offload, flash attention enabled, reasoning disabled through
both `--reasoning off` and Qwen's `/no_think` prompt switch, temperature zero,
and a fixed seed. Both runs returned the expected answer (`391`) and reported
no swap. On 2026-10-05, `--reasoning off` alone, with the same prompt minus
`/no_think`, also suppressed reasoning: the chat template inserted an empty
thinking block, and the run answered `391` after 4 generated tokens without a
reasoning trace. A separate one-repetition feasibility benchmark confirmed the
`MTL,BLAS` backends, Apple M1 Pro GPU, flash attention, and requested GPU layer
offload.

| Observation | 2026-08-13 | 2026-10-05 |
| --- | --- | --- |
| macOS | 26.6.1 | 26.6.2 |
| Smoke invocation time | 15.08 s (cold, first run) | 1.84 s (warm) |
| Smoke maximum resident set | 4,568,186,880 bytes | 5,735,251,968 bytes |
| Smoke prompt / generation | 65.6 / 22.9 tokens/s | 102.2 / 23.6 tokens/s |
| Benchmark prompt, 64 tokens | 199.0 tokens/s | 202.1 tokens/s |
| Benchmark generation, 32 tokens | 23.6 tokens/s | 23.6 tokens/s |

These are feasibility observations, not stable benchmark claims; end-to-end
MATH-500 throughput belongs to later vertical-path work.

Reasoning mode exposes an important configuration risk. On 2026-08-13 a bounded
reasoning-mode check, whose exact prompt was not recorded, derived the correct
boxed answer at approximately 23 generated tokens/second but used about 870
reasoning tokens, and a 1,024-token total generation cap cut off its final
response. On 2026-10-05 the smoke prompt without `/no_think`, run with
reasoning enabled, greedy sampling, and the same 1,024-token cap, completed with
`391` after 836 generated tokens at 23.3 tokens/second. The benchmark
prompt-protocol work must select and validate reasoning mode, context size, and
generation budget against representative benchmark prompts; a derivation
present only in the reasoning trace is not a completed answer.

The pinned CLI applies the GGUF-embedded chat template and supports explicit
reasoning control, sampling options, context limits, and timing output. Its
default console output is a formatted display with a banner, a prompt echo,
thinking markers, and timing lines, not a raw generation stream; the adapter
must select and verify a raw-capture path. Its current implementation starts an
ephemeral server for a CLI invocation. In the 2026-10-05 run it listened only on
`127.0.0.1`, without an API key or TLS, and the runtime warned that CORS allows
all origins; the pinned CLI's help lists no option to set an API key or
restrict CORS. Loopback binding therefore does not isolate the endpoint from
web pages in a local browser while a run is in progress. A future adapter must
keep that endpoint local, account for this cross-origin exposure, and must not
expose it as a production service by accident.

## Reproduction

The model and runtime belong under the ignored `models/` directory. Download
the release asset `llama-b10412-bin-macos-arm64.tar.gz` and the model file
`Qwen3-8B-Q4_K_M.gguf` from the pinned revision into `models/`, then extract
the archive there with
`tar -xzf models/llama-b10412-bin-macos-arm64.tar.gz -C models`, which creates
`models/llama-b10412/`. These are the validator's default paths;
`--runtime-archive`, `--runtime-directory`, and `--model` override them. Then
run:

```bash
poetry run python scripts/validate_runtime_feasibility.py
```

The script rechecks the archive and model digests, the runtime build and
commit, the answer, the maximum resident set and per-process swap counters
reported by macOS `time -l`, active Metal backend, GPU offload request, flash
attention, and conservative feasibility floors. It does not hash the extracted
runtime files; the 2026-10-05 re-run separately confirmed that they match the
archive members. Invocation time and throughput can vary with system caches and
load; the recorded 15.08-second observation was the first cold run. The
validator reads the whole model file to check its digest just before the smoke
run, so that run normally starts with the model in the file cache. On
2026-10-05 the first launch of the runtime from a new directory spent about 10
seconds before its first log line. The current conservative gates require no
swap, no more than 16 GiB maximum resident memory, and at least 10 prompt and
generation tokens/second. These gates preserve half the machine's unified
memory as headroom and catch a clearly impractical regression; they are not
evaluation performance claims. The validator requires local loopback and Metal
access.
The authoritative upstream records are the
[llama.cpp b10412 release](https://github.com/ggml-org/llama.cpp/releases/tag/b10412),
[pinned Qwen llama.cpp guide](https://github.com/QwenLM/Qwen3/blob/1d87faccb53bea03824f71dc92a17c410dfb7f99/docs/source/run_locally/llama.cpp.md),
and [pinned official model file](https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/7c41481f57cb95916b40956ab2f0b139b296d974/Qwen3-8B-Q4_K_M.gguf).

## Alternatives considered

- MLX-LM is optimized for Apple silicon and remains the first fallback. Qwen
  also publishes official MLX quantizations, such as `Qwen/Qwen3-8B-MLX-4bit`;
  MLX-LM was not tested because the pinned llama.cpp path passed first.
- Transformers with MPS preserves a direct path to the original Hugging Face
  checkpoint, but has a heavier Python/PyTorch dependency and memory path for
  this 32 GB machine. It was not necessary to install it after llama.cpp passed.
- Ollama offers a convenient local interface but adds an abstraction over model
  packaging and runtime controls. Direct llama.cpp made the first provenance
  boundary easier to inspect.

## Consequences

- The first runtime adapter should remain narrow and runtime-specific.
- The benchmark prompt/reasoning protocol remains a separate methodology
  decision; this feasibility prompt does not set it. That protocol work must
  ensure the configured token budget leaves room for a complete final answer.
- Model weights, native binaries, caches, and smoke output remain local and
  ignored by Git.
- A later failure in integration or methodology can reopen this decision using
  MLX-LM as the first fallback without changing dataset or scoring boundaries.
- Hosted execution remains out of scope because local feasibility was
  demonstrated.
