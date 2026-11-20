# Automatic meal quality safeguards

Policy `nutrition-safety-v23-complete-ingredient-evidence` applies to newly generated and reused recommendations through the existing validation and recovery paths. No migration or historical-plan rewrite is needed.

- Portion fitting and validation share single-serving limits: 120 g dry grains/pasta or flour/bread, 350 g cooked grains, 250 g fruit, 300 g yogurt/protein/selected vegetables, 30 g oil and 40 g sweeteners/nut butter. These are practical recipe bounds, not medical intake limits. Targets that cannot fit require a different recipe; nutrient values are never fabricated to close the gap.
- A meal with unresolved ingredient nutrition is rejected for recovery instead of using estimated meal totals. Both daily and weekly generation require complete ingredient contributions; a save-time check runs for every candidate before any existing meals are modified. Background matching can continue without making an incomplete meal eligible for saving.
- USDA selection requires staple identity and product form to agree. Display names and search queries are checked against the resolved record. Cached records are checked again against current matching and nutrient-density rules. Reused recipes with source descriptions also undergo identity validation.
- Weekly repair compares substantial ingredient sets as well as titles and protein/carbohydrate identities. The same substantial ingredient set is limited to two occurrences per meal slot in a generated batch, including cosmetically renamed meals.
- Recipe validation rejects unlisted named staples, repeated cooking steps for the same dry grain and explicit gram amounts exceeding the listed ingredient quantity. Existing dry-grain preparation, timing, seasoning and safe-doneness checks remain active. These deterministic checks cover common failures; they are not a complete language understanding check of every possible recipe.
- The existing locked-plan persistence guard runs before meal or recipe changes. Generation does not rewrite locked plans.

Regression cases are recorded in `tests/test_meal_quality_prevention.py`, based on the audited oversized fruit/rice portions, mismatched USDA foods and stale cooking directions. Nutritional targeting and restricted-diet certification are exercised by the full test suite.
