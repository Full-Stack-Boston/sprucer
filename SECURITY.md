# Security Policy

## Supported versions

Sprucer is alpha (`0.1.x`). Security fixes land on `main`.

## Threat model (honest)

- **Intended:** single-operator career desk or small authenticated deployments; drafts until you approve; local Docker or private network.
- **Not intended (yet):** public anonymous generate APIs, or treating `auth_mode=none` as multi-user safety.
- **Tenancy:** with auth enabled (`dev` / `oidc` / `api_key`), truth and applications are scoped per identity. With `auth_mode=none`, the deploy uses one shared vault. `auth_mode=demo` gives each browser session its own anonymous, ephemeral vault.
- **Demo mode:** `SPRUCER_DEMO=1` is designed to be publicly reachable. It runs a fully offline generator (no external LLM, no cost), disables outbound URL ingest, mints a random per-session vault, and sweeps stale sessions after `SPRUCER_DEMO_TTL_HOURS`. Treat demo vaults as throwaway. Do not put real personal data in a demo.

## Hardening already in place

- Auth modes: `dev` (local), `api_key`, `oidc`, `none`, `demo`
- Refuses `dev`/`none` on non-loopback binds unless `SPRUCER_ALLOW_INSECURE_DEV=1`
- Session cookies: `HttpOnly`, `SameSite=Lax`, optional `Secure` via `SPRUCER_COOKIE_SECURE`
- JD URL ingest: http(s) only, blocks loopback/private/link-local/metadata, re-checks every redirect hop (including Workday CXS follow-ups and links extracted from fetched pages); can be disabled entirely with `SPRUCER_INGEST_URL_ENABLED=0`
- Bring-your-own-key proxy (`SPRUCER_BYOK_ENABLED=1`): the visitor's provider credentials are sent per request and never persisted or logged server-side. The supplied base URL must be a public **https** endpoint (same SSRF guard as URL ingest) and can be restricted to an allowlist via `SPRUCER_BYOK_ALLOWED_HOSTS`
- Optional disable of `/docs` via `SPRUCER_DOCS_ENABLED=0`
- Dev Bearer auth accepts HMAC session tokens only (not the raw password)

## Reporting

Email security concerns to the maintainers via the GitHub security advisory flow on the public repo, or open a private report if available. Do not file public issues with exploit PoCs against live deployments.

## Operator checklist before exposure

1. Set unique `SPRUCER_SESSION_SECRET` and never keep example defaults
2. Use `SPRUCER_AUTH_MODE=oidc` (or `api_key`), not `dev`, off your laptop
3. Terminate TLS; set `SPRUCER_COOKIE_SECURE=1`
4. Restrict `SPRUCER_CORS_ORIGINS`
5. Point `SPRUCER_LLM_*` at a trusted endpoint; treat job-description text as untrusted input to the model
6. Keep backups of the SQLite/Postgres volume; they contain career PII
