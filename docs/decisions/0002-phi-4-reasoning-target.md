# 0002: Evaluate Phi-4-reasoning as a MATH-500 target — DRAFT

- Status: **DRAFT, pending maintainer approval.** This proposes a target; it is
  not an accepted decision.
- Date: 2026-11-04
- Proposed protocol values: [target protocol proposals](../target-protocol-proposals.md#phi-4-reasoning-and-phi-4-reasoning-plus)
- Related: [0003 Phi-4-reasoning-plus](0003-phi-4-reasoning-plus-target.md),
  [0005 fixtures versus targets](0005-ephemeral-fixtures-versus-targets.md)

## Context

[Interface contract v0.8.1](../math500-interface-contract-v0.8.1.md) §1 names
four evaluation targets, "approved by public decision records". Qwen3-8B is
covered by [decision 0001](0001-local-qwen-runtime.md). This record proposes
Phi-4-reasoning, so its source, conversion, template behavior and open
questions are on record before any target work.

The maintainer has already approved this model's source revision for model
tooling ([adapter source evidence](../model-adapter-source-evidence.md)). That
approval doesn't cover protocol values or evaluation.

## Proposed decision

Evaluate Microsoft Research's Phi-4-reasoning on MATH-500 from one pinned
source:

| Item | Value | Source |
|---|---|---|
| Repository and revision | `microsoft/Phi-4-reasoning` at `1de18ec97600877ce63dbf60c73b998da99f0195` | [commit](https://huggingface.co/microsoft/Phi-4-reasoning/commit/1de18ec97600877ce63dbf60c73b998da99f0195) |
| Publisher and license | Microsoft Research; MIT | [pinned model card](https://huggingface.co/microsoft/Phi-4-reasoning/blob/1de18ec97600877ce63dbf60c73b998da99f0195/README.md) |
| Model | fine-tuned from `microsoft/phi-4`: 14B parameters, dense decoder-only, 32k context | model card |
| Architecture | `Phi3ForCausalLM`, BF16, `max_position_embeddings` 32,768 | [pinned `config.json`](https://huggingface.co/microsoft/Phi-4-reasoning/blob/1de18ec97600877ce63dbf60c73b998da99f0195/config.json) |
| Source weights | six safetensors files, 29,319,042,992 bytes in total | publisher tree listing at the revision |

**Artifact.** Convert the publisher's BF16 weights locally to Q4_K_M with the
pinned converter (§12):

- llama.cpp [`b10412`](https://github.com/ggml-org/llama.cpp/releases/tag/b10412)
  at `0d0bfcd4fd8828e3e7906b6fc4561725b534511e`;
- `convert_hf_to_gguf.py` `e38975e1c68d98ac1664dfd530616eb35c72294382a4dd873d4746b23f27779f`;
- the separate conversion lock `068e45ece468b5166f84d297791b0596b97be24710a3e26326aba9b76ed6a103`;
- a BF16 intermediate and no calibration data.

The pinned source registers `Phi3ForCausalLM` for conversion
([`conversion/__init__.py`](https://github.com/ggml-org/llama.cpp/blob/0d0bfcd4fd8828e3e7906b6fc4561725b534511e/conversion/__init__.py)).
Q4_K_M is an intentional quantized representation and is never reported as
interchangeable with the BF16 checkpoint. The protocol must carry the conversion
record's SHA-256 (§4, §12.5).

**Adapter and template.** The run uses:

- the committed `phi4-reasoning` adapter `1.0.0`;
- the neutral alias `phi-4-reasoning`;
- `reasoning_mode: always_on`, the only mode it allows, because the model
  always thinks.

The approved template pin is
`5a4c7c0b12bdbbc7110aebe80b15ac3d4dbabde85be466925dc2cf52d7698ceb`. This
template always inserts the publisher's system prompt, which asks for a
`<think> {Thought section} </think> {Solution section}` structure. It renders
only user and assistant turns, so the protocol leaves `system_prompt` as
`null`; the inserted system message is captured in `prompt_sha256` (§6.1). For
the one-user evaluation prompt, the rendered prompt ends with
`<|im_start|>assistant<|im_sep|>`, with no open `<think>`. Thinking is split on
`<think>` and `</think>`.

**Settings.** Use the study's
[selected settings](../target-protocol-proposals.md#selected-study-settings-all-three-targets):

- temperature 0.6 and top-p 0.95, with top-k and min-p disabled;
- a 14,000-token output cap;
- the shared zero-shot prompt that asks for the final answer in `\boxed{}`;
- seed base 0 and one sample per item.

The card says inference "must use `temperature=0.8`, `top_k=50`, `top_p=0.95`,
and `do_sample=True`", and the pinned `generation_config.json` repeats these
values. The selected temperature and top-k are intentional deviations from
that recommendation. The runtime context must hold the rendered prompt plus
14,000 tokens within the model's 32,768-token limit. Its exact value awaits
measurement.

**Sanity route.** Smoke only, with no reference ratio. No vendor GGUF
reference is used, so the spec's `sanity.reference` is `null`. The generic
`model sanity` command may still record the converted file's absolute
perplexity on the fixed text. That is not a vendor-ratio check. The smoke run
belongs to the later runner plan.

## Evidence so far, and its limits

Verified:

- The model card, `config.json`, `generation_config.json` and
  `tokenizer_config.json` at the pinned revision. Their SHA-256 values and Git
  blob IDs are listed in the
  [protocol proposals](../target-protocol-proposals.md#publisher-metadata-used).
- The template text's SHA-256, and the rendered prompt ending above
  ([adapter source evidence](../model-adapter-source-evidence.md)).
- The converter, verification commands and runner, but only at fixture scale,
  on the ephemeral SmolLM2-135M fixture
  ([converter evidence](../model-converter-fixture-evidence.md),
  [runner evidence](../runner-fixture-evidence.md)). A fixture result says
  nothing about this target ([0005](0005-ephemeral-fixtures-versus-targets.md)).

Not done. This record claims none of these checks:

- the target weights downloaded, converted or loaded by the pinned runtime;
- the embedded template in a converted file compared with the pin;
- `verify-runtime` and `verify-artifact` for a target artifact;
- memory and throughput feasibility at the proposed context on the evaluation
  machine;
- a sanity check, a smoke-tier run or a proof run;
- any MATH-500 result.

**Published figure.** The pinned model card reports AIME 24/25, OmniMath,
GPQA-Diamond and LiveCodeBench, but no MATH-500 figure. A text search of the v1
HTML of the cited technical report
([arXiv 2504.21318](https://arxiv.org/abs/2504.21318v1)) for "MATH-500" found
no match. Three things are still open for a published comparison: its
citation, its mapping to this item set, and the provenance of its sample count.
No other benchmark stands in for it. When a sourced figure is present, the
result is described as "evaluation under the study's stated settings, compared
with the published figure; not a reproduction".

## Open inputs

1. Maintainer approval of this record's text.
2. The runtime context and `resources` values, after prompt-length and memory
   measurement.
3. The artifact and conversion-record hashes, produced by the pinned
   conversion.
4. The published comparison: its citation, its mapping and the provenance of
   its sample count.
5. The exact proof and tier plan. Smoke precedes any other tier (§12.4).

## Alternatives considered

- **A third-party GGUF.** This adds a packaging step outside the pinned
  publisher source, without a conversion record.
- **Q8_0 or BF16.** These need more memory. The BF16 weights alone are 29.3 GB,
  against the evaluation machine's 32 GB of unified memory
  ([decision 0001](0001-local-qwen-runtime.md)). Neither is proposed.
- **The base Phi-4.** It isn't a reasoning model, and it isn't one of the
  contracted targets.

## Consequences

- Conversion, verification, feasibility measurement, a smoke run and proof
  must all happen, in that order, before this target can produce a reportable
  result.
- A reported result names the Q4_K_M artifact and conversion record, the
  template pin, the always-on reasoning and every protocol value with its
  provenance.
