# Visible suite — batch-04 registration (brief §3)

- **Suite:** visible, for every entry below.
- **Status:** PROPOSED, for every entry below. Only the maintainer sets `REQUIRED`, `ADVISORY` or `INVALID`.
- **Contract:** v0.3 (`MATH500-INTERFACE-CONTRACT-v0.3.md`, SHA-256 `f3f4e397ae110c2d4e86abc4169ef89432cc71fdf91b233c33a4b4c013ba7021`).
- **Implementation commit:** none supplied. These are contract-only tests.
- **Implementation code seen before writing:** **No**, for every entry below.
- **File:** `test_vs_sensitivity.py`, SHA-256 `593c4b00ffc17f28af933837267ac8e0005176134c9103b7703002c3f4496c4c`. This was copied from `shasum -a 256` output. `SHA256.txt` lists every file in the batch.
- **Resources:** hermetic, needing only the `slm-eval` CLI. Base and variant runs are complete §6.1 run directories, with protocol and manifest copies, in each test's temporary `SLM_EVAL_RUNS`. All runs in a test share the same manifest copy, tier (smoke) and 16 items, with k=1.
- **Base protocol:** the committed `protocol.yaml` is used when it can be found and parsed (`SLM_EVAL_TEST_PROTOCOL`, or a search of the repository). Otherwise the §4 draft defaults are used. Each variant is a new `protocol_version` derived from the base. `SLM_EVAL_TEST_CMD` can override the CLI command.
- **Theme:** `sensitivity` (§3, §6.1, §7.1), focused on the v0.3 factor rules.

| ID | File | Contract clause | Expected behaviour |
|---|---|---|---|
| VS-SE-01 | `test_vs_sensitivity.py` | §7.1 schema and rules | `sampling.top_k` variant (base 4/16, variant 8/16) → exit 0; version "1", `base_run`, `base_protocol_sha256` equal to the hash of the base `protocol.yaml` copy; one comparison with factor `sampling.top_k`, base and variant values, `paired_items` 16, accuracies 0.25 / 0.5, effect 0.25 inside its interval, the stated method, integer seed and resamples |
| VS-SE-02 | `test_vs_sensitivity.py` | §3 written path printed to stdout | the `--out` path is printed on stdout |
| VS-SE-03 | `test_vs_sensitivity.py` | §7.1 `protocol_version` and provenance labels ignored | `sampling.top_k` value and provenance changed → still one factor (`sampling.top_k`); exit 0 |
| VS-SE-04 | `test_vs_sensitivity.py` | §7.1 provenance ignored; zero factors → exit 3, no output | only `reasoning_mode.provenance` (and the version) changed → exit 3; no file written |
| VS-SE-05 | `test_vs_sensitivity.py` | §7.1 leaf field named by its dotted path | `prompt.template` changed → one comparison, factor `prompt.template`, accuracies 0.25 / 0.125 |
| VS-SE-06 | `test_vs_sensitivity.py` | §7.1 `model.artifact` is one factor | artifact file and sha256 changed, same quantization → one comparison, factor `model.artifact`, accuracies 0.25 / 0.375 |
| VS-SE-07 | `test_vs_sensitivity.py` | §7.1 more than one factor → exit 3, no output | `model.artifact` plus `sampling.top_k` changed → exit 3; no file written |
| VS-SE-08 | `test_vs_sensitivity.py` | §6.1 sensitivity reads only the copies; a copy-hash failure exits 2 | altered base `protocol.yaml` copy → exit 2 |
| VS-SE-09 | `test_vs_sensitivity.py` | §6.1 record hash check; exit 2 | a variant raw output edited after hashing → exit 2 |
| VS-SE-10 | `test_vs_sensitivity.py` | §3 deterministic; §7.1 reproducible | two invocations → identical JSON |

## Notes for ruling

- **Factor names for `{value, provenance}` fields.** §7.1 says "named by its dotted path", but its examples omit `.value`. VS-SE-01 and VS-SE-03 therefore accept both `sampling.top_k` and `sampling.top_k.value`. VS-SE-05 has no such ambiguity, because `prompt.template` is itself a leaf.
- **VS-SE-06.** The representation of `base_value` and `variant_value` for `model.artifact` is unspecified, so they are not asserted.
