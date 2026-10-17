# Visible suite — batch-07 registration (brief §3)

- **Suite:** visible, for every entry below.
- **Status:** PROPOSED, for every entry below. Only the maintainer sets `REQUIRED`, `ADVISORY` or `INVALID`.
- **Contract:** v0.5 (`MATH500-INTERFACE-CONTRACT-v0.5.md`, SHA-256 `a5fe9054c0e6970ddc63b65ae2df64246d7af0472636c33e7b12c6e1b072fc42`). It supersedes v0.4 for §10 and the `verify-runtime` checks only, and §4 is unchanged. This batch was aligned with v0.5 before delivery.
- **Implementation commit:** none supplied for this work. These are contract-only tests.
- **Implementation code seen before writing:** Yes, for every entry below, but none of it bears on these tests.
  - Commit `18a67b75af9f1b1c670faa7291271033d97fefd0` had been read for batch-05. It holds only the data layer: no runtime, `verify-runtime` or `protocol.yaml` code.
  - These tests were not informed by it.
  - No runtime code has been seen.
- **Files:** copied from `shasum -a 256` output. `SHA256.txt` lists every file in the batch.
  - `test_vs_protocol.py`, SHA-256 `09914f75e0172830b1b31a9c5408c92920a8eafe217ca61a4cce4d44dda9e264`
  - `test_vs_runtime.py`, SHA-256 `ce5d81c90c4d8eb333202b533f7c2d5c82bc2f742341b6e821328e69a2678098`

## How the tests are enabled

