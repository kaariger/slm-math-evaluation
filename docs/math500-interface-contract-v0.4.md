# MATH-500 vertical path — interface contract v0.4

**About this contract**

- **Status:** APPROVED v0.4. Maintainer ruling on 2026-10-06. It supersedes v0.3 and changes only §10, the `verify-runtime` checks, and one §5 clarification; see §11.
- **Visibility:** public-safe by design. It contains no private identifiers, private paths, or process internals, and may be given to any implementer or test author.
- **Purpose:** the single shared surface that both the implementation and the independent test suite build against.
- **Out of contract:**
  - Internal module structure.
  - Anything not listed here.
- **Prohibited:** none of the following may be introduced:
  - model registries or generic model-adapter hierarchies;
  - dataset or runtime plug-in discovery;
  - generalized factories;
  - an experiment or matrix engine;
  - reference-harness integration.

## 1. Scope

The contract covers exactly four things:

- the command-line interface (§3);
- the formats of the configuration and output artifacts (§4–§7);
- the extraction boundary (§8);
- the scoring boundary (§9).

The project is Python, managed with Poetry. The model is Qwen3-8B (GGUF). The runtime is a pinned llama.cpp build.

## 2. Storage locations and data policy

| Location | Default | Override | Contents |
|---|---|---|---|
| Dataset cache | `~/.cache/slm-eval/data` | `SLM_EVAL_CACHE` | Upstream dataset files (pinned). Model files live under `models/` in this cache. |
| Run store | `~/.cache/slm-eval/runs` | `SLM_EVAL_RUNS` | Run directories (§6), including raw generations. Verification records live under `_verification/<protocol_sha256>/`. |
| Repository | — | — | Code, tests, the split manifest (§5), protocol files (§4), and aggregate reports only |

**Data policy:**

- Dataset text, prompts containing problem text, raw generations, and reference answers are **never written inside the repository**.
- Only identifiers, hashes, and aggregates are committed.
- The run store lives outside the repository, because raw generations restate problem text.

## 3. Command-line interface

**Entry point:** `slm-eval` (a Poetry script).

**General behaviour, for every command:**

- writes machine-readable output to the paths named below, and prints each written path to stdout;
- writes logs to stderr;
- never prints secrets;
- **usage errors exit with `3`, never `2`.** A usage error is an unknown command or option, or a missing or malformed argument. Implementations must override any framework default that uses `2`;
- **all accuracies and rates are fractions in [0, 1]**, never percentages.

