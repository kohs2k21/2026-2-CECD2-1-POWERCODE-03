# 연구 모델 연결 검증 기록

2026-10-14 로컬 검증. 실제 GPU 서버에 접속하지 않았다.

| 범위 | 결과 |
|---|---|
| 워커 단위 시험 | 20개 통과: 기존 수집/임대, IF, 생성 AST·타입·단위·버전, 결측·0 분모, 시간창 빈 분·revision·늦은 입력 |
| 백엔드 빌드 | 통과 |
| 프런트엔드 빌드·전체 시험 | 통과. 기존 bundle 크기 경고 있음 |
| 작업 API 통합 | 통과: 관리자 권한, idempotency, v03 LLM spec, Rule 근거 보존, 시간창 조회·권한 |
| 변수 추천 경계 통합 | 통과: 고정 스냅샷, 현재 임대, 결과 감사 기록 |
| 기존 실제 IF 통합 | 통과: 학습·평가·구성 적용·새 관측 판정 |
| 새 전체 연구 연결 통합 | 통과: 합성 P 1,000행, 생성 HTTP 계약, 파생변수 계산, grounded SR, 실제 IF, 평가·적용·판정 |
| 실제 v12 IF/K-means | 두 저장 모델 hash 검사·적재·추론 통과. 빈 분을 포함한 8분 grid에서 각각 8개 판정 |
| 모델 캐시 초기화 후 재실행 | 동일 DB 이력을 복원하고 새 중복 판정 0개 |
| 최신 DB 계약 | 기존 001/002, ERD v03, Rule/Feature 보호, 005 연구 연결 SQL을 새 PostgreSQL 17 DB에 모두 적용 |

생성 모델의 GPU 실행은 수행하지 않았다. 통합 시험에서는 tests/research_fixture.py의 명시적 계약 시험 응답을 localhost 생성 서비스에 사용한다. 이 응답은 Qwen의 실제 생성 품질을 의미하지 않는다. IF 학습과 v12 추론에는 실제 sklearn 코드·저장 모델을 사용한다.

운영 데이터의 탐지 정확도·전체 지연·GPU 메모리·새 Qwen 응답 의미 검토·ESB API 수집은 이 로컬 시험 결과에 포함하지 않는다. 관계·as-of 계약이 확인되지 않은 T/M 집계는 현재 Process 행 실행기로 변환하지 않고 보류한다.

재현은 DEPLOYMENT.md의 전체 migration을 적용한 독립 localhost DB와 연구 실행 번들이 필요하다.

```bash
cd server/worker
python -m unittest discover -s tests -p 'test_*.py' -v
# 아래 DB는 이름까지 전용 시험 DB여야 한다.
DATABASE_URL='postgresql://TEST_USER:TEST_PASSWORD@127.0.0.1:TEST_PORT/qa_research_pipeline' \
RESEARCH_BUNDLE_ROOT='/path/to/generated/research-runtime' \
python tests/integration_research_pipeline.py
```

각 통합 시험은 서로 다른 DB에서 실행한다. training 큐를 공유하면 시험이 다른 작업을 먼저 소비할 수 있다.

## Docker 구성 추가 검증

2026-10-14 로컬 Docker Desktop의 Linux ARM64 환경에서 확인했다. 서버에는 접속하지 않았다.

| 범위 | 결과 |
|---|---|
| Compose 전체 profiles 구문 검사 | 통과, 실제 비밀값 없이 `config --quiet` 사용 |
| 백엔드·nginx 프런트엔드·CPU 워커 이미지 빌드 | 통과. Node 24 / pnpm 11.1.2 / Python 3.12 |
| CPU 이미지 단위 시험 | 22개 통과. 지정한 내부 HTTP 호스트만 허용하는 경계 포함 |
| 컨테이너 DB 마이그레이션 | PostgreSQL 17 독립 DB에 전체 순서 적용. Compose migrate 도구 실행 통과 |
| 공유 artifacts 볼륨 | UID 10001 Trainer 쓰기·Detector 읽기 전용 mount에서 읽기 통과 |
| nginx·백엔드 | SPA 경로·DB health·seed 관리자 로그인·인증된 jobs API 전달 통과 |
| CPU 이미지 연구 연결 통합 | 계약 시험 생성 응답→파생변수→Rule→실제 IF 학습·평가·적용·판정 통과 |
| CPU 이미지 실제 v12 | IF/K-means 각각 8분 판정, 빈 분·재실행 중복 방지 통과 |

