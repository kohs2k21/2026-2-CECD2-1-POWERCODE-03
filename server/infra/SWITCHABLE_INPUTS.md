# API / CSV 입력 전환과 공통 전체 파이프라인

이 문서가 API와 CSV를 교체하면서 **피처 생성·데이터 변환·Rule 추천·사용자 채택·모델 학습/평가·탐지**를 실행하는 절차다. 이전 `compose.demo.yaml` / `demo.cli`는 숫자 변수와 ML만 사용하는 별도 간소 데모이며 이 절차에서는 사용하지 않는다.

## 구성

```text
실제 API 조회 ─┐
              ├─ 공통 정규화/저장 → 피처 생성·변환 → Rule 추천 → 사용자 채택
네 CSV 재생 ──┘                                      ↓
                               학습·평가 → 탐지 구성 선택/승인
                                              ↓
                    같은 변수 변환 → 같은 Rule + 모델 → 판정 저장/SSE
```

- Collector 하나가 DB에 저장된 `api / csv / paused` 모드를 따른다. **자동 fallback이 아니다.** 관리자가 화면/CLI에서 바꾼다.
- 같은 `COLLECTOR_SOURCE_KEY`와 sourceId를 유지하므로 입력을 바꿔도 피처·Rule·모델을 다시 만들 필요가 없다. 변경된 입력의 분포에서 품질이 유지되는지는 별도 검증 대상이다.
- 생성·추천·학습은 사용자가 실행하는 구성 단계다. 매 행마다 LLM을 호출하거나 사용자의 Rule 선택을 건너뛰지 않는다.
- 입력 전환은 실행 중인 입고 트랜잭션이 끝나는 경계에서 적용된다. API 요청은 DB 잠금 밖에서 수행하고, 입고 직전에 선택 revision을 다시 확인한다. CSV로 바뀐 뒤 도착한 이전 API 응답은 입고하지 않는다. 이미 큐에 커밋된 작업은 원래의 고정 구성으로 마친다.
- CSV는 중단 위치를 DB에 보존한다. API로 전환한 뒤 같은 묶음·속도·반복 횟수로 CSV를 선택하면 이어서 보낸다. 완료한 재생은 자동으로 다시 보내지 않으며 **처음부터 새 재생 실행**을 선택해야 한다.
- CSV 전송률은 T/P/M/B 참조를 합산한 **행/초의 목표 상한**이다. 느린 API/DB의 밀린 작업을 한꺼번에 전송하지 않는다.

## 실제 첨부 CSV에 맞춘 네 입력

| 파일 | 구분 | 첨부 파일 확인 건수 |
|---|---|---:|
| `EC_TRANSACTION_MONITORING.csv` | T: Transaction 실행 정보 | 27,268 |
| `EC_PROCESS_MONITORING.csv` | P: Process 실행 정보·현재 피처/탐지 입력 | 27,405 |
| `EC_MESSAGE_MONITORING.csv` | M: Message 모니터링 | 82,351 |
| `EC_MESSAGE_BODY.csv` | B: Message BODY 참조 | 579 |
| 합계 | | 137,603 |

기존 DB에서 M=579였던 값과 별개로, 실제 메시지 **모니터링 파일은 82,351행**이다. BODY 파일을 M 모니터링 파일로 대신 사용하지 않는다.

헤더 대소문자와 camelCase를 공통 컬럼명으로 맞추고 CSV의 `\N`은 NULL로 처리한다. `PROCESS_DT`는 `process_datetime_raw`로 보존한다. 원본 파일은 읽기 전용이며 `response_message`, 인증 정보와 BODY 원문은 모델/공통 입력 staging에 넣지 않는다.

B는 `source_layer='B'`, `data_message_bodies.collection_status='not_collected'`로 messageId 참조만 보관한다. BODY 원문은 수집하지 않았다고 명시하며 내용을 가진 것처럼 표시하지 않는다. T/P/M의 원천 최종 수정 순서나 시간대, BODY의 검증된 as-of 부모 관계를 만들어 내지 않는다. 현재 실행 가능한 피처는 기존 안전한 P 행 DSL 범위이며 T/M/B 전체를 임의 join하지 않는다.

