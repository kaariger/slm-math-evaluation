# Holdout suite registration — per-test entries (brief §3)

- **Suite:** holdout, for every entry below.
- **Status:** PROPOSED, for every entry below. Only the maintainer sets `REQUIRED`, `ADVISORY` or `INVALID`.
- **Implementation code seen before writing or revising:** **No**, for every entry below. No implementation existed. None was looked for, and no repository was read.
- **Contract:** v0.3, approved 2026-10-06. Clause numbers below refer to v0.3. The suite was written against v0.2 and aligned with v0.3 before registration.
- **SHA-256:** each file's hash is in the coordinator's registration listing, made with `find … -exec shasum -a 256 {} \; | sort -k2` after this file was written. This file cannot carry its own hash.
- **Withdrawn before registration:** HO-PF-04. It compared the protocol with the values shown in the contract, and v0.3 §4 makes the approved protocol file authoritative.
- **Resources:** "hermetic" means only the `slm-eval` CLI is needed. "data" needs the pinned dataset (seed cache or network). "runtime" needs the real model under `$SLM_EVAL_CACHE/models/`, the pinned runtime and the dataset. Tests whose resources are missing are skipped, not evaluated.

## `test_ho_exit_codes.py` (hermetic)

| ID | Contract clause | Expected behaviour |
|---|---|---|
| HO-EX-01 | §3 entry point | `slm-eval` can be resolved in the test environment |
| HO-EX-02 | §3 usage errors exit 3, never 2 | unknown subcommand → exit 3 |
| HO-EX-03 | §3 usage errors exit 3, never 2 | `rescore` without `--run` → exit 3 |
| HO-EX-04 | §3 rescore; exit 3 (missing run) | unknown run ID → exit 3; nothing created in the run store |
| HO-EX-05 | §3 rescore; §6.1; exit 3 | run directory without `items.jsonl` → exit 3 |
| HO-EX-06 | §3 report; exit 3 | unknown run ID → exit 3 |
| HO-EX-07 | §3 report `--sensitivity`; exit 3 | missing sensitivity file (valid run) → exit 3 |
| HO-EX-08 | §3 sensitivity; exit 3 | unknown base run → exit 3; no output |
| HO-EX-09 | §3 sensitivity; exit 3 | unknown variant run → exit 3; no output |
| HO-EX-10 | §3 `run --resume`; exit 3 | unknown run ID → exit 3 |
| HO-EX-11 | §3 run; exit 3 | missing protocol and manifest → exit 3; no run items |
| HO-EX-12 | §3 verify-runtime; exit 3 | missing protocol file → exit 3 |
| HO-EX-13 | §3 verify-artifact; exit 3 | missing protocol file → exit 3 |
| HO-EX-14 | §3 `data show-pair`; exit 3 | missing flagged file → exit 3 |
| HO-EX-15 | §3 `data apply-verdicts`; exit 3 | missing manifest and verdicts → exit 3; no manifest created |
| HO-EX-16 | §3 `data build-manifest`; exit 3 (missing file) | empty dataset cache → exit 3; no manifest written |

## `test_ho_rescore.py` (hermetic; empty dataset cache)

| ID | Contract clause | Expected behaviour |
|---|---|---|
| HO-RS-01 | §3 rescore; §6.2; §6.4 | k=2 run of unambiguous items → exit 0; `<run>/rescore.json` follows §6.4 with `all_match: true` and one matching record per item × sample; original run files byte-identical |
| HO-RS-02 | §3 rescore; §6.4 | one stored verdict changed → `all_match: false`; that record has `stored.primary` false, `recomputed.primary` true and `match` false; every other record matches; originals untouched |
| HO-RS-03 | §8 last balanced `\boxed{}` wins | the verdict follows the last of two boxed answers (both orders) |
| HO-RS-04 | §8 nested braces | `\boxed{\frac{1}{2}}` yields the whole fraction, scored correct |
| HO-RS-05 | §8 balanced `\boxed{}` | an unterminated trailing `\boxed{` does not displace an earlier complete one |
| HO-RS-06 | §8 split_reasoning (text after the last `</think>`) | a boxed answer inside the thinking, or between two `</think>`, is not the answer; one after the last `</think>` is |
| HO-RS-07 | §9 `answer = None` scores incorrect | no extractable answer → incorrect, even against the reference "None" |
| HO-RS-08 | §6.1 record hash checks; exit 2 | tampered `raw_output` (hash mismatch) → rescore exits 2 |
| HO-RS-09 | §6.1 record hash checks; exit 2 | tampered `reference_answer` (hash mismatch) → rescore exits 2 |

## `test_ho_report.py` (hermetic; reports read from the run directory)

