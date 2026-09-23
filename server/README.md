# Server runtime boundary

`server/` is the deployable runtime boundary. The two current applications are `server/frontend` and `server/backend`; they share request and event definitions through `server/contracts/`. Runtime code does not import `research/` or root-level application code.

## Run locally

From `server/`, use the existing package scripts:

```text
pnpm frontend:dev
pnpm frontend:build
pnpm backend:dev
pnpm backend:build
pnpm backend:start
pnpm test
```

Install each deployable package from its own directory before running it:

```text
cd server/backend
npm ci --ignore-scripts
cd ../frontend
pnpm install --frozen-lockfile
```

The backend listens on `PORT` (default `5001`) and requires `JWT_SECRET`. Optional backend settings are `JWT_EXPIRES_IN`, `USER_DATA_PATH`, `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD`, `SEED_USER_EMAIL`, and `SEED_USER_PASSWORD`.

When running frontend and backend on different ports, set `VITE_API_BASE_URL` to the backend origin before starting Vite or building the frontend. There is no default Vite API proxy. For local PowerShell development, run `$env:VITE_API_BASE_URL = 'http://localhost:5001'` in the frontend terminal, then `pnpm dev` from `server/frontend`. Omit this variable only when your deployment routes `/api` from the frontend's origin to the backend. This frontend variable is embedded at build time and must never contain a secret.

Set values through the local shell or a deployment secret manager. This repository contains no credential values and does not require a checked-in `.env` file. Do not print secrets or create committed `.env` files.

`pnpm test` from `server/` runs both package test suites after their dependencies are installed. You can also run `npm test` from `server/backend` or `pnpm test` from `server/frontend`. Tests use temporary local accounts and storage; they do not require production credentials. CI execution is not configured yet.

The backend is the single owner of the JSON user store. `USER_DATA_PATH` selects its persistent file, and configured seed accounts are bcrypt-hashed and added only when their email is absent. The frontend does not create or mutate that file.

## Current API scope

Authentication and the anomaly risk/SSE boundary are implemented under `server/backend`; their stable shapes are documented in [`contracts/auth.md`](contracts/auth.md) and [`contracts/anomaly.md`](contracts/anomaly.md). Public registration and email verification are disabled. Anomaly ingest requires an admin Bearer token, and stream clients require authentication. The C5 realtime panel uses a Bearer `fetch` SSE reader, validates fragmented frames against the contract, cleans up with `AbortController`, retries at most three times, and deduplicates by `eventId`.

The dashboard keeps explicit mock surfaces for home widgets, analysis list/detail/status, feature and response-code dictionaries, settings, and admin model/system controls. `MockDataNotice` identifies those surfaces, while `RealtimeAnomalyPanel` is the real authenticated SSE surface and remains separate from the KSJ detail mock. The client no longer calls unsupported `/api/process-anomalies*` routes; list/detail/status, widget, settings, and admin model/config APIs remain intentional scope boundaries tracked in `docs/mock-inventory.md`. Mock failures are not silently converted into API success.

`server/worker/` and `server/infra/` remain extension boundaries. Research notebooks and evaluation artifacts stay under `research/` and are not runtime dependencies.
