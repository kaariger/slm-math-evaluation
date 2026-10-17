# Visible suite — batch-03 registration (brief §3)

- **Suite:** visible, for every entry below.
- **Status:** PROPOSED, for every entry below. Only the maintainer sets `REQUIRED`, `ADVISORY` or `INVALID`.
- **Contract:** v0.3 (`MATH500-INTERFACE-CONTRACT-v0.3.md`, SHA-256 `f3f4e397ae110c2d4e86abc4169ef89432cc71fdf91b233c33a4b4c013ba7021`).
- **Implementation commit:** none supplied. These are contract-only tests.
- **Implementation code seen before writing:** **No**, for every entry below.
- **File:** `test_vs_report.py`, SHA-256 `a44d2ad8ea1e2293eaf817143bea8673d8ec163ff7e3e39365a68b59dc6b09a5`. This was copied from `shasum -a 256` output. `SHA256.txt` lists every file in the batch.
- **Resources:** hermetic, needing only the `slm-eval` CLI. Each test writes complete §6.1 run directories, with protocol and manifest copies, into its own temporary `SLM_EVAL_RUNS`.
- **Fixture protocol:** the committed `protocol.yaml` is used when it can be found and parsed (`SLM_EVAL_TEST_PROTOCOL`, or a search of the repository). Otherwise the §4 draft defaults are used. `SLM_EVAL_TEST_CMD` can override the CLI command.
- **Theme:** `report` (§3, §6.1, §7). v0.3 doesn't fix the report's JSON field names, so the tests look for values, and for values under keyword-matched keys.

| ID | File | Contract clause | Expected behaviour |
|---|---|---|---|
| VS-RP-01 | `test_vs_report.py` | §3 report, default `--out-dir`; written paths printed | `report.json` and `report.md` are written to the run directory and both paths are printed on stdout |
| VS-RP-02 | `test_vs_report.py` | §3 report `--out-dir <dir>` | both files are in the named directory (not the run directory) and both paths are printed |
| VS-RP-03 | `test_vs_report.py` | §7 per-scorer accuracy and Wilson 95% (k=1); §3 fractions | 25 items, 20 / 18 correct → 0.8 and 0.72 with their Wilson intervals; every accuracy value is in [0, 1] |
| VS-RP-04 | `test_vs_report.py` | §7 counts and definitions | format failures 3 (`no_answer`, not truncated), truncations 4, fallback extractions 2, thinking rate 0.4, disagreements 5 |
| VS-RP-05a | `test_vs_report.py` | §3 `--compare-published` outside [0, 1] → exit 3 | V = 1.2 → exit 3; no report written |
| VS-RP-05b | `test_vs_report.py` | §3 `--compare-published` outside [0, 1] → exit 3 | V = −0.1 → exit 3; no report written |
| VS-RP-06a | `test_vs_report.py` | §7 rule 1 | primary 0.6 (Wilson [0.407, 0.766]), V = 0.7 → CONSISTENT |
| VS-RP-06b | `test_vs_report.py` | §7 rule 2, grader range | secondary 0.88, V = 0.85 → PROTOCOL-SENSITIVE |
| VS-RP-06c | `test_vs_report.py` | §7 rule 3 | V = 0.95 → UNEXPLAINED |
| VS-RP-06d | `test_vs_report.py` | §7 rule 2, sensitivity `[min(0, lo), max(0, hi)]`; factors named | V = 0.3 (d = −0.3) with a `sampling.top_p` interval [−0.35, −0.25] → PROTOCOL-SENSITIVE, naming `sampling.top_p` |
| VS-RP-07 | `test_vs_report.py` | §7 k>1 aggregates; per-seed results | 10 items, k=3, seed accuracies 0.5 / 0.6 / 0.7 → all three, mean 0.6, an item-bootstrap interval containing 0.6, and a spread entry |
| VS-RP-08 | `test_vs_report.py` | §2 data policy; §7 aggregates only | marker strings in raw outputs, answers and references appear in neither report file |
| VS-RP-09 | `test_vs_report.py` | §6.1 report reads only the copies; a copy-hash failure exits 2 | altered `protocol.yaml` copy → exit 2 |
| VS-RP-10 | `test_vs_report.py` | §6.1 record hash check; exit 2 | a wrong `reference_sha256` in one record → exit 2 |

## Notes for ruling

- **VS-RP-04.** This includes a truncated output whose status is `no_answer`. It must count as a truncation, not a format failure, per the §7 definitions. It also includes a truncated output with an `ok` answer, which still counts as a truncation.
- **VS-RP-05.** This also asserts that no report is written when the value is rejected.
- **VS-RP-06.** Each case asserts that `report.json` carries exactly one of the three labels, and that `report.md` states it.
