# Evaluation Methodology

## Evaluation boundary

The native evaluation path is a sequence of independently testable concerns:

1. load a versioned dataset split into canonical examples;
2. build prompts from a versioned prompt protocol;
3. execute a configured generative-text model;
4. capture raw generations before interpretation;
5. extract candidate answers with a versioned method;
6. apply dataset-specific scoring;
7. retain per-example evidence; and
8. produce a per-dataset report.

Dataset loading, answer extraction, and scoring must remain separate. Runtime-
specific behavior belongs at the model boundary rather than in dataset,
scoring, reporting, or general run-state code.

## Reproducibility record

Each retained evaluation run must identify:

- model identity and immutable revision;
- dataset identity, immutable revision, and split;
- evaluation protocol and reference method, when applicable;
- prompt, generation, extraction, and scoring versions;
- project code revision;
- relevant runtime and hardware settings; and
- start time, completion state, and deterministic seed policy.

Retained evidence includes raw generations, extracted answers, score decisions,
and enough per-example metadata to investigate failures. Material configuration
changes create a new run; curated or published results are never rewritten in
place to represent a different method.

## Reporting

Per-dataset results are primary. Reports should include aggregate metrics plus
links or identifiers for auditable per-example evidence. A combined score over
heterogeneous datasets is out of scope unless a later public decision defines
and justifies its weighting.

The concrete run schema, model runtime, and first reference harness remain open
implementation decisions. They must be resolved with evidence from the first
vertical path rather than assumed by this scaffold.
