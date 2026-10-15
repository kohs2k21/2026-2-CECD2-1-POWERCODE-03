# Collector worker

`storage.py`는 Collector가 받은 T·P·M 원천값에서 허용된 필드만 골라 PostgreSQL에 기록하는 저장 경로다. 실제 ESB API를 호출하는 수집 실행기는 아직 없다. 검증된 P는 같은 트랜잭션에서 **변수 큐**에 등록하고, Feature worker가 승인 변수 계산 후 탐지 큐로 넘긴다.

동일 dedup_key·동일 허용 필드 재입력은 기존 행을 반환하고, 같은 키의 다른 내용은 오류로 보류한다. 실제 ESB API 조회, 원천 키 구성·revision 계약, 부모 T/P/M 연결은 아직 별도 구현이 필요하다.

## CSV 실시간 재생 프로토타입

`replay_csv.py`는 서버에 별도로 올린 T·P·BODY CSV를 행 단위로 일정한 속도로 재생한다. 각 CSV의 첫 줄은 헤더다. 대문자 원천 컬럼을 허용 필드로 옮기며, `START_TIME`·`END_TIME`은 원문 시각 문자열로, T의 `PROCESS_TIME`은 보고된 처리시간으로 보존한다. BODY의 `MESSAGE_ID`만 M 관측값에 저장한다. `MESSAGE_IN`·`MESSAGE_OUT`·`MESSAGE_BODY`는 읽는 즉시 버리고 DB·해시·큐·출력에 넣지 않는다.

```bash
cd server/worker
python -m collector.replay_csv \
  --transaction-csv /data/gst-dongguk/powercode-esb/tmp/transaction.csv \
  --process-csv /data/gst-dongguk/powercode-esb/tmp/process.csv \
  --body-csv /data/gst-dongguk/powercode-esb/tmp/body.csv \
  --dataset-key esb-csv-demo --replay-key sample-20261010 --rate 10
```

기본값은 DB에 쓰지 않는 검증 실행이다. 검증 결과를 확인한 뒤 같은 명령에 `--apply`를 붙이면 `server/worker/.env`의 `DATABASE_URL`로 저장한다. `--rate`는 초당 재생 행 수이고 0이면 지연 없이 재생한다. `--max-rows`로 처음 일부 행만 처리할 수 있다. CSV는 Git 저장소 밖의 제한된 디렉터리에 두고, `.env`와 CSV 원문을 커밋하지 않는다.

서버의 PostgreSQL 주소가 `anomaly-postgres`이면 재생 프로세스도 Docker의 `anomaly-net` 안에서 실행해야 한다. 호스트 Python에서 그 컨테이너 이름이 해석된다고 가정하지 않는다. Worker 전용 `.env`에는 DB URL만 넣고 비밀번호를 명령행 인자로 넘기지 않는다. 실제 DB에 적용하면 합성 관측과 탐지 큐 메시지가 남으므로 `dataset-key`와 `replay-key`를 시험용으로 명확히 지정한다.

동일 `dataset-key`와 `replay-key` 및 동일한 파일·행 순서로 다시 실행하면 중복 저장하지 않는다. 파일을 수정하거나 다른 파일을 재생할 때는 새 `replay-key`를 쓴다. 이 모드의 `revision_no`는 **CSV 안의 행 번호**이고 실행 키는 `replay-key`로 분리된다. 원천 ESB의 실제 수정 순서나 시간대가 검증됐다는 뜻이 아니다. PROCESS는 `TRANSACTION_ID`·`PROCESS_ID`·유효한 `RETRY_COUNT`가 모두 있을 때만 변수 큐에 등록한다. BODY는 현재 제공된 컬럼만으로 부모 P와 연결할 수 없어 미연결 M 관측값으로 저장한다. 실제 모델이 연결되고 구성이 승인되기 전에는 탐지 판정이 진행되지 않는다.

## 고객 API 상시 수집·동시 실시간성 측정 (2026-10-15)

기본 Collector는 `python -m collector.api_runner`이다. [7개 서비스 실행 문서](../../infra/DOCKER_DEPLOYMENT.md)를 따른다. 제공된 인터페이스 목록 POST·상세 GET·진행 중 목록 GET 클라이언트는 `api_client.py`에 구현했다. 상시 루프는 목록 페이지와 진행 중 목록을 조회한다. 상세 조회는 필요할 때 사용하며 상시 루프가 요청 수를 늘리지 않는다.

