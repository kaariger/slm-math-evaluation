# SLM Math Evaluation

SLM Math Evaluation is an inception-stage project for reproducible evaluation
of locally runnable generative language models on mathematical reasoning tasks.
The first implementation milestone will establish one complete evaluation path
before the project broadens to additional models or datasets.

This repository is moving beyond its initial scaffold. The first local runtime
has been selected through a focused Qwen3-8B feasibility check, but the runtime
adapter, datasets, and MATH-500 evaluation are not yet implemented.

## Repository map

- `configs/`: versionable model, dataset, prompt, and generation templates
- `docs/`: evaluation methodology, conformance rules, and public decisions
- `src/slm_math_evaluation/`: Python implementation package
- `tests/`: dependency-free bootstrap validation tests
- `results/`: curated, reviewable result artifacts only
- `scripts/validate_public_repo.py`: deterministic public-content leakage check
- `scripts/validate_runtime_feasibility.py`: local runtime feasibility check;
  requires the pinned artifacts described in decision 0001

## First local runtime

The first Qwen3-8B path will use pinned llama.cpp and Qwen's official Q4_K_M
GGUF. The evidence, exact artifact identities, tradeoffs, and reproduction
command are recorded in
[decision 0001](docs/decisions/0001-local-qwen-runtime.md). This selects a
runtime; it does not implement the production adapter or set the benchmark
prompt protocol.

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
