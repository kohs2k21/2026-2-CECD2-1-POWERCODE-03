# 연구 모델을 연결하는 서버 설정

서버 접속·운영 DB 변경·GPU 실행은 수행하지 않았다. 아래는 코드 반입 후 직접 실행할 설정과 순서다.

## 실행 구성

| 프로세스 | 역할 | 실행 자원 |
|---|---|---|
| generation_service | Qwen3.6-27B + feature_v5 / rule_v2, 두 어댑터 전환 | 지정 GPU 1장, NF4·bf16 |
| trainer.runner | 기간 스냅샷, 모델 호출, 파생변수 측정, Rule 근거, 새 IF 학습·평가 | CPU + 생성 서비스 |
| feature_worker | 승인 구성의 고정 파생변수 계산 | CPU |
| detector.runner | 채택 Rule + 해당 변수로 학습한 IF, 적용 요청 처리 | CPU |
| window_worker | 저장된 최신 v12 IF / K-means의 연속 1분 탐지 | CPU |
| monitor | 학습/운영 변수 결측률 비교 | CPU |

GPU가 4장이어도 scikit-learn IF/K-means에는 GPU를 할당하지 않는다. 연구의 생성 설정은 GPU 한 장이다. 먼저 GPU 0을 지정하고 다른 GPU의 기존 작업을 확인한 뒤 필요할 때 별도 생성 서비스 인스턴스를 구성한다. 해당 카드의 bf16 지원과 27B NF4 적재 여유 메모리를 실제 서버에서 확인해야 한다. 이 코드는 모든 GPU를 자동 점유하지 않는다.

## 1. 코드·모델 번들 준비

저장소 루트에서 아래를 실행한다. 목적 디렉터리는 아직 존재하지 않아야 한다.

```bash
python3 server/worker/research_bundle.py --repository "$PWD" --output /srv/powercode/research-runtime
```

번들은 피처/Rule 어댑터, 프롬프트, 규칙 검증 코드, v12 모델과 Python 클래스 코드를 복사하고 SHA-256 목록을 기록한다. 원본 참조 아카이브·학습/검증 캐시·CSV·노트북은 복사하지 않는다. 런타임은 이 번들을 사용하며 저장소의 research 경로를 직접 import하지 않는다. 번들 갱신은 새 디렉터리에 내보내고 검증한 뒤 설정 경로를 변경한다.

Base는 별도 제공해야 한다.

- 모델: Qwen/Qwen3.6-27B
- revision: 6a9e13bd6fc8f0983b9b99948120bc37f49c13e9
- 경로: RULE_MODEL_BASE_PATH
- Base config·weight index·tokenizer config·chat template 해시는 연구 manifest와 일치해야 한다.
- Base 전체 가중치의 해시 검증은 연구 패키지가 제공하지 않는다. 로컬에서 신뢰하는 pinned snapshot을 준비한다.
- 서비스는 local_files_only=True로 로드하며 Base 자동 다운로드는 하지 않는다.

## 2. 환경 분리

CPU 환경은 Python 3.12를 권장한다. 실제 v12의 라이브러리 버전을 고정한다.

```bash
python3.12 -m venv /srv/powercode/venv-cpu
/srv/powercode/venv-cpu/bin/pip install -r server/worker/requirements.txt
python3.10 -m venv /srv/powercode/venv-gpu
/srv/powercode/venv-gpu/bin/pip install -r server/worker/requirements-generation.txt
```

GPU용 Torch가 서버 CUDA/드라이버와 맞는지 확인한다. CPU와 GPU 환경을 분리하므로 transformers 변경이 sklearn 산출물 로더에 영향을 주지 않는다.

## 3. 비밀값·경로

server/worker/.env.example을 Git에서 제외되는 server/worker/.env에 복사하거나 서비스 관리자의 환경 파일을 사용한다.

| 변수 | 설정 |
|---|---|
| DATABASE_URL | 해당 서버에서 접속 가능한 PostgreSQL 주소. 운영 데이터 읽기/작업 등록 권한을 별도로 관리 |
| ARTIFACT_ROOT | Trainer와 Detector가 함께 읽는 영속 디렉터리, 예: /srv/powercode/artifacts |
| RESEARCH_BUNDLE_ROOT | 1단계 번들의 절대 경로 |
| FEATURE_DESIGN_PROVIDER | research |
| DESIGN_MODEL_URL | 같은 서버이면 http://127.0.0.1:8010. 다른 서버는 인증된 HTTPS 주소 |
| DESIGN_MODEL_TOKEN | 생성 서비스와 Trainer에 동일한 32자 이상 임의 비밀값 |
| RULE_MODEL_BASE_PATH | GPU 프로세스의 pinned Base 절대 경로 |
| CUDA_VISIBLE_DEVICES | GPU 생성 프로세스에만 0 또는 사용할 카드 번호 한 개 |
| DRIFT_WINDOW_HOURS | 기본 24 |
| DRIFT_MISSING_DELTA | 기본 0.10 |

