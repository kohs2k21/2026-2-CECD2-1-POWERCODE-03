# CSV / PostgreSQL 데이터로 실시간 이상 탐지 데모

이 문서는 기존 CPU/ML 전용 간소 데모다. API·CSV 공통 전체 프로세스와 네 CSV 사용은 [SWITCHABLE_INPUTS.md](SWITCHABLE_INPUTS.md)를 따른다. 아래 BODY를 M 메타데이터로 읽는 과거 데모 경로는 실제 네 파일을 위한 권장 입력이 아니다.

서버에는 자동 접속하지 않는다. 아래 절차는 서버 운영자가 실행한다. 실제 CSV 내용과 서버 권한은 로컬 구현 과정에서 확인하지 않았다.

## 1. `~/anomaly-postgres/data` 권한 문제

제공된 출력에서 `data`는 소유 UID `952966`, 권한 `700`이므로 일반 계정 `gst-dongguk`은 `cd`할 수 없다. PostgreSQL 컨테이너의 물리 저장소로 보이며, 다음 명령으로 마운트를 확인한다.

```bash
docker inspect anomaly-postgres --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
docker exec -it anomaly-postgres sh -c 'cd "$PGDATA" && pwd && ls -la'
docker exec -it anomaly-postgres psql -U anomaly_app -d anomaly_detection
```

DB 내용은 SQL로 조회한다. PGDATA의 파일은 CSV가 아니므로 Sender 입력으로 사용하지 않는다. 실행 중인 PGDATA에 `chmod 777`, 재귀 `chown`을 적용하지 않는다. Docker user namespace/rootless 환경에서는 UID가 호스트에서 다르게 보일 수 있다. 컨테이너/SQL 접근이면 호스트 디렉터리 권한을 바꿀 필요가 없다.

사용자가 알려 준 CSV는 별도 경로에 있다.

| 용도 | 호스트 파일 |
|---|---|
| Transaction 메타데이터 | `/data/gst-dongguk/powercode-esb/tmp/EC_TRANSACTION_MONITORING.csv` |
| Process 탐지 입력 | `/data/gst-dongguk/powercode-esb/tmp/EC_PROCESS_MONITORING.csv` |
| Message 메타데이터 | `/data/gst-dongguk/powercode-esb/tmp/EC_MESSAGE_BODY.csv` |

세 파일 모두 `.csv` 확장자가 있다. 서버에서 파일 존재/조회 권한을 확인한다.

```bash
ls -l /data/gst-dongguk/powercode-esb/tmp/EC_TRANSACTION_MONITORING.csv /data/gst-dongguk/powercode-esb/tmp/EC_PROCESS_MONITORING.csv /data/gst-dongguk/powercode-esb/tmp/EC_MESSAGE_BODY.csv
```

## 2. 구성과 측정 범위

| 구성 | 역할 | 기본 자원 |
|---|---|---|
| 기존 `anomaly-postgres` | 원본 조회, 데모 입력/전송 체크포인트/작업/판정 저장 | 기존 PostgreSQL 17 유지 |
| `sender` | CSV 또는 읽기 전용 SQL 캡처 → 데모 기준 모델 학습 → 초당 N개 P 전송 | CPU 4, RAM 8GB 상한; 명령 실행 시만 생성 |
| `detector` | 영속 큐에서 고정된 데모 구성을 읽고 실제 Isolation Forest 판정 | CPU 2, RAM 4GB 상한 |
| `api`, `web` (선택) | 로그인 및 저장된 판정의 실시간 SSE 표시 | 기존 서비스 이미지; `ui` 프로필 |

별도 `compose.demo.yaml`을 사용한다. GPU, Qwen Base, LLM, 고객 API Token은 필요하지 않다. 준비 단계에서 Sender가 기존 Trainer의 실제 CPU 학습 경로를 호출하므로 상주 Trainer가 없어도 된다. 운영 서비스 구성은 `compose.yaml`에 남아 있다.

