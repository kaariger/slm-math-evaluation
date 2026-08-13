# Project Agent Guidance

## Purpose and phase

This project builds a reproducible, inspectable evaluation pipeline for
generative language models on mathematical reasoning tasks. It is currently in
the inception/bootstrap phase; the runtime and benchmark implementation are not
yet selected or implemented.

## Structure

- `configs/` holds versionable model, dataset, prompt, and generation inputs.
- `docs/` defines methodology, conformance, decisions, and public work items.
- `src/slm_math_evaluation/` contains the Python package.
- `tests/` contains automated verification.
- `results/` contains curated results, never routine local run output.

## Project rules

- Keep dataset loading, prompt construction, model execution, raw generation
  capture, answer extraction, dataset-specific scoring, and reporting separate.
- Keep model-specific behavior behind configuration or a narrow runtime
  boundary; do not embed it in loading, scoring, reporting, or run-state logic.
- Preserve raw generations and per-example scoring evidence for retained runs.
- Record model and dataset revisions, protocol, prompt and generation versions,
  extraction and scoring methods, code revision, and relevant runtime settings.
- Keep native results distinct from reference-conformance results. Document and
  version intentional deviations from an accepted reference method.
- Treat per-dataset results as primary; do not invent a combined headline score.
- Keep local weights, datasets, caches, databases, logs, and routine run output
  out of Git. Commit only deliberately curated results with provenance.
- Do not add cloud, distributed, workflow-engine, serving, vector-store, or UI
  infrastructure without a demonstrated requirement and explicit decision.
- Do not include local machine paths, credentials, or non-public project state
  in repository content.

## Working and verification

Inspect existing files and Git status before changes. Use a task branch for
normal work; do not edit protected branches. Prefer small changes and standard
library tooling until implementation evidence justifies dependencies.

Use Poetry for dependency management and project-environment execution. Keep
`pyproject.toml` and `poetry.lock` synchronized, install the environment with
`poetry install`, and run Python project commands through `poetry run`.

Before handoff, run the most relevant tests plus:

```bash
poetry check --lock
poetry run python -m unittest discover -s tests -v
poetry run python scripts/validate_public_repo.py .
```

Report checks that could not be run. Do not weaken verification to make a
change pass.