시험 컨테이너·DB·볼륨·네트워크는 종료 후 제거했다. 기존 `frontend/pnpm-workspace.yaml`의 pnpm 11 esbuild 허용 설정을 Docker 설치 단계에 함께 복사했고 Docker의 데이터 제외 규칙은 런타임 계정 디렉터리에만 적용해 프런트엔드 소스의 data 디렉터리를 보존한다.

전환 전 GPU 이미지는 Python 3.10과 연구의 고정 GPU 요구사항으로 구성했다. 아래 2026-10-15 전환에서는 공통 worker 의존성에 맞춰 Python 3.12로 변경했다. GPU 이미지의 전체 빌드, NVIDIA runtime, Linux AMD64 서버에서의 이미지 실행, 실제 Qwen Base 적재·생성은 이 로컬 검증에 포함되지 않는다. 서버에서 `infra/DOCKER_DEPLOYMENT.md`의 GPU 확인·health 절차로 확인한다.

## 기획서 7개 서비스 전환·API 지속 축적 검증 (2026-10-15)

서버나 실제 고객 API에는 접속하지 않았다. 다음은 로컬 PostgreSQL 17 격리 DB와 명시적 생성/고객 API fixture로 검증한 결과다.

| 검증 | 결과 |
|---|---|
| 001→002→v03 enum/contracts→rule immutable→feature→005→006→007 | 새 격리 DB 두 개에 적용 통과 |
| worker 단위 계약 | 34개: 실제 IF train/score 기존 시험, 지정 HTTP host, Bearer POST 계약, projection, 병렬 barrier, 별도 Rule/ML 자원, deadline·늦은 결과 고정, 미선택 경로, 빈 Rule 미확인, 설명 fabricated claim 차단 |
| 연구 연결 | fixture 생성→파생변수→grounded SR→실제 CPU IF→평가→적용→v03 판정 통과; 실제 v12 IF/K-means 16개 시간창·재실행 중복 없음 |
| 서비스 전환 | Rule 전용 후보·평가·판정, live feature materialization, 모델 없는 parallel에서 Rule 이상 유지, Trainer/LLM 큐 격리, 설명 result lineage, stale lease 결과 차단, 판정/event 한 번 확정 통과. Linux CPU 컨테이너의 실제 인증 HTTP 직접 전달을 두 번 재생해 판정 한 번 저장도 통과 |
| 복구 | catalog page 재시작, source revision conflict의 snapshot/checkpoint rollback, 활성 pointer가 없어도 pinned job 완료, crashed/max-attempt job terminal failed 통과 |
| API 축적·동시 측정 | 모형 API 3회 반복 × 2 endpoint = 6개 sample. empty/nonempty/auth-failure 구분, 중복 snapshot 제거, BODY/토큰 제외, HTTP·DB commit 측정, SLO failure 판정 통과 |
| API/SSE | 일반 사용자 설명 요청·캐시·사용자별 격리, v03 train 등록/실행, 명시적 mode gate, 저장 partial/evidence SSE·resume cursor 범위 통과 |
| 제한 DB 역할 | LLM 결과 저장, Collector snapshot 쓰기, API 측정 읽기, BODY SELECT 거부 통과 |
| frontend | 전체 기존 회귀 suite 및 fragmented CRLF/Bearer/cursor 판정 stream 시험 통과, build 통과 |
| Compose | 모든 기본/선택 profile 구문, API·web·CPU 이미지 빌드, host auth bind·nginx API proxy·DB health·Collector 상태 조회 통과 |