재생 준비는 트랜잭션 순서와 그룹 안 T→P→M→B 순서로 입력을 고정한다. 각 파일 내부의 해당 그룹 행 순서를 유지한다. BODY는 매칭 가능한 Message ID가 있을 때 같은 재생 그룹에 배치하지만 DB에 검증된 부모 관계를 주장하지 않는다. 원본 발생 시각의 간격을 그대로 재현하는 모드는 아니다.

## 1. 배포·마이그레이션

기본 [Docker 배포](DOCKER_DEPLOYMENT.md)의 `.env`, GPU Base/번들, 기존 PostgreSQL 네트워크, 사용자·아티팩트 경로를 먼저 설정한다. 서버에는 자동 접속하지 않는다.

마이그레이션 순서:

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

백업/적용 이력을 확인해 **미적용 파일만** 실행한다. 기존 worker를 멈춘 상태에서 적용한다. 아래는 008까지 이미 적용한 DB의 추가 명령이다.

```bash
cd server/infra
docker compose run --rm migrate -f /migrations/009_switchable_inputs.sql
```

`009`는 입력 선택/실행 이력, 고정 CSV 입력, 원천 관측 provenance를 추가한다. 시작 시 자동 적용하지 않는다. 계정을 분리한 환경에서는 DBA가 최신 `scripts/database-roles.sql`을 다시 적용한다. BODY grant는 원문 열을 제외한 참조 메타데이터에만 부여한다.

`.env`의 입력 설정 예시:

```dotenv
COLLECTOR_SOURCE_KEY=customer-esb
CSV_INPUT_DIR=/data/gst-dongguk/powercode-esb/tmp
INPUT_TRANSACTION_FILE=EC_TRANSACTION_MONITORING.csv
INPUT_PROCESS_FILE=EC_PROCESS_MONITORING.csv
INPUT_MESSAGE_FILE=EC_MESSAGE_MONITORING.csv
INPUT_BODY_FILE=EC_MESSAGE_BODY.csv
INPUT_CONTRACT_DIR=./input-contracts
COLLECTOR_LOG_MAPPING_FILE=
```

첨부 파일처럼 하위 디렉터리가 있으면 `CSV_INPUT_DIR`을 상위 폴더로 하고 파일 값에 `transaction/EC_TRANSACTION_MONITORING.csv`, `process/EC_PROCESS_MONITORING.csv`, `message/EC_MESSAGE_MONITORING.csv`, `body/EC_MESSAGE_BODY.csv`를 넣는다. 웹에서 임의의 서버 파일 경로를 열지 않으며, 운영자가 등록한 묶음만 선택한다.

```bash
docker compose build collector api web
docker compose run --rm input init
docker compose run --rm input stage-csv --replay-key esb-20261002
docker compose up -d api web collector detector trainer llm
```

`stage-csv`는 파일을 정규화해 DB에 불변 재생 입력으로 고정한다. 파일을 변경한 뒤 같은 key를 쓰면 충돌을 알리므로 새 key로 등록한다. staging/초기화는 모델을 따로 학습하거나 모드를 바꾸지 않는다. 처음 시작한 Collector는 **일시정지** 상태다.

## 2. 화면에서 CSV / API 선택

로그인 후 `/detection/pipeline` → 출처에서 `customer-esb` → **입력 선택 · API / CSV**를 사용한다.

1. **CSV 순차 재생**을 선택한다.
2. 등록된 `esb-20261002` 묶음, 초당 행 수, 반복 횟수를 지정하고 **선택한 입력 적용**을 누른다.
3. 원천 T/P/M/B 건수와 저장·중복·식별자 부족 건수, 입력 오류를 확인한다.
4. 초기 기준 데이터가 부족하면 적당한 속도로 축적한 뒤 **입력 일시정지**한다. 실제 CPU IF 학습은 최소 1,000 P 정도와 충분한 완전한 학습 행/실행 그룹이 필요하다. 기준 시연 속도 10행/초로 전체 137,603행은 약 3.8시간 이상이므로 초기 축적과 시연의 속도를 구분한다.
5. 같은 화면에서 기간을 지정하고 아래 구성 단계를 진행한다.

