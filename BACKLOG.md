# Product backlog

## Next: shoppable grocery lists

Add an optional **Shop with Instacart** handoff to the consolidated Grocery List.

- Use the Instacart Developer Platform shopping-list link API.
- Send normalized names, quantities, units, and relevant dietary filters.
- Let users review retailer, product matches, prices, and substitutions on Instacart before checkout.
- Keep API credentials server-side and make the integration optional when unavailable.
- Measure grocery-list completion and outbound shopping-link usage before adding retailer-specific integrations.

Phase 1 deliberately keeps checkout out of Glycofy. The in-app grocery list is the canonical source for a later commerce adapter.

## Completed: optional passkeys and stateful sessions

Passkey registration, sign-in, naming, inventory, and revocation are implemented with exact production RP/origin validation, required user verification, single-use challenges, recent-authentication gates, and security notifications. Stateful session inventory and individual/all-other revocation are also complete. Passkeys remain optional during the close-friends beta; mandatory step-up authentication remains a public-launch security decision.
