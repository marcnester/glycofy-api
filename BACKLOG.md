# Product backlog

## Next: package-aware grocery intelligence

- Map normalized ingredients to common purchasable package sizes.
- Show needed quantity, suggested purchase quantity, and likely remainder.
- Let athletes mark pantry inventory and preferred brands or package sizes.
- Favor ingredient reuse and lower waste without compromising training nutrition.
- Produce retailer-ready identifiers and substitution constraints.

## Then: shoppable grocery lists

Add an optional **Shop with Instacart** handoff to the consolidated Grocery List.

- Use the Instacart Developer Platform shopping-list link API.
- Send normalized names, quantities, units, and relevant dietary filters.
- Let users review retailer, product matches, prices, and substitutions on Instacart before checkout.
- Keep API credentials server-side and make the integration optional when unavailable.
- Measure grocery-list completion and outbound shopping-link usage before adding retailer-specific integrations.

Phase 1 deliberately keeps checkout out of Glycofy. The in-app grocery list is the canonical source for a later commerce adapter.
