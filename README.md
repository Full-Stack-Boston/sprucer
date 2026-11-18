# Sprucer

Truth-first application materials. Keep a knowledge bank of facts you will defend, ingest a job description, generate drafts via any OpenAI-compatible chat endpoint, track applications, and approve documents before you send them.

Sprucer does **not** invent employers, dates, degrees, or metrics. Generations save as drafts until you explicitly approve them.

> Alpha software. Safe for local / trusted-network use. Read [SECURITY.md](SECURITY.md) before exposing a port.

## Features

- **Knowledge bank:** structured career truth (experience, projects, metrics, blurbs, signature stories, never-claim) plus profile, voice prefs, and accepted career-fit
- **Career-fit interview:** confirm or inquire, then draft ranked industries + titles (live LLM / BYOK); accept into `careerFit` (see [docs/FIT_INTERVIEW.md](docs/FIT_INTERVIEW.md))
- **JD ingest:** paste, URL fetch (SSRF-guarded, with Workday CXS JSON + HTML hero/`job-description` extraction and company/title/location hints), or upload (`.txt`/`.md`/`.html`/`.pdf`/`.docx`/`.doc`)
- **Generate:** cover letter, resume sections, email, interview prep, LinkedIn, custom types, grounded in your vault
- **Application tracker:** per-job status, notes, generation history
- **Document review:** draft → approve preview flow
- **Themes:** light / neutral / dark (topbar select)

## Quick start (Docker)

```bash
cp .env.example .env
# set SPRUCER_DEV_PASSWORD and SPRUCER_SESSION_SECRET to unique values
docker compose up --build
```

Open http://127.0.0.1:3737. Log in with `SPRUCER_DEV_PASSWORD`.

More recipes (Postgres, OIDC, Authentik-oriented) live under [`deploy/`](deploy/README.md). Compose files include inline `#` comments.

## Self-contained demo (no external calls or costs)

```bash
docker compose -f deploy/compose.demo.yml up --build
```

Open http://127.0.0.1:3737. A start gate asks each visitor how they want to try Sprucer, then unlocks the full workflow:

- **Explore the demo (offline).** Drafts are written by a built-in generator grounded in the vault. No LLM provider, no API keys, no cost, and nothing leaves the containers. Outbound job-URL ingest is disabled (paste/upload only).
- **Bring your own AI.** With `SPRUCER_BYOK_ENABLED=1`, the gate prompts for ephemeral OpenAI-compatible credentials and unlocks real generation. The key stays in the browser and is proxied per request, never stored server-side, and validated against SSRF (public https only, optional host allowlist). See [`deploy/env.demo.example`](deploy/env.demo.example).
- **Per-visitor sandbox.** Each browser gets its own temporary, isolated vault, auto-seeded with a sample knowledge bank + application and auto-expired after `SPRUCER_DEMO_TTL_HOURS`. The SQLite file lives on tmpfs, so a restart is a clean slate. A status strip shows the active backend and lets visitors change setup.

## Quick start (local Python + Next)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# API (SQLite default). Binds SPRUCER_HOST from .env (default 127.0.0.1)
sprucer-brain

# In another shell: web UI
cd web && npm install && npm run dev
```

Open http://localhost:3737. API defaults to http://127.0.0.1:8787 (the web UI proxies `/v1`).

Optional helper that starts mock LLM + brain + web if they are not already running:

```bash
./scripts/ensure-dev-stack.sh
```

## Configuration

| Variable | Purpose |
|----------|---------|
| `SPRUCER_DATABASE_URL` | Default `sqlite:///./data/sprucer.db`. Postgres: `postgresql+psycopg://user:pass@host/db` |
| `SPRUCER_AUTH_MODE` | `dev` (local only) \| `none` \| `oidc` \| `api_key` \| `demo` |
| `SPRUCER_DEV_PASSWORD` | Shared password when `auth_mode=dev` |
| `SPRUCER_SESSION_SECRET` | HMAC secret for session cookies. **Change this.** |
| `SPRUCER_ALLOW_INSECURE_DEV` | `1` to allow `dev`/`none` while binding `0.0.0.0` (lab/Docker only) |
| `SPRUCER_COOKIE_SECURE` | `1` to set the Secure flag (HTTPS) |
| `SPRUCER_DOCS_ENABLED` | `0` to disable `/docs` and `/redoc` |
| `SPRUCER_LLM_URL` | OpenAI-compatible base (e.g. `http://127.0.0.1:4000/v1`) |
| `SPRUCER_LLM_API_KEY` | Bearer token for the LLM endpoint |
| `SPRUCER_LLM_MODEL` | Model id (e.g. `qwen-coder`, `gpt-4o-mini`) |
| `SPRUCER_DEMO` | `1` for a self-contained demo: offline generator, per-session vaults |
| `SPRUCER_DEMO_TTL_HOURS` | Sweep stale per-session demo vaults older than this (default `24`) |
| `SPRUCER_INGEST_URL_ENABLED` | `0` to disable outbound job-URL ingest (paste/upload only) |
| `SPRUCER_BYOK_ENABLED` | `1` to let a visitor proxy generation through their own provider |
| `SPRUCER_BYOK_ALLOWED_HOSTS` | Optional provider host allowlist for BYO keys (empty = any public https) |
| `SPRUCER_OIDC_*` | Issuer / client / secret / redirect when `auth_mode=oidc` |
| `SPRUCER_CORS_ORIGINS` | Comma-separated browser origins |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/AUTH.md](docs/AUTH.md), [docs/THEMING.md](docs/THEMING.md), [docs/TRUTH_SCHEMA.md](docs/TRUTH_SCHEMA.md), [deploy/README.md](deploy/README.md), and [SECURITY.md](SECURITY.md).

## Hard rules (product)

- Ground generations only in career truth + the job description
- Respect `neverClaim`
- Prefer keyboard punctuation in operator-facing copy and generated drafts
- Confirm before delete or approve

## Development

```bash
pytest -q --cov=sprucer --cov-fail-under=80
ruff check src tests
```

CI also runs Semgrep (`p/python`, severity ERROR), OSV on the web lockfile, and a Next.js production build.

## Repo layout

```text
src/sprucer/          # brain: adapters, domain, FastAPI
web/                  # Next.js UI
deploy/               # Commented compose/env samples (OIDC, Authentik, Postgres)
fixtures/             # synthetic truth + sample JD (no real PII)
docs/
tests/
compose.yml           # default local Docker stack
```

## Status

Public alpha toward v0. Local Docker and lab installs are the supported paths. Do not commit operator truth, `.env`, or private hostnames.

## License

MIT. See [LICENSE](LICENSE).
