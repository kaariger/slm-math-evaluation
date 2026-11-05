# Target protocol value proposals

- Status: **Text approved by the maintainer on 2026-11-05** as the record of
  selected study settings and open/provisional inputs. Rows marked proposed or
  open remain provisional; no runnable/frozen configuration or unresolved
  runtime value is authorized by this text approval.
- Date: 2026-11-04
- Contract: [interface contract v0.8.1](math500-interface-contract-v0.8.1.md) §4
- Targets: [0002 Phi-4-reasoning](decisions/0002-phi-4-reasoning-target.md),
  [0003 Phi-4-reasoning-plus](decisions/0003-phi-4-reasoning-plus-target.md),
  [0004 Gemma 4 12B IT](decisions/0004-gemma-4-12b-target.md)

This document covers three kinds of value:

- **study settings already selected** for the three targets;
- **publisher recommendations**, recorded as facts but not adopted;
- **open inputs**, which need measurement, pinning or approval.

It gives each protocol field a value and a §4 provenance label. Open inputs are
never filled with a guess: artifact hashes, conversion-record hashes, runtime
context and memory thresholds come from later conversion, verification and
proof work. No protocol file is added. When protocol files are authorized, each
gets its own versioned name and binds the exact bytes; none may be named
`protocol.yaml`.

Approving a source pin, as in
[the adapter source evidence](model-adapter-source-evidence.md), doesn't approve
these values. The approved Qwen3-8B protocols and all frozen configuration
files are unchanged.

## Selected study settings (all three targets)

These are the study's selected settings for these targets. They are
`local-choice`: the study chose them, and they don't come from the publishers.

| Field or run-plan value | Selected value | Provenance |
|---|---|---|
| `sampling.temperature` | `0.6` | `local-choice` (study setting) |
| `sampling.top_p` | `0.95` | `local-choice` (study setting) |
| `sampling.top_k` | `0` (disabled, §4) | `local-choice` (study setting) |
| `sampling.min_p` | `0` (disabled, §4) | `local-choice` (study setting) |
| `sampling.max_output_tokens` | `14000` | `local-choice` (study setting) |
| `prompt.few_shot` | `0` | `local-choice` (study setting) |
| `prompt.template` | `{problem}` + `\n\n` + `Please reason step by step, and put your final answer within \boxed{}.` | `local-choice` (study setting) |
| `sampling.seed_policy` | `seed = seed_base + sample_index` | `local-choice` |
| Seed base (run-plan value, not a protocol field) | `0` | settled |
| Samples per item, `k` (run-plan value, not a protocol field) | `1` | settled |

**Intentional deviations from the publishers.** The selected temperature (0.6)
and top-k (0, disabled) differ from both publishers' recommendations, shown
below. The selected top-p (0.95) happens to equal both. The output cap stays at
14,000 tokens for all three targets. Long generations that reach it are counted
as truncations (§7); the cap isn't raised for any one model.

**Prompt.** The prompt is zero-shot, and its final answer is required in
`\boxed{}`, which the unchanged extractor reads. The exact bytes of this string
are bound later in an approved, versioned protocol file. The Qwen3-8B protocol
uses the same string, with its own provenance and an unchanged top-k of 20.

## Publisher recommendations (facts, not selected)

