# AI meal quality and nutrition safety

Glycofy treats model output as untrusted input. Both Today and Weekly AI recommendations pass through the same deterministic policy before persistence.

The policy checks:

- explicit exclusions and common allergen aliases;
- vegan, vegetarian, and pescatarian compatibility;
- complete measured ingredients and usable instructions;
- itemized calorie, protein, carbohydrate, and fat contributions for every measured ingredient;
- exact reconciliation between the ingredient sum and the displayed meal nutrition;
- real protein, carbohydrate, and fat food sources for material macro claims;
- calorie-to-macronutrient consistency and target variance beyond the 18% hard limit;
- realistic preparation, cooking, and total times;
- cooking and doneness guidance for raw animal proteins.

Unsafe or incomplete weekly cells are discarded and regenerated through the per-slot path. Glycofy does not persist rejected model output; if a complete acceptable replacement cannot be produced, planning fails without replacing the athlete's existing plan.

Meal rows are the authoritative source for daily and weekly totals. API responses recalculate totals from the current meals so a partial replacement or legacy stored total cannot disagree with the food shown to the athlete. Itemized nutrition evidence is retained on plan ingredients for traceability.

## Authoritative nutrient values

When `USDA_FDC_API_KEY` is configured, every generated ingredient must include an exact edible gram weight and a generic USDA search description. Glycofy prefers Foundation, SR Legacy, and Survey food records, rejects inappropriate processed-food matches, and recalculates calories and macros from FoodData Central per-100-gram data whenever every ingredient resolves.

Set `USDA_FDC_REQUIRED=true` in production after the private key is installed. A missing key, unsafe diet/allergen result, or unmeasured ingredient fails closed. A valid measured food that is ambiguous, unmatched, or temporarily unavailable remains provisional: Glycofy uses the meal-level AI estimate only after plausibility and target-tolerance checks, queues the food for background validation, and never replaces a previously saved week with a partial result. Verified matches are stored in a shared catalog, and FoodData Central IDs and descriptions are retained with plan ingredients for auditability.

Weekly requests use the shared catalog first and make only a bounded number of live USDA calls. The reconciliation worker retries unresolved foods with exponential backoff, while the admin Operations page reports verified catalog coverage and queue health without exposing food descriptions, meals, users, or health data.

Source: U.S. Department of Agriculture, Agricultural Research Service, FoodData Central, 2019, https://fdc.nal.usda.gov/.

Every model result carries `prompt_version` and `quality_policy_version` metadata. These values are also retained with persisted plan meals, allowing evaluation results and production behavior to be compared across prompt changes.

Automated regression profiles currently cover endurance omnivore, dairy-free HYROX, vegan strength, vegetarian/nut-free, and pescatarian/wheat-free athletes. Their assertions run in the standard test suite in `tests/test_ai_quality_harness.py`.
