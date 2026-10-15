# Public Backlog

This backlog tracks implementation work that is safe and useful to discuss in
the public repository. Priority and design should be refined from evidence.

## Implemented vertical path

- Done: selected pinned llama.cpp with Qwen's official Q4_K_M GGUF after a
  focused local feasibility check
  ([decision 0001](docs/decisions/0001-local-qwen-runtime.md)). The implemented
  runtime boundary keeps model-specific behavior separate from scoring and
  reporting.
- Implemented pinned dataset loading, manifest construction, flagged-pair
  verdicts, and dev tiers.
- Implemented the approved Q4 and Q8 protocols, keyed local runtime boundary,
  prompt construction, and raw generation capture.
- Implemented answer extraction, two scorers, per-example evidence, rescore,
  per-dataset reporting, and sensitivity analysis.
- Executed the smoke, validation and test tiers under frozen protocol v1 and published the curated result ([Qwen3-8B on MATH-500](results/2026-10-math500-qwen3-8b/)).

## Next evaluation work

- Measure run-to-run variation with additional seeds for the headline protocol.
- Re-generate capped outputs with a larger output budget as a pre-declared sensitivity reading.
- Classify the disagreements between the PRM800K grader and math-verify.
- Identify and version a reference method, then run a separate conformance comparison.
- Make the CLI tests independent of console-script launchers, so checkouts in paths containing spaces pass.

## After the first path

- Evaluate robustness on perturbed problem variants (for example GSM-Symbolic-style templates).
- Exercise a second compatible model (Qwen3.5-9B) and a cross-family model through the same architecture, documenting any abstraction leaks.
- Fine-tune the most promising model and compare it with its base under the same frozen protocol, runtime and quantization.
- Add calibrated parallel evaluation so long tiers finish in hours rather than days.