- 인증: `COLLECTOR_ACCESS_TOKEN → ACCESS_TOKEN → TOKEN` 또는 `COLLECTOR_ACCESS_TOKEN_FILE`의 **토큰 문자열만 있는 파일**. 모든 호출에 Bearer 헤더를 넣는다. 토큰 발급/갱신 API는 미제공이다.
- 포트 1112 설정, 1114 진행 중 캐시. HTTP는 `COLLECTOR_HTTP_HOST`의 정확한 호스트만 허용하고 redirect를 따르지 않는다.
- `COLLECTOR_SOURCE_KEY`는 고정 원천 식별자. 원천별 session advisory lock으로 Collector 중복 실행을 막는다.
- 기본 2초 start-to-start 목표 간격, 겹치지 않는 요청, 10초 HTTP timeout. 요청이 느리면 실제 간격이 늘어나며 측정에 그대로 남는다.
- `ops_api_catalog`: 안전하게 투영된 설정의 불변 버전. 설정 revision의 내용이 달라지면 해당 페이지 전체 저장·checkpoint 전진을 취소한다.
- `data_api_snapshots`: 설정·진행 중 응답의 허용 필드 snapshot. 같은 snapshot은 중복 제거한다. unknown 필드 개수만 저장하며 원문 BODY·SQL·토큰은 제외한다.
- `ops_api_poll_state`: 성공한 페이지 checkpoint와 조회/매핑 상태.
- `ops_api_poll_samples`: 매 호출 시각·HTTP 응답 시간·응답 bytes·상태·오류·빈 응답 여부·새 snapshot 여부·수신→DB commit 시간. 추가 시험용 API 호출은 하지 않는다.

현재 제공 응답은 진행 중 `[]`뿐이다. 허용 필드 preview는 탐지용 실행/revision 계약을 대신하지 않는다. `monitoring_log_contract_required`를 표시하며 **data_observations의 T/P/M로 변환하지 않는다.** 향후 비어 있지 않은 응답과 완료/PROCESS/MESSAGE 조회 명세를 받은 뒤 변환을 추가한다. 기존 CSV 관측 수집은 계속 사용할 수 있고, 활성 구성에 대응하는 관측 저장 시 불변 manifest·detect job·queue를 같은 트랜잭션에서 생성한다.

### 기존 토큰 파일 재사용

`.env`에 `COLLECTOR_ACCESS_TOKEN_FILE_PATH=/실제/토큰/파일`을 넣고 해당 파일을 컨테이너 UID가 읽을 수 있게 한다. 파일에는 `Authorization:`이나 `Bearer ` 접두사 없이 토큰만 넣는다.

```bash
docker compose -f compose.yaml -f compose.token-file.yaml up -d collector
```

파일은 읽기 전용 mount이며 poll마다 다시 읽어 회전에 대응한다. 일반 환경 변수 토큰은 `.env` 변경 후 컨테이너를 재생성해야 한다. 인증 실패는 `source_auth_rejected`로 저장하며 데이터를 지우거나 정상 조회로 처리하지 않는다.

### 실시간성 보고

```bash
docker compose exec collector python -m collector.report --watch-seconds 300
# 예시 기준. 실제 운영 SLO를 정해서 숫자를 지정한다.
docker compose exec collector python -m collector.report \
  --min-samples 100 --p95-limit-ms 1000 --max-error-rate 0.01 --require-nonempty
```

`report`는 DB 읽기만 수행한다. p50/p95/p99 응답 시간, 수집 오류율·빈 응답 비율, DB commit p95, 실제 조회 간격을 반환한다. 원천 시각과 수집 완료 watermark를 검증하지 않았으므로 원천 발생 지연/도착 coverage는 미측정이다. `[]`만 계속 받는 상태에서 HTTP가 빠르다는 이유로 데이터 실시간 도착까지 통과했다고 하지 않는다. 축적 DB의 보관 기간·백업은 운영 정책으로 관리하고 자동 삭제하지 않는다.

## API / 네 CSV 공통 입고 (현재 기본)

`python -m collector.input_runner`는 DB 입력 선택을 읽어 API/CSV/paused 중 하나만 입고한다. `collector.input_cli`로 init/stage-csv/status/select를 실행할 수 있고 화면 `/detection/pipeline`에서 같은 상태를 제어한다. 두 모드는 `input_contract.normalize`와 `input_store.admit`를 공유한다. 피처·Rule·모델은 기존 운영 파이프라인에서 생성/선택하며 별도 데모 모델로 우회하지 않는다.

네 파일 중 Message 모니터링과 BODY를 구분한다. CSV `\N`은 NULL, `PROCESS_DT`는 원문 시각 컬럼으로 투영한다. BODY는 현재 messageId 참조만 저장한다. API의 비어 있지 않은 로그 shape는 매핑 설정으로 연결한다. 설명과 실행 순서는 [SWITCHABLE_INPUTS.md](../../infra/SWITCHABLE_INPUTS.md)를 따른다. `api_runner` 단독 실행은 기존 API 수집 시험 경로로 남겨 두었으며 기본 컨테이너 명령은 아니다.
