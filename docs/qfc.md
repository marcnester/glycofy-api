# QFC cart integration (disabled by default)

This branch is independent of PR79 grocery normalization and PR77 planner changes. It does not alter meals, nutrition, portions, recipes, or generation prompts.

## Configuration and customer authorization

1. Deploy the reviewed QFC candidate to isolated staging and run the migration. Keep production disabled.
2. In Kroger Developer Dashboard → Glycofy → edit app, add the Public Cart API. Keep Locations and Products; do not add Profile/Identity. Review and accept any new terms yourself.
3. Add the exact deployed redirect URI: `https://staging.glycofy.ai/oauth/kroger/callback`. No wildcard. Production later requires `https://app.glycofy.ai/oauth/kroger/callback` and its own reviewed rollout.
4. Enter `KROGER_CLIENT_ID` and `KROGER_CLIENT_SECRET` only in Render staging's server environment. Set `KROGER_ENABLED=true` and confirm `PUBLIC_BASE_URL=https://staging.glycofy.ai`. Credentials must never appear in chat, local files, screenshots, or frontend JavaScript. Production defaults to false.
5. Sign in to Glycofy staging, open `/ui/qfc.html`, then Continue to Kroger authorization. Sign in to the intended QFC/Kroger customer account yourself and explicitly authorize `cart.basic:write`. The developer account alone does not grant customer access. No profile or payment scope requested.
6. Choose a QFC store near your ZIP; load an approved grocery date range, search each ingredient, and choose a product and integer package count. Unmatched/pantry items are omitted explicitly. The preview shows the full store address and requires an explicit confirmation that the shopper selected the same store and shopping method on QFC before Add is enabled. The server also requires this confirmation. This is a customer attestation, not automatic retailer verification. Verify the store and modality on QFC: the cart API payload carries modality but not store location.
7. For live testing, use a dedicated customer account/cart if available. Otherwise obtain explicit approval for the exact products and package counts before adding anything to an existing real cart. Never place an order as part of validation.

## Safety and limits

Tokens encrypted through the existing ORM encryption layer. Authorization state is browser-bound, user-bound, expiring, and consumed once in the database. Tokens refresh server-side under a row lock. Local disconnect removes stored credentials; it does not promise provider-side revocation.

Preview expires after ten minutes and binds the approved grocery snapshot, user-selected store, products, fulfillment and package counts. Packaged UNIT products only; variable-weight and ambiguous packages are rejected. Matching is manual: search rankings are not ingredient substitutions. Nonfat versus whole dairy, prepared versus raw fish, and instant versus old-fashioned oats require review. No automatic portion or nutrition conversion.

A unique database fingerprint plus atomic claim guards double clicks and concurrent workers. Successful replays do not repeat the write. Timeout/error after a cart request leaves an uncertain outcome; do not automatically retry. Check the QFC cart first. A process crash after claim leaves `sending`, also blocked from retry. This is at-most-one submission, not a guarantee of retailer exactly-once delivery. New/reapproved selections can create new submissions; users must review existing cart contents. No checkout, payment, or automatic orders.

Before enabling: mocked auth/state, user isolation, encrypted storage, refresh, stale approval, package checks and duplicate/uncertain cart tests; migration checks; full CI; staging customer consent, token refresh, exact-item cart verification and disconnect. Live authorization, Northgate product lookup, one12-count egg carton cart addition, and customer selection of Northgate/Pickup were verified in isolated staging. Token renewal and disconnect/reconnect evidence is tracked in the release record; these gates must be completed on the final staging configuration before release.

API reference: https://developer.kroger.com/reference/ and Kroger's official Postman workspace https://www.postman.com/kroger/the-kroger-co-s-public-workspace/overview