| Command | Effect |
|---|---|
| `slm-eval data fetch` | Fetches the pinned upstream files into the cache and verifies their SHA-256 against §4 `dataset.source`. A mismatch exits `2`. |
| `slm-eval data build-manifest --out <manifest.json> [--flagged-out <f.json>]` | Applies normalization and the disjointness checks, draws seeded dev tiers, and writes the manifest. **Deterministic:** the same pinned inputs and seed always produce a byte-identical manifest. If a cached pinned file doesn't match its pinned SHA-256, it exits `2` and writes nothing. Flagged near-duplicate pairs go to `--flagged-out`. The default location is the dataset cache, outside the repository. The flagged file contains only IDs and similarity scores (§5.1). |
| `slm-eval data show-pair --flagged <f.json> --pair <n>` | Prints the two problem texts of flagged pair `n` from the cache to stdout, for maintainer review. Writes nothing. |
| `slm-eval data apply-verdicts --manifest <m> --verdicts <v.json>` | Applies maintainer keep/exclude verdicts (§5.1) and deterministically rebuilds the exclusions and tiers. Fails without changing anything in either case:<br>• `flagged_sha256` doesn't match the flagged file: exits `2`;<br>• the result would change a tier that any existing run has already used: exits `3`. |
| `slm-eval verify-runtime --protocol <p>` | Starts the runtime per §10. Writes `$SLM_EVAL_RUNS/_verification/<protocol_sha256>/runtime-verification.json`, checking:<br>• key enforcement on every non-exempt route;<br>• that the open-route set equals the approved exemption (§10);<br>• no file path in the model listing;<br>• loopback-only bind;<br>• ephemeral port;<br>• cleanup;<br>• raw-capture fidelity;<br>• parameter equivalence.<br>Any unexpected unauthenticated route exits `4`. |
| `slm-eval verify-artifact --protocol <p>` | Verifies the model artifact's SHA-256 (exit `2` on mismatch) and its runtime compatibility. Writes `$SLM_EVAL_RUNS/_verification/<protocol_sha256>/artifact-verification.json`. |
| `slm-eval run --protocol <p> --manifest <m> --tier {smoke,validation,test} [--k N] [--seed-base S]` | Starts a new run (§6) and prints the run ID. Exits `3` if either verification record for this protocol is missing. Exits `2` if the manifest `source` differs from the protocol's `dataset.source`. |
| `slm-eval run --resume <run_id>` | Resumes an interrupted run (§6.3). |
| `slm-eval rescore --run <run_id>` | Re-runs extraction and scoring from the stored raw outputs only. Writes `rescore.json` (§6.4) into the run directory, recording for every item × sample whether the new verdicts equal the stored ones, plus a summary flag `all_match`. |
| `slm-eval sensitivity --base <run_id> --variant <run_id> [--variant <run_id> ...] --out <sensitivity.json>` | Computes paired effects of single-factor protocol variants against a base run (§7.1). Deterministic. |
| `slm-eval report --run <run_id> [--compare-published <value>] [--sensitivity <sensitivity.json>] [--out-dir <dir>]` | Writes `report.json` and `report.md` (§7) to `--out-dir`, defaulting to the run directory. `<value>` is a fraction in [0, 1]. A value outside that range exits `3`. |

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Other error |
| `2` | Integrity mismatch: a hash, identity, or manifest check failed. **Stop**; partial outputs are marked invalid. Used **only** for integrity failures. |
| `3` | A precondition is unmet (for example a missing file, tool, or run), or a usage error. |
| `4` | Runtime security verification failed. **Stop.** |

## 4. Protocol file (`protocol.yaml`)

The protocol file is versioned and lives in the repository.

- **Values:** the values shown below are the v1 draft defaults. The **authoritative values are those in the maintainer-approved protocol file.** Tests must read values from the protocol file under test, not from this contract.
- **Provenance:** a `provenance` label is required on `model.artifact`, `runtime`, `prompt`, `reasoning_mode`, and every `sampling` field. It isn't required on `extraction`, `scorers`, or `dataset`, which are identified by their id, pin, and hashes.

Each `provenance` label is one of:

- `source-known-paper`
- `source-known-blog`
- `vendor-filled`
- `inferred`
- `local-choice`

```yaml
protocol_version: "v1-draft"        # immutable once frozen; any change = new version
model:
  artifact: {file: <file name under $SLM_EVAL_CACHE/models/>, sha256: <hex>, quantization: Q4_K_M, provenance: local-choice}
runtime:
  build: b10412
  invocation: keyed-run-scoped-server
  provenance: local-choice
prompt:
  template: "{problem}\n\nPlease reason step by step, and put your final answer within \\boxed{}."
  system_prompt: null
  few_shot: 0
  provenance: vendor-filled
reasoning_mode: {value: hybrid, provenance: inferred}   # also: forced_think, forced_non_think
sampling:
  temperature: {value: 0.6,  provenance: source-known-paper}
  top_p:       {value: 0.95, provenance: source-known-paper}
  top_k:       {value: 20,   provenance: vendor-filled}
  min_p:       {value: 0,    provenance: vendor-filled}
  presence_penalty: {value: 0, provenance: local-choice}
  max_output_tokens: {value: <integer, declared before smoke>, provenance: source-known-paper}
  seed_policy: {value: "seed = seed_base + sample_index", provenance: local-choice}
  # every other runtime parameter is declared explicitly here
extraction: {id: <extractor-id>, version: <semver>}
scorers:
  primary:   {id: prm800k-grader, pin: <commit>}
  secondary: {id: math-verify,    pin: <version>}
dataset:
  source: {url: <upstream>, commit: <hex>, files: [{path: <p>, sha256: <hex>}]}
```

