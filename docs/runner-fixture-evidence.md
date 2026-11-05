# Runner fixture evidence (increment 5)

`slm-eval runner start|pause|resume|status` implements the fixed-sequence
runner of [contract v0.8.1 §13](math500-interface-contract-v0.8.1.md). A plan's
`run` steps execute in the runner's worker process, so that pauses, memory waits,
stuck items and retries apply to each item. Its `convert`, `sanity`, `verify`
and `report` steps run the named `slm-eval` command as a child process and map
its exit code. The `slm-eval run` command path is unchanged.

## Implementation choices within the contract

- **Step numbers** start at 1. A `report` step's `of_step` must name an earlier
  `run` step.
- **Manifest.** A run step pins `manifest_sha256` but names no path. The runner
  reads the one committed manifest at `configs/datasets/math500-manifest-v1.json`
  and checks its hash. Nothing is searched for.
- **Subsets.** A run step pins `subset_sha256` but names no path. A non-null
  value must equal the SHA-256 of the one committed subset,
  `configs/datasets/math500-validation-subset-50-seed-20261027-v1.json`
  (exit `2`; a missing file exits `3`). Nothing is searched for. `runner start`
  and the step itself also check the subset's §5.2 binding: its
  `manifest_sha256` and `tier` must match the step (exit `2`), and its IDs must
  be an ordered subset of that tier (exit `2`). A malformed subset exits `3`.
- **Subset runs.** `slm-eval run --subset <subset.json>` generates only the
  subset's IDs, in tier order. The run directory keeps a byte copy,
  `subset.json`, whose SHA-256 is `run.json` `subset_sha256`. `resume` and
  `report` read only that copy; a changed copy exits `2` and a missing one `3`.
  A resumed plan step also checks that `run.json` binds the step's subset. A
  v0.7 protocol, which has no adapter, keeps v0.7 behavior: `--subset` exits `3`.
- **Start checks.** In addition to the three §13.2 bindings, `runner start`
  makes these checks:
  - Every pinned repository input exists (exit `3`) and matches its hash
    (exit `2`).
  - Each protocol's artifact and runtime build are bound (exit `2`).
  - Every `run` step uses a protocol with an adapter and `resources` (exit `3`).
  - For each model artifact, a smoke run precedes any other tier (exit `3`).

  Each step re-checks its own inputs when it starts. A mismatch at that point
  stops the plan.
- **Harness revision.** This is the checked-out commit. A tree with
  uncommitted changes never matches a binding. Resume re-checks the revision.
- **Attempts.** `attempts_allowed` counts `runner start` calls for one plan.
  `runner start` can restart a plan only when it is `stopped`, attempts remain,
  and its stored plan and authorization copies are byte-identical. A restart
  continues from the checkpoint. Pauses and interruptions never count as
  attempts.
- **Active plan.** One plan per run store may be non-terminal. With none
  active, `runner status` shows the most recently updated finished plan.
- **Command exit codes:**
  - Exits `2`, `3` and `4` stop the plan.
  - A convert exit of `1` (for example a lost network) marks the plan
    `interrupted`.
  - Any other exit of `1` stops the plan.
  - Reaching the 5% rule also stops the plan; no command exit code applies.
- **Infrastructure failures.** These are:
  - an HTTP 5xx response;
  - a server that crashes or drops the connection while the machine is awake;
  - a stuck item, meaning no increase in the keyed `/slots` `n_decoded` count
    for `stuck_after_awake_s` awake seconds.

  After each failure, the server restarts with a new key and port. A failure
  that follows a sleep gap is an interruption instead and uses no retry. A sleep
  gap is more than 5 s of continuous time beyond awake time. Pairs that use up
  their retries are recorded in `run.json` under `infra_failures`, never in
  `items.jsonl`.
- **Clocks.** On macOS, awake time is `CLOCK_UPTIME_RAW` and continuous time is
  `CLOCK_MONOTONIC`. On Linux, they are `CLOCK_MONOTONIC` and `CLOCK_BOOTTIME`.
- **Free memory.** On macOS this is free, inactive and speculative pages from
  `vm_stat`; on Linux it is `MemAvailable`. If free memory can't be measured,
  the plan stops with exit `3`.
- **Sleep lock.** `caffeinate -i -w <worker pid>`, which also ends when the
  worker dies. Notifications use `osascript` on a best-effort basis.
- **Recovery** covers four cases:
  - A worker that ended in state `running` is recorded as `interrupted`.
  - Unterminated tails of `items.jsonl` and `attempts.log.jsonl` are dropped,
    and their hashes are recorded.
  - Orphaned server or step processes are stopped.
  - The run's `events` receive an `interrupted` event for the attempt that
    ended without a checkpoint.
- **Fixture templates.** A `role: fixture` protocol pins the SHA-256 of the
  template embedded in its own file. That hash is still verified against the
  file before use. Target protocols still require the approved source template
  hash.
- **Server log.** The model server's log goes to `server.log`. Each time the
  server stops, the log is scanned for the key. A match is redacted, and the
  plan stops with exit `4`.
