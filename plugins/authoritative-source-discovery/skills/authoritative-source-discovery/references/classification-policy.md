# Classification policy

Classify candidates relative to the approved Collection Scope.

## Decisions

- `include`: clearly relevant and supported by Authority Evidence.
- `exclude`: clearly irrelevant, operational, duplicated, invalid, or otherwise Noise.
- `uncertain`: Relevance or Authority Evidence is insufficient, or access is materially limited.

Only `high` confidence may produce `include` or `exclude`. `medium` and `low` confidence always produce `uncertain`.

## Evidence order

1. Match against product, version, language, and content-category scope.
2. Inspect provenance, discovery method, source page, anchor text, and page region.
3. Prefer Core Sources and Direct Citations backed by Authority Evidence.
4. Treat access limitation separately from Noise.
5. Choose `uncertain` instead of guessing.

## Categories

Use one of: `documentation`, `reference`, `research`, `example`, `case`, `blog`, `marketing`, `recruitment`, `navigation`, `other`.

Return a short evidence-based reason. Never claim to have read content absent from supplied evidence.
