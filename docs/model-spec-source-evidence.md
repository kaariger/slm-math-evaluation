# Target model spec source evidence

- Status: field decisions approved by the maintainer. These are metadata only:
  they authorize no download, conversion, sanity run or evaluation.
- Date: 2026-11-09
- Specs: [`phi-4-reasoning`](../configs/models/phi-4-reasoning.yaml),
  [`phi-4-reasoning-plus`](../configs/models/phi-4-reasoning-plus.yaml),
  [`gemma-4-12b`](../configs/models/gemma-4-12b.yaml)
- Decisions: [0002 Phi-4-reasoning](decisions/0002-phi-4-reasoning-target.md),
  [0003 Phi-4-reasoning-plus](decisions/0003-phi-4-reasoning-plus-target.md),
  [0004 Gemma 4 12B IT](decisions/0004-gemma-4-12b-target.md),
  [target protocol proposals](target-protocol-proposals.md)
- Contract: [interface contract v0.8.1](math500-interface-contract-v0.8.1.md) §12.1

The three specs are complete §12.1 conversion specs for the accepted targets.
They pin each source, the converter, planning sizes and sanity inputs. Source
selections, template pins and study settings come from the accepted decisions
and the [adapter source evidence](model-adapter-source-evidence.md), and are
unchanged.

## Sources

