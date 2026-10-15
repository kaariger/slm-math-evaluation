# SLM Math Evaluation

SLM Math Evaluation implements a local Qwen3-8B × MATH-500 evaluation path.
The pinned dataset, split manifest, keyed llama.cpp runtime, extraction,
two scorers, run store, rescoring, sensitivity analysis, and aggregate reports
are available. Protocols `v1` (Q4_K_M) and `v1-q8` (Q8_0) are approved;
any revision requires a new version. No tier results are included.

Results page: [Qwen3-8B on MATH-500, October 2026](docs/results/2026-10-math500-qwen3-8b/).

## Repository map

- `configs/`: versioned dataset manifest and evaluation protocol files
- `docs/`: evaluation methodology, conformance rules, and public decisions
- `src/slm_math_evaluation/`: Python implementation package
- `tests/`: unit and visible specification tests
- `results/`: curated, reviewable result artifacts only
- `scripts/validate_public_repo.py`: deterministic public-content leakage check
- `scripts/validate_runtime_feasibility.py`: local runtime feasibility check;
  requires the pinned artifacts described in decision 0001

## First local runtime

The first Qwen3-8B path uses pinned llama.cpp and Qwen's official Q4_K_M
GGUF, with an official Q8_0 variant also pinned. Artifact identities and
feasibility evidence are in
[decision 0001](docs/decisions/0001-local-qwen-runtime.md).
Run startup checks all 43 regular runtime files and 18 library links against
the pinned release archive manifest.
The [protocol decisions](docs/math500-protocol-proposal.md) identify runtime
choices and their provenance. `slm-eval run` accepts the approved files after
runtime and artifact verification records have been created for their hashes.
Generation runs require matching runtime and artifact verification records.

The CLI provides `data fetch`, `data build-manifest`, `data show-pair`,
`data apply-verdicts`, `verify-runtime`, `verify-artifact`, `run`, `run --resume`,
`rescore`, `sensitivity`, and `report`. The interface and run-store schemas
are in the [v0.6 contract](docs/math500-interface-contract-v0.6.md).

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
PYTHONDONTWRITEBYTECODE=1 poetry run pytest -q tests/holdout \
  -k "not test_ho_mf_04 and not test_ho_mf_05 and not test_ho_rp_05 and not test_ho_rt_04"
poetry run python scripts/validate_public_repo.py .
```

The holdout command runs only the required holdout cases; its `-k` expression
deselects the advisory cases HO-MF-04, HO-MF-05, HO-RP-05 and HO-RT-04.
Holdout cases that need the dataset, the network or the real runtime are
skipped, with the reason shown, unless their gates are set:

- `SLM_EVAL_HOLDOUT_NETWORK=1` lets data cases fetch the pinned dataset with
  `slm-eval data fetch`; the fetch case needs it.
- `SLM_EVAL_HOLDOUT_SEED_CACHE=<dir>` seeds each case's temporary dataset cache
  from a populated cache, enabling the other data cases without network access.
- `SLM_EVAL_HOLDOUT_RUNTIME=1`, together with a seed cache that holds `models/`,
  enables the runtime cases. They need the pinned model and llama.cpp runtime,
  `ps` and `lsof`, and perform full smoke-tier runs that can take hours;
  `SLM_EVAL_HOLDOUT_RUN_TIMEOUT` sets the per-run limit (default 14400 seconds).

Run the holdout command from the repository root. `tests/holdout/README.md`
lists every suite variable.

The public-content validator checks generic local-path and credential patterns.
It is a cheap deterministic bootstrap guard, not a complete secret scanner.

## License

Licensed under the [Apache License 2.0](LICENSE); third-party notices are in [NOTICE](NOTICE).
