# 기획서의 7개 서비스로 실행

API/CSV를 같은 전체 피처·Rule·모델 경로로 실행하는 최신 절차는 [SWITCHABLE_INPUTS.md](SWITCHABLE_INPUTS.md)를 따른다. 기본 Collector는 `collector.input_runner`이며 최초에는 일시정지 상태다.

기본 앱 컨테이너는 **web / api / collector / detector / trainer / llm** 6개다. **기존 PostgreSQL**을 포함하면 기획서의 7개 구성이다. PostgreSQL과 파일 저장소를 새로 만들지 않는다. [설계 근거](ARCHITECTURE_REVIEW.md)의 Notion 문서를 참고했다.

| 컨테이너 | 역할 | 기본 자원 한도 | 영속 데이터 |
|---|---|---|---|
| web | 화면, 동일 origin API·SSE 프록시 | GPU 없음 | 빌드된 정적 파일 |
| api | 인증, 작업·규칙·승인 요청, 저장 판정 조회·SSE | GPU 없음 | 기존 users.json 경로·DB |
| collector | API/CSV 수동 전환, 공통 관측 입고, API 응답 측정 | CPU 1 / RAM 512MB | DB: checkpoint·catalog·snapshots·측정 samples |
| detector | Rule 전용 / ML 전용 / 병렬, 임대 검증, 판정 확정 | CPU 2 / RAM 4GB | DB, 호스트 artifact 읽기 전용 |
| trainer | CPU IF 학습·평가, 공통 파생변수 계산 | CPU 4 / RAM 8GB | 호스트 artifact 쓰기, DB |
| llm | Base 하나 공유, 피처·Rule 추천 / 요청 기반 근거 설명 | GPU 0 한 장 / CPU 4 / RAM 48GB | Base·번들·artifact 읽기 전용, DB |
| 기존 PostgreSQL | 관측·4개 논리 작업 큐·판정·측정 | 기존 운영 설정 | 기존 저장소 유지 |

자원 한도는 초기 설정값이다. 서버 RAM·실측 사용량에 맞춰 변경한다. GPU 2·3의 기존 임베딩 작업은 관리 대상에 포함하지 않았다. Trainer는 LLM 준비 상태에 의존하지 않는다. 파생변수 계산은 공통 코드이고 독립 feature 컨테이너는 제거했다. monitor는 `monitoring`, 1분 window는 `windows`, bundle·migrate는 `tools` 선택 프로필이다.

## 서버에서 준비

Docker Engine·Compose v2·NVIDIA Container Toolkit은 호스트에 필요하다. Python·Node·pnpm은 이미지 안에 설치된다. CPU와 LLM 이미지 모두 Python 3.12를 사용한다.

```bash
cd ~/powercode-esb/server/infra
cp -n .env.example .env
chmod 600 .env
docker version
docker compose version
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
docker network inspect anomaly-net
```

`.env`에 기존 DB 네트워크·DATABASE_URL, JWT_SECRET, 사용자 저장 경로 `AUTH_DATA_DIR`, 산출물 경로 `ARTIFACT_ROOT_PATH`, Base 경로 `QWEN_BASE_PATH`, 번들 경로 `RESEARCH_BUNDLE_PATH`를 입력한다. 전부 서버의 실제 절대경로다. 기존 파일을 복사하거나 초기화하지 않고 그 경로를 그대로 bind mount한다. 신규 산출물 디렉터리만 미리 만들고 `tmp` 하위 폴더를 준비한다.

`PWC_CONTAINER_UID/GID`는 기존 배포 소유권과 맞춘다. rootful Docker에서는 호스트 파일 소유자 UID/GID, rootless Docker에서는 보통 컨테이너 `0:0`이 daemon 실행 계정에 대응한다. 기존 파일 전체를 재귀 chown하지 않는다. 쓰기 권한은 api의 users 디렉터리와 trainer의 artifact 디렉터리에 필요하다. llm·detector는 읽기 권한이 필요하다.

