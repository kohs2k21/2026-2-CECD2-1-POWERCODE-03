# Server runtime boundary

서버 배포는 [컨테이너 실행 절차](infra/DOCKER_DEPLOYMENT.md)를 따른다. `infra/compose.yaml`은 GPU 생성 서비스·역할별 CPU 워커·백엔드·nginx 프런트엔드를 분리한다.

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

2026-10-15 컨테이너 구조는 web/api/collector/detector/trainer/llm과 기존 PostgreSQL의 7개 기본 서비스로 전환했다. Collector는 제공된 Bearer 조회 API를 지속 호출해 안전한 응답 snapshot을 축적하며 응답 시간·오류·빈 응답·DB 저장 시간을 함께 기록한다. 진행 중 목록의 비어 있지 않은 T/P/M 형식은 미제공이므로 자동 탐지 입력 변환은 미확정 상태로 표시한다. 006/007 migration과 실행 설정은 [Docker 배포 문서](infra/DOCKER_DEPLOYMENT.md)를 따른다.

### 기존 CPU / PostgreSQL 간소 데모

원본을 보존하며 별도 데모 source에 초당 N개 Process를 보내 실제 CPU Isolation Forest 판정을 저장한다. 서버 입력은 `EC_TRANSACTION_MONITORING.csv`, `EC_PROCESS_MONITORING.csv`, `EC_MESSAGE_BODY.csv`다. 권한 문제·사전 검사·마이그레이션·재개·결과 확인은 [데모 실행 안내](infra/DEMO_REPLAY.md)를 따른다.

## API / CSV 공통 전체 프로세스

현재 권장 입력은 [입력 전환 안내](infra/SWITCHABLE_INPUTS.md)다. `009_switchable_inputs.sql` 적용 후 `/detection/pipeline`에서 API·네 CSV·일시정지를 선택한다. 같은 sourceId·피처·Rule·모델을 유지하며 Rule 추천/사용자 채택 과정도 포함한다. Message 모니터링 82,351행과 BODY 참조 579행을 구분한다. 실제 BODY 원문은 수집하지 않는다.