GPU 이미지는 서비스 전환 이후 Python 3.12 + CPU/GPU 공통 의존성으로 변경했다. 실제 NVIDIA/Base 적재·생성 품질·4090 메모리·Linux AMD64 서버 실행은 미검증이다. 고객 API 성능 수치는 모형 시험을 운영 실시간성 결과로 바꾸지 않는다. source occurrence latency는 미검증으로 NULL이며, 진행 중 cache만으로 완료 로그 coverage를 보장하지 않는다. GitHub CI runtime gate는 추가했지만 GitHub에서 실행한 결과와 GHCR/CD release 발행은 이번 로컬 결과에 포함되지 않는다.

운영 실행·측정 명령은 [DOCKER_DEPLOYMENT.md](../infra/DOCKER_DEPLOYMENT.md), API 계약/알려진 mapping 경계는 [Collector README](collector/README.md)를 따른다. 이번 시험의 DB·컨테이너·네트워크는 검증 뒤 정리한다. 계정 파일·산출물·운영 DB는 변경하지 않았다.

## 2026-10-15: CSV/SQL 실시간 탐지 데모

사용자 확정 파일명: `EC_TRANSACTION_MONITORING.csv`, `EC_PROCESS_MONITORING.csv`, `EC_MESSAGE_BODY.csv`. 서버 경로는 `/data/gst-dongguk/powercode-esb/tmp`이며, 실제 서버 연결/파일 읽기는 수행하지 않았다.

| 검증 | 결과 |
|---|---|
| Worker 단위 테스트 | 37개 통과. 재생 속도 제한/느린 쓰기 후 밀린 데이터 일괄 전송 방지, 원문 제외, CSV 실행 그룹 포함 |
| 로컬 PostgreSQL 17 신규 DB 마이그레이션 | 기존 전체 순서 + `008_demo_replay.sql` 적용 성공 |
| CSV 데모 실제 CPU 학습 | 샘플 P 1,012건 캡처, 기준 1,000건으로 실제 RobustScaler + Isolation Forest 학습. T/M 각 1건은 허용 메타데이터만 별도 저장 |
| 재생 이력 | 1차 pass 1,012건 완료, 2회 반복 pass는 1,020건까지 검사. 총 2,032개 observation/job가 모두 고유함. 중단·재개, 완료 key 재실행 시 중복 방지, 설정 변경 거부 확인 |
| 실제 Detector 판정 | 위 테스트의 선택한 20건 모두 ML 성공/Rule skipped, 판정과 이벤트 각 20개 저장, 데모 표시 및 지연 통계 확인. 나머지를 모두 탐지한 부하 시험은 아님 |
| SQL 원본 사용 | 원본 1,000건 복사 및 실제 CPU 학습 성공. 원본 행/전역 활성 구성 유지, 데모 구성에 대한 운영 적용 이력 없음 |
| Python 3.12 컨테이너 + 역할 분리 | 초기 `001/002`만 있는 별도 원본 DB도 읽기 성공. `powercode_demo`로 1,000건 실제 학습/재생, `powercode_detector`로 3/3 판정. rate=10에서 전송 간격 90ms 이상, Sender의 활성 포인터 UPDATE 권한 없음 확인 |
| CPU Docker 이미지 / 독립 Compose | worker 이미지 빌드 성공. GPU/Base/API Token 없이 `compose.demo.yaml` 전체 프로필 설정 검사 통과 |
| Frontend | 전체 테스트 및 타입 검사/production build 통과. 기존 bundle 크기 경고는 남음 |

DB 전용 역할 검증 중 파생변수 저장 권한 및 manifest 검증의 `FOR SHARE` 행 잠금에 필요한 열 권한 누락을 발견해 역할 SQL에 보완하고 재검증했다. 사용한 DB/컨테이너는 로컬 테스트 전용이다. 생성 샘플 결과는 실제 ESB 데이터의 탐지 정확도나 API 응답 실시간성 측정이 아니다. 20건 선택 판정은 전송 후 수동으로 큐를 처리했으므로 해당 P50/P95를 정상 상주 Detector의 성능 수치로 해석하면 안 된다.

재현 테스트: `tests/test_demo_replay.py`, `tests/integration_demo_replay.py`, `tests/integration_demo_permissions.py`. CI에는 실제 CPU 재생과 역할 경계 검증을 추가했으며 GitHub 실행 결과는 아직 확인하지 않았다. 서버 운영 절차는 `../infra/DEMO_REPLAY.md`를 따른다.


