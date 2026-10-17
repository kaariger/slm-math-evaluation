# Visible suite — batch-02 registration (brief §3)

- **Suite:** visible, for every entry below.
- **Status:** PROPOSED, for every entry below. Only the maintainer sets `REQUIRED`, `ADVISORY` or `INVALID`.
- **Contract:** v0.3 (`MATH500-INTERFACE-CONTRACT-v0.3.md`, SHA-256 `f3f4e397ae110c2d4e86abc4169ef89432cc71fdf91b233c33a4b4c013ba7021`).
- **Implementation commit:** none supplied. These are contract-only tests.
- **Implementation code seen before writing:** **No**, for every entry below.
- **File:** `test_vs_rescore.py`, SHA-256 `7a4b653e336b86eab6ade658ca82628f52ee5da89f0de333c59b046139264dda`. This was copied from `shasum -a 256` output. `SHA256.txt` lists every file in the batch.
- **Resources:** hermetic, needing only the `slm-eval` CLI. Each test writes a complete §6.1 run directory into its own temporary `SLM_EVAL_RUNS`, including `protocol.yaml` and `manifest.json` copies whose hashes match `run.json`. `SLM_EVAL_CACHE` is left empty.
- **Fixture protocol:** fixture runs use the committed `protocol.yaml`, found through `SLM_EVAL_TEST_PROTOCOL` or by searching the repository from the pytest working directory, whenever PyYAML can parse it. Otherwise they use the §4 draft defaults. `SLM_EVAL_TEST_CMD` can override the CLI command.
- **Theme:** `rescore` (§3, §6.1, §6.2, §6.4) and the §8/§9 rules it exercises. Stored verdicts are ones every correct implementation must reproduce: identical integers are correct, different integers are incorrect, and `None` is incorrect.

| ID | File | Contract clause | Expected behaviour |
|---|---|---|---|
| VS-RS-01 | `test_vs_rescore.py` | §3 rescore; §6.4 schema | k=3, 3 items, empty cache → exit 0; `<run_dir>/rescore.json` has `rescore_version` "1", the `run_id`, `all_match: true`, and 9 records with stored, recomputed and `match: true` |
| VS-RS-02 | `test_vs_rescore.py` | §3 written path printed to stdout | stdout includes the path of the `rescore.json` written |
| VS-RS-03 | `test_vs_rescore.py` | §6.2 immutability; rescore writes beside the original | every original file is byte-identical; `rescore.json` is the only new file |
| VS-RS-04 | `test_vs_rescore.py` | §3 rescore; §6.4 | stored secondary true on a wrong answer → that record has `stored.secondary` true, `recomputed.secondary` false and `match` false; the others match; `all_match: false` |
| VS-RS-05 | `test_vs_rescore.py` | §8 an unclosed `<think>` gives empty `final`; §9 | a boxed answer inside a never-closed `<think>` is not extracted → scored incorrect; `all_match: true` against those stored verdicts |
| VS-RS-06 | `test_vs_rescore.py` | §8 no `<think>` means the whole output is `final`; last boxed wins | outputs without a think block are scored by their last `\boxed{}`; `all_match: true` |
| VS-RS-07 | `test_vs_rescore.py` | §8/§9 deterministic; §6.4 | rescoring twice gives the same `all_match` and records |
| VS-RS-08 | `test_vs_rescore.py` | §6.1 record hash check; exit 2 | a wrong `raw_output_sha256` field (text unchanged) → exit 2 |
| VS-RS-09a | `test_vs_rescore.py` | §6.1 rescore reads only the copies; a copy-hash failure exits 2 | altered `protocol.yaml` copy → exit 2 |
| VS-RS-09b | `test_vs_rescore.py` | §6.1 rescore reads only the copies; a copy-hash failure exits 2 | altered `manifest.json` copy → exit 2 |

## Notes for ruling

- **VS-RS-03.** This reads "writes `rescore.json`" (§3, §6.2) as meaning no other file is added to the run directory.
- **VS-RS-04.** The exit code is not asserted when verdicts don't match, because v0.3 doesn't define one.