| ID | Contract clause | Expected behaviour |
|---|---|---|
| HO-RP-01 | §7 per-scorer accuracy, Wilson 95% interval (k=1); §3 default `--out-dir` | 20 items, 13 / 11 correct → `report.json` carries 0.65 and 0.55 with their Wilson intervals; both files are in the run directory |
| HO-RP-02 | §7 counts and definitions | 3 non-truncated `no_answer`, 15 with thinking, 2 disagreements → format failures 3, thinking rate 0.75, disagreements 2 |
| HO-RP-03 | §7 truncation | 4 of 20 truncated → truncations 4 (or rate 0.2) |
| HO-RP-04 | §7 provenance table and deviations; §4 | every provenance label and sampling field of the run's protocol appears in `report.json`; every label appears in `report.md`; a deviations entry exists |
| HO-RP-05 | §7 k>1 aggregates and per-seed results | k=2 with seed accuracies 0.7 / 0.4 → both appear, mean 0.55, a bootstrap interval containing 0.55, a spread entry |
| HO-RP-06 | §7 rule order | V = 0.6 inside the primary interval → CONSISTENT, although the other conditions also hold |
| HO-RP-07 | §7 rule 2, grader range | primary 0.5, secondary 0.8, V = 0.75 → PROTOCOL-SENSITIVE |
| HO-RP-08 | §7 rule 2, closed interval | V = 0.8, equal to the secondary accuracy → PROTOCOL-SENSITIVE |
| HO-RP-09 | §7 rule 2, between the two accuracies | primary 0.5, secondary 0.2, V = 0.25 → PROTOCOL-SENSITIVE |
| HO-RP-10 | §7 rule 3 | V = 0.9 with no condition met → UNEXPLAINED |
| HO-RP-11 | §7 rule 2, `[min(0, lo), max(0, hi)]` | d = 0.25 inside [0, 0.40] but not [0.30, 0.40] → PROTOCOL-SENSITIVE, naming `reasoning_mode` |
| HO-RP-12 | §7 rule 2, negative side | d = −0.25 inside [−0.40, 0] → PROTOCOL-SENSITIVE, naming the factor |
| HO-RP-13 | §7 rule 3 with a sensitivity file | d = 0.45 outside [0, 0.40] → UNEXPLAINED |
| HO-RP-14 | §7 every satisfying factor named | grader range and a comparison both hold → PROTOCOL-SENSITIVE, naming `reasoning_mode` |
| HO-RP-15 | §7 reading only with `--compare-published` | no option → no reading label |
| HO-RP-16 | §2 data policy; §7 aggregates only | sentinel tokens in raw outputs, answers and references appear in neither report file |

## `test_ho_sensitivity.py` (hermetic; factors compared on the run-directory copies)

| ID | Contract clause | Expected behaviour |
|---|---|---|
| HO-SE-01 | §7.1 schema and rules | one `reasoning_mode` variant → exit 0; the file has version "1", base run and protocol hash, and one comparison with correct factor, values, `paired_items` 20, accuracies 0.5 / 0.7, effect 0.2 inside its interval, the stated method, and seed and resamples recorded |
| HO-SE-02 | §3 / §7.1 deterministic and reproducible | two invocations → identical JSON |
| HO-SE-03 | §3 repeated `--variant` | `reasoning_mode` and `sampling.temperature` variants → two correct comparisons |
| HO-SE-04 | §7.1 more than one factor → exit 3, no output | variant differing in two factors → exit 3; no file written |
| HO-SE-05 | §7.1 exit 3, no output | one valid variant plus one two-factor variant → exit 3; no file written |
| HO-SE-06 | §7.1 manifest differs → exit 3, no output | variant on a different manifest copy → exit 3; no file written |
| HO-SE-07 | §7.1 tier differs → exit 3, no output | same manifest, smoke vs validation → exit 3; no file written |
| HO-SE-08 | §7.1 item set differs → exit 3, no output | same manifest and tier, one item missing → exit 3; no file written |
| HO-SE-09 | §7.1 zero factors differ → exit 3, no output | identical protocol → exit 3; no file written |
| HO-SE-10 | §7.1 `model.artifact` is one factor | quantization variant (file and hash change too) → exit 0; one comparison with factor `model.artifact`, differing values, accuracies 0.5 / 0.45 |

## `test_ho_contract_files.py` (repository files, read-only)

| ID | Contract clause | Expected behaviour |
|---|---|---|
| HO-PF-01 | §4 protocol structure | every section and key listed in §4 is present, with the right types |
| HO-PF-02 | §4 provenance scope and labels | `model.artifact`, `runtime`, `prompt`, `reasoning_mode` and every sampling field carry provenance; every label is one of the 5 allowed |
| HO-PF-03 | §4 placeholders filled | hex hashes, positive `max_output_tokens`, mode enum, `{problem}` in the template, semver extractor version, concrete scorer pins, hex commit |
| HO-MF-01 | §5 schema; §5.1 invariants | well-formed manifest; exclusions on dev items only, with source "maintainer"; tiers hold dev, non-excluded ids |
| HO-MF-02 | §5 items (MATH-500) | exactly 500 test items |
| HO-MF-03 | §5 source; §4 `dataset.source`; §3 run source check | manifest source equals the protocol's `dataset.source` |
| HO-MF-04 | §5 membership_derivation, normalization, provenance_note | method enum, a complete independent source, a numeric threshold, a non-empty note |
| HO-MF-05 | §5 no dataset text; §2 | no text fields; no string over 256 chars except `provenance_note` |