- `prepare`: 원본을 읽어 **별도 데모 source**와 불변 입력을 생성한다. `limit` 기본 최대 1,000,000 P, 기준 데이터 최대 5,000 P. 알려진 수동 이상 라벨은 기준 데이터에서 제외한다. 최소 1,000 P와 학습 가능한 실행 그룹/수치 분산이 필요하다.
- 데모 변수는 90% 이상 관측되고 값이 변하는 P의 `success_count`, `error_count`, `total_count`, `retry_count` 중 선택한다. `operator_defined`로 등록한다. 실제 RobustScaler + Isolation Forest를 학습하고 학습 내부 보정 자료의 P99 임계값을 저장한다. LLM 추천이나 가짜 Rule/score를 만들지 않는다.
- `play --rate 10`: 초당 최대 목표 10개의 **Process**를 한 건씩 보낸다. Transaction/Message 허용 메타데이터는 준비 시 한 번 저장하며 초당 전송 건수에 포함하지 않는다. BODY 및 `response_message` 값은 저장하지 않는다. T/P/M의 원천 실행 조인은 확인되지 않았으므로 임의 연결하지 않는다.
- 준비한 모델은 데모 source 작업에만 고정한다. 운영 전역 활성 구성과 승인/적용 이력은 변경하지 않는다. 원본 source의 관측 행도 변경하지 않는다.
- `run-key`: 고정된 입력과 모델 이름. 동일 설정으로 다시 준비하면 기존 준비 결과를 반환한다. 파일 내용이 바뀌어도 이미 고정한 입력은 유지한다. 다른 파일/설정/새 모델은 **새 run-key**를 사용한다.
- `pass-key`: 한 번의 재생 이름. 중단 후 같은 key·rate·repeat로 실행하면 마지막 커밋 이후부터 재개한다. 완료된 key는 재전송하지 않는다. 새 pass-key 또는 `--repeat 2`는 별도 관측/작업을 만들어 다시 탐지한다.
- 원본 API 발생 시각은 재현하지 않는다. 보고서의 지연은 **데모 큐 등록→최종 판정 저장**이며 실제 고객 API 지연과 다르다. `observedSendRate`는 첫 전송부터 마지막 전송까지의 관측 속도로 중단 시간을 포함한다. 느린 DB/Detector에서는 목표 속도 미달과 backlog를 그대로 표시한다.
- 라벨 없는 과거 데이터는 정상 정답이 아니다. 동일 자료를 재생하는 이 데모는 연결/처리 성능 검증이며 탐지 정확도 평가가 아니다. 정확도는 별도 라벨/독립 검증 자료로 평가해야 한다.

## 3. 환경 설정

서버의 저장소 `server/infra`에서 시작한다. 호스트 Python 3.10이어도 컨테이너는 Python 3.12를 사용한다.

```bash
cd /data/gst-dongguk/powercode-esb/server/infra
cp .env.demo.example .env.demo
mkdir -p "$HOME/powercode-demo/artifacts" "$HOME/powercode-demo/auth"
docker inspect anomaly-postgres --format '{{json .NetworkSettings.Networks}}'
docker info --format '{{json .SecurityOptions}}'
id -u
id -g
```

저장소가 `~/powercode-esb`에 있으면 해당 저장소의 `server/infra`로 이동한다. `.env.demo`의 다음 항목을 실제 값으로 수정한다.

- `DATABASE_URL`: 데모 **대상 DB**. 예시 DB `anomaly_detection`, 계정 `anomaly_app`, 내부 호스트 `anomaly-postgres:5432`. 실제 비밀번호를 URL 인코딩한다. Git에 저장하지 않는다.
- `PWC_DB_NETWORK`: 기존 PostgreSQL 컨테이너가 연결된 네트워크 이름. 기본 `anomaly-net`은 예시다. 기본 bridge만 연결되어 있다면 운영자가 사용자 정의 네트워크를 만들고 기존 PG 컨테이너를 연결한 뒤 그 이름을 설정한다.
- `ARTIFACT_ROOT_PATH`, `AUTH_DATA_DIR`: 방금 만든 실제 호스트 디렉터리. 물리 PGDATA 경로를 넣지 않는다.
- `PWC_CONTAINER_UID/GID`: rootful Docker는 호스트 `id -u`, `id -g`에 맞춘다. rootless Docker는 컨테이너 `0:0`이 호스트 로그인 계정으로 매핑되는지 확인한 뒤 사용한다. CSV 마운트는 읽기 전용이고 아티팩트/auth만 쓰기가 필요하다.
- `DEMO_CSV_DIR` 및 세 `DEMO_*_FILE`: 위 확정 경로/파일명이 기본값이다. PostgreSQL 모드도 공통 Sender 마운트에 이 폴더가 필요하다. CSV를 쓰지 않는 환경은 존재하는 빈 폴더를 지정할 수 있다.
- 선택 UI: `JWT_SECRET`에 충분히 긴 무작위 문자열. 기존 `users.json`을 auth 폴더에 복사해 사용하거나 `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD`를 설정해 최초 관리자 계정을 생성한다. 기존 운영 auth 폴더와 겹치지 않는 데모 전용 경로가 기본이다.

