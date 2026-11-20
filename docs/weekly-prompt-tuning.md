# Weekly prompt candidate

`WEEKLY_PROMPT_VARIANT=evidence_first` enables the candidate only for freeform weekly generation and its batch repairs. The default, an unset value, `control`, and unrecognized values keep the existing prompt. Daily recommendations and the existing verified-menu shortcut retain their behavior. Nothing in `render.yaml` enables the candidate.

The candidate retains the complete original instructions and structured-output schema. It adds a bounded reference library of foods resolved by the existing, unchanged nutrition matcher, using the exact verified query keys and preparation states. References are filtered against the active diet and exclusions. A short decision order and final checklist address practical carbohydrate portions, measured fats, duplicate cooking, ingredient/direction consistency, and variety. Unsupported secondary restrictions or a library without compatible protein options fall back to the original context.

The reference builder performs no live USDA calls or writes. It reads the public-food cache and checksum-pinned local catalog, rechecks cached evidence, and retains actual source descriptions. It does not declare generated meals verified. Nutrition verification, portion fitting, daily target enforcement, variety checks, repair limits, catalog recovery, persistence checks and the locked-plan guard remain unchanged.

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
