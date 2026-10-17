# slm-eval MATH-500 vertical path — hidden holdout suite

- **Contract:** interface contract v0.3 (approved 2026-10-06). The suite was written against v0.2, as quoted in test-author brief 02, and aligned with v0.3 before registration.
- **Written by:** the independent test author (Challenger), Phase 1, from the contract alone and before any implementation existed. No implementation code had been seen when any file here was written or revised.
- **Status:** every test is `PROPOSED`. Only the maintainer sets `REQUIRED`, `ADVISORY` or `INVALID`. Per-test entries are in `REGISTRATION.md`.
- **Scope:** the tests touch the implementation only through the `slm-eval` command line and the contract's file and run-store schemas. They import no implementation module.

## Files

| File | Covers | Needs |
|---|---|---|
| `test_ho_exit_codes.py` | §3 entry point and exit codes | nothing beyond the CLI |
| `test_ho_rescore.py` | §3 rescore, §6.1, §6.2, §6.4, §8, §9 via run-store fixtures | nothing beyond the CLI |
| `test_ho_report.py` | §7 report contents and comparison reading | committed protocol (recommended) |
| `test_ho_sensitivity.py` | §7.1 sensitivity | committed protocol (recommended) |
| `test_ho_contract_files.py` | committed `protocol.yaml` (§4) and `manifest.json` (§5) | repository checkout |
| `test_ho_data_real.py` | `data` commands on the pinned dataset | dataset (seed cache or network) |
| `test_ho_runtime_real.py` | §10 runtime security, runs, resume, evidence | real model + runtime + seed cache with `models/` |
| `_ho_support.py`, `conftest.py`, `pytest.ini` | shared helpers and wiring | — |
| `REGISTRATION.md`, `README.md` | per-test entries; this guide | — |

## Running at the review gate

Run from the repository root, inside the project environment, so that `slm-eval` is on `PATH`:

```sh
PYTHONDONTWRITEBYTECODE=1 poetry run pytest /path/to/holdout
```

- `pytest.ini` disables pytest's cache directory and `conftest.py` turns off bytecode writing, so nothing is written next to the registered files. If the volume is read-only, copy the folder elsewhere first and check its hashes.
- Each test creates its own temporary `SLM_EVAL_CACHE`, `SLM_EVAL_RUNS` and working directory. The real `~/.cache/slm-eval` is never used.

Optional environment variables. They are read by the suite only and never passed to `slm-eval`:

| Variable | Effect |
|---|---|
| `SLM_EVAL_HOLDOUT_CMD` | command used to invoke the CLI (default: `slm-eval` on `PATH`) |
| `SLM_EVAL_HOLDOUT_REPO` | repository root (default: found from the working directory) |
| `SLM_EVAL_HOLDOUT_PROTOCOL` | committed protocol file (default: discovered in the repository) |
| `SLM_EVAL_HOLDOUT_MANIFEST` | committed split manifest (default: discovered in the repository) |
| `SLM_EVAL_HOLDOUT_SEED_CACHE` | populated dataset cache copied into each test's cache (enables `requires_data`). Runtime tests need its `models/` directory, since §2 puts model files in the dataset cache. |
| `SLM_EVAL_HOLDOUT_NETWORK=1` | tests may run `slm-eval data fetch` (enables `requires_data` and `requires_network`) |
| `SLM_EVAL_HOLDOUT_RUNTIME=1` | real model artifact and pinned runtime available. Together with the seed cache, this enables `requires_runtime`. |
| `SLM_EVAL_HOLDOUT_RUN_TIMEOUT` | seconds allowed for one real run (default 14400) |

Tests marked `requires_*` are **skipped**, with the reason shown by `-ra`, unless their resources are enabled. A skipped test has not been evaluated. Runtime tests also need `ps` and `lsof`. Several of them perform full smoke-tier runs, so expect them to take hours.

## Points v0.3 still leaves open

- **Report field names.** v0.3 defines what the report contains but not its field names. Report tests look for values under keyword-matched keys.
- **Factor names for `{value, provenance}` fields.** §7.1 names factors "by dotted path", but its examples omit the final `.value`. Both `reasoning_mode` and `reasoning_mode.value` are accepted, and likewise for `sampling.*`.
- **`base_value` and `variant_value` for `model.artifact`.** Their representation is unspecified, so HO-SE-10 only requires that the two differ.