## 2026-10-15: API / 네 CSV 공통 전체 입력

- `009_switchable_inputs.sql`을 적용한 새 로컬 PostgreSQL 17에서 검증했다. 서버에 접속하거나 고객 API를 호출하지 않았다.
- 첨부 실제 CSV 전체를 읽어 공통 형식으로 staging: **137,603행 = T 27,268 + P 27,405 + M 82,351 + B 579**. `\N` → NULL, `PROCESS_DT` 보존, BODY 값 미저장 확인. 원본 파일은 수정하지 않았다. 이 검증은 전 행의 실제 모델 판정 시험이 아니라 전체 입력 등록/변환 시험이다.
- `integration_switchable_inputs.py`: 실제 로그인/관리자 HTTP API로 API·CSV·일시정지·재개를 전환했다. 1,000개 P 기준 CSV를 수집하고 **피처 생성 → 전체 snapshot 변환 → Rule 추천 → 실제 채택 API → 실제 CPU IF 학습 → 평가 → 같은 후보 구성으로 API/CSV Rule+ML 판정**을 검증했다. LLM 생성은 명시적인 계약 시험 응답을 사용했으며 GPU/Base 추론·생성 품질 시험은 아니다.
- API는 로컬 Bearer HTTP 시험 서버를 사용했다. 중복 응답 제거, 실제 HTTP 401 후 자동 fallback하지 않음, API 응답 대기 중 CSV로 바꾼 뒤 구 응답 입고 거부, 대기 입력의 선택 구성 복구를 확인했다. 서로 다른 입력의 판정은 같은 config/featureSet/선택 Rule을 사용하고 input origin을 남겼다.
- 관리자 외 입력 전환 거부, 오래된 revision 충돌, 상태 조회 및 화면의 충돌 시 거짓 성공 미표시, 일시정지가 미적용 후보 구성을 바꾸지 않음 확인.
- `integration_input_roles.py`: 제한된 Collector 역할로 네 CSV 준비/입고 성공. BODY 원문 열 SELECT는 거부됨. Python 3.12 CPU 이미지에서도 같은 역할 시험 통과.
- 기존 `integration_research_pipeline.py` 회귀: 승인 구성의 추천/파생변수/Rule/실제 CPU IF/평가/적용/판정 및 실제 v12 IF·K-means 통과. 현재 input 선택이 없는 원천의 기존 활성 구성 경로 유지.
- 단위 테스트 **41개**, Frontend 전체 테스트, Backend/Frontend 빌드, CPU Docker 이미지 빌드 및 Compose tools 설정 검사 통과. 기존 frontend bundle 크기/테스트 렌더러 경고는 남아 있다.
- 실제 P 27,405행을 전체 snapshot으로 사용할 때 추천 기준 행 10,000개 상한 때문에 실패하던 경로를 명시적 bounded reference sampling으로 수정했다. 추천 결과에 표본/모집단 수를 기록하고, 승인된 피처 변환은 전체 snapshot에 수행한다. 전체 snapshot 기본 크기 제한은 완화하지 않았다.
- 정답 없는 평가는 unmeasured를 유지한다. 검증용 후보 선택은 운영 활성 포인터나 승인 이력을 변경하지 않는다.

실제 API 문서의 세 응답은 설정 목록, 설정 상세, 진행 중 빈 목록이다. 실행 로그의 비어 있지 않은 실제 shape는 미확인이다. 공통 tagged 입력과 설정 가능한 필드 매핑으로 API 입고 경로를 구현/시험했으며, 실제 서버에서는 비어 있지 않은 로그에 맞춰 매핑을 확정해야 한다. GPU 추천 모델 실제 실행과 서버 배포 검증은 수행하지 않았다. GitHub CI 단계는 추가했으나 원격 실행 결과는 아직 없다. 운영 절차: `../infra/SWITCHABLE_INPUTS.md`.

## 2026-10-21: Trainer PG 통합 시험 로컬 재현 기반 (Issue #69)

