# MATH-500 data foundation

The split manifest uses the exact `openai/prm800k` revision and file hashes in
`data.py`. `test.jsonl` provides 500 frozen test items. The dev pool consists of
the 7,499 rows in `train.jsonl` whose `unique_id` starts with `train/`. This is
the original-split marker; no independent content-hash membership fallback is
used. The manifest provenance note records the upstream count discrepancy and
the duplicated test-origin ID.

The duplicate audit compares test and dev problems after Unicode NFC
normalization and whitespace collapse. Exact matches and character 5-gram
Jaccard similarities of at least 0.85 are flagged. The threshold and method
are versioned in the manifest. The flagged file contains only IDs, match type,
and similarity. It stays in the dataset cache, outside the repository. A
maintainer can inspect a pair with `slm-eval data show-pair` and provide the
verdicts file defined by the interface contract. The 16 dev-side verdicts are
also retained in `configs/datasets/flagged-verdicts-v1.json` as an explicit
reviewed exception: the file contains pair numbers and verdicts only, with no
problem text.

Smoke and validation tiers are disjoint, seeded draws of 16 and 128 dev IDs,
respectively. These sizes are local choices for this manifest. A manifest
with `review_status: pending` has flagged pairs awaiting maintainer verdicts;
it must not be used for a tier run. `data apply-verdicts` applies a complete
verdict set and redraws both tiers after exclusions. It refuses any change to
a tier already used by a run.

Example with an external cache override:

```sh
SLM_EVAL_CACHE=/tmp/slm-eval-data poetry run slm-eval data fetch
SLM_EVAL_CACHE=/tmp/slm-eval-data poetry run slm-eval data build-manifest \
  --out configs/datasets/math500-manifest-v1.json
```
