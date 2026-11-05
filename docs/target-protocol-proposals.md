# Target protocol value proposals — DRAFT

- Status: **DRAFT, pending maintainer approval.** Nothing here is an approved
  protocol value, a frozen protocol file or a runnable configuration.
- Date: 2026-11-04
- Contract: [interface contract v0.8.1](math500-interface-contract-v0.8.1.md) §4
- Targets: [0002 Phi-4-reasoning](decisions/0002-phi-4-reasoning-target.md),
  [0003 Phi-4-reasoning-plus](decisions/0003-phi-4-reasoning-plus-target.md),
  [0004 Gemma 4 12B IT](decisions/0004-gemma-4-12b-target.md)

These tables propose a value and a §4 provenance label for every protocol field
of each target. A value is **unresolved** when it needs a maintainer choice or a
later feasibility measurement on the evaluation machine. Unresolved values are
never filled with a guess: artifact hashes, conversion-record hashes, memory
figures and sample counts are produced by later conversion, verification and
proof work. No protocol file is added by this proposal. When protocol files are
authorized, each needs its own versioned name; none may be named `protocol.yaml`.

Approving a source pin, as in
[the adapter source evidence](model-adapter-source-evidence.md), doesn't approve
these protocol values. Results remain "evaluation under the study's stated
settings, compared with the published figure; not a reproduction".

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

## Fields shared by all three proposals

| Field | Proposed value | Provenance | Status |
|---|---|---|---|
| `role` | `target` | not required (§4) | proposed |
| `runtime.build`, `runtime.invocation` | `b10412`, `keyed-run-scoped-server` | `local-choice` | proposed; the one pinned runtime and §10 path |
| `runtime` bind, port, UI, reasoning format | `127.0.0.1`, ephemeral, disabled, `none` | `local-choice` | proposed; required by §10 and the adapters |
| `runtime` thread, batch, cache, load and slot settings | as approved for Qwen3-8B: threads `-1`/`-1`, batch `2048`/`512`, f16 K/V cache, `mmap`, no context shift, one slot, Jinja template, unrestricted reasoning budget | `local-choice` / `vendor-filled` | proposed for continuity; feasibility on the evaluation machine is unmeasured |
| `runtime.gpu_layers`, `flash_attention` | `99` requested, on | `local-choice` | proposed; per-model offload and attention support are unverified |
| `prompt.few_shot` | `0` | `local-choice` | proposed |
| `sampling.presence_penalty`, `repeat_penalty` | `0`, `1` | `local-choice` | proposed; no added penalty, as for Qwen3-8B |
| `sampling.min_p` | `0` (disabled) | `local-choice` | proposed; neither publisher states a min-p value |
| `sampling.stop` | `[]` | `local-choice` | proposed; generation is expected to end at the model's own end-of-generation tokens, which the target smoke run must confirm |
| `sampling.seed_policy` | `seed = seed_base + sample_index` | `local-choice` | proposed |
| `extraction` | `boxed-last-v1` `1.0.0` | not required (§4) | proposed; unchanged extractor |
| `scorers` | PRM800K grader at `7ecc794703b2877f63226f2477a49b34f9b25163`; `math-verify` `0.8.0` | not required (§4) | proposed; unchanged scorers |
| `dataset.source` | PRM800K at `7ecc794703b2877f63226f2477a49b34f9b25163`, with the two committed file hashes | not required (§4) | proposed; frozen manifest `a10c02e7…e48c71` |
| `resources.max_memory_gb`, `min_free_memory_gb`, `stuck_after_awake_s` | — | `local-choice` | **unresolved**: set from measured peak memory and generation progress on the evaluation machine |
| `model.artifact.sha256`, `conversion_record_sha256` | — | `local-choice` | **unresolved**: produced by the pinned conversion (§12); never filled in before it runs |

Samples per item (`k`), seed base and the tier sequence are run-plan choices, not
protocol fields. They are listed among the maintainer choices in each target
record and aren't proposed here.

