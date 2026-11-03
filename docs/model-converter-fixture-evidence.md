# Converter fixture evidence (increment 4)

The committed [fixture spec](../configs/models/fixture-smollm2-135m-instruct.yaml)
uses publisher `HuggingFaceTB/SmolLM2-135M-Instruct` at immutable revision
[`12fd25f77366fa6b3b4b768ec3050bf629380bac`](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct/commit/12fd25f77366fa6b3b4b768ec3050bf629380bac).
Its eight source-file SHA-256 values are in the spec; the downloaded files
matched all eight. The `model.safetensors` SHA-256 was
`5af571cbf074e6d21a03528d2330792e532ca608f24ac70a143f6b369968ab8c`.

The converter used [llama.cpp b10412](https://github.com/ggml-org/llama.cpp/releases/tag/b10412)
at commit `0d0bfcd4fd8828e3e7906b6fc4561725b534511e`. The pinned source archive
SHA-256 was `ce8b09b263886ffcf011b632a2e62d04136f8cfdcd7f7f417d77e962d0dcc977`;
`convert_hf_to_gguf.py` was `e38975e1c68d98ac1664dfd530616eb35c72294382a4dd873d4746b23f27779f`.
The [separate conversion Python lock](../tooling/conversion/poetry.lock) was
`068e45ece468b5166f84d297791b0596b97be24710a3e26326aba9b76ed6a103`.
Preflight checks every applicable installed package version against that lock.
The pinned source registers the Phi targets' `Phi3ForCausalLM` and Gemma 4
12B's `Gemma4UnifiedForConditionalGeneration` text conversion classes in
[`conversion/__init__.py`](https://github.com/ggml-org/llama.cpp/blob/0d0bfcd4fd8828e3e7906b6fc4561725b534511e/conversion/__init__.py).
Full target conversion and loading remain for the Coordinator's later proof.

| Fixture artifact | SHA-256 |
|---|---|
| Spec | `0ee8d55c7a599961321448e7ae3d00fc4310ba4a175a3957e14d21bc229148d2` |
| BF16 intermediate | `a6212d1c5b1d3c39b0086fe017427138429aa1440dc1a5794daf9af71af61343` |
| Q4_K_M output | `a6da5bd7e3dd3d08d8da8e22449960829cd8d2729833e94420a9734e07fef8ca` |
| Conversion record | `ef76dbc7fa2ece871ee02753d89628f8bbaf5683445beb288853866556bbdcf8` |
| Sanity record | `4c0ea8be262419538256457d88d2ac0880f5fe46e2d1a9ba959e393934b2e810` |

The fixed non-math text stayed outside the repository; its SHA-256 was
`6a5f0f0322d941de9d5e0f78be9120b2f44f892328ad2a693b8034bae29b0ec3`.
The pinned CPU-only perplexity command measured `1.1891` on the converted
fixture. A second conversion invocation left the Q4_K_M output and conversion
record byte-identical. No target or reference weights were downloaded.

For later execution, install `tooling/conversion/poetry.lock` as a separate
Poetry environment. Set `SLM_EVAL_CONVERTER_PYTHON` to its Python executable,
`SLM_EVAL_LLAMA_BIN` to the verified b10412 binary directory, and
`SLM_EVAL_SANITY_TEXT` to an external file matching the spec hash. The
converter uses `SLM_EVAL_SCRATCH` for downloads and intermediates, and
`SLM_EVAL_CACHE/models` for the final model and records.