## `test_ho_data_real.py` (data)

| ID | Contract clause | Expected behaviour |
|---|---|---|
| HO-DR-01 | §3 `data fetch`; §4 (needs network) | fetch → exit 0; every pinned hash is matched by a cached file |
| HO-DR-02 | §3 build-manifest is deterministic | two builds from different working directories → byte-identical |
| HO-DR-03 | §5 schema, invariants, no text | 500 test items; source equals the pinned source; no problem text |
| HO-DR-04 | §3 `--flagged-out`; §5.1 flagged schema | exact keys; flagged ids are dev items; no problem text |
| HO-DR-05 | §3 default flagged location | the flagged file is written under `SLM_EVAL_CACHE`, not the working directory |
| HO-DR-06 | §3 `data show-pair` | exit 0; both problem texts printed; nothing written (skips if no pairs) |
| HO-DR-07 | §3 apply-verdicts; §5.1 invariants | exit 0; deterministic; test items unchanged; verdicts recorded with source "maintainer"; excluded ids in no tier |
| HO-DR-08 | §3 apply-verdicts, `flagged_sha256` mismatch → exit 2 | exit 2; manifest byte-identical |
| HO-DR-09 | §3 build-manifest pinned-file mismatch → exit 2, writes nothing | tampered cached file → exit 2; no manifest or flagged file written |

## `test_ho_runtime_real.py` (runtime)

| ID | Contract clause | Expected behaviour |
|---|---|---|
| HO-RT-01 | §3 verify-artifact; output location | exit 0; `_verification/<protocol_sha256>/artifact-verification.json` records the model hash |
| HO-RT-02 | §3 verify-artifact mismatch → exit 2 | protocol copy with a changed model hash → exit 2 |
| HO-RT-03 | §3 verify-runtime; §10; output location | exit 0; `_verification/<protocol_sha256>/runtime-verification.json` covers all six checks |
| HO-RT-04 | §10 key required on every endpoint | no key or wrong key → 401/403 on every probed endpoint (404 allowed for absent routes) |
| HO-RT-05 | §10 loopback-only, ephemeral port | every listening socket is on 127.0.0.1, with its port in the OS ephemeral range |
| HO-RT-06 | §10 key never written or printed | no `--api-key-file`; the observed key appears in no file, stdout or stderr (skips if the key isn't observable) |
| HO-RT-07 | §10 cleanup on normal exit | no surviving process or listening port after verify-runtime (skips if no server observed) |
| HO-RT-08 | §10 cleanup on interrupt | SIGINT to the CLI alone → no surviving process or port |
| HO-RT-09 | §3 run; §6.1 evidence; §8/§9; §10 raw output | k=2 smoke run: run ID printed; protocol and manifest byte copies matching their hashes; full `run.json` with protocol identities; complete, consistent `items.jsonl` |
| HO-RT-10 | §3 rescore; §6.2; §6.4; §7 aggregates only | real run → §6.4 `rescore.json` with `all_match: true`; originals untouched; report counts match `items.jsonl`; no per-item text |
| HO-RT-11 | §6.3 resume; §6.1 attempts | interrupted run then resume → only missing pairs generated, completed lines unchanged, attempts [interrupted, completed], status completed (skips if finished before SIGINT) |
| HO-RT-12a | §6.3; §6.1 copy hash; exit 2 | recorded `protocol_sha256` changed → resume exits 2; items unchanged |
| HO-RT-12b | §6.3; §6.1 copy hash; exit 2 | recorded `manifest_sha256` changed → resume exits 2; items unchanged |
| HO-RT-12c | §6.3; exit 2 | recorded `model_artifact_sha256` changed → resume exits 2; items unchanged |
| HO-RT-12d | §6.3 runtime build; exit 2 | recorded `runtime_build` changed → resume exits 2; items unchanged |
| HO-RT-13 | §5.1 used tier immutable; §3 apply-verdicts exit 3 | after a run on the smoke tier, exclude-all verdicts → exit 3 with the manifest byte-identical, or exit 0 with smoke unchanged (skips if no pairs) |
| HO-RT-14 | §3 run, manifest source mismatch → exit 2 | verification records present; manifest source differs from the protocol → exit 2 |

## Notes for the maintainer at the gate

- **Report field names.** v0.3 doesn't fix them, so report tests match values under keyword-matched keys.
- **Factor names.** §7.1 names factors "by dotted path", but its examples omit `.value`, so both spellings are accepted. The representation of `base_value` and `variant_value` for `model.artifact` is unspecified, so HO-SE-10 only requires that they differ.
- **Clauses not exercised:** exit code `4`, `--compare-published` outside [0, 1], `run` exiting 3 when a verification record is missing, the fallback-extraction count, `data fetch` exiting 2, and scanning the repository for forbidden content. The suite is kept to the clauses above.
