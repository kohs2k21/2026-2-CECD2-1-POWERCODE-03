# Python 워커 운영 경계

현재 경로는 `Collector → 원천 관측 저장 + 변수 큐 → Feature worker → 탐지 큐 → Detector → 판정 저장`이다. 변수 생성·Rule 추천·학습·평가는 별도 학습 큐와 Trainer가 담당한다. 큐에는 원문 대신 관측/작업 ID와 버전만 넣는다. PostgreSQL 큐는 at-least-once로 동작하므로 처리와 ack를 같은 DB 트랜잭션으로 묶는다.

실제 모델 연결 지점은 [`model_adapters.py`](model_adapters.py)이다. 피처 v5·Rule v2는 GPU 생성 서비스로 호출하며 Process 행 계산 AST·측정·조건 근거를 검증한다. 해당 고정 변수셋으로 Process IF를 새로 학습한다. 최신 v12 IF/K-means는 `window_worker.py`에서 연속 1분 이력으로 별도 실행한다. 설정·번들·GPU 배치·DB 적용 순서는 [DEPLOYMENT.md](DEPLOYMENT.md)를 따른다. 생성 서비스가 없으면 모델 추천 성공을 만들지 않는다.

`DATABASE_URL`과 `ARTIFACT_ROOT`는 프로세스 환경 또는 Git에서 제외한 `server/worker/.env`에서 읽는다. 환경 변수가 우선한다. 최신 ERD와 005 연구 연결까지 필요하며 정확한 migration 순서는 DEPLOYMENT.md를 따른다. `server/worker`에서 의존성 설치 후 다음 실행 진입점을 각각 별도 프로세스로 실행한다.

```text
python -m collector.replay_csv ... --apply
python -m feature_worker
python -m trainer.runner
python -m detector.runner
python -m monitor
```

승인 구성이 없으면 변수 큐 메시지는 대기한다. 실제 ESB API 계약, 운영 데이터에서의 탐지 품질과 전체 판정 지연은 별도 검증 대상이다. scikit-learn Isolation Forest는 CPU 구현이다.

격리 DB의 큐 비교는 `benchmark_queue.py --queue feature|detection|training`을 사용한다. Redis Streams/Kafka와 비교할 때는 동일한 입력·소비자 수·하드웨어·측정 범위를 사용한다. `benchmark_db_path.py`는 실제 모델/외부 API 지연을 포함하지 않는다.
