# Local USDA catalog (staging first)

`USDA_LOCAL_CATALOG_ENABLED` defaults false. Existing verified query mappings are
read first. New queries search the immutable checksum-pinned bundled catalog,
using exactly the current raw/cooked, processing, density and ambiguity checks.
Missing, ambiguous or unavailable local results fall back to the existing USDA
API and its persistent cache. Local matches never overwrite existing mappings.
No recipe, weight, portion-fitting, meal-generation or QFC code changes.

The snapshot contains Foundation October 2026, SR Legacy April 2018 and FNDDS
2021–2023. Source file hashes and release are embedded. Negative/non-finite or
incomplete nutrition records are excluded rather than silently repaired. Nutrient
amounts retain USDA per100g values and the existing energy precedence. Branded
foods are outside current supported search types. No runtime download or SQLite.
The immutable JSON and inverted token index live in each worker's memory; measure
RSS and concurrent readiness before enabling. Rollback is disabling the flag and
restarting, with no database migration or data rollback.

Sources: https://fdc.nal.usda.gov/download-datasets/ (public domain USDA data).
Foundation CSV October2026 currently lacks food.csv/food_nutrient.csv, so use its
JSON download. Compile extracted official CSV directories and/or JSON files:

    python -m scripts.usda.import_snapshot --release RELEASE --output assets/usda/catalog.json INPUT ...

Review source hashes, excluded counts, and update USDA_LOCAL_CATALOG_SHA256 in
config with the printed digest. Release updates are reviewed PRs, never automatic
in-place replacements. Existing stored meals/nutrition are not recalculated.

Validation:

    python -m scripts.usda.validate_catalog --output /tmp/local.json
    python -m scripts.usda.validate_catalog --api --output /tmp/comparison.json

The second command uses the existing API key only in outbound HTTPS requests;
it reports no secrets and makes24public synthetic searches. Compare exact FDC IDs
and nutrient differences, manually review differing matches and fallback rates.
Benchmark cold load, uncached median/p95, API time and staging RSS/CPU/readiness.
Do not call this an end-to-end planning speedup or load test.

Before production: all CI/security/native gates; publish exact tested image;
staging with flag enabled, isolated database and existing provider test setup;
validate synthetic queries, API fallback/missing-file behavior and unchanged
verified mappings/portions. Retain comparison evidence. No production activation
or merge until accuracy differences are resolved and staging performance passes.
