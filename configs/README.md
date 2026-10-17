# Configuration

- `protocols/protocol.yaml`: approved Q4_K_M `v1` protocol.
- `protocols/math500-v1-q8.yaml`: approved Q8_0 `v1-q8` protocol.
- `datasets/math500-manifest-v1.json`: frozen MATH-500 membership and dev tiers.
- `datasets/flagged-verdicts-v1.json`: maintainer verdicts for the 16 flagged dev pairs.

The protocol files contain model, dataset, prompt, generation, extraction, and
scoring settings. A change to an approved protocol requires a new version.
The old placeholder templates were removed because they were not runnable and
no command reads them.
