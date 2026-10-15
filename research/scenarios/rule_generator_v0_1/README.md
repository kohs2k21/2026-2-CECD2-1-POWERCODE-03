# Rule generator v0.1 개발 사례

`coverage.json`은 이미 사용한 32개 집계 입력의 목적·피처·타입·관측 수를 설명한다. 원본 로그나 새로운 독립 벤치마크가 아니다. 생성 입력에는 정답 목록을 제공하지 않는다.

| 판단 목적 | 사례 수 |
|---|---:|
| 확인된 NOT NULL HR | 5 |
| 현재 HR 계약 미확인·적용 불가 | 5 |
| 관측 SR | 9 |
| 제공 p95 기준선 SR | 7 |
| 관측 부족·조건 보존 보류 | 1 |
| 기준선/단위/표본 근거 부족 | 5 |

`confirmed_hr_response.json`은 저장된 모델 HR 응답 하나의 복사본으로 CPU 검증 명령의 입력이다. 새로운 모델 생성이나 수동 보충 규칙으로 세지 않는다. 원문 전체는 [평가 자료](../../evaluation/rule_generator_v0_1/model_outputs.jsonl)에 있다.

변경한 SHA·타입·관측 상태·없는 기준선 ID·새 측정값의 거부는 [CPU 시험](../../models/rule_generator_v0_1/tests/test_publication.py)에서 확인한다. 사례들은 연구 개발 자료이며 서버 운영 fixture로 승격하지 않는다.