| 단계 | 사용자의 실행 |
|---|---|
| 파생변수 생성 | 변수 생성 작업 등록 → LLM 피처 모델 출력 검증 → 기간 snapshot 전체에 변환/물질화 |
| Rule 추천 | 같은 featureSet·snapshot으로 추천 → 제안 근거 확인 |
| Rule 선택 | 사용할 제안을 **검토 후 초안 채택**하고 학습에 사용할 Rule 목록에 포함 |
| 학습 | Rule+ML/Rule만/ML만 모드와 처리 기한 선택 → 학습 실행 |
| 평가 | 고정 구성·snapshot의 validation/test 평가 |
| 실행 구성 | 승인된 운영 구성 또는 명시적으로 선택한 검증용 후보 사용 |

피처 추천의 기준 행은 고정 snapshot의 train 분할에서 관측 ID 순 최대 10,000개를 선택한다. 표본 수와 train 모집단 수를 결과에 기록한다. 추천이 검증되면 변환은 기간 snapshot 전체에 적용하므로 실제 첨부 P 27,405행 때문에 추천 기준 상한 오류가 발생하지 않도록 했다.

정답 라벨이 없는 CSV의 평가는 `unmeasured`다. 이를 정상 정답이나 운영 승인으로 바꾸지 않는다. 라벨/평가 조건이 충족되면 기존 승인·적용 절차로 운영 구성을 활성화한다.

라벨 없는 데이터로 **전체 기능 연결을 시연**하려면 입력 패널의 **공통 탐지 구성 → 검증용 후보**에서 방금 학습한 구성을 선택한다. 새 CSV 재생 또는 API 입력에 같은 후보를 고정해 Rule+ML 판정한다. 운영 전역 활성 포인터와 승인 이력은 바꾸지 않고 판정에 검증용 입력 구성임을 표시한다. 검증용 후보 선택은 관리자에게만 허용한다.

6. CSV를 다시 처음부터 보내려면 **처음부터 새 재생 실행**을 체크한다. 단순 전환 후 재개라면 체크하지 않는다. 속도/반복 횟수를 바꾸려면 새 실행을 선택한다.
7. API를 쓰려면 같은 패널에서 **API 실시간 조회**, 조회 간격을 선택한다. 피처·Rule·모델 구성은 유지한다. API 장애가 나면 오류를 확인한 뒤 직접 CSV로 전환한다.
8. `/analysis/process-decisions`에서 저장된 Rule/ML 상태, API/CSV 구분과 검증용/운영 구성을 확인한다. 설명 요청은 기존 LLM 작업 경로를 사용한다.

후보 선택 전 축적한 P는 대기 큐에 남는다. 운영 구성을 적용하거나 입력용 후보를 선택하면 Detector가 같은 source의 대기를 복구한다. 이미 작업으로 고정된 과거 판정을 새 구성으로 몰래 다시 쓰지는 않는다.

## 3. CLI로 같은 입력 제어

화면과 같은 DB 상태를 제어한다. `status`의 최신 revision과 replayId를 사용한다.

```bash
docker compose run --rm input status
# 아래 revision/replayId는 status 출력의 실제 값으로 바꾼다.
docker compose run --rm input select csv --expected-revision 0 --replay-id '<replayId>' --rate 100 --repeat 1
docker compose run --rm input select paused --expected-revision 1
docker compose run --rm input select api --expected-revision 2 --poll-seconds 2
# 기본은 기존 선택 구성을 유지한다. 후보를 지정하거나 운영 구성을 선택할 수 있다.
docker compose run --rm input select csv --expected-revision 3 --replay-id '<replayId>' --rate 100 --repeat 1 --config '<configVersionId>'
# 처음부터 재생할 때만 --restart. 운영 구성으로 돌아갈 때 --config active.
```

화면에서도 변경하면 revision이 올라간다. 오래된 revision의 요청은 409/충돌로 거부하며 입력이 바뀐 것처럼 표시하지 않는다. `COLLECTOR_POLL_SECONDS`는 이전 단독 API runner의 설정이며, 새 기본 Collector는 선택한 실행의 `pollSeconds`를 사용한다.

