# Collector worker

`storage.py`는 Collector가 받은 T·P·M 원천값에서 허용된 필드만 골라 PostgreSQL에 기록하는 저장 경로다. 실제 ESB API를 호출하는 수집 실행기는 아직 없다.

`storage.py`는 정규화된 T·P·M 입력의 허용 필드만 투영해 `data_observations`와 계층별 테이블에 저장한다. 검증된 PROCESS 실행 ID와 revision을 받은 경우에만 같은 트랜잭션에서 탐지 큐에 관측 ID를 넣는다. 동일 dedup_key·동일 허용 필드 재입력은 기존 행을 반환하고, 같은 키의 다른 내용은 오류로 보류한다. 실제 ESB API 조회, 원천 키 구성·revision 계약, 부모 T/P/M 연결은 아직 별도 구현이 필요하다.

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

동일 `dataset-key`와 `replay-key` 및 동일한 파일·행 순서로 다시 실행하면 중복 저장하지 않는다. 파일을 수정하거나 다른 파일을 재생할 때는 새 `replay-key`를 쓴다. 이 모드의 `revision_no`는 **CSV 안의 행 번호**이고 실행 키는 `replay-key`로 분리된다. 원천 ESB의 실제 수정 순서나 시간대가 검증됐다는 뜻이 아니다. PROCESS는 `TRANSACTION_ID`·`PROCESS_ID`·유효한 `RETRY_COUNT`가 모두 있을 때만 큐에 등록한다. BODY는 현재 제공된 컬럼만으로 부모 P와 연결할 수 없어 미연결 M 관측값으로 저장한다. 큐에 등록된 메시지를 실제 판정하는 Detector 실행기는 아직 없다.
