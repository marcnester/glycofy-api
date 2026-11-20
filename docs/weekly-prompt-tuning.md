# Weekly prompt candidate

`WEEKLY_PROMPT_VARIANT=evidence_first` enables the candidate only for freeform weekly generation and its batch repairs. The default, an unset value, `control`, and unrecognized values keep the existing prompt. Daily recommendations keep their existing prompt, and the existing verified-menu shortcut remains in place. Nothing in `render.yaml` enables the candidate.

The candidate retains the complete original instructions and structured-output schema. It adds a bounded reference library of foods resolved by the existing, unchanged nutrition matcher, using the exact verified query keys and preparation states. References are filtered against the active diet and exclusions. A short decision order and final checklist address practical carbohydrate portions, measured fats, duplicate cooking, ingredient/direction consistency, and variety. Unsupported secondary restrictions or a library without compatible protein options fall back to the original context.

The reference builder performs no live USDA calls or writes. It reads the public-food cache and checksum-pinned local catalog, rechecks cached evidence, and retains actual source descriptions. It does not declare generated meals verified. Nutrition verification, portion fitting, daily target enforcement, variety checks, repair limits, catalog recovery, persistence checks and the locked-plan guard retain their behavior. Staging identified a protein-source naming false positive for USDA's “Cheese, cottage” and “Yogurt, Greek”; policy v24 recognizes those equivalent names. Regression tests still reject a meal claiming protein from mixed vegetables.

## Draft comparison

[Recorded results](evaluations/weekly-prompt-2026-11-19.json) use the same configured `gpt-5.6-luna` model, reasoning settings, output schema, targets, training inputs and frozen nutrition evidence for both prompts. Two seven-day pairs were counterbalanced: control/candidate, then candidate/control. The nutrition snapshot contained 712 checked cache records plus the pinned local catalog. No live USDA lookups or cache warming occurred between variants.

| Metric, two 35-meal runs per prompt | Control | Candidate |
| --- | ---: | ---: |
| First-pass acceptance after variety checks | 28/70 (40.0%) | 47/70 (67.1%) |
| Meals requiring repair or replacement | 42 | 23 |
| Mean first-pass wall time | 48.7 s | 44.0 s |
| Mean app-estimated first-pass model cost | $0.0193 | $0.0284 |
| Accepted meals failing repeat validation | 0 | 0 |

This is 45.2% fewer meals needing repair in the paired athlete-week drafts. A separate two-day vegan, soy/wheat-free stress case improved from 0/10 to 8/10 accepted, leaving two candidate meals for recovery. That stress case reused the macro targets; it is not evidence for every restricted profile.

The larger reference context increases first-pass model cost by roughly one cent per week in this experiment. These costs are the application's configured estimates, not billing measurements. Total cost after repairs was not measured.

This is an exploratory comparison with two full-week samples per variant. It measures unpersisted first-pass generation and existing variety checks, **not** complete repaired-week success, saved-week latency, cold-cache performance or statistical significance. The earlier live control week took 155.4 seconds end to end; no candidate end-to-end speedup is claimed.

The comparison process blocked SQL writes, disabled validation-queue and operation-record writes in that process, and checked every saved-plan fingerprint before and after. Saved meals, portions, instructions and lock states remained unchanged. No production service setting was changed.

## Repeat and roll out

Use a separate process with the application's existing configured credentials; never export or print them:

```sh
python -m scripts.compare_weekly_prompts \
  --job-id COMPLETED_SEVEN_DAY_JOB_ID \
  --repeats 2 --holdout-days 2 \
  --output /tmp/weekly-prompt-comparison.json
```

The harness never invokes meal persistence. It fails if a draft passes the pipeline but fails the repeat validation check, or saved-plan fingerprints change. It retains per-run acceptance, repair demand, latency, rejection reasons and estimated cost.

Before enabling the candidate in production, deploy it to isolated staging, enable `WEEKLY_PROMPT_VARIANT=evidence_first` there, and verify full seven-day/35-meal success over several runs, difficult diet/exclusion cases, a locked-day save attempt, saved ingredient nutrition and instructions, and end-to-end time/cost including repairs. Keep the existing repair/recovery paths enabled. Return the setting to `control` to roll back prompt selection; no migration or meal rewrite is needed.

The evaluation approach follows [official OpenAI guidance on systematic prompt testing and reference context](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy#optimization). Measured results above come from this application, not that guidance.

## Staging validation — 2026-11-20

[Recorded staging results](evaluations/weekly-prompt-staging-2026-11-20.json) cover runtime commit `c0f0df0`, image digest `cf4d6ac920c2c94cd1124087df9c0361235df1c783689b9b23fa0d65de625179`, and policy `nutrition-safety-v24-usda-dairy-aliases`. Staging has `WEEKLY_PROMPT_VARIANT=evidence_first`; production remains on its previous image and prompt setting. PostgreSQL is at `add_grocery_stock`; this change adds no migration.

| Case | Saved meals | Server time, including repairs | App-estimated model cost | Result |
| --- | ---: | ---: | ---: | --- |
| Standard week 1 | 35 | 102.8 s | $0.0441 | Passed saved-meal audit |
| Standard week 2 | 35 | 103.5 s | $0.0435 | Passed saved-meal audit |
| Locked-day week | 30 unlocked | 86.5 s | $0.0402 | Passed; locked day exactly unchanged |
| Signed-in browser week | 35 | 115.3 s | $0.0436 | Passed; 117.2 s click to response |
| Vegan, wheat-free candidate | 0 | 134.1 s | $0.0452 | Failed safely; incomplete week rolled back |
| Vegan, wheat-free control | 0 | 144.6 s | $0.0273 | Failed safely; incomplete week rolled back |

All 135 newly saved meals in successful runs passed an independent audit of measured portions, itemized nutrition, nutrient-source identity, dietary exclusions, cooking directions and timing. Nutrition reconciled under the existing rounding tolerances. No standard-week title repeated; substantial composition repeats stayed within the existing limit of two. The lock test compared exact fingerprints of every plan, meal and ingredient column before/after the weekly job and a direct replacement attempt. Both fingerprints matched, and direct persistence returned `skipped: true`, `applied: 0`, `created_recipes: 0`. Temporary preferences and the test lock were restored; all pre-existing plans remained unchanged.

The first staging run revealed three false protein-source warnings when USDA used reversed dairy word order. Adding the two equivalent names fixed these without changing nutrient evidence, portion limits, model settings, repair budgets or lock behavior. All 741 Python tests and the release checks passed.

**Validation is not fully green for production.** Both prompts failed the full vegan, wheat-free stress case with `restricted_week_incomplete`; neither saved a partial week. One paired case suggests an existing recovery limitation, not proof that all restricted profiles are equivalent. Improve restricted-week completion and repeat that gate before promoting the candidate. The draft first-pass gains do not yet establish a general end-to-end speedup: these staging runs used baseline-only synthetic weeks and warmed food evidence, unlike the earlier high-training production week.
