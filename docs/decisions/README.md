# Public Decisions

Record durable architecture or methodology decisions here when implementation
evidence makes them necessary. Use short numbered Markdown records containing
status, context, decision, consequences, and alternatives considered.

Likely early decisions include the local model runtime, reference evaluation
method, configuration validation approach, and persisted run-state format.
Until those decisions are recorded, example configuration fields are
provisional and must not be treated as stable public interfaces.

## Accepted decisions

- [0001: Use llama.cpp for the first local Qwen runtime](0001-local-qwen-runtime.md)

## Draft decisions (pending maintainer approval)

These are proposals, not accepted decisions. Their
[target protocol value proposals](../target-protocol-proposals.md) are drafts
too.

- [0002: Evaluate Phi-4-reasoning as a MATH-500 target](0002-phi-4-reasoning-target.md)
- [0003: Evaluate Phi-4-reasoning-plus as a MATH-500 target](0003-phi-4-reasoning-plus-target.md)
- [0004: Evaluate Gemma 4 12B IT as a MATH-500 target](0004-gemma-4-12b-target.md)
- [0005: Ephemeral test fixtures versus evaluation targets](0005-ephemeral-fixtures-versus-targets.md)
