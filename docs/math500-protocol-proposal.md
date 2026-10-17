# MATH-500 protocol decisions

The maintainer approved the values below for `configs/protocols/protocol.yaml`
(`v1`, Q4_K_M) and `configs/protocols/math500-v1-q8.yaml` (`v1-q8`, Q8_0).
The files differ only in `protocol_version` and `model.artifact`. Their version
names identify these exact files. Dev-tier experiments may use a separately
versioned protocol; an approved version is never silently rewritten.

| Setting | Approved value | Provenance | Reason |
|---|---:|---|---|
| Output cap | 14,000 tokens | source-known-paper | Maintainer-approved literal reading of the paper's “14k”. |
| Context size | 16,384 tokens | local-choice | Allows the proposed output cap plus a bounded prompt in the first local path. Long prompts could reduce the available output budget. |
| GPU layers | 99 requested | local-choice | Requests full offload on the selected Apple Silicon machine. The keyed API does not expose the actual offload count at safe logging verbosity. |
| Flash attention | on | local-choice | Matches the pinned runtime's successful local feasibility check. |
| CPU threads, batch threads | -1, -1 (harness selection) | local-choice | The harness selects host counts and passes explicit thread arguments; effective values are recorded per run. |
| Logical, physical batch sizes | 2,048, 512 | vendor-filled | Explicitly pins the selected server build's defaults. |
| KV cache K/V types | f16, f16 | vendor-filled | Explicitly pins the selected server build's defaults. |
| Model load mode | mmap | local-choice | Uses a mapped local GGUF without copying the full artifact into process memory. |
| Context shift | off | local-choice | Makes the output cap terminal instead of silently shifting context. |
| Parallel slots | 1 | local-choice | Keeps the run-scoped server to one generation at a time. |
| Bind, port, UI | loopback, ephemeral, disabled | local-choice | Implements contract §10. |
| Model alias | `qwen3-8b` | local-choice | Implements the contract's neutral alias. |
| Reasoning | hybrid; raw reasoning format | inferred / local-choice | Uses template auto reasoning and leaves generated thinking markers in raw content. |
| Chat template, reasoning budget | embedded Jinja template, unrestricted (-1) | vendor-filled / local-choice | Uses the GGUF template and leaves the hybrid reasoning budget unrestricted within the overall output cap. |
| Temperature, top-p | 0.6, 0.95 | source-known-paper | Contract defaults approved by the maintainer. |
| Top-k, min-p | 20, 0 | vendor-filled | Contract defaults approved by the maintainer. |
| Presence, repeat penalty | 0, 1 | local-choice | No added penalty in the first path. |
| Seed | `seed_base + sample_index` | local-choice | Gives each sample a reproducible request seed. |
| Default seed base, samples per item | 0, 1 (CLI overrides available) | local-choice | Makes a single local run deterministic when the caller omits both flags. |
| Prompt, system message, examples | `{problem}` + `\n\n` + `Please reason step by step, and put your final answer within \boxed{}.`; no system message; zero examples | vendor-filled | The rendered user message must match these bytes exactly. |
| Chat response transport | non-streaming `/v1/chat/completions` | local-choice | Preserves the complete raw content in one response; streamed equivalence was checked by the runtime fixture. |

The harness sets CPU, batch, and HTTP thread counts from the host CPU count and
passes explicit server arguments. It records those effective values in `run.json` before
generation. The server is fixed at INFO verbosity (3); TRACE (4) is disabled
because the pinned server prints a key fragment at trace level. `run.json`
labels the GPU layer cap as requested, since the keyed API does not expose the
actual offload count at INFO verbosity. The keyed `/props` endpoint verifies context size,
slot count, alias, and disabled UI. Keyed `/slots` confirms each generation
request's sampling values and seed; mismatches stop the run. Each attempt
records its effective settings and confirmed sampling. A generation request's
read timeout is 120 seconds plus the 14,000-token cap divided by the 5
tokens/second conservative floor (2,920 seconds for the approved cap). Each
attempt and the top-level run record store the effective timeout.

`v1-ft` and `v1-nt` are reasoning-mode probes based on Q4 `v1`. They change
only the protocol version and the reasoning-mode factor, to `forced_think` and
`forced_non_think` respectively. They preserve the exact user-message prompt.
No tier results are implied by these protocol files.

The server starts with the listed runtime settings. The verification fixture
temporarily uses temperature 0 and a 16-token output cap to compare streamed
and non-streamed raw content without conducting a benchmark run. The extractor
is `boxed-last-v1` 1.0.0. The primary scorer is the PRM800K grader at dataset
commit `7ecc794703b2877f63226f2477a49b34f9b25163`; the secondary is
`math-verify` 0.8.0. These pins are part of the approved protocol files.
