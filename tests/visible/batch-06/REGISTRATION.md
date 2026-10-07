# Visible suite — batch-06 registration (brief §3)

- **Suite:** visible, for every entry below.
- **Status:** PROPOSED, for every entry below. Only the maintainer sets `REQUIRED`, `ADVISORY` or `INVALID`.
- **Supersedes batch-05** (maintainer ruling). VS-DT-05 now follows the contract v0.4 §5 clarification (`MATH500-INTERFACE-CONTRACT-v0.4.md`, SHA-256 `aaac6969fc51fa5dbf225113cbc5085eacb39af2da31720be7fd4188d961e463`). Every other test and entry is unchanged from batch-05.
- **Contract:** v0.3 (`MATH500-INTERFACE-CONTRACT-v0.3.md`, SHA-256 `f3f4e397ae110c2d4e86abc4169ef89432cc71fdf91b233c33a4b4c013ba7021`).
- **Implementation commit read:** `18a67b75af9f1b1c670faa7291271033d97fefd0`.
  - It was fetched read-only from `https://github.com/kaariger/slm-math-evaluation/archive/18a67b75af9f1b1c670faa7291271033d97fefd0.tar.gz` into `ref-18a67b75af9f1b1c670faa7291271033d97fefd0/` in scratch.
  - The archive's SHA-256 is `b37e46c560c0fcb7331b86f43cc15aa6d8f1fb647be870f4b9f49c4e0236732f`, and its pax header names the same commit.
  - Nothing was cloned, committed or pushed, and no code from it was run.
- **Implementation code seen before writing:** **Yes**, for every entry below: commit `18a67b75…fd0`. These are therefore visible tests by definition. They still exercise the implementation only through the `slm-eval` CLI and the contract's schemas, with no imports from the package.
- **File:** `test_vs_data.py`, SHA-256 `ba808bdc812936c35e0e871722ce19c3c196ff08d7d39e227b1bdb731ce6a48b`. This was copied from `shasum -a 256` output. `SHA256.txt` lists every file in the batch.
- **Resources** (stated in each docstring):
  - *hermetic:* the CLI only.
  - *repo:* the committed manifest, found through `SLM_EVAL_TEST_MANIFEST` or by content in the repository.
  - *data:* `SLM_EVAL_TEST_SEED_CACHE` or `SLM_EVAL_TEST_NETWORK=1`. Skipped otherwise.
  - *network:* `SLM_EVAL_TEST_NETWORK=1`. Skipped otherwise.
- **Theme:** §3 `data` commands, the §5 manifest, and §5.1 flagged pairs, verdicts and invariants.

