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

The analysis inbox and detail view use authenticated SSE events only (up to 50 events per mounted session); historical queries, workflow status persistence, hierarchy details, and LLM results are not implemented. Home and model/system administration retain mock data. Settings display the authenticated profile and disable unsupported operations. UI source badges have been removed; current implementation boundaries are described above. API errors and empty responses are never replaced with mock success.

`server/worker/`에는 CSV Collector, 변수·학습·탐지 워커와 모니터의 DB 경로가 구현되어 있다. 최신 ERD와 `005_research_runtime.sql`까지 필요하다. P 수치 변수 추천과 Process 행 Isolation Forest 학습·실시간 점수 계산은 `server/worker/model_adapters.py`에 연결되어 있다. 피처 v5·Rule v2 생성 서비스와 최신 v12 1분 시간창 탐지를 연결했다. 정확한 마이그레이션·서버 설정은 `server/worker/DEPLOYMENT.md`를 따른다. 관리자 화면의 학습·탐지 운영 탭은 실데이터 작업·평가·판정 조회를 사용하고 기존 모델 시뮬레이션 탭은 예시 화면으로 남아 있다. Research 노트북은 런타임 의존성이 아니다.
