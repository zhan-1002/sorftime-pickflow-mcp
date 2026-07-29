# Private evaluation sample schema V1

The private sample set remains outside Git. This document defines its CSV
contract so product labels can be validated without exposing ASINs, keywords or
notes in normal output.

## Columns

| Column | Required for V1 | Values / meaning |
| --- | --- | --- |
| `asin` | yes | 10-character uppercase Amazon ASIN |
| `keyword` | yes | discovery keyword; private and never printed |
| `schema_version` | yes | `1.0` |
| `marketplace` | yes | defaults to `US`; one of the supported stations |
| `dataset_split` | yes | `calibration`, `validation`, or `disputed` |
| `label_status` | yes | `confirmed` or `disputed` |
| `expected_discovery` | at least one expected field | `found`, `not_found`, or `unknown` |
| `expected_hard_filter` | at least one expected field | `pass`, `fail`, or `unknown` |
| `expected_tier` | at least one expected field | `S`, `A`, `B`, `C`, or `unknown` |
| `expected_outcome` | at least one expected field | `select`, `reject`, `review`, or `unknown` |
| `product_tags` | recommended | pipe-separated stable codes such as `bulk|gift` |
| `reason_codes` | recommended | pipe-separated stable snake-case reason codes |
| `notes` | optional | private annotation notes; ignored by the evaluator |

`expected_outcome` is stored for the future selection-policy evaluator. The
current evaluator reports direct agreement only for discovery, hard-filter and
tier labels; it does not invent a composite select/reject policy.

## Validation

Validate structure and aggregate composition without making Sorftime calls:

```powershell
pickflow-evaluate --data-dir "D:/private/pickflow-eval" --limit 0 --validate-only --require-v1-labels
```

Legacy files containing only `asin,keyword` remain accepted when
`--require-v1-labels` is omitted. They are reported as unlabeled and are not
silently treated as confirmed positives.

Strict V1 readiness requires every row to have:

- schema version `1.0`;
- an assigned calibration, validation or disputed split;
- confirmed or disputed label status;
- at least one explicit expected-stage or expected-outcome label;
- valid, non-duplicate `asin,keyword` pairs.

## Privacy

- Standard output contains aggregate counts only.
- Validation issues use anonymous `case-0001` identifiers.
- Optional diagnostic JSON belongs in the private data directory.
- ASINs, keywords, notes, credentials and raw API responses must not be added to
  Git or synthetic fixtures.

## Annotation sequence for the 97-ASIN set

1. Preserve the original file as an immutable snapshot.
2. Add V1 columns in a new private file.
3. Mark uncertain labels `disputed`; do not force them into calibration or
   validation conclusions.
4. Assign the held-out validation split before changing scoring rules.
5. Run `--validate-only --require-v1-labels` until the set is strict-ready.
6. Tune only on calibration cases, then run one final held-out evaluation.
