# Meal planner beta certification

This is the release gate for Glycofy's Today and Weekly meal planners. It is deliberately separate from model demos: model output is untrusted until the deterministic policy accepts it.

## Certified matrix

The automated matrix covers:

- four diets: omnivore, pescatarian, vegetarian, and vegan;
- all nine profile allergens: milk, egg, fish, shellfish, tree nuts, peanuts, wheat, soy, and sesame;
- every diet x individual-allergen pair (36 profiles);
- five high-risk exclusion sets for every diet: milk + egg, peanut + tree nuts, fish + shellfish, wheat + soy + sesame, and all nine allergens together;
- both the one-day and seven-day planning workflows for all 56 profiles.

One certification run therefore exercises 112 complete planner requests covering 448 planned days and 1,792 meals. It separately rejects all 69 recognized aliases for the nine allergens and verifies that explicitly plant-based dairy alternatives do not create false milk-allergy or vegan-diet failures.

The suite is in `tests/test_meal_planner_certification.py` and `tests/test_ai_quality_harness.py`.

## Nutrition acceptance rules

Every accepted meal must have:

- calories, protein, carbohydrate, and fat as finite non-negative values;
- calorie energy reasonably consistent with `4 x protein + 4 x carbohydrate + 9 x fat`;
- itemized nutrition that reconciles to the displayed meal totals whenever all ingredients are USDA-resolved;
- all four meal macros within the configured target tolerance, with complete-day totals enforced before persistence;
- measured, practical ingredient quantities and a real food source for material protein, carbohydrate, and fat claims;
- complete timing and cooking directions, including safe-doneness guidance when required.

Training-target certification adds 960 athlete/session combinations across four body weights, five sports, four intensities, and twelve duration boundaries. It enforces monotonic energy/carbohydrate response, a current 3–8 g/kg/day carbohydrate range, a 1.4–2.2 g/kg/day protein range, and calorie/macro reconciliation. See `docs/ATHLETE_FUELING_POLICY.md`.

These ranges are guardrails, not individual medical advice. They align with the personalized and periodized approach in the [World Athletics nutrition consensus](https://worldathletics.org/download/download?filename=23fb9de0-6699-4d5b-b075-42f5da5518f5.pdf&urlslug=nutrition%2Bfor%2Bathletics%2B-%2B2019%2Biaaf%2Bconsensus%2Bstatement) and the [Academy of Nutrition and Dietetics, Dietitians of Canada, and ACSM position statement](https://pubmed.ncbi.nlm.nih.gov/26891166/).

## Commands

Run the certification gate:

```bash
pytest -q tests/test_meal_planner_certification.py tests/test_ai_quality_harness.py tests/test_training_nutrition.py tests/test_weekly_batch_generation.py tests/test_weekly_meal_uniqueness.py tests/test_usda_nutrition.py
```

Run the complete release gate:

```bash
ruff check app tests
mypy app
pytest -q
```

## What “passed” means

A green run means every enumerated deterministic case passed the current code and policy versions. It does not mean every possible future model response is guaranteed to be correct. Production sampling, failure-rate monitoring, latency monitoring, user feedback, and periodic review by a registered sports dietitian remain required release controls.
