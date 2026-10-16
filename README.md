# SLM Math Evaluation

A reproducible, auditable evaluation of small open language models on mathematical
reasoning, run entirely on a laptop. The first path measures **Qwen3-8B on MATH-500**
with a pinned local runtime (llama.cpp and Qwen's official GGUF), a frozen protocol,
two independent graders, and per-problem evidence.

**Result (October 2026):** **90.2%** on MATH-500 (451/500; 95% interval 87.3–92.5%),
from one pass with 4-bit weights on a laptop. MathGPT.ai reported 88.80% under a
protocol whose sampling settings, output budget and grader aren't stated. That figure
lies inside our 95% interval. The protocols may differ, so this compares two
measurements rather than verifying theirs. Thirty answers hit the output cap and count
as wrong, and a second grader scores the same answers at 92.0%. With reasoning turned
off, accuracy falls to 81.4%. The [results page](results/2026-10-math500-qwen3-8b/)
covers the method, sensitivities, caveats and next steps.

This project was developed with AI assistance.

## Reproducing the results

Tested on an Apple M1 Pro with 32 GB of memory. The 500-problem test tier takes
about 33 hours with reasoning on; start with the 16-problem smoke tier.

1. Install the locked environment: `poetry install`
2. Download the pinned llama.cpp runtime archive and Qwen3-8B GGUF named in
   [decision 0001](docs/decisions/0001-local-qwen-runtime.md#reproduction) into
   the dataset cache's `models/` directory, then extract the archive there:

   ```bash
   tar -xzf ~/.cache/slm-eval/data/models/llama-b10412-bin-macos-arm64.tar.gz -C ~/.cache/slm-eval/data/models
   ```

   `slm-eval` reads the runtime and model from the cache, not from the
   repository `models/` directory that decision 0001's feasibility script uses.
3. Fetch the pinned dataset: `poetry run slm-eval data fetch`
4. Verify the runtime and model for the protocol:

   ```bash
   poetry run slm-eval verify-runtime --protocol configs/protocols/protocol.yaml
   poetry run slm-eval verify-artifact --protocol configs/protocols/protocol.yaml
   ```

5. Run a tier (`smoke`, `validation` or `test`). The run ID is printed on its own
   line before generation starts:

   ```bash
   poetry run slm-eval run --protocol configs/protocols/protocol.yaml --manifest configs/datasets/math500-manifest-v1.json --tier smoke
   ```

   Resume an interrupted run with `poetry run slm-eval run --resume <run-id>`.
6. Report, compared with the published figure as a fraction:
   `poetry run slm-eval report --run <run-id> --compare-published 0.888`

The dataset cache defaults to `~/.cache/slm-eval/data` (`SLM_EVAL_CACHE`), and runs
are stored under `~/.cache/slm-eval/runs` (`SLM_EVAL_RUNS`).

## Repository map

- `configs/`: versioned dataset manifest and evaluation protocol files
- `docs/`: evaluation methodology, conformance rules, and public decisions
- `src/slm_math_evaluation/`: Python implementation package
- `tests/`: unit and visible specification tests, plus the holdout suite in
  `tests/holdout/`, run with the documented holdout command under Verification
- `results/`: curated results, starting with [Qwen3-8B on MATH-500 (October 2026)](results/2026-10-math500-qwen3-8b/)
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
are in the [v0.7 contract](docs/math500-interface-contract-v0.7.md).

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
deselects the four non-required cases: HO-MF-05 and HO-RP-05 (advisory), and
HO-MF-04 and HO-RT-04 (invalid).
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
