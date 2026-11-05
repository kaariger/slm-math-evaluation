# 0003: Evaluate Phi-4-reasoning-plus as a MATH-500 target — DRAFT

- Status: **DRAFT, pending maintainer approval.** This proposes a target; it is
  not an accepted decision.
- Date: 2026-11-04
- Proposed protocol values: [target protocol proposals](../target-protocol-proposals.md#phi-4-reasoning-and-phi-4-reasoning-plus)
- Related: [0002 Phi-4-reasoning](0002-phi-4-reasoning-target.md),
  [0005 fixtures versus targets](0005-ephemeral-fixtures-versus-targets.md)

## Context

[Interface contract v0.8.1](../math500-interface-contract-v0.8.1.md) §1 names
Phi-4-reasoning-plus among the evaluation targets, "approved by public decision
records". The maintainer has already approved its source revision for model
tooling ([adapter source evidence](../model-adapter-source-evidence.md)). That
approval doesn't cover protocol values or evaluation.

This model shares Phi-4-reasoning's architecture, chat template, generation
defaults and adapter. [Decision 0002](0002-phi-4-reasoning-target.md) proposes
that target. This record states what differs and repeats what each target
needs on its own.

## Proposed decision

Evaluate Microsoft Research's Phi-4-reasoning-plus on MATH-500 from one pinned
source:

| Item | Value | Source |
|---|---|---|
| Repository and revision | `microsoft/Phi-4-reasoning-plus` at `69baf8528e1bcf05f475034d9e5dd32875ed125f` | [commit](https://huggingface.co/microsoft/Phi-4-reasoning-plus/commit/69baf8528e1bcf05f475034d9e5dd32875ed125f) |
| Publisher and license | Microsoft Research; MIT | [pinned model card](https://huggingface.co/microsoft/Phi-4-reasoning-plus/blob/69baf8528e1bcf05f475034d9e5dd32875ed125f/README.md) |
| Model | Phi-4 fine-tuned with supervised fine-tuning and reinforcement learning: 14B parameters, dense, 32k context | model card |
| Architecture | `Phi3ForCausalLM`, BF16, `max_position_embeddings` 32,768 | [pinned `config.json`](https://huggingface.co/microsoft/Phi-4-reasoning-plus/blob/69baf8528e1bcf05f475034d9e5dd32875ed125f/config.json), identical in bytes to Phi-4-reasoning's |
| Source weights | six safetensors files, 29,319,042,992 bytes in total | publisher tree listing at the revision |

The card says this model "has higher accuracy but generates on average 50% more
tokens, thus having higher latency". The shared 14,000-token output cap still
applies. More of its generations may reach the cap, and the report counts those
as truncations (§7).

**Artifact.** Convert the publisher's BF16 weights locally to Q4_K_M with the
same pinned converter as [0002](0002-phi-4-reasoning-target.md):

- llama.cpp `b10412` at `0d0bfcd4fd8828e3e7906b6fc4561725b534511e`;
- `convert_hf_to_gguf.py` `e38975e1…`;
- conversion lock `068e45ec…`;
- a BF16 intermediate and no calibration.

The protocol must carry the conversion record's SHA-256 (§4, §12.5). The two
Phi conversions are separate artifacts, and each needs its own record and
verification.

**Adapter and template.** The run uses the `phi4-reasoning` adapter `1.0.0`,
the alias `phi-4-reasoning-plus` and `reasoning_mode: always_on`. The pinned
`tokenizer_config.json` is byte-identical to Phi-4-reasoning's (SHA-256
`6d3efcfa…`). The template pin is `5a4c7c0b…`, it inserts the publisher's
system prompt itself, and the protocol keeps `system_prompt: null`. The
rendered one-user prompt ends with `<|im_start|>assistant<|im_sep|>`, and
thinking is split on `<think>` and `</think>`.

**Settings.** Use the study's
[selected settings](../target-protocol-proposals.md#selected-study-settings-all-three-targets),
as for 0002:

- temperature 0.6 and top-p 0.95, with top-k and min-p disabled;
- a 14,000-token output cap;
- the shared zero-shot `\boxed{}` prompt;
- seed base 0 and one sample per item.

The card recommends temperature 0.8, top-k 50 and top-p 0.95, which
`generation_config.json` repeats. The selected temperature and top-k are
intentional deviations from that recommendation. The card also reports
experiments that "extended its maximum number of tokens to 64k". This exceeds
the model's `max_position_embeddings` and isn't proposed. The runtime context
must hold the rendered prompt plus 14,000 tokens within the 32,768-token limit,
and its exact value awaits measurement.

**Sanity route.** Smoke only, with no reference ratio, as for 0002. The spec's
`sanity.reference` is `null`. Any absolute perplexity the generic command
records is not a vendor-ratio check.

## Evidence so far, and its limits

What is verified and what isn't matches [0002](0002-phi-4-reasoning-target.md)
point for point. That covers the pinned metadata hashes, the template behavior
and fixture-scale tooling evidence only. No target weights have been
downloaded, converted or loaded. No security verification, feasibility
measurement, sanity check, smoke run, proof or MATH-500 result exists for this
target.

**Published figure.** The pinned model card reports AIME 24/25, OmniMath,
GPQA-Diamond and LiveCodeBench for this model, but no MATH-500 figure. The
citation, mapping and sample-count provenance of a published comparison are
open, as for 0002. No other benchmark stands in for it.

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

The alternatives match [0002](0002-phi-4-reasoning-target.md): a third-party
GGUF, Q8_0 or BF16. One more: running only one of the two Phi models. They
share a template and architecture but differ in training, and the contract
names both. Their protocols would differ in both the artifact and the alias.
That is two factors, so the §7.1 `sensitivity` command can't pair them. A
comparison between them is reported as two per-model results under otherwise
matching settings.

## Consequences

- Conversion, verification, feasibility measurement, a smoke run and proof must
  happen before this target can produce a reportable result.
- Results for the two Phi models are reported per model. Any comparison
  between them names both protocols and artifacts.
