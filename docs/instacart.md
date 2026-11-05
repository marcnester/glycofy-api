# Instacart grocery shopping

Meal generation, recipes, portions, and nutrition targets are unchanged. This
integration creates a shoppable list; customers review store products and
quantities and complete checkout on Instacart. It does not place an order.

The integration is disabled by default. Both `INSTACART_ENABLED=true` and a
server-side `INSTACART_API_KEY` are required. The status endpoint hides shopping
when disabled; the POST endpoint also enforces the flag. Keys never go to the
browser. Approved lists exclude pantry items; changed plans require reapproval.
Links are reused only for unchanged lists within the configured expiry period.

## API access and validation

Obtain Instacart Developer Platform dashboard access and a development API key
with permission to create shopping-list pages. Do not paste credentials into
chat or commit them. Set the key through an approved secret-entry mechanism.

For local development, use `INSTACART_API_BASE=https://connect.dev.instacart.tools`.
Production uses `https://connect.instacart.com` and needs a production API key.
Production security validation rejects the development endpoint. An isolated
production-mode staging service therefore requires the matching production key;
otherwise conduct development-key validation locally with synthetic groceries.

Validate synthetic items and quantities, brand filtering, pantry exclusions,
approval invalidation, expired-link regeneration, and the generated shopping
page. Stop before checkout. API access and a successful real-provider test are
required before enabling the feature for users. Mock tests do not establish
provider access or retail quantity matching.

Build, scan and stage a new immutable image before rollout. The current
production Blueprint remains pinned to its validated image until a separately
approved release updates the digest. Keep the feature flag false until live
provider validation passes. No database migration is required.