## 5. Split manifest (`manifest.json`) — committed, contains no dataset text

```json
{
  "manifest_version": "1",
  "source": {"url": "...", "commit": "...", "files": [{"path": "...", "sha256": "..."}]},
  "membership_derivation": {"method": "unique_id | content-hash", "independent_source": {"url": "...", "revision": "...", "sha256": "..."}},
  "normalization": {"version": "...", "near_duplicate": {"method": "...", "threshold": 0.0}},
  "items": [{"id": "...", "role": "test | dev", "content_sha256": "...", "subject": "...", "level": 0}],
  "exclusions": [{"id": "...", "matched_id": "...", "match": "exact | near", "similarity": 0.0, "verdict": "exclude | keep", "verdict_source": "maintainer"}],
  "tiers": {"seed": 0, "smoke": ["..."], "validation": ["..."]},
  "provenance_note": "upstream licensing/provenance uncertainty statement"
}
```

### 5.1 Flagged pairs and verdicts (stored outside the repository)

The flagged file:

```json
{"flagged_version": "1", "manifest_inputs_sha256": "...",
 "pairs": [{"pair": 1, "id": "...", "matched_id": "...", "match": "exact | near", "similarity": 0.0}]}
```

The verdicts file:

```json
{"verdicts_version": "1", "flagged_sha256": "...",
 "verdicts": [{"pair": 1, "verdict": "exclude | keep"}]}
```

Neither file contains problem text.

**Invariants:**

- `membership_derivation.independent_source` depends on `method`:
  - it is `null` when `method` is `unique_id`, because no independent source is used;
  - it is an object `{url, revision, sha256}` when `method` is `content-hash`.
- Test items are never excluded or altered.
- Exclusions apply to dev items only.
- Tiers are drawn after exclusions.
- A tier is immutable once it is used.

## 6. Run store

### 6.1 Run directory: `$SLM_EVAL_RUNS/<run_id>/`

- **`protocol.yaml`** and **`manifest.json`**: exact byte copies made at run start. Their SHA-256 must equal `protocol_sha256` and `manifest_sha256` in `run.json`. `resume`, `rescore`, `sensitivity`, and `report` read only these copies, and exit `2` if a copy's hash fails.

- **`run.json`**, containing:
  - `run_id`
  - `protocol_version`, `protocol_sha256`
  - `manifest_sha256`, `tier`, `k`, `seeds`
  - `model_artifact_sha256`, `runtime_build`
  - `adapter: {id, version}`, `extractor: {id, version}`, `scorers: [{id, pin}]`
  - `executor` — a free-text actor label
  - `host: {os, arch, memory_gb}`
  - `attempts: [{n, started, ended, status: completed|aborted|interrupted, resumed_from}]`
  - `status`
- **`items.jsonl`** — one line per item × sample, containing:
  - `item_id`, `sample_index`, `seed`
  - `prompt_sha256`
  - `raw_output`, `raw_output_sha256`
  - `thinking_present`, `truncated`, `finish_reason`, `tokens_generated`
  - `reference_answer`, `reference_sha256` — taken from the pinned upstream `answer` field. Kept only in the run store, never in the repository.
  - `extraction: {answer, rule, status}`
  - `verdicts: {primary: bool, secondary: bool}`
  - `timing_ms`
- **`runtime-verification.json`** and **`artifact-verification.json`**, copied in at run start.

Every command that reads `items.jsonl` checks each record's `raw_output_sha256` and `reference_sha256`, and exits `2` on any mismatch.

### 6.2 Immutability

- A completed run is never rewritten.
- `rescore` writes `rescore.json` beside the original and leaves the original untouched.

### 6.3 Resume

- Resume is allowed only when the protocol, manifest, model, and runtime build are all identical to the original run. Any difference exits `2` and generates nothing.
- Only missing item × sample pairs are generated. Completed pairs are never regenerated.
- Each resume appends an attempt record.

