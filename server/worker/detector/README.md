# Detector

`python -m detector.runner`는 승인 구성의 P 관측에 Rule과 IF를 적용하고 `run_process_decisions`에 입력 manifest 기준으로 멱등 저장한다. 일부 구성요소가 미실행이면 `partial`/`unavailable`과 사유를 남긴다. 적용·복원은 `applications.py`가 산출물 검증, 모델 준비, 활성 revision CAS 순으로 처리한다. 재시작 시 활성 포인터를 재확인한다.

실제 IF 로더·점수 계산·메모리 캐시는 `../model_adapters.py`에 연결되었다. 승인된 구성의 파일 해시를 확인하고 로드한 뒤 P 관측 한 건마다 점수를 계산한다. 현재는 P 숫자 필드만 변수 계산에 사용한다. 최신 v12 시간창 모델은 ../window_worker.py의 별도 경로로 연결한다. 수집 완료·시각·도착 이벤트 계약이 확인된 stream만 평가하며 margin과 미평가 상태를 그대로 기록한다. T/M as-of 관계와 GPU 상주를 주장하지 않는다. 원천 시간대가 미확정이면 발생→판정 지연을 산출하지 않는다. 판정 조회 API는 DB 기반이고 기존 데모 SSE와 별도다.

판정 구조 v2(`../../contracts/detection-decision.md`)의 순수 판정 로직은 `policy.py`의 v2 파서, `hard_rules.py`·`soft_rules.py`(방법 실행), `decision.py`(조합·등급·위험도·판정 행 열), `evidence_v4.py`(evidence v4·중간 판정 기록 payload)에 있다. DB를 읽지 않으며 `runner.py` 실행 경로에는 아직 연결되지 않았다. 현재 판정은 기존 v1·legacy 경로와 evidence v3로 기록된다.
