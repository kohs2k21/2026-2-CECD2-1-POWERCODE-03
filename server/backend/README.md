# Server backend

This Node.js and Express service owns login, role checks, JSON user persistence, and the authenticated anomaly boundary. It is deployable from `server/backend` without importing `research/` or root-level application code.

## Runtime configuration

`JWT_SECRET` is required. The process exits during startup when it is missing; the secret is never logged. `PORT` defaults to `5001`, `JWT_EXPIRES_IN` defaults to `24h`, and `USER_DATA_PATH` defaults to `data/users.json` relative to the backend working directory. Set an absolute path when the data directory is mounted separately.

For PostgreSQL operations, put `DATABASE_URL` in the backend process environment or a local `server/backend/.env` file. The existing Express auth and anomaly endpoints do not yet use PostgreSQL; `GET /health/database` checks connectivity and returns only `OK` or `UNAVAILABLE`. Keep the URL and password out of Git and logs. When Express runs on the host, use a host-reachable database address; the Docker name `anomaly-postgres` resolves only inside `anomaly-net`.

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

## 작업·구성 API

`/api/jobs`는 변수 생성·Rule 추천·수동 학습·평가 작업을 영속 큐에 등록하고 상태를 조회한다. 추천 Rule의 채택은 불변 Rule 초안 버전을 생성한다. `/api/configurations`는 활성 구성과 적용·복원 요청을 관리한다. `/api/pipeline`은 출처·PROCESS 관측 검토·수동 라벨·판정·큐 지연·변수 결측률 변화를 조회한다. 기존 JWT/JSON 계정 권한을 그대로 사용한다. `DATABASE_URL`이 필요하고 migration 001~004가 선행되어야 한다. 실제 모델이 없는 상태에서는 모델 작업이 성공하지 않는다.

## Local commands

From `server/`:

```text
pnpm backend:dev
pnpm backend:build
pnpm backend:start
```

Set the local environment variables in the shell or an ignored local environment file before starting the process. Never commit that file.

피처·Rule 추천도 관리자 전용이다. 최신 ERD v03 LLM 요청/결과 감사 계약과 `005_research_runtime.sql`이 필요하다. v12 시간창 판정은 `/api/pipeline/windows`, 감시 설정·오류는 `/api/pipeline/window-monitors`에서 조회한다. 서버 실행 설정은 `../worker/DEPLOYMENT.md`를 따른다.