Each was read at the pinned revision; see [the metadata used](#publisher-metadata-used).

| Model | Publisher recommendation | Source |
|---|---|---|
| Phi-4-reasoning, Phi-4-reasoning-plus | Inference "must use `temperature=0.8`, `top_k=50`, `top_p=0.95`, and `do_sample=True`"; "for more complex queries, set `max_new_tokens=32768`". | model cards; `generation_config.json` repeats 0.8 / 0.95 / 50 |
| Gemma 4 12B IT | A "standardized sampling configuration across all use cases": `temperature=1.0`, `top_p=0.95`, `top_k=64`. | model card; `generation_config.json` repeats them |

## Other fields shared by all three proposals

| Field | Proposed value | Provenance | Status |
|---|---|---|---|
| `role` | `target` | not required (§4) | proposed |
| `runtime.build`, `runtime.invocation` | `b10412`, `keyed-run-scoped-server` | `local-choice` | proposed; the one pinned runtime and §10 path |
| `runtime` bind, port, UI, reasoning format | `127.0.0.1`, ephemeral, disabled, `none` | `local-choice` | proposed; required by §10 and the adapters |
| `runtime` thread, batch, cache, load and slot settings | as approved for Qwen3-8B: threads `-1`/`-1`, batch `2048`/`512`, f16 K/V cache, `mmap`, no context shift, one slot, Jinja template, unrestricted reasoning budget | `local-choice` / `vendor-filled` | proposed for continuity; feasibility on the evaluation machine is unmeasured |
| `runtime.gpu_layers`, `flash_attention` | `99` requested, on | `local-choice` | proposed; per-model offload and attention support are unverified |
| `runtime.context_size` and `sampling.context_length` | — | `local-choice` | **open.** It must hold the rendered prompt plus the 14,000-token output cap, within the model's limit. The exact value awaits a prompt-length and memory measurement. |
| `sampling.presence_penalty`, `repeat_penalty` | `0`, `1` | `local-choice` | proposed; no added penalty, as for Qwen3-8B |
| `sampling.stop` | `[]` | `local-choice` | proposed; generation is expected to end at the model's own end-of-generation tokens, which the target smoke run must confirm |
| `extraction` | `boxed-last-v1` `1.0.0` | not required (§4) | proposed; unchanged extractor |
| `scorers` | PRM800K grader at `7ecc794703b2877f63226f2477a49b34f9b25163`; `math-verify` `0.8.0` | not required (§4) | proposed; unchanged scorers |
| `dataset.source` | PRM800K at `7ecc794703b2877f63226f2477a49b34f9b25163`, with the two committed file hashes | not required (§4) | proposed; frozen manifest `a10c02e7…e48c71` |
| `resources.max_memory_gb`, `min_free_memory_gb`, `stuck_after_awake_s` | — | `local-choice` | **open.** These are set from measured peak memory and generation progress on the evaluation machine. |
| `model.artifact.sha256`, `conversion_record_sha256` | — | `local-choice` | **open.** These are produced by the pinned conversion (§12). |

## Phi-4-reasoning and Phi-4-reasoning-plus

The two Phi targets share their architecture, chat template, generation
defaults and adapter. They differ only in their weights and alias.

| Field | Proposed value | Provenance | Source or status |
|---|---|---|---|
| `protocol_version` | `phi-4-reasoning-v1` / `phi-4-reasoning-plus-v1` | not required | proposed names |
| `model.adapter` | `phi4-reasoning` `1.0.0` | not required | committed adapter |
| `model.artifact.file`, `quantization` | `phi-4-reasoning-Q4_K_M.gguf` / `phi-4-reasoning-plus-Q4_K_M.gguf`, `Q4_K_M` | `local-choice` | converter naming `<spec name>-Q4_K_M.gguf`; local conversion from the BF16 source |
| `model.chat_template` | `{source: embedded, sha256: 5a4c7c0b12bdbbc7110aebe80b15ac3d4dbabde85be466925dc2cf52d7698ceb}` | not required | approved source-template pin; the converted file's embedded template must match it before use |
| `runtime.model_alias` | `phi-4-reasoning` / `phi-4-reasoning-plus` | `local-choice` | §10 neutral aliases |
| `prompt.system_prompt` | `null` | `vendor-template` | The pinned template always inserts the publisher's system prompt and renders only user and assistant turns, so no protocol system message is added. The inserted message is part of `prompt_sha256` (§6.1). |
| `reasoning_mode` | `always_on` | `vendor-template` | the only mode the adapter allows; `thinking_off` is refused |
| Context limit | `max_position_embeddings` `32768` | `vendor-filled` (model limit) | The runtime context must fit between the rendered prompt plus 14,000 tokens and 32,768. |

For the one-user prompt, the rendered prompt ends with
`<|im_start|>assistant<|im_sep|>`, with no open `<think>`. Thinking is split on
`<think>` and `</think>`.

**Sanity route.** Smoke only, with no reference ratio. No vendor GGUF
reference is used for the Phi models, so the spec's `sanity.reference` is
`null`. With a `null` reference, the generic `model sanity` command may still
record the converted file's absolute perplexity on the fixed text. That is not
a vendor-ratio check. The smoke run belongs to the later runner plan.

## Gemma 4 12B IT

| Field | Proposed value | Provenance | Source or status |
|---|---|---|---|
| `protocol_version` | `gemma-4-12b-v1` | not required | proposed name |
| `model.adapter` | `gemma4` `1.0.0` | not required | committed adapter |
| `model.artifact.file`, `quantization` | `gemma-4-12b-Q4_K_M.gguf`, `Q4_K_M` | `local-choice` | text model only, converted from the BF16 source (`text_only: true`) |
| `model.chat_template` | `{source: embedded, sha256: ae53464bf3be25802b3a5b37def7fd89667067d7577049b3b2d74c4d8de4c6d4}` | not required | approved source-template pin; the converted file's embedded template must match it before use |
| `runtime.model_alias` | `gemma-4-12b` | `local-choice` | §10 neutral alias |
| `prompt.system_prompt` | `null` | `local-choice` | No system override. With thinking on, the template itself opens a system turn starting `<|think|>`, and that turn is part of `prompt_sha256` (§6.1). |
| `reasoning_mode` | `thinking_on` | `local-choice` (study setting) | selected primary mode |
| Context limit | text `max_position_embeddings` `262144` | `vendor-filled` (model limit) | The runtime context must hold the rendered prompt plus 14,000 tokens. |

`thinking_off` is a capability and proof check only, showing that the template
argument and the split work. It isn't an authorized full sensitivity
evaluation. With thinking on, the rendered one-user prompt ends with
`<|turn>model\n`. With thinking off, it ends with
`<|turn>model\n<|channel>thought\n<channel|>`, so the generated text starts
after an empty, closed thought block. Thinking is split on `<|channel>thought\n`
and `<channel|>`. The committed source evidence records both rendered suffixes
and their prompt hashes.

**Sanity route.** A fixed-text perplexity comparison against a pinned artifact
from `ggml-org/gemma-4-12B-it-GGUF`, plus a smoke run. The reference is
required, so `null` is not an alternative. Several inputs to the later proof
are still open:

- the reference's exact revision, file and SHA-256;
- the fixed text and its hash;
- the ratio limit;
- approval to download the reference.

Nothing is downloaded now. The smoke run belongs to the later runner plan.

## Comparisons and framing

- **Phi.** The pinned model cards report no MATH-500 figure. A text search of
  the v1 HTML of the Phi technical report
  ([arXiv 2504.21318](https://arxiv.org/abs/2504.21318v1)) for "MATH-500" found
  no match. Three things are still open for a published comparison: its
  citation, its mapping to this item set, and the provenance of its sample
  count. No other benchmark stands in for it. When a sourced figure is present,
  the result is described as "evaluation under the study's stated settings,
  compared with the published figure; not a reproduction".
- **Gemma.** It is reported standalone. The study's Qwen3-8B thinking-mode
  results from the same harness are retained as context, not as a published
  comparison.

## Open inputs

These await measurement, pinning or approval:

- the runtime context and `resources` values for each target, after
  prompt-length and memory measurement;
- the artifact and conversion-record hashes, produced by the pinned
  conversion;
- the exact Gemma sanity reference pin, its fixed text, its ratio limit and
  approval to download it;
- the Phi published-figure citation, its mapping and sample-count provenance;
- the exact proof and tier plan, including the smoke runs;

## Publisher metadata used

These files were read without credentials at the pinned revisions. Their bytes
were hashed locally, and their Git blob IDs matched the publisher's tree listing
at the same revision. No weights or other artifacts were downloaded.

| Model | File | SHA-256 | Git blob ID |
|---|---|---|---|
| Phi-4-reasoning | [`README.md`](https://huggingface.co/microsoft/Phi-4-reasoning/blob/1de18ec97600877ce63dbf60c73b998da99f0195/README.md) | `39025e32b099f529aaa87bd5c147d83f6e5af1d8846e77c41231c7d8ca72f7af` | `a6304048b97922706a5c550e1d8dcc0b45168881` |
| Phi-4-reasoning | [`config.json`](https://huggingface.co/microsoft/Phi-4-reasoning/blob/1de18ec97600877ce63dbf60c73b998da99f0195/config.json) | `0a13d8a96ac97a77871e10739669fd75cb1d7e2e403d5cd6ee0819233a30eff8` | `2e02e6d85b0d12b97a86486b48da9582789a84a5` |
| Phi-4-reasoning | [`generation_config.json`](https://huggingface.co/microsoft/Phi-4-reasoning/blob/1de18ec97600877ce63dbf60c73b998da99f0195/generation_config.json) | `3c0aeb1af3855927b0cc6238651fb56b7727e2fa580528ab28ae31289da12fd6` | `649fd15bb5ed715022d23f3d7fe9167aa438d179` |
| Phi-4-reasoning | [`tokenizer_config.json`](https://huggingface.co/microsoft/Phi-4-reasoning/blob/1de18ec97600877ce63dbf60c73b998da99f0195/tokenizer_config.json) | `6d3efcfa6a8a438e089633132f3bb4ba3d192a5a912f5e908fcce56b1de9d4ce` | `cbe31b763ce812bdf37e486044fca80949bc7982` |
| Phi-4-reasoning-plus | [`README.md`](https://huggingface.co/microsoft/Phi-4-reasoning-plus/blob/69baf8528e1bcf05f475034d9e5dd32875ed125f/README.md) | `f69218261cb11d0afd25720537eeea4082efe9b80be0c23eb6919aed38ca4016` | `2267c0f34861de87c7de22479ad6376c7c241908` |
| Phi-4-reasoning-plus | `config.json`, `generation_config.json`, `tokenizer_config.json` | identical to Phi-4-reasoning | identical to Phi-4-reasoning |
| Gemma 4 12B IT | [`README.md`](https://huggingface.co/google/gemma-4-12B-it/blob/43c9d014b231474fa7212dbb48a762566c2b255d/README.md) | `04a06469e3d83b941eaa776a3647b8690b5a7cc5c833af2381e5345bb1bb45c0` | `99f6f1ce7651f7c7436612f82038ddc7258542c1` |
| Gemma 4 12B IT | [`config.json`](https://huggingface.co/google/gemma-4-12B-it/blob/43c9d014b231474fa7212dbb48a762566c2b255d/config.json) | `478c46e8d2c52d5c2d85bf67e3b3e8c90e7c9d91086cee27e3c267907e936bd9` | `324c841b79e36f42e27f08c572c327533469c859` |
| Gemma 4 12B IT | [`generation_config.json`](https://huggingface.co/google/gemma-4-12B-it/blob/43c9d014b231474fa7212dbb48a762566c2b255d/generation_config.json) | `a8349d9bd64cc5841297fcb5002f0fdc4749c473c8f1b10ea337f9ce4ee7014e` | `d09dccf1b9aee197ef72a6e88a1ceff74b842ff3` |
| Gemma 4 12B IT | [`chat_template.jinja`](https://huggingface.co/google/gemma-4-12B-it/blob/43c9d014b231474fa7212dbb48a762566c2b255d/chat_template.jinja) | `ae53464bf3be25802b3a5b37def7fd89667067d7577049b3b2d74c4d8de4c6d4` (the committed template pin) | `4741bf6e4132ba23a5537f9d6e74e9a6d613d7cd` |

The `tokenizer_config.json` hashes and the Gemma template hash equal those in
the committed adapter source evidence.
