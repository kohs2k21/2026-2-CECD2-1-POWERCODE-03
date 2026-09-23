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
```

Install each deployable package from its own directory before running it:

```text
cd server/backend
npm ci --ignore-scripts
cd ../frontend
pnpm install --frozen-lockfile
```

The frontend uses Vite and accepts an optional `VITE_API_BASE_URL`. The backend listens on `PORT` (default `5001`) and requires `JWT_SECRET`. Optional backend settings are `JWT_EXPIRES_IN`, `USER_DATA_PATH`, `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD`, `SEED_USER_EMAIL`, and `SEED_USER_PASSWORD`.

Set values through the local shell or a deployment secret manager. This repository contains no credential values and does not require a checked-in `.env` file. Do not print secrets or create committed `.env` files.

The backend is the single owner of the JSON user store. `USER_DATA_PATH` selects its persistent file, and configured seed accounts are bcrypt-hashed and added only when their email is absent. The frontend does not create or mutate that file.

## Current API scope

Authentication and the anomaly risk/SSE boundary are implemented under `server/backend`; their stable shapes are documented in [`contracts/auth.md`](contracts/auth.md) and [`contracts/anomaly.md`](contracts/anomaly.md). Public registration and email verification are disabled. Anomaly ingest requires an admin Bearer token, and stream clients require authentication. The C5 realtime panel uses a Bearer `fetch` SSE reader, validates fragmented frames against the contract, cleans up with `AbortController`, retries at most three times, and deduplicates by `eventId`.

The dashboard keeps explicit mock surfaces for home widgets, analysis list/detail/status, feature and response-code dictionaries, settings, and admin model/system controls. `MockDataNotice` identifies those surfaces, while `RealtimeAnomalyPanel` is the real authenticated SSE surface and remains separate from the KSJ detail mock. The client no longer calls unsupported `/api/process-anomalies*` routes; list/detail/status, widget, settings, and admin model/config APIs remain intentional scope boundaries tracked in `docs/mock-inventory.md`. Mock failures are not silently converted into API success.

`server/worker/` and `server/infra/` remain extension boundaries. Research notebooks and evaluation artifacts stay under `research/` and are not runtime dependencies.