### 6.4 `rescore.json`

```json
{"rescore_version": "1", "run_id": "...", "all_match": true,
 "records": [{"item_id": "...", "sample_index": 0,
              "stored":     {"primary": true, "secondary": true},
              "recomputed": {"primary": true, "secondary": true},
              "match": true}]}
```

## 7. Report (`report.json` + rendered `report.md`) — committed, aggregates only

**Contents:**

- per scorer: accuracy, with a Wilson 95% interval for k=1;
- for k>1: per-item seed mean, an item-bootstrap interval, and the spread across seeds;
- per-seed results;
- counts: format failures, truncations, fallback extractions, thinking rate, and grader disagreements. Definitions:
  - **Truncation:** generation stopped at the output cap (`truncated: true`).
  - **Format failure:** extraction status `no_answer` on an output that was **not** truncated.
  - **Fallback extraction:** extraction status `fallback`.
  - **Thinking rate:** the fraction of outputs with `thinking_present: true`.
  - **Grader disagreement:** the primary and secondary verdicts differ;
- the protocol provenance table and the list of deviations.

With `--compare-published V`, the report adds a **comparison reading**. Rules are applied in this order, and the first match wins:

1. `CONSISTENT` — V lies inside the primary-scorer 95% interval.
2. `PROTOCOL-SENSITIVE` — either of these holds:
   - V lies inside the grader-sensitivity range: the closed interval between the primary-scorer and secondary-scorer accuracies of this run; or
   - the discrepancy `d = V − (primary-scorer accuracy)` lies inside `[min(0, lo), max(0, hi)]` for some comparison in the `--sensitivity` file, where `[lo, hi]` is that comparison's effect interval.

   Every factor that satisfies a condition is named.
3. `UNEXPLAINED` — anything else.

The reading makes no causal claim.

### 7.1 Sensitivity file (`sensitivity.json`)

```json
{"sensitivity_version": "1",
 "base_run": "<run_id>", "base_protocol_sha256": "...",
 "comparisons": [{
   "variant_run": "<run_id>",
   "factor": "<the single differing factor, e.g. model.artifact | reasoning_mode | sampling.temperature>",
   "base_value": "...", "variant_value": "...",
   "paired_items": 0,
   "base_accuracy": 0.0, "variant_accuracy": 0.0,
   "effect": 0.0,
   "effect_interval_95": [0.0, 0.0],
   "method": "paired bootstrap over items, primary scorer",
   "bootstrap_seed": 0, "bootstrap_resamples": 0}]}
```

**Rules:**

- Base and variant runs must have the same `manifest_sha256`, tier, and item set.
- They must differ in **exactly one factor**, compared on the run-directory protocol copies.
  - `protocol_version` and all `provenance` labels are ignored.
  - `model.artifact` (file, sha256, quantization together) counts as **one** factor.
  - Every other differing leaf field is its own factor, named by its dotted path.
- These cases exit `3` and write no output:
  - zero factors differ, or more than one does;
  - the manifest differs;
  - the tier differs;
  - the item set differs.
- `effect = variant_accuracy − base_accuracy`, computed on paired items only.
- The bootstrap seed and the number of resamples are recorded, so the output is reproducible.

## 8. Extraction boundary — pure, deterministic

```text
split_reasoning(raw_output: str) -> (thinking: str | None, final: str)
    final = text after the last </think>; the whole output if there is no <think>;
            the empty string if a <think> is never closed
extract(final: str, truncated: bool) -> Extraction
    Extraction = {answer: str | None,
                  rule:   "boxed_last" | <fallback rule id> | "none",
                  status: "ok" | "fallback" | "no_answer" | "truncated"}
```

**Rules:**

- The last balanced `\boxed{...}` wins; nested braces are supported.
- An empty `final` gives status `truncated` if the output was truncated, otherwise `no_answer`.
- Fallback rules form a fixed, ordered, versioned list.
- No model calls are made.
- The same inputs always give the same output.

