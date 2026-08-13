# SLM Math Evaluation

SLM Math Evaluation is an inception-stage project for reproducible evaluation
of locally runnable generative language models on mathematical reasoning tasks.
The first implementation milestone will establish one complete evaluation path
before the project broadens to additional models or datasets.

This repository currently contains only the initial project scaffold. It does
not yet select a model runtime, download models or datasets, or implement the
MATH-500 evaluation.

## Repository map

- `configs/`: versionable model, dataset, prompt, and generation templates
- `docs/`: evaluation methodology, conformance rules, and public decisions
- `src/slm_math_evaluation/`: Python implementation package
- `tests/`: dependency-free bootstrap validation tests
- `results/`: curated, reviewable result artifacts only
- `scripts/validate_public_repo.py`: deterministic public-content leakage check

Local datasets, model weights, caches, databases, logs, and full run artifacts
are ignored by default. A result belongs under `results/` only after it has
been deliberately curated with enough provenance to audit the claim.

## Bootstrap verification

Install the dependency-free project environment and run the checks from the
repository root:

```bash
poetry install
poetry check --lock
poetry run python -m unittest discover -s tests -v
poetry run python scripts/validate_public_repo.py .
```

The public-content validator checks generic local-path and credential patterns.
It is a cheap deterministic bootstrap guard, not a complete secret scanner.
