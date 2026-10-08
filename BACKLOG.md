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

## Next evaluation work

- Execute and retain complete tier runs with per-item evidence.
- Curate a result only after protocol, dataset, runtime, and scorer identities
  have been verified against its retained artifacts.
- Identify and version a reference method before any separate conformance
  comparison. Reference conformance is outside the current native path.

## After the first path

- Exercise a second compatible generative-text model through the same
  architecture and document any abstraction leaks.
- Revisit additional models or datasets only if the first two-model evidence
  justifies expanding scope.
