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
  A resumed plan step also checks that `run.json` binds the step's subset. The
  original subset path may be moved or removed after the run starts. A v0.7
  protocol may use `--subset` too. Its `run.json` then adds `subset_sha256`, and
  its extraction, scoring and record shape are unchanged. A v0.7 run without a
  subset is unchanged.
- **Start checks.** In addition to the three §13.2 bindings, `runner start`
  makes these checks:
  - Every pinned repository input exists (exit `3`) and matches its hash
    (exit `2`).
  - Each protocol's artifact and runtime build are bound (exit `2`).
  - Every `run` step uses a protocol with an adapter and `resources` (exit `3`).
  - For each model artifact, a smoke run precedes any other tier (exit `3`).

  Each step re-checks its own inputs when it starts. A mismatch at that point
  stops the plan.
- **Runtime files.** The authorization's runtime build is checked for every
  plan. `runner start` and `runner resume` verify the installed runtime files
  only when a `verify` or `run` step is still to execute. Each of those steps
  verifies them again when it starts. A plan that has only approval steps left
  needs no runtime files, and no server ever starts for an approval step.
- **Harness revision.** This is the checked-out commit. A tree with
  uncommitted changes never matches a binding, and neither does one with
  untracked files that aren't ignored. Ignored run and cache files don't count.
  Resume re-checks the revision.
- **Attempts.** `attempts_allowed` counts admitted `runner start` calls for one
  plan. A start refused before admission, for example on a hash or
  authorization mismatch, spends none. `runner start` can restart a plan only
  when it is `stopped`, attempts remain, and its stored plan and authorization
  copies are byte-identical. A restart continues from the checkpoint. Pauses,
  interruptions, approval waits and resumes never count as attempts. Attempts
  are separate from per-item infrastructure retries.
- **Active plan.** One plan per run store may be non-terminal. With none
  active, `runner status` shows the most recently updated finished plan.
- **Command exit codes:**
  - Exits `2`, `3` and `4` stop the plan.
  - Exit `1`, or a child ended by a signal, also stops the plan, unless the
    worker itself observed a sleep gap while that child ran. A sleep gap is
    §13.5 evidence of an interruption: the plan is then `interrupted`, spends
    no attempt or retry, and is resumed by hand. An exit code alone is no
    evidence. A convert exit `1`, for instance, may be a genuine tool failure
    rather than a lost network.
  - Any other positive exit has an unknown cause and stops the plan, even after
    an observed sleep. It is reported with the established failure exit, `1`.
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
  - The run's `events` receive a `previous_attempt_ended` interruption for an
    attempt that ended without recording an interruption of its own, such as
    after `kill -9` or power loss. That event names no item.
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
  children also get none, and neither do the local helpers `caffeinate`,
  `osascript`, `vm_stat`, `git` and `ps`, or the public-content validator's
  `git`. The runner's background worker and its `convert` and `sanity` children
  keep the environment, because they download from the pinned sources.
- **Download redirects.** The converter attaches its bearer only to requests
  for the origin `https://huggingface.co` (port `443`). A redirect to any other
  scheme, host or port drops the `Authorization` header, and no later redirect
  gets it back. A redirect from HTTPS to another scheme is refused (exit `1`).
  `Range` still follows each redirect, so a CDN download resumes, and the
  publisher hash is still checked at the end. Errors name no URL, so no signed
  redirect address is printed.
- **Report counts.** For v0.8 runs, `report` adds the §7 counts:
  - missing final answers (extraction status `no_answer` or `truncated`);
  - infrastructure failures (pairs that exhausted their retries);
  - `interrupted` events in `run.json`, with `interruption_thinking_states`.
    That is the distribution of the interrupted attempts' own states: the four
    §8 states, plus two bins. `unknown` is an event naming an item × sample
    whose attempt returned nothing to classify, or an older event without
    state metadata. `no_item` is an event that names no item × sample. The two
    bins are not thinking states, and all six sum to the interruption count.

  Accuracy and its intervals are over the scored item × sample pairs.
  Exhausted pairs are never written to `items.jsonl`, so they are neither
  wrong answers nor part of an accuracy denominator. `completeness` gives
  `planned_pairs` (for the declared run, or its subset), `scored_pairs` and
  `exhausted_pairs`. A result with fewer scored than planned pairs, or an
  unfinished run, is marked incomplete: `complete` is `false`, and `report.md`
  says the accuracy is conditional and not a completed evaluation.

  With `--compare-published V`, a v0.8 report also sets the comparison's
  `not_whole_count` flag. The flag is set when `V` times the planned pairs is
  not a whole number. That still holds when infrastructure failures leave
  fewer pairs scored. `V` is still shown and still read, with the same
  comparison reading. Reports of v0.7 runs are unchanged. A v0.7 subset run's
  report also names its `subset_sha256`.
