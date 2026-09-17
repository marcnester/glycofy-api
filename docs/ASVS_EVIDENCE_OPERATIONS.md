# ASVS infrastructure evidence record

- Evidence date: September 17, 2026
- Owner: Glycofy project owner
- Review cadence: quarterly

## Render

- Web service and PostgreSQL are in the Oregon region and linked through Blueprint `fromDatabase`, which selects the private internal connection URL.
- The application adds PostgreSQL `sslmode=require` in production, preventing plaintext fallback on Render's TLS-capable internal database endpoint.
- PostgreSQL external IP allowlist is empty in `render.yaml`.
- Production runtime secrets use `sync: false`; secret values are not present in the repository or image.
- The container runs as the unprivileged `glycofy` user, is digest-pinned, and is scanned in CI. Application logs go to stdout/stderr and not to container files.
- Production remains one web process. Database-backed weekly jobs are durable, claimed atomically, bounded to one job per user, recover after deployment, and serialize execution. Horizontal scaling is prohibited until shared rate limiting and dedicated workers are enabled and load tested.

## Cloudflare and origin

- `app.glycofy.ai` is the only allowed application host and origin.
- Render's default subdomain is disabled.
- Cloudflare adds a static secret `X-Glycofy-Edge-Auth` request header and overwrites any client-supplied value. The application rejects production traffic without the matching Render-held secret, except the non-sensitive `/health` and `/ready` endpoints required for direct platform health probes.
- Uvicorn proxy-header trust is disabled. The application ignores `X-Forwarded-For` and accepts a syntactically valid `CF-Connecting-IP` only behind the authenticated origin boundary.
- Cloudflare WAF/security rules and DDoS protections remain enabled. Changes to DNS proxying or the header rule require a production smoke test before completion.

## Access review checklist

Quarterly, confirm:

1. Google, GitHub, Cloudflare, Render, and OpenAI owner accounts remain protected by phishing-resistant MFA or Google SSO backed by it.
2. No unexpected workspace members, deploy keys, OAuth applications, API keys, or administrator emails exist.
3. Render secret values are current and unavailable to application logs; stale keys are revoked.
4. Cloudflare DNS records remain proxied and the origin-authentication transform is active.
5. Database external access remains empty or narrowly allowlisted and backups/restores remain tested.
6. Security alerts arrive in the independent security mailbox and include a usable request ID.
