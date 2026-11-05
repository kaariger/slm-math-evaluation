# 0004: Evaluate Gemma 4 12B IT as a MATH-500 target — DRAFT

- Status: **DRAFT, pending maintainer approval.** This proposes a target; it is
  not an accepted decision.
- Date: 2026-11-04
- Proposed protocol values: [target protocol proposals](../target-protocol-proposals.md#gemma-4-12b-it)
- Related: [0005 fixtures versus targets](0005-ephemeral-fixtures-versus-targets.md)

## Context

[Interface contract v0.8.1](../math500-interface-contract-v0.8.1.md) §1 names
Gemma 4 12B among the evaluation targets, "approved by public decision
records". The maintainer has already approved its source revision for model
tooling ([adapter source evidence](../model-adapter-source-evidence.md)). That
approval doesn't cover protocol values or evaluation.

## Proposed decision

Evaluate Google DeepMind's Gemma 4 12B instruction-tuned model on MATH-500,
using its text model only, from one pinned source:

| Item | Value | Source |
|---|---|---|
| Repository and revision | `google/gemma-4-12B-it` at `43c9d014b231474fa7212dbb48a762566c2b255d` | [commit](https://huggingface.co/google/gemma-4-12B-it/commit/43c9d014b231474fa7212dbb48a762566c2b255d) |
| Publisher and license | Google DeepMind; the card's metadata declares `license: apache-2.0` and links the Gemma 4 license page | [pinned model card](https://huggingface.co/google/gemma-4-12B-it/blob/43c9d014b231474fa7212dbb48a762566c2b255d/README.md) |
| Model | "12B Unified": 11.95B parameters, 48 layers, 1,024-token sliding window, 256K context; text, image and audio inputs | model card |
| Architecture | `Gemma4UnifiedForConditionalGeneration`; text model `gemma4_unified_text`, `max_position_embeddings` 262,144; BF16 | [pinned `config.json`](https://huggingface.co/google/gemma-4-12B-it/blob/43c9d014b231474fa7212dbb48a762566c2b255d/config.json) |
| Source weights | one safetensors file, 23,919,549,408 bytes | publisher tree listing at the revision |

**Artifact.** Convert only the text model of the publisher's BF16 weights to
Q4_K_M locally with the pinned converter (§12, `text_only: true`):

- llama.cpp [`b10412`](https://github.com/ggml-org/llama.cpp/releases/tag/b10412)
  at `0d0bfcd4fd8828e3e7906b6fc4561725b534511e`;
- `convert_hf_to_gguf.py` `e38975e1c68d98ac1664dfd530616eb35c72294382a4dd873d4746b23f27779f`;
- the conversion lock `068e45ece468b5166f84d297791b0596b97be24710a3e26326aba9b76ed6a103`;
- a BF16 intermediate and no calibration.

The pinned source registers a `Gemma4UnifiedForConditionalGeneration` text
conversion class
([`conversion/__init__.py`](https://github.com/ggml-org/llama.cpp/blob/0d0bfcd4fd8828e3e7906b6fc4561725b534511e/conversion/__init__.py)).
Full conversion and loading of this model are not yet shown. Image and audio
inputs are out of scope. Q4_K_M is never reported as interchangeable with the
BF16 checkpoint, and the protocol must carry the conversion record's SHA-256
(§4, §12.5).

**Adapter and template.** The run uses the committed `gemma4` adapter `1.0.0`
and the neutral alias `gemma-4-12b`. The approved template pin is
`ae53464bf3be25802b3a5b37def7fd89667067d7577049b3b2d74c4d8de4c6d4`, and
thinking is split on `<|channel>thought\n` and `<channel|>`.

The card says thinking "is enabled by including the `<|think|>` token at the
start of the system prompt". The pinned template does this from its
`enable_thinking` argument, which the adapter sends in keyed requests (§10).
Otherwise the protocol keeps `system_prompt: null`.

| Mode | Rendered one-user prompt | Notes |
|---|---|---|
| `thinking_on` | opens a system turn beginning `<|think|>`; ends `<|turn>model\n` | |
| `thinking_off` | ends `<|turn>model\n<|channel>thought\n<channel|>` | Generation starts after an empty, closed thought block. The card says the model still emits empty thought tags when thinking is off. Whether the converted model repeats them is for the thinking-off capability check to show; either form splits under §8. |

Template-inserted text is part of `prompt_sha256` (§6.1). The template also has
a tool-response branch that can end with an open thought delimiter. The
evaluation prompt uses no tools.

**Settings.** Use the study's
[selected settings](../target-protocol-proposals.md#selected-study-settings-all-three-targets):

- temperature 0.6 and top-p 0.95, with top-k and min-p disabled;
- a 14,000-token output cap;
- the shared zero-shot `\boxed{}` prompt;
- seed base 0 and one sample per item;
- `reasoning_mode: thinking_on` as the selected primary mode.

The card gives a "standardized sampling configuration across all use cases",
which the pinned `generation_config.json` repeats: temperature 1.0, top-p 0.95
and top-k 64. The selected temperature and top-k are intentional deviations
from that recommendation. `thinking_off` is a capability and proof check only,
showing that the template argument and the split work; it isn't an authorized
full sensitivity evaluation. The runtime context must hold the rendered prompt
plus 14,000 tokens, within the text model's 262,144-token limit. Its exact
value awaits measurement.

**Sanity route.** A fixed-text perplexity comparison against a pinned artifact
from `ggml-org/gemma-4-12B-it-GGUF`, plus a smoke run. The reference is
required, so `sanity.reference` is not `null`. Several inputs to the later
proof are still open:

- the reference's exact revision, file and SHA-256;
- the fixed text and its hash;
- the ratio limit;
- approval to download the reference.

Nothing is downloaded now. The smoke run belongs to the later runner plan.

## Evidence so far, and its limits

Verified:

- The model card, `config.json`, `generation_config.json`,
  `tokenizer_config.json` and `chat_template.jinja` at the pinned revision.
  Their hashes are in the
  [protocol proposals](../target-protocol-proposals.md#publisher-metadata-used)
  and the [adapter source evidence](../model-adapter-source-evidence.md).
- The rendered prompt suffixes and their hashes for both modes. These were
  rendered with Jinja2 3.1.6, not by the pinned server.
- The converter and runner, but only at fixture scale
  ([converter evidence](../model-converter-fixture-evidence.md),
  [runner evidence](../runner-fixture-evidence.md)).

Not done. This record claims none of these checks:

- the target weights downloaded, converted or loaded;
- server-side rendering of the template compared with the Jinja2 rendering;
- `verify-runtime` and `verify-artifact` for a target artifact;
- feasibility at any proposed context length;
- a sanity check, a smoke run or a proof run;
- any MATH-500 result.

**Published figure and framing.** For the 12B model, the pinned card reports
AIME 2026 without tools (77.5%) and MATH-Vision (79.7%), among other
benchmarks. It reports no MATH-500 figure. Gemma is reported standalone. The
study's Qwen3-8B thinking-mode results from the same harness are retained as
context, not as a published comparison. No other benchmark stands in for a
MATH-500 figure.

## Open inputs

1. Maintainer approval of this record's text.
2. The runtime context and `resources` values, after prompt-length and memory
   measurement.
3. The artifact and conversion-record hashes, produced by the pinned
   conversion.
4. The sanity reference pin from `ggml-org/gemma-4-12B-it-GGUF` (revision,
   file and SHA-256), its fixed text, its ratio limit and approval to download
   it.
5. The exact proof and tier plan, including the smoke run and the
   thinking-off capability check. Smoke precedes any other tier (§12.4).

## Alternatives considered

- **Other Gemma 4 sizes** (E2B, E4B, 26B A4B, 31B). They aren't the contracted
  target. The two larger ones need more memory than the 12B model.
- **`thinking_off` as the primary mode.** Not selected. It stays a capability
  and proof check, not a full evaluation.
- **A third-party GGUF as the evaluated artifact.** This adds unpinned
  packaging without a conversion record. The `ggml-org` GGUF serves only as
  the pinned sanity reference.

## Consequences

- Conversion of the text model, verification, feasibility measurement, a smoke
  run and proof must happen before this target can produce a reportable result.
- A reported result names the text-only Q4_K_M artifact and conversion record,
  the template pin, the reasoning mode and every protocol value with its
  provenance.