Base는 Qwen3.6-27B revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`을 사용한다. 자동 다운로드하지 않는다. snapshot symlink가 mount 외부 blobs를 가리키면 끊어지므로 독립 디렉터리 또는 유효한 mount를 준비한다. `DESIGN_MODEL_TOKEN`, `DETECTOR_DELIVERY_TOKEN`은 각각 32자 이상의 별도 내부 토큰이다.

## Collector 인증·연결

첨부된 API 호출 결과에 따라 구현했다. 서버에 등록된 Bearer 토큰을 재사용한다. 로그인/갱신 API는 제공되지 않았으므로 자동으로 발급하거나 갱신하지 않는다.

| API | 요청 | 응답 계약 |
|---|---|---|
| 인터페이스 목록 | POST `http://183.111.104.98:1112/configuration/interfaces/list` | `responseCode=0000`, `responseObject.content`, `totalPages` |
| 인터페이스 상세 | GET `http://183.111.104.98:1112/configuration/interfaces/{interfaceId}` | `responseObject` 객체, 조회 클라이언트 제공 |
| 진행 중 목록 | GET `http://183.111.104.98:1114/monitoring/transactions/in-progress` | `responseObject` 배열 |

목록 요청 body는 `{"body":{"searchConditions":[],"currentPage":0,"pageSize":10}}`이며 모든 요청에 `Authorization: Bearer {TOKEN}`을 넣는다. 수집 루프는 목록 페이지를 순회하고 진행 중 목록을 매번 조회한다. 상세 조회는 클라이언트 기능으로 제공하며 매 페이지마다 추가 호출하지 않는다.

```bash
# ACCESS_TOKEN이 현재 서버 shell에 등록되어 있는 경우. 토큰을 출력하지 않는다.
export COLLECTOR_ACCESS_TOKEN="$ACCESS_TOKEN"
```

Compose는 `COLLECTOR_ACCESS_TOKEN`, `ACCESS_TOKEN`, `TOKEN` 순으로 값을 재사용한다. export되지 않은 shell 변수는 위처럼 전달해야 한다. `COLLECTOR_SOURCE_KEY`는 재시작 후에도 같은 값으로 유지한다. 제공 주소는 HTTP이므로 `COLLECTOR_HTTP_HOST=183.111.104.98`에서 그 호스트만 명시적으로 허용한다. 토큰·SQL·adapter 접속 정보·원문 BODY는 snapshot에 저장하지 않는다. 토큰 파일을 쓰려면 [Collector 문서](../worker/collector/README.md)의 override를 사용한다.

현재 고객 API에서 **확인된 비어 있지 않은 로그 형식은 없다.** 지속 수집은 설정 버전과 허용 필드로 투영한 진행 중 응답 snapshot을 축적한다. `[]`도 조회 측정값은 저장하며 snapshot은 중복 제거한다. 원천 실행/revision/as-of/완료 로그 계약이 미확정인 응답을 T/P/M 실행 관측으로 임의 변환하거나 탐지 성공으로 표시하지 않는다. 화면의 `monitoring_log_contract_required`가 이를 나타낸다. 진행 중 캐시만으로 완료된 전체 트랜잭션 수집을 보장할 수 없다.

## 마이그레이션·권한

먼저 기존 worker를 중지하고 DB·파일 백업과 적용 이력을 확인한다. 기존 데이터는 유지한다. 신규 DB 순서는 다음과 같다. 이미 적용된 파일은 재실행하지 않는다.

```text
001_postgres_queues.sql
002_core_erd.sql
003_erd_v03_enums.sql
004_erd_v03_contracts.sql
003_rule_versions_immutable.sql
004_feature_pipeline.sql
005_research_runtime.sql
006_service_transition.sql
007_api_collection_metrics.sql
008_demo_replay.sql
009_switchable_inputs.sql
```

```bash
docker compose run --rm migrate -f /migrations/006_service_transition.sql
docker compose run --rm migrate -f /migrations/007_api_collection_metrics.sql
docker compose run --rm migrate -f /migrations/008_demo_replay.sql
docker compose run --rm migrate -f /migrations/009_switchable_inputs.sql
```

