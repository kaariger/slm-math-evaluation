# MATH-500 evaluation harness — interface contract v0.8

**About this contract**

- **Status:** APPROVED v0.8.1. Maintainer ruling on 2026-10-28. It supersedes v0.8 and only clarifies the subset draw, the reasoning split, conversion provenance, the attempt-log hash chain and sleep-gap verification; see §11.
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
- **Allowed:** one concrete adapter per model family, named explicitly by the protocol (§4) and selected by that name alone. This is not an adapter hierarchy.

## 1. Scope

The contract covers exactly six things:

- the command-line interface (§3);
- the formats of the configuration and output artifacts (§4–§7);
- the extraction boundary (§8);
- the scoring boundary (§9);
- the model converter (§12);
- the runner (§13).

The project is Python, managed with Poetry. Models are GGUF files, run by one pinned llama.cpp build. The evaluation targets are the models approved by public decision records: Qwen3-8B, Phi-4-reasoning, Phi-4-reasoning-plus and Gemma 4 12B. Ephemeral test fixtures (§4) may be used by tests and proofs, and are never reported.

## 2. Storage locations and data policy

| Location | Default | Override | Contents |
|---|---|---|---|
| Dataset cache | `~/.cache/slm-eval/data` | `SLM_EVAL_CACHE` | Upstream dataset files (pinned). Model files live under `models/` in this cache. |
| Run store | `~/.cache/slm-eval/runs` | `SLM_EVAL_RUNS` | Run directories (§6), including raw generations. Verification records live under `_verification/<protocol_sha256>/`. Runner state lives under `_runner/<plan_sha256>/` (§13.3). |
| Scratch | `~/.cache/slm-eval/scratch` | `SLM_EVAL_SCRATCH` | Converter downloads, the Hugging Face download cache, temporary files and intermediate conversion files (§12). Rebuildable; never authoritative. |
| Repository | — | — | Code, tests, the split manifest (§5), protocol files (§4), and aggregate reports only |

**Data policy:**

