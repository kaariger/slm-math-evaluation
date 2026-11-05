# 0005: Ephemeral test fixtures versus evaluation targets — DRAFT

- Status: **DRAFT, pending explicit maintainer approval.** Until approved, this
  text is a proposed interpretation, not a final one.
- Date: 2026-11-04
- Related: [0002](0002-phi-4-reasoning-target.md),
  [0003](0003-phi-4-reasoning-plus-target.md),
  [0004](0004-gemma-4-12b-target.md)

## Context

[Interface contract v0.8.1](../math500-interface-contract-v0.8.1.md) introduces
two roles:

- **§1:** "Ephemeral test fixtures (§4) may be used by tests and proofs, and are
  never reported."
- **§4:** `role: fixture` "marks an ephemeral test fixture: a small model used
  by tests and proofs. Fixture runs can't be reported (§7)."
- **§7:** `report` exits `3` for a fixture run.

The public contributor guidance ([`AGENTS.md`](../../AGENTS.md)) says "Adding
models, datasets or runtimes requires a new public decision". It also says to
keep curated results separate from routine run output. The pinned SmolLM2-135M fixture has exercised the
converter and the runner
([converter evidence](../model-converter-fixture-evidence.md),
[runner evidence](../runner-fixture-evidence.md)) without a model decision of
its own. That needs an explicit reading, so a fixture can't be mistaken for a
target and the guidance stays consistent.

## Proposed decision

**An evaluation target** is a model named by an accepted public decision
record. Only a target, run under an approved protocol, can produce a result
that may be reported, curated under `results/` or compared with a published
figure. It must also pass its own artifact and runtime verification, smoke run
and proof first.

**An ephemeral test fixture** is a small, pinned model used only to exercise
harness capabilities and fault paths. Examples are conversion and its resume,
runtime and artifact verification, runner pause, resume and recovery,
interruption handling and report refusal. It's an instrument for testing the
harness, not a subject of study. A fixture:

- **Is labeled.** Its protocol has `role: fixture`, a `fixture-` protocol
  version and file name, and a `fixture-<n>` model alias. It has no
  target-source revision in `run.json`.
- **Is never reported.** `report` exits `3`. Its numbers never appear in
  `results/`, in README result statements or beside a published figure. Its
  outputs and run directories stay outside the repository like any run.
- **Carries no scientific authority.** Its protocol values are labeled
  `local-choice`. A value chosen for a fixture is not evidence for any target's
  protocol value.
- **Pins its own embedded template.** Its protocol pins the SHA-256 of the
  template embedded in its own converted file. That hash is still checked
  against the actual bytes before every use, and a mismatch or missing pin is
  refused. This never relaxes a target's approved template pin.
- **Is documented by evidence.** It needs an immutable publisher revision,
  publisher file hashes, a license that permits local use, and a public
  evidence record of what it verified. It doesn't need a target decision.
- **Stays on harmless inputs.** It runs on the smoke tier, or on synthetic
  inputs, unless a maintainer decision allows more. This stops fixture runs on
  the validation or test tier from informing protocol choices. This default,
  and any scoped exception to it, is a proposed maintainer policy. The harness
  doesn't enforce it, and no CLI enforcement is added or claimed. No existing
  test case is retired by it. Existing fixture runs used the smoke tier only.

**A fixture result never stands in for a target result.** A fixture
verification shows only that the harness path works on that fixture. Each
target still needs its own conversion record, verification records,
feasibility measurement, smoke run and proof.

## Reconciling the contributor guidance

This draft proposes reading the guidance's "Adding models … requires a new
public decision" as adding evaluation targets. For maintainer review, the proposed wording, without
editing the guidance itself, is:

> Adding an evaluation target model, dataset or runtime requires a new public
> decision. An ephemeral test fixture (contract §4) is not an evaluation target:
> it needs a pinned evidence record, is labeled `role: fixture`, and is never
> reported.

The fixture uses the one pinned runtime, so no new runtime is added. Whether the
guidance adopts this wording, or keeps requiring a decision per fixture, is the
maintainer's choice.

## Alternatives considered

- **A full decision record for every fixture.** This is simple and uniform, but
  it gives instruments the weight of research subjects. It also slows
  fault-path testing, which needs a small real model.
- **Synthetic fakes only.** Fake servers already cover much of the runner. But
  conversion, template embedding and runtime loading need real, if tiny, model
  files.
- **Treating a fixture as a "small target".** Not proposed. It would blur the line
  between results and harness evidence.

## Consequences

- The existing fixture keeps its current labels and refusal, and needs no
  migration.
- Public documents describe fixture runs as "fixture verification, not a target
  result".
- The interpretation becomes final only if the maintainer approves this
  record. Changing the contributor guidance's wording is then a separate,
  explicitly scoped task.