ARTIFACT_ROOT 아래 tmp 디렉터리를 만들고 Trainer에 쓰기 권한, Detector에 읽기 권한을 준다. 토큰·DB 비밀번호·Base 경로를 Git에 커밋하지 않는다. 생성 서비스는 기본 localhost에만 바인딩한다. 외부에서 접근하려면 TLS 프록시와 네트워크 접근 제한을 구성한다. 클라이언트는 redirect를 따르지 않는다.

백엔드는 DATABASE_URL, JWT_SECRET, USER_DATA_PATH, PORT를 별도로 설정한다. 프런트엔드가 별도 origin이면 빌드 전에 VITE_API_BASE_URL을 설정한다.

## 4. DB 적용 순서

기존 운영 DB는 적용 이력과 백업을 확인한다. 신선한 DB에서는 아래 순서로 각각 실행한다. 특히 enum 변경은 후속 계약 SQL 전에 커밋되어야 한다.

1. 001_postgres_queues.sql
2. 002_core_erd.sql
3. 003_erd_v03_enums.sql
4. 004_erd_v03_contracts.sql
5. 003_rule_versions_immutable.sql
6. 004_feature_pipeline.sql
7. 005_research_runtime.sql

번호가 같은 파일들이 있으므로 파일명 정렬만으로 순서를 정하지 않는다. 004_erd_v03_contracts.sql은 한 번만 적용하는 SQL이다. 이 경로의 추천 작업은 v03 LLM 감사 계약을 사용하고 train/evaluate는 기존 호환 계약을 사용한다. 실제 계산 입력의 snapshotId와 sourceVersion은 생성 결과에 함께 기록한다.

## 5. 프로세스 실행

각 프로세스의 작업 디렉터리는 server/worker이다. 아래 명령의 설정값은 환경에 주입한다.

```bash
# GPU 서비스: EnvironmentFile 또는 shell 환경에 Base·번들·토큰을 넣는다.
CUDA_VISIBLE_DEVICES=0 /srv/powercode/venv-gpu/bin/python -m generation_service

# CPU 프로세스는 각각 실행한다.
/srv/powercode/venv-cpu/bin/python -m trainer.runner
/srv/powercode/venv-cpu/bin/python -m feature_worker
/srv/powercode/venv-cpu/bin/python -m detector.runner
/srv/powercode/venv-cpu/bin/python -m monitor
/srv/powercode/venv-cpu/bin/python -m window_worker
```

systemd 등의 서비스 관리자로 프로세스 재시작, 로그 보관, 환경 파일 권한을 설정한다. /health는 GPU 모델과 어댑터의 초기 적재가 끝난 뒤 ready를 반환한다. 이 응답이 준비되기 전에 Trainer를 실행하면 추천 작업은 미설정/연결 오류로 끝날 수 있다.

## 6. 수집·생성·학습·적용

1. Collector로 T/P/M의 허용 필드와 검증된 실행 식별을 저장한다. 실제 외부 ESB 수집은 해당 원천 API 계약에 맞는 Collector가 필요하다. CSV 재생은 기존 collector.replay_csv를 사용한다.
2. 관리자 화면의 학습·탐지 운영에서 출처와 기간을 지정해 파생변수 생성을 요청한다.
3. Trainer는 고정 스냅샷의 train 필드 통계를 feature_v5에 보내고 응답의 버전·출력 타입·단위·원천 의존성을 확인한다. 실행 가능한 Process 행 정의로 해당 스냅샷을 정제한다.
4. Rule 추천은 같은 변수셋의 실제 측정과 train P95 기준을 rule_v2에 전달한다. 연구의 규칙 검증 코드로 조건·근거 연결을 검사한다. 보류·근거 요청·적용 불가는 리뷰로 보존한다.
5. 관리자가 Rule 의미를 검토하고 초안을 채택한다. 해당 추천 작업·측정·모델의 근거가 Rule 버전에 보존된다.
6. 선택 기간에서 같은 변수셋으로 IF를 학습한다. 알려진 이상 라벨과 결측 행을 제외한다. 완전한 train 행은 최소 625개 필요하며 그 안에서 학습과 임계값 기준 집합을 분리한다.
7. validation/test 평가에 수동 normal/anomaly 라벨이 필요하다. 라벨 부족·미평가 상태는 적용 조건을 통과하지 않는다.
8. 구성 적용 요청 후 Detector가 해시·모델·fit·열 순서를 확인하고 활성 포인터를 전환한다.
9. 새 Process 관측은 변수 큐→고정 변수 계산→탐지 큐→채택 Rule·IF→판정 DB로 처리된다.

