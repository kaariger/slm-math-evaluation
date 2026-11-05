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
tokens, thus having higher latency". The output cap matters more here than for
Phi-4-reasoning.

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

**Settings.** Use the shared Phi proposals: temperature 0.8, top-k 50 and
top-p 0.95, as the card and `generation_config.json` specify, and a
32,768-token context. The card also reports experiments that "extended its
maximum number of tokens to 64k". This exceeds the model's
`max_position_embeddings` and isn't proposed.

## Evidence so far, and its limits

What is verified and what isn't matches [0002](0002-phi-4-reasoning-target.md)
point for point. That covers the pinned metadata hashes, the template behavior
and fixture-scale tooling evidence only. No target weights have been
downloaded, converted or loaded. No security verification, feasibility
measurement, sanity check, smoke run, proof or MATH-500 result exists for this
target.

**Published figure.** The pinned model card reports AIME 24/25, OmniMath,
GPQA-Diamond and LiveCodeBench for this model, but no MATH-500 figure.

## Maintainer choices still needed

1. Approve or amend this target.
2. The output cap. Given the longer generations, decide whether this target's
   cap matches Phi-4-reasoning's, or is set from smoke-tier truncation evidence
   within the 32,768-token context.
3. The user prompt (shared with 0002).
4. The `resources` values, after a memory measurement.
5. `sanity.reference` (a reference GGUF, which needs an approved download, or
   `null`).
6. The published comparison figure and its source, if any.
7. Samples per item, the seed base and the tier sequence.

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