CI `worker-runtime` 작업과 같은 순서를 Windows 로컬에서 재현했다. 운영 DB·공용 `pwc-postgres`(55432)·고객 API·GPU는 사용하지 않는다.

```bash
# 1. 격리 PostgreSQL 17 (loopback, 임시 named volume; 55432/5432 회피)
docker run -d --name pwc69-pg -p 127.0.0.1:55469:5432 -e POSTGRES_PASSWORD=local-ci-only \
  -v pwc69-pg-data:/var/lib/postgresql/data postgres:17-alpine
# 2. Python 3.12 venv (워크트리 안 .venv, Git ignore) + 워커 의존성
uv venv --python 3.12 .venv && uv pip install --python .venv/Scripts/python.exe -r server/worker/requirements.txt
# 3. 허용 QA DB 6개를 새로 만든다(기존 DB가 있으면 실패하므로 재실행 전 수동 DROP)
RUNTIME_TEST_ADMIN_URL=postgresql://postgres:local-ci-only@127.0.0.1:55469/postgres \
  .venv/Scripts/python.exe server/infra/tests/prepare-runtime-db.py
# 4. 연구 번들: core.autocrlf=true 체크아웃은 adapter_config.json이 CRLF로 바뀌어 hash 검증에 실패하므로 LF 사본에서 내보낸다
git -c core.autocrlf=false archive HEAD research server/worker | tar -x -C <scratch>/lf-export
.venv/Scripts/python.exe server/worker/research_bundle.py --repository <scratch>/lf-export --output <scratch>/research-runtime
# 5. backend 빌드(Node 22): cd server/backend && npm ci && npm run build
# 6. 단위 → 통합(ci.yml worker-runtime 단계와 같은 DB·환경변수·순서)
PYTHONUTF8=1 PYTHON_DOTENV_DISABLED=1 .venv/Scripts/python.exe -m unittest discover -s server/worker/tests -p 'test_*.py'
DATABASE_URL=postgresql://postgres:local-ci-only@127.0.0.1:55469/qa_research_pipeline RESEARCH_BUNDLE_ROOT=<scratch>/research-runtime \
  .venv/Scripts/python.exe server/worker/tests/integration_research_pipeline.py
# ... 이하 ci.yml의 DATABASE_URL / PIPELINE_TEST_DATABASE_URL 짝을 그대로 사용
# 7. 종료: docker rm -f pwc69-pg && docker volume rm pwc69-pg-data
```

- 포트는 CI와 달리 임의(55469)이며 공용 `pwc-postgres` 55432와 로컬 5432를 피한다.
- `PYTHON_DOTENV_DISABLED=1`로 저장소 `.env`를 읽지 않는다. 시험 자격증명은 위 임시 컨테이너 전용 값이다.
- CI에 없는 `integration_storage/csv_replay/feature_pipeline/training_boundary/model_ports`는 training 큐를 공유하면 서로 작업을 가로채므로 각각 새 DB에서 실행한다.
- `integration_feature_pipeline.py`(ML `not_ready` 기대 vs 현재 `failed/component_failed`)와 `integration_model_ports.py`(학습 행 수가 현재 최소 fit 500+보정 125 미만)는 서비스 전환 이전 fixture라 격리 DB에서도 실패한다. CI 비포함 상태 그대로이며 이번 범위에서 수정하지 않았다.

결과(2026-10-21, 위 절차·변경 후): 단위 113개 통과. CI `worker-runtime` 동등 15단계 전부 통과(research_pipeline, service_transition, api_collection, demo_replay, demo_permissions, switchable_inputs, trainer_service_contract, input_roles, metrics_service --utilities/본체, jobs.integration.mjs, synthetic-catalog-parity.mjs, synthetic_service, prepared_csv_service, service-transition.integration.mjs). CI 비포함 5종은 storage·csv_replay·training_boundary 통과, feature_pipeline·model_ports는 위 사유로 실패. 변경 전 기준 실행에서도 같은 두 시험만 실패했고 `switchable_inputs`는 SYSTEMROOT 미전달로 Windows에서만 실패하던 것을 시험 측에서 보완했다.

