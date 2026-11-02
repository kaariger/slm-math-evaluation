# Model adapter source evidence (increment 2)

The maintainer approved these immutable publisher revisions for model-tooling
implementation. This records source identity and template behavior; model
protocol values and final target decisions remain separate approval work.

| Model | Publisher | Source revision | Pinned template source | SHA-256 of template text |
|---|---|---|---|---|
| Phi-4-reasoning | Microsoft Research, [`microsoft` model card](https://huggingface.co/microsoft/Phi-4-reasoning/blob/1de18ec97600877ce63dbf60c73b998da99f0195/README.md) | [`1de18ec97600877ce63dbf60c73b998da99f0195`](https://huggingface.co/microsoft/Phi-4-reasoning/commit/1de18ec97600877ce63dbf60c73b998da99f0195) | `tokenizer_config.json` `chat_template` | `5a4c7c0b12bdbbc7110aebe80b15ac3d4dbabde85be466925dc2cf52d7698ceb` |
| Phi-4-reasoning-plus | Microsoft Research, [`microsoft` model card](https://huggingface.co/microsoft/Phi-4-reasoning-plus/blob/69baf8528e1bcf05f475034d9e5dd32875ed125f/README.md) | [`69baf8528e1bcf05f475034d9e5dd32875ed125f`](https://huggingface.co/microsoft/Phi-4-reasoning-plus/commit/69baf8528e1bcf05f475034d9e5dd32875ed125f) | `tokenizer_config.json` `chat_template` | `5a4c7c0b12bdbbc7110aebe80b15ac3d4dbabde85be466925dc2cf52d7698ceb` |
| Gemma 4 12B IT | Google DeepMind, [`google` model card](https://huggingface.co/google/gemma-4-12B-it) | [`43c9d014b231474fa7212dbb48a762566c2b255d`](https://huggingface.co/google/gemma-4-12B-it/commit/43c9d014b231474fa7212dbb48a762566c2b255d) | `chat_template.jinja` | `ae53464bf3be25802b3a5b37def7fd89667067d7577049b3b2d74c4d8de4c6d4` |

Metadata fetched at those exact revisions into scratch, checked against the
publisher's pinned Git tree blob IDs, and hashed locally:

| Model | File | SHA-256 | Git blob ID |
|---|---|---|---|
| Phi-4-reasoning | `tokenizer_config.json` | `6d3efcfa6a8a438e089633132f3bb4ba3d192a5a912f5e908fcce56b1de9d4ce` | `cbe31b763ce812bdf37e486044fca80949bc7982` |
| Phi-4-reasoning-plus | `tokenizer_config.json` | `6d3efcfa6a8a438e089633132f3bb4ba3d192a5a912f5e908fcce56b1de9d4ce` | `cbe31b763ce812bdf37e486044fca80949bc7982` |
| Gemma 4 12B IT | `chat_template.jinja` | `ae53464bf3be25802b3a5b37def7fd89667067d7577049b3b2d74c4d8de4c6d4` | `4741bf6e4132ba23a5537f9d6e74e9a6d613d7cd` |
| Gemma 4 12B IT | `tokenizer_config.json` | `a62f4e85a47c0c136edaaa3a4f591fd6783717299a9def47e5ad03a49f6a5eb9` | `6520cdc25462a5eba96fdac6d8cf3caa461feb35` |

For the one-user evaluation prompt, no tools and `add_generation_prompt=True`,
both Phi templates end with the assistant role header and no open `<think>`.
Gemma's thought delimiters are `<|channel>thought\n` and `<channel|>`.
Rendering the pinned Gemma template with Jinja2 3.1.6 gave these suffixes
and prompt hashes for a synthetic `What is 2 + 2?` item:

- Thinking on: `<|turn>model\n`; SHA-256 `c5e62252c8d44d00c226ffd16e63e707e9ef43e8da25cefbfd5fb6b93cc2579c`.
- Thinking off: `<|turn>model\n<|channel>thought\n<channel|>`; SHA-256 `1a5356f06c9014880cdf157436c2dffcaa8058a1b911c71bc9f4950a65da193d`.

The pinned [Gemma tree](https://huggingface.co/google/gemma-4-12B-it/tree/43c9d014b231474fa7212dbb48a762566c2b255d)
and [template source](https://huggingface.co/google/gemma-4-12B-it/blob/43c9d014b231474fa7212dbb48a762566c2b255d/chat_template.jinja)
also show a tool-response branch that can end with an open thought delimiter.
The evaluation prompt has no tools. Runtime prompt rendering and full model
compatibility require later proof with converted target weights.