위 예시는 005까지 적용한 DB용이다. 006~009는 각각 한 번만 적용한다. 서비스 시작 시 자동 실행하지 않는다. 과거 training 큐의 추천 작업, feature 큐 backlog는 새 LLM/Detector의 복구 경로가 임대·시도 이력을 유지하며 처리한다. 이전 worker와 새 worker를 동시에 실행하지 않는다.

DBA는 `scripts/database-roles.sql`로 서비스별 NOLOGIN 역할을 준비하고 별도 login 계정에 해당 역할을 부여할 수 있다. 실제 비밀번호를 SQL/Git에 넣지 않는다. `API_DATABASE_URL / COLLECTOR_DATABASE_URL / DETECTOR_DATABASE_URL / TRAINER_DATABASE_URL / LLM_DATABASE_URL`을 분리해 지정한다. 공통 DATABASE_URL fallback은 로컬 검증용이다. BODY 원문 열 권한은 기본 역할에 부여하지 않으며 Collector는 참조 메타데이터만 쓸 수 있다. 추가 monitor/window는 별도 DBA 권한을 준비한다.

## 빌드·시작

```bash
docker compose --profile tools --profile monitoring --profile windows config --quiet
docker compose build api web collector llm
# 번들이 아직 없을 때만 출력 parent 디렉터리를 준비하고 export한다.
docker compose run --rm --no-deps bundle
docker compose up -d api web collector detector trainer llm
docker compose ps
docker compose logs --tail 100 collector detector trainer llm
```

web만 공개 포트 8080을 가진다. api·Detector 직접 전달·LLM 생성 HTTP는 내부 네트워크용이다. GPU Base 적재는 수 분 걸릴 수 있고 healthcheck start_period는 20분이다. LLM 오류가 있어도 Collector·Rule 경로·Trainer는 독립 실행된다. GPU Base 실제 적재/메모리와 고객 API 응답은 서버에서 확인해야 한다.

호스트 소스 빌드는 서버 검증용 절차다. 기획서의 GHCR digest release 배포는 별도 `origin/ci/21-ci-cd` 구현과 통합해야 한다. 그 브랜치의 현재 web/api 전용 allowlist 및 120초 준비 예산으로 worker/LLM까지 배포됐다고 간주하면 안 된다. 현재 작업은 GitHub 발행·서버 배포를 실행하지 않았다.

## 지속 수집하면서 실시간성 테스트

Collector는 기본 2초 목표 간격으로 겹치지 않게 요청한다. 느린 응답을 따라잡기 위한 병렬 폭주를 만들지 않는다. 페이지·snapshot 저장과 checkpoint는 함께 커밋한다. 수신→그 트랜잭션 commit 시간도 측정하고 각 호출의 sample은 별도로 append한다. sample 저장 직전 프로세스가 중단되면 해당 호출의 측정값이 누락될 수 있지만 이미 커밋한 데이터·checkpoint는 유지된다.

다른 터미널에서 추가 고객 API 호출 없이 Collector의 DB 측정값을 관찰한다:

```bash
docker compose exec collector python -m collector.report --watch-seconds 300 --every-seconds 5
```

SLO를 정한 뒤 명시적 기준으로 검사한다. 다음 숫자는 **사용 예시**이며 승인된 성능 기준이 아니다.

```bash
docker compose exec collector python -m collector.report \
  --window-minutes 10 --min-samples 100 --p95-limit-ms 1000 \
  --max-error-rate 0.01 --require-nonempty
```

검사 실패/샘플 부족이면 exit 2다. `--require-nonempty`는 진행 중 응답이 계속 비어 있는 상태를 데이터 도착 시험의 통과로 처리하지 않는다. HTTP 응답 p50/p95/p99, 오류율·빈 응답 비율, DB 저장 p95, 실제 조회 간격 p95를 출력한다. **원천 발생→수집 지연은 원천 시각 검증이 없어 NULL**이다. UI의 관리자 학습·탐지 운영 화면에서도 최근 10분 API 측정값과 수집 상태를 본다.

계속 쌓이는 samples/snapshots의 디스크 사용량과 보관 기간은 운영 정책으로 정한다. 이 구현은 과거 데이터를 자동 삭제하지 않는다. DB와 참조 artifact를 같은 백업 시점으로 보존한다.

## 판정·설명

