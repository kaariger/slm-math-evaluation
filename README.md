# SLM Math Evaluation

SLM Math Evaluation implements a local Qwen3-8B × MATH-500 evaluation path.
The pinned dataset, split manifest, keyed llama.cpp runtime, extraction,
two scorers, run store, rescoring, sensitivity analysis, and aggregate reports
are available. The protocol files remain proposals until the maintainer
approves the outstanding runtime parameters. No tier results are included.

## Repository map

- `configs/`: versionable model, dataset, prompt, and generation templates
- `docs/`: evaluation methodology, conformance rules, and public decisions
- `src/slm_math_evaluation/`: Python implementation package
- `tests/`: unit and maintainer-ruled visible specification tests
- `results/`: curated, reviewable result artifacts only
- `scripts/validate_public_repo.py`: deterministic public-content leakage check
- `scripts/validate_runtime_feasibility.py`: local runtime feasibility check;
  requires the pinned artifacts described in decision 0001

## First local runtime

The first Qwen3-8B path uses pinned llama.cpp and Qwen's official Q4_K_M
GGUF, with an official Q8_0 variant also pinned. Artifact identities and
feasibility evidence are in
[decision 0001](docs/decisions/0001-local-qwen-runtime.md).
The [protocol proposal](docs/math500-protocol-proposal.md) identifies every
remaining runtime choice and its provenance. `slm-eval run` rejects a draft
protocol; it can start only after the maintainer freezes an approved version.

The CLI provides `data fetch`, `data build-manifest`, `data show-pair`,
`data apply-verdicts`, `verify-runtime`, `verify-artifact`, `run`, `run --resume`,
`rescore`, `sensitivity`, and `report`. The interface and run-store schemas
are in the [v0.5 contract](docs/math500-interface-contract-v0.5.md).

Local datasets, model weights, caches, databases, logs, and full run artifacts
are ignored by default. A result belongs under `results/` only after it has
been deliberately curated with enough provenance to audit the claim.

## Verification

Install the locked project environment and run the checks from the repository root:

```bash
poetry install
poetry check --lock
poetry run python -m unittest discover -s tests -v
poetry run pytest -q tests/visible
poetry run python scripts/validate_public_repo.py .
```

The public-content validator checks generic local-path and credential patterns.
It is a cheap deterministic bootstrap guard, not a complete secret scanner.