- Dataset text, prompts containing problem text, raw generations, and reference answers are **never written inside the repository**.
- Only identifiers, hashes, and aggregates are committed.
- The run store lives outside the repository, because raw generations restate problem text.
- Commands that download or convert set the Hugging Face cache and temporary-file locations explicitly, inside the scratch location. They write nothing large anywhere else.
- Converter records, runner plans, authorizations and status files contain no dataset text.

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
| `slm-eval verify-runtime --protocol <p>` | Starts the runtime per §10. Writes `$SLM_EVAL_RUNS/_verification/<protocol_sha256>/runtime-verification.json`, checking:<br>• key enforcement on every non-exempt route;<br>• that the open-route set equals the approved exemption (§10);<br>• no file path in the model listing;<br>• loopback-only bind;<br>• ephemeral port;<br>• cleanup;<br>• raw-capture fidelity;<br>• parameter equivalence.<br>Any unexpected unauthenticated route exits `4`. An empty-body `OPTIONS` preflight response is not "unexpected" (§10). |
| `slm-eval verify-artifact --protocol <p>` | Verifies the model artifact's SHA-256 (exit `2` on mismatch) and its runtime compatibility. Writes `$SLM_EVAL_RUNS/_verification/<protocol_sha256>/artifact-verification.json`. |
| `slm-eval run --protocol <p> --manifest <m> --tier {smoke,validation,test} [--k N] [--seed-base S] [--subset <subset.json>]` | Starts a new run (§6) and prints the run ID. Exits `3` if either verification record for this protocol is missing. Exits `2` if the manifest `source` differs from the protocol's `dataset.source`, or if the subset's `manifest_sha256` or `tier` doesn't match. With `--subset`, only the subset's items are generated (§5.2). |
| `slm-eval run --resume <run_id>` | Resumes an interrupted run (§6.3). |
| `slm-eval rescore --run <run_id>` | Re-runs extraction and scoring from the stored raw outputs only. Writes `rescore.json` (§6.4) into the run directory, recording for every item × sample whether the new verdicts equal the stored ones, plus a summary flag `all_match`. |
| `slm-eval sensitivity --base <run_id> --variant <run_id> [--variant <run_id> ...] --out <sensitivity.json>` | Computes paired effects of single-factor protocol variants against a base run (§7.1). Deterministic. |
| `slm-eval report --run <run_id> [--compare-published <value>] [--sensitivity <sensitivity.json>] [--out-dir <dir>]` | Writes `report.json` and `report.md` (§7) to `--out-dir`, defaulting to the run directory. `<value>` is a fraction in [0, 1]. A value outside that range exits `3`. |
| `slm-eval data subset --manifest <m> --tier validation --size <N> --stratify subject --seed <S> --out <subset.json>` | Draws a subset of a frozen tier (§5.2). **Deterministic:** the same manifest, size and seed always produce a byte-identical file. A size larger than the tier exits `3`. |
| `slm-eval model convert --spec <spec.yaml> [--clean-scratch]` | Downloads, verifies, converts and quantizes one model (§12), and writes its conversion record. Re-running it resumes from the last verified step. Scratch files are kept unless `--clean-scratch` is given. |
| `slm-eval model sanity --spec <spec.yaml>` | Runs the spec's sanity check (§12.4) on the converted file, and writes a sanity record. |
| `slm-eval runner start --plan <plan.yaml> --authorization <authorization.json>` | Validates the plan against the authorization (§13.2), stores copies of both in the runner state (§13.3), and starts the runner in the background. Prints the plan's SHA-256. |
| `slm-eval runner pause [--now]` | Pauses the active plan (§13.4). Without `--now`, the pause takes effect after the item in progress completes. |
| `slm-eval runner resume` | Resumes a paused, interrupted or approval-waiting plan from its last checkpoint (§13.4). |
| `slm-eval runner status` | Prints the plan's state, progress, recent events and the attempt-log head hash, from the status file (§13.3). Writes nothing. |

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Other error |
| `2` | Integrity mismatch: a hash, identity, or manifest check failed. **Stop**; partial outputs are marked invalid. Used **only** for integrity failures. |
| `3` | A precondition is unmet (for example a missing file, tool, or run), or a usage error. |
| `4` | Runtime security verification failed. **Stop.** |

Under §12 and §13, a disk-floor breach exits `3`, and a plan, authorization, source or artifact hash mismatch exits `2`. A pause is a state, not an exit code.

## 4. Protocol file (`protocol.yaml`)

The protocol file is versioned and lives in the repository.

- **Values:** the values shown below are the v1 draft defaults. The **authoritative values are those in the maintainer-approved protocol file.** Tests must read values from the protocol file under test, not from this contract.
- **Provenance:** a `provenance` label is required on `model.artifact`, `runtime`, `prompt`, `reasoning_mode`, every `sampling` field, and every `resources` field. It isn't required on `role`, `model.adapter`, `model.chat_template`, `extraction`, `scorers`, or `dataset`, which are identified by their id, version, pin, or hashes.

Each `provenance` label is one of:

- `source-known-paper`
- `source-known-blog`
- `vendor-filled`
- `vendor-template` (behavior fixed by the model's own chat template)
- `inferred`
- `local-choice`

```yaml
protocol_version: "v1-draft"        # immutable once frozen; any change = new version
role: target                        # target | fixture; fixture runs are never reported (§7)
model:
  adapter: {id: qwen3, version: <semver>}         # qwen3 | phi4-reasoning | gemma4
  artifact: {file: <file name under $SLM_EVAL_CACHE/models/>, sha256: <hex>, quantization: Q4_K_M,
             conversion_record_sha256: <hex or null>, provenance: local-choice}
  chat_template: {source: embedded, sha256: <hex>}
runtime:
  build: b10412
  invocation: keyed-run-scoped-server
  provenance: local-choice
prompt:
  template: "{problem}\n\nPlease reason step by step, and put your final answer within \\boxed{}."
  system_prompt: null                # a system message the chat template inserts is captured by prompt_sha256 (§6.1)
  few_shot: 0
  provenance: vendor-filled
reasoning_mode: {value: hybrid, provenance: inferred}   # allowed values depend on the adapter (below)
sampling:
  temperature: {value: 0.6,  provenance: source-known-paper}
  top_p:       {value: 0.95, provenance: source-known-paper}
  top_k:       {value: 20,   provenance: vendor-filled}   # 0 disables top-k
  min_p:       {value: 0,    provenance: vendor-filled}   # 0 disables min-p
  presence_penalty: {value: 0, provenance: local-choice}
  max_output_tokens: {value: <integer, declared before smoke>, provenance: source-known-paper}
  context_length:    {value: <integer>, provenance: local-choice}
  stop:              {value: [], provenance: local-choice}
  seed_policy: {value: "seed = seed_base + sample_index", provenance: local-choice}
  # every other runtime parameter is declared explicitly here
resources:
  max_memory_gb:       {value: <number>, provenance: local-choice}
  min_free_memory_gb:  {value: <number>, provenance: local-choice}   # the runner waits below this (§13.5)
  stuck_after_awake_s: {value: <integer>, provenance: local-choice}  # no new tokens for this long while awake (§13.5)
extraction: {id: <extractor-id>, version: <semver>}
scorers:
  primary:   {id: prm800k-grader, pin: <commit>}
  secondary: {id: math-verify,    pin: <version>}
dataset:
  source: {url: <upstream>, commit: <hex>, files: [{path: <p>, sha256: <hex>}]}
```

**Reasoning modes by adapter:**

| Adapter | Allowed `reasoning_mode` values | Control |
|---|---|---|
| `qwen3` | `hybrid`, `forced_think`, `forced_non_think` | The runtime's reasoning flag, as in v0.7 |
| `phi4-reasoning` | `always_on` | None: the model always thinks |
| `gemma4` | `thinking_on`, `thinking_off` | A chat-template argument |

**Rules:**

- A `reasoning_mode` value the adapter doesn't allow makes `verify-artifact` and `run` exit `3`.
- The adapter turns `reasoning_mode` into runtime flags and template arguments. The values actually sent are recorded in `run.json` (§6.1).
- `model.chat_template.sha256` is the hash of the chat template embedded in the model file. `verify-artifact` exits `2` if it differs.
- `model.artifact.conversion_record_sha256` is a 64-hex SHA-256 for every artifact produced by `model convert` (§12.5). It is `null` only for a vendor-supplied artifact that has no conversion record.
- `resources` replaces any fixed memory or throughput gate. There is no throughput minimum: slow progress is never a failure (§13.5).
- `role: fixture` marks an ephemeral test fixture: a small model used by tests and proofs. Fixture runs can't be reported (§7).
- **Compatibility:** a protocol file written under v0.7 stays valid. Its missing fields take v0.7 behavior: `role: target`, adapter `qwen3`, no subset, and v0.7's resource behavior. Re-scoring and reporting of runs made under v0.7 are unchanged.

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

### 5.2 Subset file (`subset.json`) — committed, IDs only

```json
{"subset_version": "1", "manifest_sha256": "...", "tier": "validation", "size": 0,
 "stratify": "subject",
 "method": "largest-remainder allocation by subject, then a seeded draw within each subject",
 "seed": 0, "ids": ["..."]}
```

**Rules:**

- `ids` are a subset of the named tier, listed in the order of IDs in the manifest's `tiers.validation` list.
- **Allocation:** each subject's share of `size` is proportional to its count in the tier, rounded by largest remainder. Ties go to subjects in name order.
- **Draw:** for each subject, create a fresh `random.Random(seed)` and call `.sample` over that subject's IDs in `tiers.validation` order.
- A subset is immutable once it is used, and contains no problem text.

## 6. Run store

### 6.1 Run directory: `$SLM_EVAL_RUNS/<run_id>/`

- **`protocol.yaml`** and **`manifest.json`**: exact byte copies made at run start. Their SHA-256 must equal `protocol_sha256` and `manifest_sha256` in `run.json`. `resume`, `rescore`, `sensitivity`, and `report` read only these copies, and exit `2` if a copy's hash fails.

- **`run.json`**, containing:
  - `run_id`
  - `protocol_version`, `protocol_sha256`
  - `manifest_sha256`, `tier`, `k`, `seeds`
  - `model_artifact_sha256`, `runtime_build`
  - `adapter: {id, version}`, `extractor: {id, version}`, `scorers: [{id, pin}]`
  - `role`, `subset_sha256` (or `null`), `chat_template_sha256`
  - `effective: {reasoning, template_args, sampling}` — the values actually sent to the server
  - `executor` — a free-text actor label
  - `host: {os, arch, memory_gb}`
  - `attempts: [{n, started, ended, status: completed|aborted|interrupted|paused, resumed_from}]`
  - `events: [{type, at_utc, awake_s, detail}]`. `type` is one of `sleep`, `wake`, `interrupted`, `pause`, `resume`, `server_restart`, `memory_wait` or `infra_retry`.
  - `status`: `in_progress`, `paused`, `interrupted`, `completed`, `aborted` or `stopped`
- **`items.jsonl`** — one line per item × sample, containing:
  - `item_id`, `sample_index`, `seed`
  - `prompt_sha256` — the SHA-256 of the exact prompt text the server rendered from the chat template, including any system message the template inserts
  - `raw_output`, `raw_output_sha256`
  - `thinking_present`, `thinking_state`, `truncated`, `finish_reason`, `tokens_generated`
  - `infra_retries` — how many retries the item needed after infrastructure failures (§13.5)
  - `reference_answer`, `reference_sha256` — taken from the pinned upstream `answer` field. Kept only in the run store, never in the repository.
  - `extraction: {answer, rule, status}`
  - `verdicts: {primary: bool, secondary: bool}`
  - `timing_ms`, `awake_ms`
- **`runtime-verification.json`** and **`artifact-verification.json`**, copied in at run start.

Every command that reads `items.jsonl` checks each record's `raw_output_sha256` and `reference_sha256`, and exits `2` on any mismatch.

A partial generation is never written to `items.jsonl`. It is recorded as an `interrupted` event.

### 6.2 Immutability

- A completed run is never rewritten.
- `rescore` writes `rescore.json` beside the original and leaves the original untouched.

### 6.3 Resume

- Resume is allowed only when the protocol, manifest, model, and runtime build are all identical to the original run. Any difference exits `2` and generates nothing.
- Only missing item × sample pairs are generated. Completed pairs are never regenerated.
- Each resume appends an attempt record.
- After a pause, an interruption or a crash, the run continues from the last completed item × sample. The item in progress is regenerated from scratch.
- Pauses and interruptions are events, not failures. They never count toward a retry limit (§13.5).

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
  - **Thinking rate:** the fraction of outputs with `thinking_present: true`, as defined in §8.
  - **Grader disagreement:** the primary and secondary verdicts differ.
  - **Missing final answer:** extraction status `no_answer` or `truncated`.
  - **Infrastructure failure:** an item that exhausted its retries (§13.5). Reported separately from wrong answers.
  - **Interruptions:** the number of `interrupted` events, with the distribution of `thinking_state`;
- the protocol provenance table and the list of deviations.

With `--compare-published V`, the report adds a **comparison reading**. Rules are applied in this order, and the first match wins:

1. `CONSISTENT` — V lies inside the primary-scorer 95% interval.
2. `PROTOCOL-SENSITIVE` — either of these holds:
   - V lies inside the grader-sensitivity range: the closed interval between the primary-scorer and secondary-scorer accuracies of this run; or
   - the discrepancy `d = V − (primary-scorer accuracy)` lies inside `[min(0, lo), max(0, hi)]` for some comparison in the `--sensitivity` file, where `[lo, hi]` is that comparison's effect interval.

   Every factor that satisfies a condition is named.
3. `UNEXPLAINED` — anything else.

The reading makes no causal claim.

**Published reference figures:** if `V × (items × samples)` is not a whole number, the report flags `V` as `not_whole_count`, because it can't be an accuracy over this item set. It is still shown as a reference.

A run whose protocol has `role: fixture` can't be reported: `report` exits `3`.

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
split_reasoning(raw_output: str, adapter: AdapterId) -> (thinking: str | None, final: str, thinking_state: ThinkingState)
    ThinkingState = "absent" | "empty" | "present" | "unterminated"
    qwen3, phi4-reasoning:
        final = the whole output if there is no opening <think>;
                otherwise the empty string if the last <think> comes after the last </think>;
                otherwise the text after the last </think>
    gemma4:
        the same rules, applied to the thought-channel delimiters that the pinned
        chat template emits; the adapter version fixes the exact delimiters
extract(final: str, truncated: bool) -> Extraction
    Extraction = {answer: str | None,
                  rule:   "boxed_last" | <fallback rule id> | "none",
                  status: "ok" | "fallback" | "no_answer" | "truncated"}
```

**Rules:**

- The last balanced `\boxed{...}` wins; nested braces are supported.
- An empty `final` gives status `truncated` if the output was truncated, otherwise `no_answer`.
- With no opening delimiter, thinking is `absent` and `final` is the whole raw output, even if a stray closing delimiter appears. An adapter whose pinned chat template ends the rendered prompt with an opening delimiter instead treats the raw output as starting inside that block. This never applies to qwen3, whose v0.7 behaviour is unchanged. For phi4-reasoning and gemma4, the adapter version fixes whether it applies, from the pinned chat template.
- An opening delimiter after the last closing delimiter leaves an unterminated block and an empty `final`, even when an earlier block was closed.
- **`thinking_state`:**
  - `absent` — no thinking block or channel; `thinking` is `None`.
  - `empty` — a closed block or channel containing only whitespace.
  - `present` — a closed block or channel with at least one non-whitespace character.
  - `unterminated` — a block or channel that opens and never closes; `final` is empty.
- **`thinking_present`** keeps its v0.7 meaning: `true` if and only if `thinking` contains at least one non-whitespace character.
  - An empty or whitespace-only block is **not** thinking, so `thinking_present` is `false`.
  - It is also `false` when `thinking` is `None`.
  - `thinking_present` and `thinking_state` affect reporting only; extraction and scoring are unchanged.
- Each adapter's splitting is pure and deterministic, and is tested with golden fixtures for every `thinking_state`, including an absent final answer.
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

  - **CORS preflight:** an `OPTIONS` request on any route may be answered without the key, provided the response body is **empty**. The same route's data-bearing methods must still require the key.

  - **Fixed not-found responses:** an unauthenticated request that receives a fixed, data-free not-found response (for example a static `404` or "File Not Found" body that is identical for every such request) is **not** answering. The verification must probe `/`, `/index.html` and every web-UI path the pinned build serves, and record each status and body.

  Nothing else may answer without the key.
- **Model alias:** the server is started with a neutral alias per model (`qwen3-8b`, `phi-4-reasoning`, `phi-4-reasoning-plus`, `gemma-4-12b`; fixtures use `fixture-<n>`), so the model-listing route exposes no local file path.
- **Verification record:** `runtime-verification.json`:
  - lists **every route probed**, with the HTTP status with and without the key;
  - for each `OPTIONS` probe, records its status and body length, and the result of the same route's data-bearing method without the key;
  - classifies each route as protected or exempt;
  - shows that exempt responses contain no file path, prompt, output, slot, property, or generation data.
- **Residual risk (accepted):** while a run is active, a local web page could detect that the server exists and read the model alias.
- **Raw output:** reasoning parsing is disabled, so raw output is captured as generated.
- **Template arguments and progress:** chat-template arguments travel only in keyed requests. Progress polling (§13.5) reads only keyed routes, and logs no key, prompt or output.
- **Per artifact:** `verify-runtime` and `verify-artifact` run for every protocol, so every new model file is verified before it is used.
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

**Changes in v0.5 from v0.4** (maintainer ruling after the second runtime-security stop):

1. §10: an empty-body CORS preflight (`OPTIONS`) response without the key is permitted on any route. Data-bearing methods still require the key.
2. §10 and §3: the verification record covers every `OPTIONS` probe, and an empty-body preflight does not trigger exit `4`.

**Changes in v0.6 from v0.5** (maintainer ruling after the P-012 review):

1. §10: a fixed, data-free not-found response to an unauthenticated request is not "answering".
2. §10: the verification must probe `/`, `/index.html` and every web-UI path of the pinned build, and record their status and body.

**Changes in v0.7 from v0.6** (maintainer ruling after the C1 diagnostic):

1. §8: `thinking_present` is `true` only when the thinking text contains a non-whitespace character. In non-thinking mode, the model emits an empty `<think>` block, which had been counted as thinking.
2. §7: the thinking rate refers to that definition.

**Changes in v0.8 from v0.7** (approved by the maintainer on 2026-10-27):

1. Scope: four evaluation targets, ephemeral test fixtures, and two new surfaces, the converter (§12) and the runner (§13). One concrete adapter per model family is allowed.
2. §2: a scratch location (`SLM_EVAL_SCRATCH`) and runner state under the run store. Download caches and temporary files are set explicitly.
3. §3: new commands `data subset`, `model convert`, `model sanity` and `runner start|pause|resume|status`; `run --subset`. How the exit codes apply to §12 and §13.
4. §4: `role`, `model.adapter`, `model.artifact.conversion_record_sha256`, `model.chat_template`, reasoning modes per adapter, top-k and min-p `0` meaning disabled, `context_length`, `stop`, and `resources`. A new provenance label, `vendor-template`. Compatibility for v0.7 protocol files.
5. §5.2: the subset file.
6. §6: in `run.json`, `role`, `subset_sha256`, `chat_template_sha256`, `effective`, `events` and the new status values. In `items.jsonl`, a definition of `prompt_sha256`, plus `thinking_state`, `infra_retries` and `awake_ms`. Partial generations are never recorded. Resume after a pause, interruption or crash.
7. §7: counts of missing final answers, infrastructure failures and interruptions; the `not_whole_count` flag; fixture runs can't be reported.
8. §8: thinking is split per adapter; `thinking_state`; `thinking_present` is unchanged.
9. §10: an alias per model; template arguments and progress polling only in keyed requests; verification for every artifact.
10. Any fixed throughput minimum is removed. Memory gates come from `resources`.

**Clarifications in v0.8.1 from v0.8 (approved by the maintainer on 2026-10-28):**

1. §4: converted artifacts require a conversion-record hash; only vendor-supplied artifacts use `null`.
2. §5.2: "manifest order" means `tiers.validation` order; each subject draws with a fresh generator seeded identically.
3. §8: no opening delimiter means absent thinking and the whole output as final, even with a stray close, except when the pinned template ends with an opening delimiter; a reopened, unclosed block has an empty final.
4. §13.3: attempt-log hashes exclude line terminators; the first previous hash is 64 zeros, and the reported head uses the same byte rule.
5. §13.5: sleep-gap verification uses injected-clock tests and proof-run records; the contract defines no public test hook.

## 12. Model converter

### 12.1 Spec file (`model-spec.yaml`) — committed

```yaml
spec_version: "1"
name: <alias, e.g. gemma-4-12b>
role: target                          # target | fixture
source:
  repo: <owner/name, with the exact capitalization the host reports>
  commit: <40-hex>                    # pinned; never a branch name
  files: [{path: <p>, sha256: <hex>}] # the publisher's hashes, recorded when the spec is written
architecture: <the architecture id expected in the source config>
text_only: true                       # for a multimodal source, only the text model is converted
template: {inserts_system_message: <bool>, thinking_control: none | always | template_arg | runtime_flag}
conversion:
  tool: {release: b10412, script_sha256: <hex>, python_lock_sha256: <hex>}
  intermediate: bf16
  quantization: Q4_K_M
  calibration: none
expected_sizes_gb: {source: <n>, intermediate: <n>, output: <n>}
sanity:
  reference: {repo: <owner/name>, commit: <hex>, file: <name>, sha256: <hex>}   # or null
  perplexity_text_sha256: <hex>       # a fixed non-math text, kept outside the repository
  max_perplexity_ratio: <number>      # this file's perplexity divided by the reference's
```

### 12.2 Steps

1. **Preflight:** validate the spec, check the tool pins, and check free space (§12.3).
2. **Download** every `source.files` entry at `source.commit` into the scratch location. Compute each file's SHA-256 locally and compare it with the spec; a mismatch exits `2`. Partial downloads resume.
3. **Convert** to the intermediate with the pinned script. For a multimodal source, only the text model is converted.
4. **Quantize** to `conversion.quantization`, with no calibration data.
5. **Record:** hash the intermediate and the output, move the output into `$SLM_EVAL_CACHE/models/`, and write the conversion record (§12.5).
6. **Scratch:** kept by default. It is removed only with `--clean-scratch`, after the record is written.

**Atomic artifacts:** every file is written under a `.partial` name and renamed only after its hash is recorded. On a re-run, completed steps whose outputs verify are skipped. Anything incomplete is discarded and redone from verified inputs.

### 12.3 Disk floors

- Before each step, the converter projects the step's peak use on each volume from `expected_sizes_gb`. It refuses to start the step (exit `3`) unless free space after that peak stays above the floor:
  - on the model-cache volume, `SLM_EVAL_FLOOR_GB` (default `20`);
  - on the scratch volume, `SLM_EVAL_SCRATCH_FLOOR_GB` (default `10`).
- Free space is also checked periodically during long writes. A breach stops the step cleanly (exit `3`) and leaves only `.partial` files.
- A scratch volume that can't hold files larger than 4 GB exits `3`.

### 12.4 Sanity check

- **With a reference file:** the perplexities of the converted file and of the reference are measured on the fixed text with the pinned runtime. The check fails (exit `1`) if their ratio exceeds `max_perplexity_ratio`.
- **For every model:** the smoke tier is run before any other tier. This is part of the runner plan (§13.1), not of this command.
- The command writes `sanity-record.json` beside the conversion record: the two perplexities, their ratio, the text hash and the pass/fail result.

### 12.5 Conversion record (`conversion-record.json`) — stored beside the model file

```json
{"record_version": "1", "spec_sha256": "...",
 "source": {"repo": "...", "commit": "...",
            "files": [{"path": "...", "sha256_expected": "...", "sha256_observed": "..."}]},
 "tool": {"release": "...", "script_sha256": "...", "python_lock_sha256": "..."},
 "intermediate": {"type": "bf16", "sha256": "..."},
 "output": {"file": "...", "sha256": "...", "quantization": "Q4_K_M", "calibration": "none"},
 "chat_template_sha256": "...", "started_utc": "...", "ended_utc": "...",
 "host": {"os": "...", "arch": "...", "memory_gb": 0}}
```

The protocol's `model.artifact.conversion_record_sha256` is this file's SHA-256. No token or credential appears in it. Download credentials, if any, come from the environment and are never written anywhere.

## 13. Runner

### 13.1 Plan (`plan.yaml`)

```yaml
plan_version: "1"
steps:
  - {type: convert,  spec: <path>, spec_sha256: <hex>}
  - {type: sanity,   spec: <path>, spec_sha256: <hex>}
  - {type: verify,   protocol: <path>, protocol_sha256: <hex>}
  - {type: run,      protocol: <path>, protocol_sha256: <hex>, manifest_sha256: <hex>,
                     tier: smoke, subset_sha256: null, k: 1, seed_base: 0}
  - {type: report,   of_step: <n>}
  - {type: approval, id: <label>}     # waits until the maintainer resumes
```

**Rules:**

- Steps run in order, once each.
- There is no branching, looping, expansion or scheduling.
- Each step pins its inputs by hash. A hash that doesn't match when the step starts stops the plan (state `stopped`).
- Paths in a plan are relative to the repository root, so a plan doesn't depend on where the repository is checked out.

### 13.2 Authorization (`authorization.json`) — kept outside the repository

```json
{"authorization_version": "1", "plan_sha256": "...", "scope": "<free text>",
 "bindings": {"protocols": ["<sha256>"], "artifacts": ["<sha256>"], "specs": ["<sha256>"],
              "manifest": "<sha256>", "subsets": ["<sha256>"],
              "runtime_build": "b10412", "harness_revision": "<commit>"},
 "attempts_allowed": 1, "stop_conditions": ["..."],
 "approved_by": "maintainer", "approved_utc": "..."}
```

**Rules:**

- `runner start` exits `2` unless all three of these hold:
  - the plan's SHA-256 equals `plan_sha256`;
  - every hash the plan pins is listed in `bindings`;
  - the installed harness revision equals `harness_revision`.
- An authorized plan is never edited. A change means a new plan and a new authorization.
- `runner start` stores byte-identical copies of the plan and the authorization in the runner state (§13.3). Every later runner command uses those copies, so moving or deleting the originals never blocks `resume`.

### 13.3 State: `$SLM_EVAL_RUNS/_runner/<plan_sha256>/`

- **`status.json`** is authoritative. It holds:
  - `plan_sha256` and `authorization_sha256`;
  - `state`: `running`, `paused`, `waiting_approval`, `interrupted`, `stopped` or `completed`;
  - `step`, `run_id` and `progress: {done, total}`;
  - `last_event` and `updated_utc`.
- **`plan.yaml`** and **`authorization.json`** are the copies stored by `runner start`. Their SHA-256s equal `plan_sha256` and `authorization_sha256` in `status.json`.
- **`attempts.log.jsonl`** is append-only. Each line holds `{seq, prev_sha256, at_utc, step, event, detail}`.
  - `prev_sha256` is the SHA-256 of the previous line's UTF-8 bytes, excluding its line terminator, so an edit or deletion inside the log can be detected. The first line's `prev_sha256` is 64 zeros.
  - `runner status` prints the head hash: the SHA-256 of the last line's UTF-8 bytes, excluding its line terminator, so it can be recorded elsewhere. The head hash of an empty log is 64 zeros.
- **`server.log`** is the model server's log. The key is never written to it.

### 13.4 Pause and resume

- **`pause`** takes effect after the item in progress completes.
- **`pause --now`** takes effect at once. The item in progress is discarded, and regenerated on resume.
- **Either way,** the runner writes a checkpoint, stops the model server, releases the sleep lock (§13.6) and sets state `paused`.
- **`resume`:**
  - re-verifies the hashes of the stored plan and authorization (§13.3), and of the protocol, artifact and runtime, and exits `2` on any mismatch;
  - restarts the server;
  - continues from the last checkpoint.

  The same command recovers after a crash, a reboot or a power loss (state `interrupted`). Nothing restarts automatically.
- **An `approval` step** sets state `waiting_approval` and releases the sleep lock. `resume` continues past it.

### 13.5 Interruptions, stuck items and retries

- **Awake clock:** every timeout is measured on a clock that doesn't advance while the system sleeps. On macOS that is `CLOCK_UPTIME_RAW`.
- **Sleep-gap verification:** this contract defines no public test hook. Injected-clock tests check simulated sleep gaps; proof-run records are checked for actual interruption behavior.
- **Interruptions** are recorded as events and never count as failures or retries. These are interruptions:
  - sleep, hibernation and a closed lid;
  - power loss;
  - lost network;
  - load from other work on the machine.
- **Stuck:** an item is stuck when no new tokens arrive for `resources.stuck_after_awake_s` of awake time, as reported by the keyed server. A stuck item is aborted, and counts as an infrastructure failure for that item.
- **Memory:** before each item, if free memory is below `resources.min_free_memory_gb`, the runner waits and records `memory_wait`. It never fails for this.
- **Retries:**
  - An item that fails for an infrastructure reason may be retried up to 2 times. Infrastructure reasons are a server error, a server crash while the machine is awake, and a stuck item.
  - Every retry is recorded, and the first completed generation is the one scored.
  - When items that exhausted their retries reach 5% of the run's items, the runner stops (state `stopped`) for the maintainer.
- **Integrity failures** always stop the plan.
- **Slow progress** is never a failure.

### 13.6 Sleep lock and notifications

- The runner holds a sleep-prevention assertion only while a step is actively working. It releases it while paused or waiting for approval.
- It doesn't try to prevent sleep from a closed lid or low power. Those are interruptions (§13.5).
- On completion, a stop, or an approval wait, it sends a best-effort local notification. `status.json` remains authoritative.