| Spec | Source | Revision | Entries | Source bytes |
|---|---|---|---|---|
| `phi-4-reasoning` | `microsoft/Phi-4-reasoning` | [`1de18ec97600877ce63dbf60c73b998da99f0195`](https://huggingface.co/microsoft/Phi-4-reasoning/commit/1de18ec97600877ce63dbf60c73b998da99f0195) | 13: six weight shards, seven metadata files | 29,327,849,488 (27.314 GiB) |
| `phi-4-reasoning-plus` | `microsoft/Phi-4-reasoning-plus` | [`69baf8528e1bcf05f475034d9e5dd32875ed125f`](https://huggingface.co/microsoft/Phi-4-reasoning-plus/commit/69baf8528e1bcf05f475034d9e5dd32875ed125f) | 13: six weight shards, seven metadata files | 29,327,849,488 (27.314 GiB) |
| `gemma-4-12b` | `google/gemma-4-12B-it` | [`43c9d014b231474fa7212dbb48a762566c2b255d`](https://huggingface.co/google/gemma-4-12B-it/commit/43c9d014b231474fa7212dbb48a762566c2b255d) | 7: weight file, tokenizer, five metadata files | 23,951,734,406 (22.307 GiB) |

Each set lists every conversion and tokenizer input in the publisher's tree at
that revision. Documentation and license files aren't converter inputs. Both
Phi `model.safetensors.index.json` maps cover exactly the six listed shards.

## Hash basis

Each spec entry records a path and a SHA-256 only. The SHA-256 has one of two
bases:

- **Publisher LFS SHA-256.** For Git LFS files, the SHA-256 is the one in the
  publisher's tree at the pinned revision. These payloads weren't downloaded.
  This covers each Phi model's six `model-0000N-of-00006.safetensors` shards,
  and Gemma's `model.safetensors` (23,919,549,408 bytes) and `tokenizer.json`
  (32,157,161 bytes).
- **Locally computed, publisher-corroborated.** For ordinary Git files, the
  SHA-256 was computed from fetched bytes whose Git blob ID and size matched the
  publisher's tree at the pinned revision. This doesn't claim that the
  publisher published these SHA-256 values directly.

| Model | File | Bytes | Git blob ID |
|---|---|---|---|
| Phi-4-reasoning, Phi-4-reasoning-plus | `config.json` | 796 | `2e02e6d85b0d12b97a86486b48da9582789a84a5` |
| Phi-4-reasoning, Phi-4-reasoning-plus | `generation_config.json` | 222 | `649fd15bb5ed715022d23f3d7fe9167aa438d179` |
| Phi-4-reasoning, Phi-4-reasoning-plus | `model.safetensors.index.json` | 20,364 | `d9c4e28d4f7cc72949fdc86940c8a67583750436` |
| Phi-4-reasoning, Phi-4-reasoning-plus | `special_tokens_map.json` | 431 | `27191d3f9dbc836d4dc18eec8f6a5d07e1deb309` |
| Phi-4-reasoning, Phi-4-reasoning-plus | `tokenizer.json` | 7,153,070 | `44e48a4d87380f0810e3896f39a9315e1e259e4a` |
| Phi-4-reasoning, Phi-4-reasoning-plus | `tokenizer_config.json` | 18,985 | `cbe31b763ce812bdf37e486044fca80949bc7982` |
| Phi-4-reasoning, Phi-4-reasoning-plus | `vocab.json` | 1,612,628 | `a3bc034f6adc81ad7c7b63b3d89867275a705be2` |
| Gemma 4 12B IT | `chat_template.jinja` | 18,683 | `4741bf6e4132ba23a5537f9d6e74e9a6d613d7cd` |
| Gemma 4 12B IT | `config.json` | 4,423 | `324c841b79e36f42e27f08c572c327533469c859` |
| Gemma 4 12B IT | `generation_config.json` | 260 | `d09dccf1b9aee197ef72a6e88a1ceff74b842ff3` |
| Gemma 4 12B IT | `processor_config.json` | 1,382 | `b889adcd78015bf5a0139438c5fb0b7067529a61` |
| Gemma 4 12B IT | `tokenizer_config.json` | 3,089 | `6520cdc25462a5eba96fdac6d8cf3caa461feb35` |

The two Phi revisions reuse identical metadata Git objects; only their weight
shards differ. Where these files already appear in the
[adapter source evidence](model-adapter-source-evidence.md) or the
[protocol proposals](target-protocol-proposals.md#publisher-metadata-used),
their SHA-256 values are the same. The converter compares each downloaded
file with the spec and stops on a mismatch (§12.2).

## Converter and template declarations

All three specs use the converter pins from the
[converter fixture evidence](model-converter-fixture-evidence.md): llama.cpp
[`b10412`](https://github.com/ggml-org/llama.cpp/releases/tag/b10412) at
`0d0bfcd4fd8828e3e7906b6fc4561725b534511e`, `convert_hf_to_gguf.py`
`e38975e1c68d98ac1664dfd530616eb35c72294382a4dd873d4746b23f27779f`, and the
[conversion lock](../tooling/conversion/poetry.lock)
`068e45ece468b5166f84d297791b0596b97be24710a3e26326aba9b76ed6a103`. Each
converts to a BF16 intermediate and then Q4_K_M, with no calibration and
`text_only: true`; for Gemma's multimodal source only the text model is
converted.

| Spec | `architecture` | `inserts_system_message` | `thinking_control` |
|---|---|---|---|
| `phi-4-reasoning`, `phi-4-reasoning-plus` | `Phi3ForCausalLM` | `true` | `always` |
| `gemma-4-12b` | `Gemma4UnifiedForConditionalGeneration` | `true` | `template_arg` |

Both pinned Phi templates insert the publisher's own system prompt, and the
model always thinks. Gemma's default thinking-on template inserts a system turn
starting `<|think|>`, and its thought channel is a template argument. These
record existing template facts; they don't choose a reasoning mode or freeze a
protocol.

## Planning sizes

The converter reads `expected_sizes_gb` as GiB (1024³ bytes).

| Spec | Source | Intermediate | Output | BF16 source payload and metadata |
|---|---|---|---|---|
| Both Phi specs | 32 | 32 | 12 | 27.314 GiB |
| `gemma-4-12b` | 28 | 28 | 10 | 22.307 GiB |

Source and intermediate allowances exceed the publisher's BF16 payload and
metadata, rounded up with headroom. Output allowances are conservative budgets
for a mixed-precision Q4_K_M file. All six are planning estimates, not measured
output sizes, hard upper bounds or peak-memory measurements. The converter
still checks actual free space against the disk floors before each step and
during long writes (§12.3), and a drive and proof preflight is still required
before any conversion.

## Sanity inputs

**Fixed text.** All three specs name the existing synthetic, non-math fixed
text: 18,640 bytes, SHA-256
`6a5f0f0322d941de9d5e0f78be9120b2f44f892328ad2a693b8034bae29b0ec3`. It is
repetitive and narrow by design, for an inexpensive conversion check. It isn't a
representative language benchmark or evidence about MATH accuracy. It is kept
outside the repository and is never committed.

**Phi.** Both specs keep the accepted `reference: null` smoke route. The generic
sanity command may record the converted file's absolute perplexity, but no
reference ratio is evaluated. The schema requires a positive ratio, so
`max_perplexity_ratio: 1.1` is present but unused. The smoke run belongs to the
later runner plan.

**Gemma.** The reference is the publisher BF16 GGUF:

| Item | Value |
|---|---|
| Repository and revision | `ggml-org/gemma-4-12B-it-GGUF` at [`790d826c6ec7dc8aa0ab23af33e5b8313140a231`](https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/commit/790d826c6ec7dc8aa0ab23af33e5b8313140a231) |
| File | `gemma-4-12B-it-BF16.gguf`, 23,832,061,120 bytes (22.195 GiB) |
| SHA-256 | `7ddcd5e95ca59f07c2f76fb6c233cf29bae66bced50b36827d6391cb26fe3e3c`, the publisher's LFS SHA-256 |
| Declared source | [`.src_sha`](https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/blob/790d826c6ec7dc8aa0ab23af33e5b8313140a231/.src_sha) gives PRIMARY `43c9d014b231474fa7212dbb48a762566c2b255d`, the approved source revision |

That commit updates the BF16 and Q8 primary files. The repository's Q4_0 file
comes from a separate QAT source and isn't used. BF16 gives an unquantized
baseline from the same source; no smaller QAT, assistant, projection or other
quantized file is substituted.

The ratio limit is `1.1`: the converted Q4_K_M file's perplexity divided by
the reference's, on the same fixed text with the pinned runtime, must not
exceed it. This is a precommitted local sanity guard. It isn't a publisher
threshold or a bound on task accuracy. Perplexities aren't comparable across
models or tokenizers
([llama.cpp perplexity notes](https://github.com/ggml-org/llama.cpp/blob/0d0bfcd4fd8828e3e7906b6fc4561725b534511e/tools/perplexity/README.md)),
which is why the reference must come from the same source. A genuine failure
stops for review; neither the ratio nor the text changes after results are seen.

## Not shown

Pinning these values doesn't authorize or demonstrate any of the following:

- downloading target or reference weights (none were downloaded for these
  specs), conversion, sanity execution or a smoke run, each of which needs later
  approval;
- byte verification of the reference, its tokenizer and runtime compatibility,
  and memory feasibility, which remain later proof even though its declared
  primary revision matches the approved source;
- runtime context and memory thresholds, and artifact and conversion-record
  hashes. These aren't fields of this spec and stay
  [open inputs](target-protocol-proposals.md#open-inputs).