새 구성은 모드·마감 시간·결합 정책을 명시한다. 병렬의 `any_anomaly`는 해당 구성 생성 시 사용자가 선택하며, severity를 하나의 임의 수치로 합산하지 않는다. 각 Rule의 severity 근거를 저장한다. ML 부족/실패 시 정상으로 바꾸지 않고 partial/unavailable을 구분한다. ML 전용은 Rule로 몰래 바꾸지 않는다.

저장 판정은 임대 token/current attempt를 검증한 트랜잭션에서 한 번 확정한다. 늦은 계산은 DB를 쓰지 않으며 API는 저장 판정을 SSE로 전달한다. 일반 사용자도 `/analysis/process-decisions`에서 판정과 요청한 근거 설명을 볼 수 있다. 설명은 saved facts ID·허용된 점검 항목만 출력 검증하며 SHAP나 확정 원인을 만들어 내지 않는다.

과거 정책 없는 구성은 호환용 parallel / 3000ms / any_anomaly로 실행하고 `legacy=true`를 기록한다. 과거 구성 자체를 수정하지 않는다. IF P99는 기존 train 내부 보정 방식이며 validation에서 임계값 선택으로 전환한 것으로 주장하지 않는다. 파생변수의 실행 범위는 현재 검증된 Process 행 AST이다.

추천 작업의 재사용은 같은 Idempotency-Key 재요청에 한정한다. 같은 기간으로 새로운 추천을 요청하면 새 snapshot을 고정하며, 기간 문자열만 같은 것을 데이터 근거까지 같다고 간주하지 않는다. 확정 판정 설명은 실제 saved evidence/hash가 같을 때 사용자별 재사용한다.

## API 미사용 실시간 데모

CSV/SQL 입력을 초당 N개 P로 실제 Detector에 전달하는 CPU 구성과 서버의 확정 CSV 파일명은 [DEMO_REPLAY.md](DEMO_REPLAY.md)를 따른다. `compose.demo.yaml`은 별도 실행 파일이며 GPU/Base/고객 API 토큰 없이 사용할 수 있다.

## 502와 판정 unavailable 확인

`502`가 계속되면 `docker compose ps`와 `docker compose logs --since 10m --tail 80 web api`를 확인한다. API 중지·재시작·연결 실패를 구분해야 하며 HTTP 상태만으로 원인을 확정하지 않는다. 컨테이너 재생성으로 API IP가 바뀌는 경우를 위해 Nginx는 Docker DNS를 5초 간격의 유효 기간으로 다시 조회한다. 주소 전환 중에는 일시적인 실패가 가능하다.

```bash
# API 직접 연결과 프록시를 통한 연결을 각각 확인한다.
docker compose exec web wget -qO- http://api:5001/health/database
docker compose exec web wget -qO- http://127.0.0.1/health/database
```

프록시 수정은 `docker compose build web` 후 `docker compose up -d --no-deps web`으로 반영한다. API/Worker/LLM 재시작이나 DB 마이그레이션은 필요하지 않다.

`unavailable`은 판정에 필요한 경로가 완료되지 않았음을 뜻한다. 운영 화면과 저장 판정 화면은 기존 evidence의 `deadline_exceeded`, `worker_busy`, 피처 부족·모델 파일 검증 실패 등의 사유를 표시한다. 과거 사유가 없는 결과는 추정하지 않으며, 이미 확정된 판정은 수정하지 않는다. 502와 unavailable이 같은 원인이라고 가정하지 않는다.

로컬 Docker 회귀 검증은 `python3 server/infra/tests/integration-proxy-recreation.py`로 실행한다. 사전에 로컬 `powercode-web:latest`, `powercode-worker:local` 이미지를 준비한다. `PROXY_TEST_WEB_IMAGE`, `PROXY_TEST_API_IMAGE`로 다른 로컬 이미지를 지정할 수 있다. 격리된 네트워크·임시 HTTP 서버를 만들고, API 주소를 강제로 바꾼 뒤 프록시 자동 복구와 경로·쿼리·인증·POST·SSE 전달을 확인하고 정리한다. 실제 DB/GPU/서버에는 연결하지 않는다.
