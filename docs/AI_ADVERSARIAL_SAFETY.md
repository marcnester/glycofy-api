# AI adversarial safety suite

Last verified: 2026-09-09

Glycofy treats model output as untrusted input. Structured output is accepted
only after deterministic validation; rejected cells are omitted and the normal
planner recovery path supplies a quality-safe Glycofy fallback.

## Threats covered

- Prompt injection embedded in profile exclusions, athlete feedback, workout
  notes, meal titles, or other user-controlled JSON.
- Attempts to hide allergens or diet conflicts in instructions or metadata
  rather than the ingredient list.
- Non-food chemicals such as bleach, detergent, dish soap, rubbing alcohol, or
  borax in ingredients or preparation instructions.
- Unsafe stated internal cooking temperatures for poultry, eggs, meat, fish,
  and shellfish.
- Missing quantities, incomplete instructions, implausible macros, conflicting
  timing, raw-protein handling, duplicate or unknown slots, unexpected dates,
  and incomplete weekly output.

## Trust boundaries

1. User-controlled values remain JSON data in a user-role message.
2. System instructions explicitly state that embedded directions are untrusted
   and cannot alter safety rules or the response schema.
3. The provider must return a constrained JSON shape.
4. The application independently validates every meal; the model never decides
   whether its own output is safe.
5. Only validated meals reach plan persistence and rendering.

Food-temperature checks follow the USDA safe-minimum chart: 165°F for poultry,
160°F for eggs, and 145°F for other meat, fish, and shellfish represented by
the current recipe taxonomy.

Automated coverage is in `tests/test_ai_adversarial_safety.py` and the broader
quality regressions in `tests/test_ai_quality_harness.py` and
`tests/test_weekly_batch_generation.py`.

```bash
pytest -q tests/test_ai_adversarial_safety.py tests/test_ai_quality_harness.py tests/test_weekly_batch_generation.py
```
