# Curated Results

This directory is for deliberately reviewed result artifacts that support a
public benchmark claim. Routine local output belongs under `results/local/` or
`runs/`, both of which are ignored.

A curated result must include or reference:

- a summary with clearly labeled native or reference-conformance mode;
- model and dataset identities with immutable revisions;
- prompt, generation, extraction, and scoring versions;
- project code revision and relevant runtime configuration; and
- retained raw generations and per-example scoring evidence, or a documented
  durable location for those artifacts.

Do not replace an existing curated result after a material methodology change;
publish a new, separately identifiable run.
