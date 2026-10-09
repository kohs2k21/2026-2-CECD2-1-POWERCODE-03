# Worker boundary

Worker 단계는 수집, 탐지, 학습, 평가로 분리한다. Collector는 허용된 T·P·M 관측값을 PostgreSQL에 먼저 기록하고, 실행 식별과 revision이 검증된 P 관측값만 같은 트랜잭션에서 탐지 큐에 등록한다. 큐 메시지에는 원천 로그 대신 관측 ID를 넣는다. 커밋 후 Detector가 그 ID로 DB 행을 조회한다. Express가 등록하는 학습 작업은 별도 학습 큐를 사용한다. 큐 계약은 `server/contracts/queues.md`를 참고한다.

## PostgreSQL queue prototype

`pg_queue.py` implements enqueue, leased claim (`FOR UPDATE SKIP LOCKED`), acknowledge and bounded retry/dead-letter transitions over separate PostgreSQL tables. Each call participates in the caller's transaction. Consumers must commit the claim before processing, commit their domain result and acknowledgement together, and use an appropriate lease duration or renewal strategy for long work. Trainer execution and model inference are not wired yet.

Queue acknowledgement and retry now reject expired leases even before another consumer reclaims the row. `renew_lease` extends only a still-valid lease with the matching token. `claim` moves leases that reached `max_attempts` to `dead` after expiration. Long training must also renew the ERD's job-attempt lease; see `trainer/lease.py`.

For an isolated development database, install `requirements.txt`, apply `server/infra/migrations/001_postgres_queues.sql`, then set `DATABASE_URL` locally without printing it. Run `python benchmark_queue.py --queue detection --count 1000 --consumers 4` and repeat for `training`. This benchmark sends synthetic references directly, measures PostgreSQL queue transport only, and leaves completed rows for inspection. Do not run it against a database carrying operational messages. Compare Redis Streams and Kafka later using the same payload counts, consumer counts, hardware, producer rate, and latency/throughput measurements; include database write and model processing in a separate end-to-end benchmark.

The benchmark reads `DATABASE_URL` from the process environment or a local `server/worker/.env` file. The process environment takes precedence. Do not commit the `.env` file. Python Collector, Trainer, and Detector runtime entry points are not implemented yet; their eventual connection settings should use this same convention.

The benchmark scopes both expired-message cleanup and claims to its own `benchmarkRunId`; it cannot lease a message from another run or operational traffic. Still use an isolated database for representative measurements and to avoid load on production tables.

## PostgreSQL 경유 지연 측정

두 migration을 적용한 격리 DB에서 `python benchmark_db_path.py --count 1000 --consumers 4 --rate 100`을 실행하면 합성 PROCESS 관측값을 PostgreSQL에 저장하고 같은 트랜잭션에서 탐지 큐에 ID를 등록한다. 별도 소비자가 큐에서 ID를 임대해 관측값을 조회하고 완료 처리한다. `--rate 0`은 최대 속도 입력이다. p50/p95/p99는 입력 트랜잭션부터 조회·완료까지의 경과 시간이다. 실행 결과는 시험 DB에 남으므로 매번 격리 DB를 사용한다.

이 측정은 `collector/storage.py`의 허용 필드 투영·계층별 저장·탐지 큐 등록과 `detector/input_reader.py`의 P 조회를 사용한다. 고객 ESB API 공개 지연, GPU 모델 추론, 판정 결과 저장, SSE는 포함되지 않는다. 2~3초 전체 목표의 한 구간만 측정한다.
