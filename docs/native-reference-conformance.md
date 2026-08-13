# Native and Reference Conformance

The project keeps two related but distinct evaluation modes.

| Mode | Purpose | Claim boundary |
| --- | --- | --- |
| Native | Provide an inspectable project-owned path from examples to scores. | Describes results under this repository's documented protocol. |
| Reference conformance | Exercise an official or broadly accepted evaluator, harness, prompt protocol, split, or scorer. | Supports comparison only to the recorded reference and version. |

Before making a comparable benchmark claim, identify the accepted reference
method and record its version. An official competition evaluator is
authoritative for competition-conformance claims.

For every conformance comparison, record:

- reference implementation and immutable version;
- model, dataset, split, prompt, and generation settings;
- extraction and scoring differences;
- environment or runtime differences that could affect output;
- aggregate deltas and relevant per-example disagreements; and
- whether each difference is intentional, unavoidable, or unexplained.

Native and reference results must use distinct labels and artifact locations.
Intentional deviations must be explicit, reproducible, and versioned. Exact
agreement is not assumed: a characterized discrepancy is a valid result when
the available methodology is incomplete or produces materially different
outcomes.
