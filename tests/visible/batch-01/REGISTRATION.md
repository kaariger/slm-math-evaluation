# Visible suite — batch-01 registration (brief §3)

- **Suite:** visible, for every entry below.
- **Status:** PROPOSED, for every entry below. Only the maintainer sets `REQUIRED`, `ADVISORY` or `INVALID`.
- **Contract:** v0.3 (`MATH500-INTERFACE-CONTRACT-v0.3.md`, SHA-256 `f3f4e397ae110c2d4e86abc4169ef89432cc71fdf91b233c33a4b4c013ba7021`).
- **Implementation commit:** none supplied. These are contract-only tests.
- **Implementation code seen before writing:** **No**, for every entry below.
- **File:** `test_vs_cli_usage.py`, SHA-256 `844657c7d9c9033ebadaa54b987839d9701739c187416f9517133f0e018ef181`. This was copied from `shasum -a 256` output. `SHA256.txt` lists every file in the batch.
- **Resources:** hermetic, needing only the `slm-eval` CLI. Each test uses its own temporary `SLM_EVAL_CACHE`, `SLM_EVAL_RUNS` and working directory. `SLM_EVAL_TEST_CMD` can override the CLI command.
- **Theme:** v0.3 §3 general behaviour. Usage errors exit `3`, never `2`. Only written paths go to stdout, and logs go to stderr.

| ID | File | Contract clause | Expected behaviour |
|---|---|---|---|
| VS-CLI-01 | `test_vs_cli_usage.py` | §3 usage errors (missing argument) | `slm-eval` with no command → exit 3; nothing written |
| VS-CLI-02 | `test_vs_cli_usage.py` | §3 usage errors (missing argument) | `slm-eval data` with no subcommand → exit 3; nothing written |
| VS-CLI-03 | `test_vs_cli_usage.py` | §3 usage errors (unknown option) | `data fetch --definitely-not-an-option` → exit 3; nothing fetched, cache empty |
| VS-CLI-04 | `test_vs_cli_usage.py` | §3 usage errors (missing argument); §3 sensitivity `--out` | `sensitivity` without `--out` → exit 3; nothing written |
| VS-CLI-05 | `test_vs_cli_usage.py` | §3 usage errors (malformed argument); §3 run `--k N` | `run … --k two` → exit 3; no run directory |
| VS-CLI-06 | `test_vs_cli_usage.py` | §3 run `--tier {smoke,validation,test}`; usage errors | `run … --tier gold` → exit 3; no run directory |
| VS-CLI-07 | `test_vs_cli_usage.py` | §3 report `<value>` is a fraction; usage errors (malformed argument) | `report … --compare-published high` → exit 3; nothing written |
| VS-CLI-08 | `test_vs_cli_usage.py` | §3 `show-pair --pair <n>`; usage errors (malformed argument) | `--pair first` → exit 3; nothing written |
| VS-CLI-09a | `test_vs_cli_usage.py` | §3 written paths to stdout, logs to stderr | no command → fails with empty stdout (usage text, if any, goes to stderr) |
| VS-CLI-09b | `test_vs_cli_usage.py` | §3 written paths to stdout, logs to stderr | `rescore` on a missing run → fails with empty stdout |
| VS-CLI-09c | `test_vs_cli_usage.py` | §3 written paths to stdout, logs to stderr | `show-pair` on a missing flagged file → fails with empty stdout |

## Notes for ruling

- **VS-CLI-01 and VS-CLI-09a.** These read "no command" as a missing argument, which is a usage error under §3. Many CLI frameworks print help to stdout and exit `0` in this case instead.
- **VS-CLI-05 to 08.** The files the commands name don't exist, so a missing-file precondition would also give exit `3`. The tests therefore pin the exit code §3 requires, never `2`, without depending on the order of checks.