| Resource class | Needs | Enabled by |
|---|---|---|
| repo | the committed `protocol.yaml` | `SLM_EVAL_TEST_PROTOCOL`, or the single `protocol.yaml` in the repository containing the pytest working directory (paths under tests/ and fixtures/ are used only if nothing else exists). Parsed with PyYAML or ruamel.yaml, or as plain JSON; otherwise skipped. |
| hermetic | the `slm-eval` CLI only | always runs |
| **live** (real model + live server) | the pinned llama.cpp runtime, the real model, the dataset, the committed protocol, `ps` and `lsof` | `SLM_EVAL_TEST_RUNTIME=1` **and** `SLM_EVAL_TEST_SEED_CACHE` (a populated cache with the pinned dataset and `models/<model.artifact.file>`, copied into each test's temporary `SLM_EVAL_CACHE`, files over 256 MiB symlinked). Skipped if either is missing. |
| **live + run** | as live, plus the committed manifest | as live, plus `SLM_EVAL_TEST_MANIFEST` or the manifest found in the repository. The test runs `verify-artifact` and `verify-runtime` first (`run` exits 3 without both records), then starts `run --tier smoke --k 1` and stops it with SIGINT. |

`SLM_EVAL_TEST_CMD` can override the CLI command. No `SLM_EVAL_TEST_*` variable is passed to the implementation. Live processes run in their own session, and their process tree is killed after each test.

## `test_vs_protocol.py` — §4 protocol structure and provenance scope

| ID | Resources | Contract clause | Expected behaviour |
|---|---|---|---|
| VS-PF-01 | repo | §4 structure | `protocol_version`; `model.artifact` {file, sha256, quantization}; `runtime` {build, invocation}; `prompt` {template, system_prompt, few_shot}; `reasoning_mode.value`; `sampling`; `extraction` {id, version}; `scorers` primary/secondary {id, pin}; `dataset.source` {url, commit, files} |
| VS-PF-02 | repo | §4 sampling ("every other runtime parameter is declared explicitly here") | the seven named fields are present, and every sampling entry, extras included, is a {value, provenance} mapping |
| VS-PF-03 | repo | §4 provenance required on model.artifact, runtime, prompt, reasoning_mode, every sampling field | each of these carries a non-empty provenance |
| VS-PF-04 | repo | §4 provenance label set | every provenance label in the file is one of the five allowed labels |
| VS-PF-05 | repo | §4 model.artifact; §2 model files under `models/` | `file` is a bare file name (no directory, not absolute, no `~`); `sha256` is 64 lowercase hex; `quantization` is non-empty |
| VS-PF-06 | repo | §4 extraction/scorers/dataset identified by id, pin and hashes | extraction id and semver version; non-empty scorer ids and pins; dataset url, hex commit, and files with a path and 64-hex sha256 |
| VS-PF-07 | repo | §4 placeholders filled; values read from the file under test | non-empty `protocol_version`; mode is one of the three; positive integer `max_output_tokens`; template contains `{problem}`; `system_prompt` is null or a string; `few_shot` ≥ 0; no `<placeholder>` string left |

## `test_vs_runtime.py` — §10 runtime security (v0.4 and v0.5) and §3 `verify-runtime`

| ID | Resources | Contract clause | Expected behaviour |
|---|---|---|---|
| VS-RT-01 | hermetic | §3 exit 3 (missing file); §2 model under `models/` | empty `SLM_EVAL_CACHE` → `verify-runtime` exit 3; no `runtime-verification.json`; no surviving process (uses the committed protocol if found, else a §4 draft protocol) |
| VS-RT-02 | live | §3 verify-runtime; record location; written path printed | exit 0; `$SLM_EVAL_RUNS/_verification/<SHA-256 of the protocol file>/runtime-verification.json` is written as JSON and its path is printed on stdout |
| VS-RT-03 | live | §10 verification record; §3 verify-runtime checks | the record lists routes probed, each with a protected/exempt class and ≥ 2 HTTP statuses; it covers `/health`, `/v1/models`, `/props`, `/slots` and a generation route; `/health` and `/v1/models` are exempt; protected routes show 401/403; exempt routes show a 2xx; any route without a 401/403 is classified exempt (records of `OPTIONS` probes are excluded here and judged by VS-RT-11) |
| VS-RT-04 | live + run | §10 key required except the approved exemption; nothing else answers without the key (data-bearing GET/POST; preflight is VS-RT-10) | during a smoke run, `/health` and `/v1/models` answer 200 without a key; every other probed route, with no key, a wrong bearer key or a wrong X-Api-Key, gets 401/403/404, or is a GET alias whose body matches the `/health` or `/v1/models` response |
| VS-RT-05 | live + run | §10 model alias `qwen3-8b`; no file path in the listing | `/v1/models` without a key → 200, naming `qwen3-8b`; no path-like string (`/…`, `~…`, `.gguf`), cache directory or home directory in it or in `/health` |
| VS-RT-06 | live + run | §10 loopback-only, ephemeral port | every listening socket of the run's process tree is on 127.0.0.1, with its port in the OS ephemeral range |
| VS-RT-07 | live + run | §10 key in memory only, never written | no `--api-key-file`; the key seen in the server's arguments or environment is in no file under the test's cache, run store or working directory, nor in captured stdout or stderr (skips if the key isn't observable) |
| VS-RT-08 | live | §10 terminated on normal exit | after `verify-runtime` exits 0, no process it started survives and no port it opened is listening (skips if no server observed) |
| VS-RT-09 | live + run | §10 terminated on interrupt | SIGINT to the `slm-eval run` process alone → it exits; no surviving process or listening port |
| VS-RT-10 | live + run | §10 CORS preflight (v0.5): an empty-body `OPTIONS` answer without the key is allowed; data-bearing methods still need the key | for every probed route, `OPTIONS` with preflight headers and no key → 401/403/404, or an empty body; where it is answered, the route's data-bearing method without the key is refused (401/403/404) unless it is `/health`, `/v1/models` or a GET alias of their handlers (skips if no route answers `OPTIONS`) |
| VS-RT-11 | live | §10 verification record (v0.5): each `OPTIONS` probe recorded with its status, body length and the data-bearing result without the key | the record has `OPTIONS` probe records, at least one on a non-exempt route; each has a body length and ≥ 2 statuses; on non-exempt routes the data-bearing result is 401/403, and no 2xx `OPTIONS` answer has a non-zero body length (exempt = `/health`, `/v1/models`, and routes the record classifies as exempt) |

## Notes for ruling

- **"Any unexpected unauthenticated route exits 4" (§3).** This is not exercised. Triggering it would mean modifying the pinned runtime, and contract-only tests can't do that. The v0.5 rule that an empty-body preflight doesn't trigger exit 4 is covered indirectly: VS-RT-02, VS-RT-03 and VS-RT-11 expect `verify-runtime` to exit 0.
- **Aliases of the exempt handlers.** In VS-RT-04 an alias is recognised by behaviour, not from a list: a GET whose body matches `/health` or `/v1/models`, with volatile `created` fields ignored.
- **Minimum route coverage in VS-RT-03.** VS-RT-03 requires the record to cover `/props`, `/slots` and a generation route, as well as the two exempt routes. These are the routes whose data §10 names (property, slot, generation). The record's field names aren't fixed by the contract, so routes, classes and statuses are found by value and key keywords.
- **Fallback in VS-RT-01.** VS-RT-01 falls back to a §4 draft protocol only when no committed `protocol.yaml` is found. Run from the repository, it uses the committed one.
- **`OPTIONS` records in VS-RT-11.** These are found in either of two shapes: a probe object naming the route and the method `OPTIONS`, or an `options` object inside a route-keyed object, in which case the route object's statuses count as the data-bearing result. Body lengths are integers under `length`, `size` or `bytes` keys.
