# Validation report

Date: 2026-09-23
Scope: C1 structure relocation through C5 backend, authentication, and realtime frontend work. The checks below were reported by QA; no production services, credentials, email, or raw fixture data were used.

## QA checks

- In a temporary copy containing tracked `server/` files and the two new test harnesses, a fresh backend `npm install` completed, followed by `npm run build` and `node tests/integration.mjs` from `server/backend`. The integration checks passed authentication, role enforcement, seed persistence across restarts, registration/verification disablement, and authenticated SSE behavior.
- From `server/frontend`, `pnpm install --frozen-lockfile`, `pnpm exec tsc -b`, and `pnpm build` passed.
- From `server/frontend`, `node ../backend/node_modules/tsx/dist/cli.mjs tests/realtime-stream.mjs` passed Bearer/Accept headers, fragmented frames, optional fields, invalid-frame handling, rejection before fetching when the token is missing, and abort behavior.
- The frontend build retains Vite's existing large-chunk warning (7,430 modules and about 1.079 MB JavaScript output); it does not fail the build.
- Direct React hook lifecycle/reconnect/deduplication automation was not run because no renderer was available; those paths received source review. New temporary tests are not recorded as passing unless listed above.

## Scope limits

The backend contract covers login, Bearer `/me`, authenticated risk evaluation/SSE, and admin-only ingest. The dashboard uses an authenticated realtime panel alongside explicit mock services for list/detail/status, widget, settings, model, and system surfaces; `docs/mock-inventory.md` records those intentional boundaries and replacement conditions.

The backend stores users in the JSON path selected by `USER_DATA_PATH` and creates only locally configured seed accounts. Research files are not runtime dependencies, and no external email or production API calls are part of these checks.