- **Interruption states.** When an attempt is interrupted after its response
  arrived, the pinned adapter classifies the output that attempt returned. Only
  the resulting `thinking_state` goes into the event; the text is never kept.
  Two examples: a server lost across a sleep before sampling is confirmed, and
  a signal before the record is written. The state is never taken from a later
  retry. Generation is not streamed, so an attempt lost before its response
  arrived has no output to classify, and its event carries no state.
- **Direct-run interruptions.** A v0.8 `slm-eval run` that ends on a signal,
  or on an unexpected error that leaves it `interrupted`, records an
  `interrupted` event. Its reason is `signal` or `error`. It names the attempt,
  the item × sample in progress if there is one, and that attempt's state when
  it was observed. Runner steps record the same event when their worker is
  signaled or hits an unexpected error. A `resume` after an attempt that
  recorded no interruption (a crash, `kill -9` or power loss) records
  `previous_attempt_ended`, as runner steps do. Exits for integrity,
  precondition or HTTP errors are not interruptions. The item in progress is
  never recorded, and v0.7 runs gain no events.

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
| `28b04b0f5c93067254d6548dc45d97dd6eb718adc72f8a08404144d6512df838` | `cf08b77e96fa6dfebcc802a8cd830e3f92191e74` | verify, smoke run, approval | synthetic Hugging Face credential sentinels in the runner's environment; approval wait, then resume | `fc61068ad1fe739a9190ee3ba5244bf195ca50cb7ec861a8f381882c1f26ab4c` |

All three plans reached `completed` with valid attempt-log chains. Each run
held all 16 smoke pairs exactly once. After each pause, wait and crash recovery,
no model server or runner sleep assertion remained. Once the harness revision
moved, the first authorization was refused with exit `2`.

The third plan checked the credential correction against the pinned runtime.
For `runner start` and `runner resume`, all four Hugging Face variables held
synthetic sentinel values, never real credentials. During the run, `ps -E`
showed those variables in the runner worker. It showed none in these children:

- the `verify-runtime` and `verify-artifact` command children;
- the three keyed `llama-server` processes;
- the `--version` probe.

No file in the run store contained a sentinel. The fixture's own template pin
was verified against the file's bytes, and `report` refused the fixture run with
exit `3`.

[`tests/test_plan_runner.py`](../tests/test_plan_runner.py) adds deterministic
fault injection with a fake keyed server and an injected clock. It covers:

- kill and resume, including partial item and log writes;
- pause and `pause --now` during items, commands and memory waits;
- plan, authorization and stored-copy hash mismatches;
- committed manifest and subset byte checks;
- subset plan steps and direct `run --subset`, with their resume, refusal and
  tampering cases;
- credential-free command and server children;
- retry accounting and the 5% stop rule;
- a disk-floor exit and a short run-store write;
- memory waits and stuck items;
- sleep gaps on the injected clock.

[`tests/test_credential_propagation.py`](../tests/test_credential_propagation.py)
uses synthetic sentinels and real child processes. Those children run through
each model-server, `--version`, converter-tool and local-helper path. The same
file drives a fake transport through urllib's real redirect handling, covering
same-origin, cross-origin, changed-port, downgrade, redirect-chain and resumed
CDN downloads.
[`tests/test_report_counts.py`](../tests/test_report_counts.py) covers:

- the §7 counts and the interruption-state bins;
- completeness;
- `not_whole_count` on planned versus scored pairs;
- the unchanged v0.7 report.

`tests/test_plan_runner.py` also covers:

- approval-only plans in an empty cache;
- direct and runner interruption events, including an interrupted attempt's
  state that differs from its retry's;
- a v0.7 protocol with and without `--subset`;
- command exits with and without an observed sleep;
- refused starts that spend no attempt;
- a disk that also blocks status and log writes.

## Limits

- Interruption states come only from output an interrupted attempt actually
  returned. In the fault-injection tests that happens when an item is lost
  after its response arrived. A run interrupted mid-generation reports `unknown`.
- Direct-run interruption events and download redirect scoping are verified by
  deterministic tests only: injected signals, and a fake transport behind
  urllib's own redirect handling. No real signal was sent to a model run, and
  no network request was made.
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
  `interrupted`, with the server stopped and the sleep lock released. No model
  retry is spent. `resume` drops the torn line and regenerates only that item.
  Fault injection also covers the case where the attempt log and `status.json`
  can't be written either. The worker then exits in state `running` and claims
  no checkpoint, and `resume` records the interruption. No proactive run-store
  floor is defined.
- Delivery of notifications was not confirmed.
