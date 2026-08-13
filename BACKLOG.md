# Public Backlog

This backlog tracks implementation work that is safe and useful to discuss in
the public repository. Priority and design should be refined from evidence.

## Bootstrap follow-up

- Select and document the first local model runtime after a focused feasibility
  check; avoid coupling the rest of the evaluator to it.
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