| ID | File | Resources | Contract clause | Expected behaviour |
|---|---|---|---|---|
| VS-DT-01 | `test_vs_data.py` | hermetic | §3 exit 2 (manifest check); apply-verdicts fails without changing anything | manifest whose `source` is not the pinned source → `apply-verdicts` exit 2; manifest byte-identical |
| VS-DT-02 | `test_vs_data.py` | hermetic | §5.1 `manifest_inputs_sha256`; §3 exit 2 (identity) | flagged file built from other inputs → `show-pair` exit 2; empty stdout; nothing written |
| VS-DT-03 | `test_vs_data.py` | hermetic | §3 build-manifest; exit 3 (missing file) | empty cache, with `--out` and `--flagged-out` → exit 3; neither file written |
| VS-DT-04 | `test_vs_data.py` | repo | §5 schema; §5.1 invariants; §2 no dataset text | committed manifest: all §5 keys, version "1", 500 test items, unique well-formed items, dev-only maintainer exclusions, tiers of non-excluded dev ids, no text fields or long strings |
| VS-DT-05 | `test_vs_data.py` | repo | §5 `membership_derivation` (v0.4 clarification) | method is `unique_id` or `content-hash`; `independent_source` is `null` when method is `unique_id`, and an object with `url`, `revision` and a 64-hex `sha256` when method is `content-hash` |
| VS-DT-06 | `test_vs_data.py` | network, repo | §3 data fetch; written paths printed | fetch into an empty cache → exit 0; the printed paths are files in `SLM_EVAL_CACHE` whose SHA-256 values are exactly the committed manifest's pinned file hashes |
| VS-DT-07 | `test_vs_data.py` | data | §3 data fetch: a mismatch exits 2 | one byte appended to a cached pinned file → fetch exit 2 |
| VS-DT-08 | `test_vs_data.py` | data | §3 build-manifest; written paths printed; flagged file defaults to the cache | no `--flagged-out` → exit 0; stdout lists the manifest and a flagged file inside `SLM_EVAL_CACHE`; only the manifest lands in the working directory |
| VS-DT-09 | `test_vs_data.py` | data | §3 build-manifest deterministic (byte-identical) | builds with different `--flagged-out` paths, from different working directories → byte-identical manifests |
| VS-DT-10 | `test_vs_data.py` | data | §5 / §5.1 invariants; neither file contains problem text | built manifest passes the §5/§5.1 checks; flagged file has exactly the §5.1 keys and pair fields (dev `id`, test `matched_id`, exact/near, similarity in [0, 1]); no cached problem text in either file |
| VS-DT-11 | `test_vs_data.py` | data | §3 build-manifest: pinned-file mismatch exits 2 and writes nothing | altered pinned files → exit 2; neither manifest nor flagged file written |
| VS-DT-12 | `test_vs_data.py` | data | §3 data show-pair | first flagged pair → exit 0; both problem texts on stdout; nothing written (skips if no pairs) |
| VS-DT-13 | `test_vs_data.py` | data | §3 show-pair; exit 3 (precondition) | pair number not in a valid flagged file → exit 3; empty stdout |
| VS-DT-14 | `test_vs_data.py` | data | §3 apply-verdicts; §5 exclusions | all "keep" → exit 0; manifest path printed; every pair recorded as `keep` / `maintainer`; tiers and test items unchanged |
| VS-DT-15 | `test_vs_data.py` | data | §3 apply-verdicts deterministic; §5.1 invariants | all "exclude", applied to a manifest and to its byte copy → identical bytes; flagged dev ids excluded and in no tier; test items unchanged; §5/§5.1 checks pass |
| VS-DT-16 | `test_vs_data.py` | data | §3 apply-verdicts: `flagged_sha256` mismatch exits 2 | wrong `flagged_sha256` → exit 2; manifest byte-identical |
| VS-DT-17 | `test_vs_data.py` | data | §3 apply-verdicts: a change to a used tier exits 3; §5.1 used tier immutable | run directory in `SLM_EVAL_RUNS` (with a `manifest.json` copy, tier smoke) + exclude verdicts → exit 3 with the manifest byte-identical, or exit 0 with smoke unchanged (skips if no pairs) |
| VS-DT-18 | `test_vs_data.py` | data | §3 apply-verdicts / §5.1 verdicts (implementation-informed) | verdicts omitting one flagged pair → exit 3; manifest byte-identical (skips if no pairs) |
| VS-DT-19 | `test_vs_data.py` | data, repo | §3 build-manifest deterministic; §5 committed manifest | while the committed manifest has no exclusions, a fresh build is byte-identical to it (skips once verdicts are applied) |

## Notes for ruling

- **VS-DT-05** asserts the v0.4 §5 rule. At commit `18a67b75…fd0` the committed manifest uses `method: unique_id` with `independent_source: null`, which satisfies that rule.
- **VS-DT-18 and VS-DT-01 / VS-DT-02.** VS-DT-18 encodes a behaviour of this commit that the contract doesn't state: a verdict set must cover every flagged pair. VS-DT-01 and VS-DT-02 rest on §3's general "manifest / identity check → 2" rule, not on a command-specific clause.
- **Extra manifest keys.** The commit's manifest carries `review_status`, `flagged_sha256` and `flagged_pairs_count`, which §5 doesn't list. No test here asserts or forbids them.
- **CLI options.** The commit adds an optional `apply-verdicts --flagged`. These tests use only the §3 signature and rely on the default flagged location in the cache.