이후 명령을 짧게 쓰기 위한 함수다. 새 터미널에도 같은 함수를 정의한다.

```bash
dc() { docker compose --env-file .env.demo -f compose.demo.yaml "$@"; }
dc --profile tools --profile ui config --quiet
dc build sender
```

DB 연결은 Docker 내부 네트워크에서 수행한다. 호스트 CSV 경로는 컨테이너 `/inputs`로 읽기 전용 연결된다. `/inputs`는 컨테이너 경로이며 CSV를 복사하는 과정은 없다.

## 4. 기존 DB 점검 및 마이그레이션

제공된 24개 public 테이블은 초기 스키마에 해당하며 최신 작업/판정/데모 테이블이 빠져 있을 수 있다. 먼저 DB를 백업하고 기존 worker를 정지한 상태에서 적용 이력을 확인한다. 초기 DB를 재생성하거나 기존 데이터 디렉터리를 삭제하지 않는다.

```bash
docker exec anomaly-postgres pg_dump -U anomaly_app -d anomaly_detection -Fc > "$HOME/powercode-demo/anomaly_detection.before-demo.dump"
dc run --rm sender preflight
```

`preflight`는 누락 객체/enum/Rule 불변 trigger를 보여 주며 누락 시 종료 코드 2다. 자동 마이그레이션이나 모든 스키마 차이 검사를 수행하지 않는다. **기존 적용 이력과 함께 확인해 누락된 파일만** 실행한다. 전체 순서는 아래와 같다. 숫자가 중복되므로 파일명까지 그대로 사용한다.

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

`001/002`가 이미 적용된 DB라면 재실행하지 않는다. `003_erd_v03_enums.sql`은 별도 명령으로 커밋한 뒤 `004_erd_v03_contracts.sql`을 실행해야 한다. 각 파일에 자체 트랜잭션이 있다. 아래 명령은 해당 파일이 **아직 적용되지 않은 경우에만** 한 번씩 실행한다.

```bash
dc run --rm migrate -f /migrations/003_erd_v03_enums.sql
dc run --rm migrate -f /migrations/004_erd_v03_contracts.sql
dc run --rm migrate -f /migrations/003_rule_versions_immutable.sql
dc run --rm migrate -f /migrations/004_feature_pipeline.sql
dc run --rm migrate -f /migrations/005_research_runtime.sql
dc run --rm migrate -f /migrations/006_service_transition.sql
dc run --rm migrate -f /migrations/007_api_collection_metrics.sql
dc run --rm migrate -f /migrations/008_demo_replay.sql
dc run --rm migrate -f /migrations/009_switchable_inputs.sql
dc run --rm sender preflight
```

마이그레이션에는 스키마 소유자/DBA 권한이 필요하다. Sender는 데모 관측·학습/구성/작업/큐를 쓰는 관리자 CLI이므로 예시에서는 해당 스키마의 소유 계정을 사용한다. 최종 서비스 계정을 분리할 경우 DBA가 `scripts/database-roles.sql`의 Detector/API 역할과 `scripts/demo-role.sql`의 Sender 역할을 준비하고 login 역할에 부여한다. 역할 SQL은 마이그레이션 이후 적용한다.

## 5. CSV 검증 → 준비 → 재생