### 2단계: snapshot revision 방어 규칙 (Issue #69)

- `trainer/snapshot.py`: PROCESS(`execution_id`)마다 기간·cutoff 안 최신 revision 1개만 멤버(`revision_no DESC, persisted_at DESC, id DESC`, Backend 운영 보고 규칙과 동일). 기간 필터를 먼저 적용한 뒤 선택하며, 최소 행 20·그룹 10·블록 3 검사는 선택 후 기준. `selection_spec`에 `revisionRule=latest-per-execution-v1`·`revisionOrder`·`candidateRows`·`supersededRevisionCount`를 기록한다(기존 키는 유지, 소비자는 `from`/`until`만 읽음).
- 단위 `tests/test_snapshot_selection.py` 9개: 단일 revision 불변, 늦게 저장된 낮은 revision보다 높은 revision 우선, 동률 시 `persisted_at`→`id`, 입력 순서 무관, 블록 70/15/15·같은 시각 한 블록·3블록 미만 거부.
- 통합 `integration_trainer_service_contract.py` fixture: 1,000 execution 중 5개에 기간 안 revision 2(멤버·train 분할), 1개에 `until` 뒤 revision 2(revision 1이 멤버로 유지). 후보 1,005행 → 멤버 1,000·`supersededRevisionCount=5`, splitCounts 700/150/150·평가 분모 150·fit referenceCounts 700 불변.

### 2단계: 실제 worker 이미지 E2E (Issue #69, 2026-10-21)

compose 없이 로컬 빌드 이미지와 격리 PG로 train→evaluate(일반·ESB P02~P04·ADBench)를 컨테이너 trainer가 처리하는지 확인했다. 공용 `pwc-local-*`·`pwc-postgres`(55432)·compose 프로젝트는 접촉하지 않았고, 데이터는 `integration_synthetic_service.seed`의 통제 fixture만 사용했다.

```bash
# 0. Git Bash에서는 MSYS 경로 변환이 `-e ARTIFACT_ROOT=/artifacts`를 `C:/Program Files/Git/artifacts`로 바꿔
#    컨테이너의 tmp 정리·학습이 조용히 건너뛰어진다. docker 명령 전 반드시 설정한다(PowerShell은 불필요).
export MSYS_NO_PATHCONV=1
# 1. 이미지(컨텍스트 server/, compose와 같은 Dockerfile) — 코드 수정 후에는 반드시 재빌드
docker build -f server/worker/Dockerfile -t pwc69-worker:local server/
# 2. 격리 PG(새 이름·포트 55471·새 볼륨·전용 네트워크) + 허용 QA DB
docker network create pwc69-e2e-net
docker run -d --name pwc69-e2e-pg --network pwc69-e2e-net -p 127.0.0.1:55471:5432 \
  -e POSTGRES_PASSWORD=local-ci-only -v pwc69-e2e-pg-data:/var/lib/postgresql/data postgres:17-alpine
RUNTIME_TEST_ADMIN_URL=postgresql://postgres:local-ci-only@127.0.0.1:55471/postgres .venv/Scripts/python.exe server/infra/tests/prepare-runtime-db.py
# 3. 산출물 named volume(이미지의 /artifacts 소유권 uid 10001이 복사됨) + 고아 tmp/<uuid> 사전 생성(uid 10001)
docker volume create pwc69-e2e-artifacts
docker run --rm -v pwc69-e2e-artifacts:/artifacts pwc69-worker:local sh -c 'mkdir -p /artifacts/tmp/<uuid> && printf partial > /artifacts/tmp/<uuid>/model.joblib'
# 4. trainer 컨테이너(상주 루프, compose trainer 서비스와 같은 command·env 이름; 값은 시험용)
docker run -d --name pwc69-e2e-trainer --network pwc69-e2e-net --init \
  -e DATABASE_URL=postgresql://postgres:local-ci-only@pwc69-e2e-pg:5432/qa_synthetic_service \
  -e ARTIFACT_ROOT=/artifacts -e PYTHONUTF8=1 -e PYTHON_DOTENV_DISABLED=1 \
  -v pwc69-e2e-artifacts:/artifacts pwc69-worker:local python -m trainer.runner
docker exec pwc69-e2e-trainer ls -lnR /artifacts   # tmp/ 비어 있음 = 기동 시 고아 정리
# 5. 호스트에서 임시 backend API(통합 시험의 Service helper) + fixture seed + API로 작업 제출, 컨테이너가 소비
#    (scratch 드라이버: Service/seed/registration 재사용, train → evaluate validation → ESB P02/P03/P04 seed 11 ×2 → ADBench global)
#    드라이버 핵심(저장소 미포함, 직접 작성): tests 경로를 sys.path에 추가 → integration_trainer_service_contract.Service(tempdir, dsn)로 임시 API 기동,
#    integration_synthetic_service.seed(conn)/submit(service, spec)으로 작업 제출 → state(conn, job) 폴링. Service.worker()는 호출하지 않음(컨테이너가 소비)
PIPELINE_TEST_DATABASE_URL=postgresql://postgres:local-ci-only@127.0.0.1:55471/qa_synthetic_service .venv/Scripts/python.exe <scratch>/e2e_driver.py
# 6. 정리
docker rm -f pwc69-e2e-trainer pwc69-e2e-pg && docker volume rm pwc69-e2e-artifacts pwc69-e2e-pg-data && docker network rm pwc69-e2e-net && docker rmi pwc69-worker:local
```

