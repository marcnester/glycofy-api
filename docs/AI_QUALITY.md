# AI meal quality and nutrition safety

Glycofy treats model output as untrusted input. Both Today and Weekly AI recommendations pass through the same deterministic policy before persistence.

The policy checks:

- explicit exclusions and common allergen aliases;
- vegan, vegetarian, and pescatarian compatibility;
- complete measured ingredients and usable instructions;
- plausible single-meal nutrition and calorie-to-macronutrient consistency;
- target variance beyond the normal macro tolerance;
- realistic preparation, cooking, and total times;
- cooking and doneness guidance for raw animal proteins.

Unsafe or incomplete weekly cells are discarded and regenerated through the per-slot path. Questionable per-slot creations use a verified catalog meal or deterministic preference-safe fallback; Glycofy does not persist the rejected model output.

Every model result carries `prompt_version` and `quality_policy_version` metadata. These values are also retained with persisted plan meals, allowing evaluation results and production behavior to be compared across prompt changes.

Automated regression profiles currently cover endurance omnivore, dairy-free HYROX, vegan strength, vegetarian/nut-free, and pescatarian/wheat-free athletes. Their assertions run in the standard test suite in `tests/test_ai_quality_harness.py`.