```bash
# 파일/헤더, P 행 수, 사용 가능한 수치 변수 확인. DB에는 쓰지 않는다.
dc run --rm sender check-csv

# 별도 데모 source를 만들고 기준 데이터로 실제 CPU 모델 학습
# 첫 시연은 최대 10,000 P 캡처; 기준 5,000 P를 사용한다.
dc run --rm sender prepare --input csv --run-key esb-csv-01 --limit 10000 --baseline-limit 5000

# 모델/입력 준비 완료 후 실제 Detector 시작
dc up -d detector

# 초당 목표 10 P. 실시간 로그는 집계 수치만 출력한다.
dc run --rm sender play --run-key esb-csv-01 --pass-key demo-01 --rate 10
```

다른 터미널에서 같은 폴더/함수를 사용한다.

```bash
dc run --rm sender status --run-key esb-csv-01 --pass-key demo-01 --watch-seconds 300
dc logs --tail 100 detector
```

`senderStatus=completed`는 송신 완료다. 탐지까지 끝났는지는 `pending=0`, `failed=0`, `complete=sent`로 확인한다. `decided`에는 partial/unavailable도 포함되므로 `decided=sent`만으로 전체 ML 성공을 판단하지 않는다. 실제 이상이 없으면 `anomalous=0`일 수 있다. 점수를 임의로 바꾸지 않는다.

중단 후 동일 명령은 이어서 전송한다. 속도/횟수 변경, 처음부터 다시 시연하려면 새 pass-key를 사용한다.

```bash
dc run --rm sender play --run-key esb-csv-01 --pass-key demo-02 --rate 20 --repeat 2
```

입력이 10,000 P이면 repeat 2는 총 20,000 P다. 데이터는 DB에 계속 쌓이며 실행 종료 시 삭제하지 않는다. 준비 도중 중단/실패한 run은 상태와 이력을 남긴다. 원인을 해결한 뒤 새 run-key로 준비한다.

## 6. PostgreSQL 원본을 대신 재생

원본은 SQL의 `READ ONLY / REPEATABLE READ`로 읽고 허용 P 필드만 데모 source로 복사한다. 원본 source ID를 명시하며 여러 source를 섞거나 데모 source를 다시 원본으로 읽지 않는다. 초기 002 스키마의 라벨 테이블 부재도 읽기 경로에서 지원한다. 대상 DB에는 전체 최신 마이그레이션이 필요하다.

```bash
dc run --rm sender sources
# 위 결과에서 processCount > 0인 원본 sourceId를 선택한다.
dc run --rm sender prepare --input postgres --source-id '<원본 sourceId>' --run-key esb-pg-01 --limit 10000 --baseline-limit 5000
dc up -d detector
dc run --rm sender play --run-key esb-pg-01 --pass-key demo-01 --rate 10
dc run --rm sender status --run-key esb-pg-01 --watch-seconds 300
```

별도 DB를 읽을 때만 `DEMO_SOURCE_DATABASE_URL`에 읽기 전용 원본 계정을 설정한다. `DATABASE_URL`은 작업/판정/아티팩트 메타데이터를 저장하는 대상 DB다.

## 7. 화면에서 확인 (선택)

```bash
dc --profile ui up -d --build api web
```

서버의 `http://<서버주소>:8081`에 접속/로그인한 뒤 `/analysis/process-decisions`를 연다. 저장된 실제 판정이 SSE로 추가되며 `과거 데이터 재생 데모`와 run-key를 표시한다. 데모는 Rule을 건너뛰고 ML 결과를 표시한다. LLM을 구동하지 않는 이 구성에서 데모 설명 요청 버튼은 비활성화된다. 기존 운영 판정도 같은 DB에 있으면 함께 표시된다.

운영 Detector가 이미 최신 코드로 동작하며 같은 아티팩트 경로/DB를 사용하면 별도 데모 Detector 대신 기존 Detector가 큐를 처리할 수 있다. 서로 다른 버전의 worker를 같은 큐에 붙이지 않는다. 별도 `compose.demo.yaml` Detector도 같은 DB의 다른 대기 작업/승인 적용 작업을 처리할 수 있으므로, 공유 운영 DB에서는 기존 최신 Detector 재사용 또는 별도 데모 대상 DB를 선택한다. 데모 Sender 자체는 운영 활성 구성을 변경하지 않는다.

종료는 `dc --profile ui down`으로 데모 컨테이너만 정지한다. 외부 PostgreSQL과 bind mount 데이터는 유지된다. 데모 큐에 남은 작업은 다음 Detector가 처리할 수 있다.