- **Credentials.** Only the converter's own Hugging Face requests use a download
  credential (§12.5). No child process inherits `HF_TOKEN`, `HF_HUB_TOKEN`,
  `HUGGING_FACE_HUB_TOKEN` or `HUGGINGFACE_TOKEN`. This covers every model
  server and `--version` probe, from `run`, `verify-runtime`, `verify-artifact`
  and the runner, as well as the converter's dependency check, conversion,
  quantization and perplexity tools. The runner's `verify` and `report` command
  children also get none. Its `convert` and `sanity` children keep the
  environment, because they download from the pinned sources.
- **Report counts.** For v0.8 runs, `report` adds the §7 counts:
  - missing final answers (extraction status `no_answer` or `truncated`);
  - infrastructure failures (pairs that exhausted their retries);
  - `interrupted` events in `run.json`.

  Exhausted pairs are never written to `items.jsonl`. They are neither wrong
  answers nor part of an accuracy denominator, and `completeness` counts them
  as missing. A subset run reports the subset's size as its expected item count.
  Reports of v0.7 runs are unchanged.

## Fixture verification

The fixture is `HuggingFaceTB/SmolLM2-135M-Instruct` at
`12fd25f77366fa6b3b4b768ec3050bf629380bac`, using the increment-4 conversion.
It ran through the [fixture protocol](../configs/protocols/fixture-smollm2-135m.yaml)
on the smoke tier only. The plans and authorizations were synthetic, kept
outside the repository, and are not authority for target evaluation. Fixture
runs are never reported.

| Artifact | SHA-256 |
|---|---|
| Fixture protocol | `87b6781985ed22e93cce2a2ccbd79bb5bd4579772185852b2b713a39defd8359` |
| Q4_K_M output, re-verified by the plan's convert step | `a6da5bd7e3dd3d08d8da8e22449960829cd8d2729833e94420a9734e07fef8ca` |
| Conversion record, unchanged | `ef76dbc7fa2ece871ee02753d89628f8bbaf5683445beb288853866556bbdcf8` |
| Sanity record, byte-identical to increment 4 | `4c0ea8be262419538256457d88d2ac0880f5fe46e2d1a9ba959e393934b2e810` |

Runtime verification of the fixture protocol passed, with no unexpected open
routes.

| Plan | Harness revision | Steps | Exercised | Attempt-log head |
|---|---|---|---|---|
| `ffa07bd6edd6f2218b82f1bac28d32e1004866c74fbe1f7c5f2d84ed27dbd216` | `0b5851541d8982ed4ed2a01460e4a19ce665daf1` | convert, sanity, verify, smoke run, approval | pause after item; `pause --now` (it landed between items); `kill -9` of the worker mid-item with an orphaned server, then resume; approval wait, then resume | `a44e5abc688eac28371543118b6f67c970d0104436eb4ad9689551c59c2f4ca8` |
| `e96245d940d6e3a7f9ed51cb4929b919900fdf0dbdb699645f3744c793be7023` | `63a25e5d10ae0b49b17e046b529153dfb81b1470` | verify, smoke run, approval | `kill -9` with a recorded run interruption; `pause --now` during an item, which discarded and later regenerated that one item; approval | `d75757c834962044a05627dfb520f5cdfcbc243e267f8756f3e2190ba6146473` |

Both plans reached `completed` with valid attempt-log chains. Each run held
all 16 smoke pairs exactly once. After each pause, wait and crash recovery, no
model server or runner sleep assertion remained. Once the harness revision
moved, the first authorization was refused with exit `2`.

[`tests/test_plan_runner.py`](../tests/test_plan_runner.py) adds deterministic
fault injection with a fake keyed server and an injected clock. It covers:

- kill and resume, including partial item and log writes;
- pause and `pause --now` during items, commands and memory waits;
- plan, authorization and stored-copy hash mismatches;
- retry accounting and the 5% stop rule;
- a disk-floor exit;
- memory waits and stuck items;
- sleep gaps on the injected clock.

## Limits

- The §7 distribution of `thinking_state` across interruptions is not reported.
  An interrupted generation is never recorded (§6.1), so its own thinking state
  is unknown. Which records the distribution should cover awaits a contract
  clarification.
- A direct `slm-eval run` records no `interrupted` event when it is
  interrupted, so its interruption count is `0`. Only runner steps record these
  events.
- Subset mechanics are verified on synthetic tiers only. No fixture or target
  model has run on the validation subset.
- Sleep, lid close and power loss on real hardware were not exercised. As
  §13.5 provides, sleep gaps use injected-clock tests, and proof-run records
  remain the Coordinator's.
- No infrastructure fault, memory wait or stuck item occurred in the fixture
  runs. Those paths are verified by fault injection only.
- The runner relies on the converter's disk floors. It does not check the
  free space of the run-store volume itself, and the contract defines no
  run-store floor. A failed run-store write is handled as an interruption. Fault
  injection of a short `items.jsonl` write (`ENOSPC`) leaves the plan
  `interrupted`, with the server stopped and the sleep lock released. `resume`
  drops the torn line and regenerates only that item. By inspection only, not
  fault injection: if the attempt log or `status.json` can't be written either,
  the worker exits in state `running`, and `resume` records that as an
  interruption.
- Delivery of notifications was not confirmed.