결과(두 번 실행, 2회차 기준; 1회차도 4개 작업 모두 succeeded):

| 단계 | 증거 |
|---|---|
| 기동 고아 정리 | 사전 생성한 `tmp/<uuid>/model.joblib`(uid 10001)이 컨테이너 기동 직후 제거, `tmp/` 비어 있음. `ARTIFACT_ROOT=/artifacts`, 프로세스 uid 10001 확인 |
| train | run_jobs succeeded attempt 1, 1.0초, worker_id `trainer-<uuid>`; splitCounts 700/150/150, 멤버 1,000, `selection_spec.revisionRule=latest-per-execution-v1`·`candidateRows=1000`·`supersededRevisionCount=0`(단일 revision fixture라 수치 불변), trainingSelection.selectedCount 692 |
| 후보 저장 | ml_config_versions 1행, ml_artifacts model(1,461,358B)·fit(1,435B); 볼륨의 `models/*.joblib`·`fit/*.joblib` sha256이 `content_hash`와 일치, 소유 uid 10001, `tmp/` 비어 있음 |
| evaluate validation | succeeded 3초, run_evaluations population 150·scored 130·unavailable 20, synthetic `unavailable`, execution_cost `measured` |
| evaluate ESB P02~P04 | succeeded 3초, synthetic `measured`, `evaluations/<job>/<sha256>.json`(281,006B) 파일 sha256 = manifestRef.sha256 |
| evaluate ADBench global | succeeded 7초, synthetic `measured`, manifest 81,100B sha256 일치 |
| 큐·활성 포인터 | training 큐 미완료 0, ops_system_state 0행(활성 구성 변경 없음) |
| 호스트 bind 변형 | Windows Docker Desktop bind는 root:root 0777로 마운트돼 uid 10001 쓰기·`--once` 기동 고아 정리 통과. Linux 호스트의 chown 10001 bind 권한은 이 로컬에서 재현되지 않음(DEPLOYMENT.md 운영 절차로 확인) |

- 컨테이너 로그는 성공 경로에서 비어 있다(runner는 성공 시 출력 없음). 이미지에 `.env`는 포함되지 않는다(`.dockerignore`).
- 처음 빌드한 이미지는 코드 수정 전 컨텍스트를 담고 있어(`/app/trainer/snapshot.py`에 revisionRule 없음) 재빌드 후 실행했다. 이미지 E2E는 항상 최종 코드로 재빌드한 뒤 수행한다.
- 시험 컨테이너·이미지·볼륨·네트워크는 종료 후 제거했고 공용 `pwc-local-*`·`pwc-postgres`·`powercode-worker:local`·`pwc-pg-data`는 접촉하지 않았다.