현재 피처 v5의 실행 대상은 Process 한 행이다. 지원 연산은 수치 뺄셈/비율, 비교, Boolean 결합·결측 진단, ISO 시각 차이와 검증된 시간 단위 변환이다. T/M 관계 집계, 그룹 빈도, 부모 조회는 현재 관계·as-of 계약이 없으므로 보류한다. 원래 계산 의미를 다른 P 계산으로 바꾸지 않는다. 원문 BODY·response message는 모델에 전달하지 않는다.

## 7. 최신 v12 시간창 탐지 설정

v12는 새 파생변수를 입력으로 받지 않는다. 고정된 Process-derived 1분 특징을 사용한다. 사용자 변수로 새로 학습한 IF와 별도 경로이며 각각의 점수 계약을 유지한다.

해당 ops_sources.clock_basis에 status=verified, timezone=Asia/Seoul, arrivalEventsAvailable=true가 확인되어야 한다. 마지막 값은 시작 이벤트도 수집된다는 실제 원천 계약을 확인한 경우에만 설정한다. 수집기는 모든 P partition의 ops_collection_checkpoints에 query_from, completed_until, last_success_at, error_code를 정확히 갱신해야 한다. 이 감시 경로에서 query_from은 연속 수집이 검증된 최초 범위의 시작이고 completed_until은 누락 없이 수집 완료된 끝이다. 현재 조회 요청의 시작만 기록하는 checkpoint라면 별도 감시 coverage partition을 구성해야 한다. 수집 완료가 확인되지 않으면 입력 중단으로 판단하지 않고 collection_coverage_unavailable을 기록한다. 최초 시작 관측과 완료 관측이 기한 내 제공되어야 하며 완료 로그만 뒤늦게 도착하면 요청 수를 정확히 재현할 수 없다.

```bash
python -m window_worker --register-source SOURCE_UUID --algorithm if \
  --history-start 2026-10-01T00:00:00+09:00 --lateness-seconds 120 --accept-synthetic-prior
python -m window_worker --register-source SOURCE_UUID --algorithm kmeans \
  --history-start 2026-10-01T00:00:00+09:00 --lateness-seconds 120 --accept-synthetic-prior
```

SOURCE_UUID는 실제 등록 출처 ID로 바꾼다. 모델 내장 traffic prior가 합성임을 명시적으로 수용하는 연구 실행 설정이다. 운영 prior 적격성 평가를 대신하지 않는다.

워커는 수집 완료 시점과 현재 시각-lateness 중 더 이른 시점까지 판단한다. stream은 Adapter+Process로 분리하며, 첫 관측 이후 0건 분도 포함한다. 재시작 때 DB에서 동일 prefix를 다시 구성한다. 기한 뒤 도착한 관측을 과거 완료 판정에 소급 반영하지 않는다. 기본 상한은 관측 100,000개, 전체 grid 100,000행과 stream별 이력 43,200분이며 상한을 넘으면 명시적 오류를 남긴다. 장기간 운영의 증분 상태·모델 갱신은 이 제한을 평가한 뒤 확장한다.

결과 API는 /api/pipeline/windows와 /api/pipeline/window-monitors이다. v12 margin은 0~1 확률이 아니며 margin>0만 이상 후보로 표시한다. trafficAssessed/latencyAssessed가 false이면 해당 부분은 미평가다.

## 8. 반입 후 확인

- 번들 해시, pinned Base metadata, GPU bf16·메모리, localhost 생성 서비스 ready
- DB 마이그레이션 적용 이력과 백엔드 /health/database
- 파생변수 생성·보류 결과, Rule 근거 검증, 실제 GPU 응답의 의미 검토
- 충분한 정상 참조 및 라벨로 실제 IF 학습·검증·적용
- 재시작 후 활성 모델 재적재, 큐 중복 전달 처리
- v12 시간대, 수집 watermark, 빈 분, 지연 도착과 재시작 결과

로컬에서는 생성 서비스를 계약 시험 응답으로 대체하고 실제 IF/v12 추론을 검증한다. Qwen Base 가중치 및 서버 GPU를 사용한 신규 생성은 실행하지 않았으므로 서버 반입 후 별도 확인한다.