**User prompt (all three targets) — maintainer choice.** The proposal reuses the
approved Qwen3-8B user message,
`{problem}` + `\n\n` + `Please reason step by step, and put your final answer within \boxed{}.`,
labeled `local-choice` for these targets. It keeps the `\boxed{}` instruction the
unchanged extractor relies on, and keeps the user message identical across
targets. Neither publisher states a MATH prompt in the pinned card. Sending the
problem alone, with no added instruction, is the main alternative, at the risk of
answers that the extractor cannot find.

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
| `prompt.system_prompt` | `null` | `vendor-template` | The pinned template always inserts the publisher's system prompt and renders only user and assistant turns. A protocol system message would not reach the model. The inserted message is part of `prompt_sha256` (§6.1). |
| `reasoning_mode` | `always_on` | `vendor-template` | the only mode the adapter allows; the model always thinks |
| `sampling.temperature`, `top_p`, `top_k` | `0.8`, `0.95`, `50` | `vendor-filled` | The model card says inference "must use `temperature=0.8`, `top_k=50`, `top_p=0.95`, and `do_sample=True`"; `generation_config.json` holds the same values. |
| `sampling.context_length` and `runtime.context_size` | `32768` | `vendor-filled` | `max_position_embeddings` is `32768`, and the card states a 32k context. Memory feasibility at this length on the evaluation machine is **unmeasured**. |
| `sampling.max_output_tokens` | — | `vendor-filled` or `local-choice` | **Unresolved.** The card suggests `max_new_tokens=32768` "for more complex queries", but prompt plus output must fit the 32,768-token context. A cap of 32,768 minus the rendered prompt length, or a lower rounded value, needs maintainer selection after the prompt length is measured. The plus card's 64k experiment exceeds `max_position_embeddings` and isn't proposed. |

For the one-user prompt, the rendered prompt ends with
`<|im_start|>assistant<|im_sep|>`, with no open `<think>`. Thinking is split on
`<think>` and `</think>`.

## Gemma 4 12B IT

| Field | Proposed value | Provenance | Source or status |
|---|---|---|---|
| `protocol_version` | `gemma-4-12b-v1` | not required | proposed name |
| `model.adapter` | `gemma4` `1.0.0` | not required | committed adapter |
| `model.artifact.file`, `quantization` | `gemma-4-12b-Q4_K_M.gguf`, `Q4_K_M` | `local-choice` | text model only, converted from the BF16 source (`text_only: true`) |
| `model.chat_template` | `{source: embedded, sha256: ae53464bf3be25802b3a5b37def7fd89667067d7577049b3b2d74c4d8de4c6d4}` | not required | approved source-template pin; the converted file's embedded template must match it before use |
| `runtime.model_alias` | `gemma-4-12b` | `local-choice` | §10 neutral alias |
| `prompt.system_prompt` | `null` | `local-choice` | With thinking on, the template itself opens a system turn starting `<|think|>`; that inserted turn is part of `prompt_sha256` (§6.1). |
| `reasoning_mode` | `thinking_on` | `local-choice` | **Maintainer choice.** The card describes "configurable thinking modes". `thinking_off` is proposed as a single-factor variant for the sensitivity analysis (§7.1), as for Qwen3-8B. |
| `sampling.temperature`, `top_p`, `top_k` | `1.0`, `0.95`, `64` | `vendor-filled` | The card gives a "standardized sampling configuration across all use cases"; `generation_config.json` holds the same values. |
| `sampling.context_length` and `runtime.context_size` | — | `local-choice` | **Unresolved.** The text model allows 262,144 positions (256K), far more than any proposed output budget needs. Choose the prompt length plus the output cap; memory use at that length on the evaluation machine is unmeasured. |
| `sampling.max_output_tokens` | — | `local-choice` | **Unresolved.** The card states no output budget for reasoning tasks. Options: the Phi value, for cross-target parity, or a value set from smoke-tier truncation evidence. |

With thinking on, the rendered one-user prompt ends with `<|turn>model\n`. With
thinking off, it ends with `<|turn>model\n<|channel>thought\n<channel|>`, so the
generated text starts after an empty, closed thought block. Thinking is split on
`<|channel>thought\n` and `<channel|>`. The committed source evidence records
both rendered suffixes and their prompt hashes.

## Not covered here

- **Published reference figures for `--compare-published`.** The pinned Phi and
  Gemma model cards report no MATH-500 figure, and the v1 HTML of the Phi
  technical report ([arXiv 2504.21318](https://arxiv.org/abs/2504.21318v1))
  doesn't mention MATH-500. A comparison figure needs a cited source that the
  maintainer selects.
- Model spec files (§12.1) with the publisher's per-file hashes, and runner
  plans. Those are written when conversion is authorized.
