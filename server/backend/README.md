# Server backend

This Node.js and Express service owns login, role checks, JSON user persistence, and the authenticated anomaly boundary. It is deployable from `server/backend` without importing `research/` or root-level application code.

## Runtime configuration

`JWT_SECRET` is required. The process exits during startup when it is missing; the secret is never logged. `PORT` defaults to `5001`, `JWT_EXPIRES_IN` defaults to `24h`, and `USER_DATA_PATH` defaults to `data/users.json` relative to the backend working directory. Set an absolute path when the data directory is mounted separately.

Accounts are seeded only from local environment inputs. Provide both values in each pair to enable a seed; existing users with the same email are preserved on later starts.

| Role | Environment pair |
|---|---|
| Administrator | `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD` |
| User | `SEED_USER_EMAIL` and `SEED_USER_PASSWORD` |

Seed passwords are hashed with `bcryptjs` before they are written to the JSON file. No credential values belong in the repository or in logs. If no seed pairs are supplied, the service starts with an empty user file and login returns an authentication error until a local seed is configured.

## Auth boundary

- `POST /api/auth/login` accepts `{ "email": "…", "password": "…" }` and returns a JWT plus the public user record.
- `GET /api/auth/me` requires `Authorization: Bearer <token>` and returns the current public user record.
- `POST /api/auth/register`, `/send-code`, and `/verify-code` return `410` with `code: "registration_disabled"`. They do not create users, store verification codes, or contact SMTP.
- `GET`, `PUT`, and `DELETE /api/users/*` require a valid admin token.

## Anomaly boundary

- `POST /api/anomaly/evaluate-risk` requires any authenticated user and evaluates validated raw log fields.
- `GET /api/anomaly/realtime-stream` requires any authenticated user. The SSE event is named `anomaly`, includes an `id`, and carries the versioned event shape in `server/contracts/anomaly.md`.
- `POST /api/anomaly/logs` requires an authenticated admin token. It broadcasts only raw fields plus the deterministic risk result; fabricated transaction or LLM fields are not added.

Bearer tokens are accepted through request headers. Tokens in query strings are not supported. The mock sender and its CSV remain development fixtures under this directory and are not read or transmitted by the service at startup.

## Local commands

From `server/`:

```text
pnpm backend:dev
pnpm backend:build
pnpm backend:start
```

Set the local environment variables in the shell or an ignored local environment file before starting the process. Never commit that file.
