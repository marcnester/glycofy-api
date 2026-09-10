# Cloudflare and Render exposure review — September 9, 2026

## Scope

This review traced the production request path from public DNS through
Cloudflare to the Render web service and PostgreSQL database. It used
low-impact DNS, TLS, HTTP-header, and authenticated dashboard checks.

## Verified controls

- `app.glycofy.ai` is a proxied Cloudflare record and redirects HTTP to HTTPS.
- Cloudflare's managed WAF ruleset is active and its automated security level
  is always protected.
- Cloudflare TLS 1.3 is enabled and the visitor minimum is TLS 1.2.
- Cloudflare-to-Render encryption is **Full (strict)**, so the origin
  certificate is authenticated instead of merely encrypted.
- Always Use HTTPS is enabled at the edge.
- The application emits HSTS, CSP, frame-denial, MIME-sniffing, referrer,
  permissions, and cross-origin isolation headers.
- Production API documentation and development routes are disabled.
- Browser CORS and trusted hosts are restricted to `app.glycofy.ai`.
- The managed PostgreSQL database has no public inbound access.

## Corrected exposure

The default `glycofy-api.onrender.com` hostname initially served the complete
application. That route bypassed Cloudflare's WAF and edge controls. The Render
Blueprint now sets `renderSubdomainPolicy: disabled`; after deployment, direct
requests to that hostname must return Render's 404 and must not reach Glycofy.

## Deployment verification

After the Blueprint change is live:

1. Confirm `https://app.glycofy.ai/health` and `/ready` return 200.
2. Confirm `https://glycofy-api.onrender.com/health` returns 404.
3. Confirm an authenticated login and page load through the custom domain.
4. Recheck HTTP redirect, HSTS, CSP, and Cloudflare response headers.

## Residual boundaries

Cloudflare and Render provide network-level DDoS protection, but no public
application can be guaranteed attack-free. Authentication abuse controls and
incident-response readiness are assessed separately. The in-process rate
limiter remains appropriate only for the current single-instance controlled
beta and must move to shared infrastructure before horizontal scaling.
