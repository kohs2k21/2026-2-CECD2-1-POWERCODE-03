# 공개 재현 시나리오

`research/integration/hybrid_sr_ml/smoke_test.py`가 가상의 새 T/P/M CSV 32거래를 생성하고
실제 동결 `1.1.0` Detector에 전달한다. 고객 원본·실거래 ID·기존 피처 캐시를 사용하지 않는다.

검사: manifest 무결성, OR·결측·관계 CPU 대조, 분모 0 실제 대체 평가, strict 비교,
ML·논리 판정 보존, TEST를 학습 자료로 잘못 사용하는 요청의 거부.

모든 생성·판정·로그는 `--output`으로 지정한 새 경로에 저장한다.
이 예시는 기능과 연결 검사이며 운영 이상 정답·탐지 정확도의 근거가 아니다.