## 9. Scoring boundary — pure, deterministic, two implementations

```text
score(answer: str | None, reference: str) -> Verdict
    Verdict = {correct: bool, scorer_id: str, scorer_pin: str}
```

**Rules:**

- `answer = None` scores as incorrect.
- Both scorers implement this exact signature.
- Disagreements are counted, never adjudicated inside the tool.

## 10. Runtime security (fixed)

- **One runtime path:** a key-protected server, scoped to a single run, started and stopped by the harness.
- **Network exposure:** bound to `127.0.0.1` only, on an ephemeral port.
- **API key:**
  - generated per run, in memory only;
  - required on every route **except the approved exemption** below;
  - never written to disk, logs, artifacts, or stdout.
- **Approved exemption** (maintainer ruling; the pinned server build always leaves these open):
  - the liveness route `/health` and the model-listing route `/v1/models`;
  - any path alias the pinned build maps to those **same two handlers**.

  Nothing else may answer without the key.
- **Model alias:** the server is started with the neutral model alias `qwen3-8b`, so the model-listing route exposes no local file path.
- **Verification record:** `runtime-verification.json`:
  - lists **every route probed**, with the HTTP status with and without the key;
  - classifies each route as protected or exempt;
  - shows that exempt responses contain no file path, prompt, output, slot, property, or generation data.
- **Residual risk (accepted):** while a run is active, a local web page could detect that the server exists and read the model alias.
- **Raw output:** reasoning parsing is disabled, so raw output is captured as generated.
- **Cleanup:** the server is terminated on normal exit and on interrupt.

## 11. Change control

- This contract carries a version.
- A change is proposed and recorded, and takes effect only once the maintainer approves it.
- Tests written against an earlier version stay valid until a change explicitly retires them.

**Changes in v0.2 from v0.1:**

1. The full prohibited-constructs list (top of this contract).
2. `build-manifest` is declared deterministic. Flagged output moves out of the repository and contains no problem text. New `show-pair` command. Flagged and verdict file schemas added (§5.1).
3. `rescore` records whether the new verdicts match the stored ones.
4. New `sensitivity` command and its file schema (§7.1). `report` accepts `--sensitivity`. Deterministic `PROTOCOL-SENSITIVE` conditions.
5. Per-item `reference_answer` and `reference_sha256` added to the run store.

**Changes in v0.3 from v0.2** (clarifications only):

1. Usage errors exit `3`; `2` is reserved for integrity failures.
2. Accuracies and `--compare-published` are fractions in [0, 1].
3. Output locations are defined:
   - verification records under `_verification/<protocol_sha256>/`;
   - reports default to the run directory;
   - written paths are printed.
4. Model files live in `$SLM_EVAL_CACHE/models/`.
5. Each run directory holds byte copies of the protocol and the manifest. Later commands use only those copies.
6. Integrity exits are defined: the `fetch` and `build-manifest` pinned-file checks, run-store record hashes, and a manifest source that differs from the protocol.
7. The `rescore.json` schema (§6.4).
8. Definitions of format failure, truncation, fallback extraction, thinking rate, and grader disagreement.
9. Sensitivity factor rules, and precondition exits.
10. Provenance scope. The authoritative protocol values come from the approved protocol file.
11. Tier immutability: a change to a used tier exits `3`.
12. An unclosed `<think>` gives an empty `final`.

**Changes in v0.4 from v0.3** (maintainer ruling after the runtime-security stop):

1. §10: the API key is required on every route except an approved exemption: `/health`, `/v1/models`, and their path aliases to the same handlers.
2. §10: a neutral model alias, `qwen3-8b`, so the model listing exposes no file path.
3. §10: the verification record enumerates every route probed. The residual risk is stated.
4. §3: `verify-runtime` checks the open-route set against the exemption. An unexpected open route exits `4`.
5. §5: `independent_source` is `null` for `unique_id` derivation, and an object for `content-hash` derivation. This is a clarification raised by a visible test.
