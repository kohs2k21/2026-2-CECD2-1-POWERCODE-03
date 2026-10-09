# Detector worker

상시 실행될 Python Detector는 `detection.logs.v1`만 소비하고 승인된 모델을 GPU 메모리에 상주시킬 계획이다. 실제 추론 실행기는 아직 없다. 큐 계약은 `server/contracts/queues.md`를 참고한다.

`server/worker/benchmark_db_path.py`는 큐에서 관측 ID를 받아 PostgreSQL 행을 읽고 완료 처리하는 구간만 측정한다. 실제 모델 추론이나 판정 저장은 수행하지 않는다.

`input_reader.py`는 탐지 메시지의 관측 ID로 저장된 PROCESS 관측값을 읽는다. 현재는 P 행만 조회하며 당시 가용한 T·M 관계 결합, 승인 모델 추론, 판정 저장, SSE 발행은 구현 전이다.
