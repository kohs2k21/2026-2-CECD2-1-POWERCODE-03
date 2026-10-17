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

## 운영·비용·후보 보고 API

- 공통: 기존 JWT 인증·관리자 권한 필수, migration 001~009·기존 API DB 조회 권한 사용. 신규 테이블·성능 자동 승인 기준 없음
- `GET /api/pipeline/reports/operational`: `sourceId`, `configVersionId`, UTC ISO `from`, `until`, IANA `timezone`, `scope=live|replay` 필수. 반개구간 `[from, until)`, 최대 31×24시간. 일별 `limit` 1~100·기본 31, `offset` 0~10000
- 출처·구성 실제 연결 검증, 알 수 없는 필터·배열·잘못된 날짜·시간대·UUID 거절. SQL 값 매개변수 바인딩·읽기 전용 REPEATABLE READ 스냅샷 사용
- PROCESS 식별: source별 `execution_id`. 보고 cutoff=`until` 전에 저장된 최신 revision 선택(`revision_no`, `persisted_at`, `id` 내림차순), 해당 관측의 동일 구성·cutoff 전 최신 판정 선택(`decided_at`, `id` 내림차순). 최신 revision에 판정이 없으면 이전 revision 판정으로 대체 금지
- 판정 모집단: 선택한 판정의 `decided_at`이 기간 안인 PROCESS. 보고 시간대의 **판정일** 기준 일별 분류. 수집 발생 시각·수집일·SSE 전송 시각으로 판정일 대체 금지. 다른 기간의 판정은 비율 분모에서 제외
- `processCount`: 판정 모집단, `alarmProcessCount`: 명시적 이상 `true`인 complete/partial 판정, `alarmRatio`: 이상/판정 가능 PROCESS, `unavailableRate`: 판정 불가/전체 판정 모집단. complete+명시적 `false`만 비경보, partial+`true`는 확인된 이상 보존. unavailable·누락/알 수 없는 이상 값은 정상으로 처리 금지
- `failedProcessCount`: 선택 판정의 Rule 또는 ML 구성요소가 `failed`인 PROCESS, 업무상 F 상태의 건수와 별개. 판정 없이 실패한 작업은 `queue.failedJobsInPeriod`로 별도 제공
- `pendingArrivalCount`: 최신 revision에 cutoff 전 판정이 없고 해당 revision `persisted_at`이 기간 안인 고유 PROCESS. 도착/저장일 기준 별도 모집단이며 경보/미판정률 분모에서 제외
- 입력 범위: `data_input_origins.mode=api`→live, csv→replay 우선. 과거 source의 `customer-api`→live, `csv-replay-v1`/`demo-replay`→replay. 미확인 입력 범위·누락 execution identity는 집계 제외, 기간 내 제외 관측 수를 `excluded`에 제공. live/replay 합산 없음
- `comparisons.hrMl`/`srMl`: 같은 선택 관측·구성의 명시적 `checkers` 두 경로 모두 succeeded+boolean일 때만 공통/Rule만/ML만/비경보 집계. 비교 가능/불가/전체·coverage 별도. 과거 checker 근거 누락·SR 미연결은 null+사유. 정답 없는 교차표를 정확도/오탐률로 표시 금지
- 모든 지표: `{status,value,unit,numerator,denominator,coverage,reason}`. 대상 0·측정 불가 `value=null`+사유. 실제 관측한 0만 숫자 0. 분모가 있는 경보율과 교차표는 각 분모/coverage 명시. 외부 알림 전송 미계측은 `externalNotificationCount=null`, ESB 원천 시계 정합성 미검증은 종단 지연 null+사유
- Detector 비용: 선택한 고유 판정의 `evidence.telemetry`에서 `execution-cost-v1`·measured·기대 단위·비음수 수치만 집계. 시간 총합/P50/P95·샘플/대상/coverage 제공. 메모리는 프로세스 현재 RSS 샘플의 분포, peak/메모리 합산 아님. 처리량은 저장된 건당 처리율 샘플의 P50/P95, 합산 처리율 아님. 원본 조건은 `/api/pipeline/decisions`의 evidence에 보존
- `queue`: scoped 관측(cutoff 전 저장)에 연결된 현재 detect 작업의 대기·실행 수와 최장 경과 시간. 조회 시각 `observedAt` 별도, 과거 시점 큐 재구성 아님. 작업 실패 수만 `finished_at` 기간 기준, 재시도/SSE는 고유 판정 수에 추가되지 않음
- `GET /api/pipeline/reports/execution-cost`: 같은 필수 필터, `scope=offline`. 동일 source·입력/결과 구성의 train/evaluate 완료 작업을 `finished_at` 기간으로 집계. 실행 시간·RSS·처리량은 최종 완료 시도에서 저장한 배치별 샘플, 배치 P50/P95는 배치 샘플 간 분위수. 개별 행 분위수 병합 금지. `conditions`에 배치 크기/동시성/warm-cold/측정 주기/자원 제한/메모리 관측 범위 원문 보존. 정적 평가와 운영/재생 판정 합산 없음
- `GET /api/pipeline/reports/candidates?sourceId=<uuid>&limit=31[&cursor=<nextCursor>]`: 구성·학습 snapshot/선정 정책·학습 요약·최신 평가 원문 조회. `pagination.hasMore/nextCursor`로 과거 버전 탐색, 생성 시각 microsecond·UUID 내림차순 커서. 이전 후보 결과·envelope 재계산/덮어쓰기 없음. 특정 과거 평가/적용 이력은 기존 작업·구성 API 조회 사용
- 수동 선택: 성공한 학습/평가만으로 활성 포인터 변경 없음. 새 상태 HR 정책은 source/version/매핑 엄격 검증, 기존 모델/fit/hash/스키마·판정 가용성 적용 차단 유지

로컬 검증: `npm test`(단위 집계·필터·권한/HTTP·기존 회귀). 실제 PostgreSQL SQL/워커/API 통합 검증은 별도 격리 DB가 필요하며 `server/worker/tests/integration_metrics_service.py`에서 수행한다. fixture HTTP 시험만으로 DB/모델 통합 완료를 주장하지 않는다.
