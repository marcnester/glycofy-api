# Product backlog

## Next: shoppable grocery lists

Add an optional **Shop with Instacart** handoff to the consolidated Grocery List.

- Use the Instacart Developer Platform shopping-list link API.
- Send normalized names, quantities, units, and relevant dietary filters.
- Let users review retailer, product matches, prices, and substitutions on Instacart before checkout.
- Keep API credentials server-side and make the integration optional when unavailable.
- Measure grocery-list completion and outbound shopping-link usage before adding retailer-specific integrations.

Phase 1 deliberately keeps checkout out of Glycofy. The in-app grocery list is the canonical source for a later commerce adapter.

## After planning reliability: optional passkeys

Add passkeys as a secure, low-friction sign-in option before expanding beyond the close-friends beta.

- Keep Google sign-in and email/password available during adoption and recovery.
- Let authenticated users create, name, review, and revoke multiple passkeys from Account & Privacy.
- Add a prominent **Sign in with a passkey** option to the login experience.
- Use a mature WebAuthn server library; do not implement credential verification cryptography directly.
- Bind WebAuthn to the production `app.glycofy.ai` relying-party domain and validate origins explicitly.
- Require recent authentication before adding or removing passkeys or performing sensitive account actions.
- Preserve verified-email recovery so losing every passkey cannot permanently lock out an account.
- Send security notifications whenever a passkey is added or removed.
- Cover registration, authentication, duplicate registration, revocation, recovery, cross-device use, and failure states with automated tests.

Passkeys remain optional and are not a blocker for the initial close-friends beta. Weekly and daily planning reliability remains the immediate product priority.
