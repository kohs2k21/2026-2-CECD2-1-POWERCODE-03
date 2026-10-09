# Collector worker

`storage.py`는 Collector가 받은 T·P·M 원천값에서 허용된 필드만 골라 PostgreSQL에 기록하는 저장 경로다. 실제 ESB API를 호출하는 수집 실행기는 아직 없다.

`storage.py`는 정규화된 T·P·M 입력의 허용 필드만 투영해 `data_observations`와 계층별 테이블에 저장한다. 검증된 PROCESS 실행 ID와 revision을 받은 경우에만 같은 트랜잭션에서 탐지 큐에 관측 ID를 넣는다. 동일 dedup_key·동일 허용 필드 재입력은 기존 행을 반환하고, 같은 키의 다른 내용은 오류로 보류한다. 실제 ESB API 조회, 원천 키 구성·revision 계약, 부모 T/P/M 연결은 아직 별도 구현이 필요하다.