## 4. 제공된 API 응답과 필드 매핑

첨부 문서의 실제 응답 세 개는 모두 확인했다.

- 1번: 인터페이스 설정 목록 10개와 totalPages/totalCount.
- 2번: 인터페이스 설정 상세와 processInfo(프로세스 **정의**).
- 3번: 실행 중 캐시의 `responseObject: []`.

1·2번의 설정을 실행 로그나 성공/실패 건수로 변환하지 않는다. 3번은 빈 정상 조회로 기록하며 데이터/이상 판정을 만들지 않는다. 이 캐시 API만으로 완료된 전체 로그의 수집을 보장하지 않는다. 실제 비어 있지 않은 실행 로그의 형식은 아직 문서에 없다.

로그가 이미 공통 형식이면 기본 어댑터가 처리한다. 아래는 **지원 입력 계약 예시**이며 실제 고객 응답이 이 형식이라는 뜻이 아니다.

```json
{
  "responseCode": "0000",
  "responseObject": [
    {"layer":"P","values":{"TRANSACTION_ID":"T-1","PROCESS_ID":"P-1","RETRY_COUNT":0,"ERROR_COUNT":1,"TOTAL_COUNT":10}}
  ]
}
```

다른 구조이면 `input-contracts/customer-logs.json`에 명시적 매핑을 작성하고 `.env`에 다음을 지정한다.

```dotenv
COLLECTOR_LOG_MAPPING_FILE=/input-contracts/customer-logs.json
```

`nested-transactions.example.json`은 transaction/processes/messages/bodies 구조의 **예시일 뿐**, 확인된 고객 계약이 아니다. `path`는 responseObject 배열의 각 객체 기준 키 목록이고, 배열/단일 객체를 선택할 수 있다. `fields`는 대상 DB 컬럼 → 선택한 객체 기준 경로, `inherit`는 대상 DB 컬럼 → 바깥 객체 기준 경로다. 임의 코드 실행/외부 조회는 하지 않는다.

예를 들어 실제 로그가 `{"tx":"T-1","processes":[{"pid":"P-1","errors":1,"total":10}]}`이면:

```json
{"version":1,"streams":[{"layer":"P","path":["processes"],"fields":{"PROCESS_ID":["pid"],"ERROR_COUNT":["errors"],"TOTAL_COUNT":["total"]},"inherit":{"TRANSACTION_ID":["tx"]}}]}
```

매핑 파일을 바꾸면 Collector만 재시작한다. 런타임 shape가 맞지 않으면 원문을 제외한 응답 preview·오류 측정값을 저장하고 실행 관측을 임의 생성하지 않는다. 실제 로그가 제공되면 그 응답에 맞춰 이 파일만 확정하면 되며 이후 피처/Rule/모델 경로는 공통이다.

## 5. 데이터 식별·측정 범위

- T는 transactionId, P는 transactionId+processId, M은 transactionId+processId+messageId, B는 messageId를 사용하고 제공된 retry 값을 구분한다. 없는 retry를 0으로 만들지 않는다. 필수 식별자가 없는 P는 저장하되 탐지에 보내지 않고 부족 건수에 표시한다.
- API는 같은 식별자의 허용 메타데이터가 같으면 중복 제거하고, 달라지면 새 관측을 추가한다. revision은 **로컬 수신 순서**이며 원천 최종 수정 버전이라는 뜻이 아니다.
- CSV의 새 실행/반복 회차는 별도 namespace로 새 관측을 만든다. 모드를 바꿔도 모델을 선택하는 sourceId는 같고 원래 API 관측과 섞어 덮어쓰지 않는다.
- API 측정은 실제 HTTP 호출 및 DB 입고 시간, CSV는 지정한 속도의 재생이다. 원천 발생 시각 지연·도착 누락률·실제 정확도를 이 결과로 주장하지 않는다.
- 모델/Rule/피처가 준비되지 않은 상황은 대기 또는 unavailable로 남긴다. 정상 판정이나 가짜 점수로 대체하지 않는다.
