# Public Backlog

This backlog tracks implementation work that is safe and useful to discuss in
the public repository. Priority and design should be refined from evidence.

## Bootstrap follow-up

- Done: selected pinned llama.cpp with Qwen's official Q4_K_M GGUF after a
  focused local feasibility check
  ([decision 0001](docs/decisions/0001-local-qwen-runtime.md)). The production
  adapter remains part of the first vertical path and must not couple the rest
  of the evaluator to this runtime.
- Identify and version the accepted reference method for the first benchmark.
- Convert configuration templates into validated schemas alongside their first
  real consumers.
- Define the smallest retained-run manifest and artifact layout that satisfies
  provenance and audit requirements.

## First vertical path

- Implement dataset loading and canonical examples.
- Implement prompt construction and raw generation capture.
- Add a narrow model runtime boundary for the first model.
- Implement answer extraction and dataset-specific scoring.
- Produce per-example evidence and a per-dataset report.
- Compare the native path with the selected reference method.

## After the first path

- Exercise a second compatible generative-text model through the same
  architecture and document any abstraction leaks.
- Revisit additional models or datasets only if the first two-model evidence
  justifies expanding scope.
