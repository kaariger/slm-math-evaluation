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

The smoke tier keeps the 16 IDs and their order from manifest SHA-256
`0252cdc6d2fa4084109a237aef432ba6bca4ff8f3301f53279ca2f10c9a47a47`.
The validation tier has 200 dev items. For each subject × level cell, its
target is the MATH-500 test count multiplied by 200/500. Take integer floors,
then give the remaining slots to cells with the largest fractional remainders;
ties use ascending subject and level. After the 16 maintainer exclusions and
the smoke IDs are removed, sort each eligible dev cell by ID. A single
`random.Random(20261006)` samples each cell uniformly without replacement in
ascending cell order. The validation list concatenates those cell draws. A
cell shortage is an error; no replacement or quota transfer is allowed.

| Subject | Level | MATH-500 count | Target | Drawn |
|---|---:|---:|---:|---:|
| Algebra | 1 | 17 | 7 | 7 |
| Algebra | 2 | 21 | 8 | 8 |
| Algebra | 3 | 26 | 10 | 10 |
| Algebra | 4 | 30 | 12 | 12 |
| Algebra | 5 | 30 | 12 | 12 |
| Counting & Probability | 1 | 2 | 1 | 1 |
| Counting & Probability | 2 | 7 | 3 | 3 |
| Counting & Probability | 3 | 4 | 2 | 2 |
| Counting & Probability | 4 | 13 | 5 | 5 |
| Counting & Probability | 5 | 12 | 5 | 5 |
| Geometry | 1 | 2 | 1 | 1 |
| Geometry | 2 | 8 | 3 | 3 |
| Geometry | 3 | 8 | 3 | 3 |
| Geometry | 4 | 10 | 4 | 4 |
| Geometry | 5 | 13 | 5 | 5 |
| Intermediate Algebra | 1 | 7 | 3 | 3 |
| Intermediate Algebra | 2 | 12 | 5 | 5 |
| Intermediate Algebra | 3 | 19 | 8 | 8 |
| Intermediate Algebra | 4 | 23 | 9 | 9 |
| Intermediate Algebra | 5 | 36 | 14 | 14 |
| Number Theory | 1 | 5 | 2 | 2 |
| Number Theory | 2 | 10 | 4 | 4 |
| Number Theory | 3 | 16 | 6 | 6 |
| Number Theory | 4 | 19 | 8 | 8 |
| Number Theory | 5 | 12 | 5 | 5 |
| Prealgebra | 1 | 7 | 3 | 3 |
| Prealgebra | 2 | 19 | 8 | 8 |
| Prealgebra | 3 | 17 | 7 | 7 |
| Prealgebra | 4 | 20 | 8 | 8 |
| Prealgebra | 5 | 19 | 7 | 7 |
| Precalculus | 1 | 3 | 1 | 1 |
| Precalculus | 2 | 13 | 5 | 5 |
| Precalculus | 3 | 15 | 6 | 6 |
| Precalculus | 4 | 13 | 5 | 5 |
| Precalculus | 5 | 12 | 5 | 5 |
| **Total** | | **500** | **200** | **200** |

A manifest with `review_status: pending` has flagged pairs awaiting maintainer
verdicts; it must not be used for a tier run. `data apply-verdicts` applies the
complete verdict set and draws tiers after exclusions. It refuses any change to
a tier already used by a run. The flagged-file input hash retains its original
reviewed tier-size marker (128 validation items), solely to preserve the
maintainer verdicts' flagged-file SHA-256 binding. That marker does not control
the current 200-item validation draw.

Example with an external cache override:

```sh
SLM_EVAL_CACHE=/tmp/slm-eval-data poetry run slm-eval data fetch
SLM_EVAL_CACHE=/tmp/slm-eval-data poetry run slm-eval data build-manifest \
  --out configs/datasets/math500-manifest-v1.json
```
