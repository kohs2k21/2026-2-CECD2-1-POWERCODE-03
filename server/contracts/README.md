# Runtime contracts

Reserved for versioned request, response, event, and worker contracts shared by deployable server components. Keep definitions dependency-light and independent of `research/` experiments.

- `auth.md` defines login, Bearer `/me`, and disabled registration responses.
- `anomaly.md` defines authenticated risk evaluation, admin ingest, and SSE events.
- `queues.md`는 PostgreSQL 변수·탐지·학습 큐의 전달 계약을 정의한다.
- `rules.md`는 버전별 Rule 초안 CRUD와 파생변수 참조 규칙을 정의한다.
- `model_adapters.md`는 추후 제공할 변수 생성·Rule 추천·IF 모델의 연결 함수를 정의한다.
- `pipeline.md`는 저장, 기간 학습, 승인, 실시간 탐지의 현재 구현 경계를 정리한다.
- `detection-config-version.md`는 HR·SR·ML 세부 방법 목록 기반 불변 판정 구성 버전(configuration_spec v3)·조합 저장·목록·교체 요청을 정의한다 (#74 초안 — 사용자 결정 반영 완료, 구현은 MyDocs 구현 Issue 분할안 참조).
- `method-catalog.md`·`method-catalog.schema.json`은 시스템 내장 방법 카탈로그(식별자·상태·UI 제작 대상·실행 가능 경로·파라미터 스키마·기본값)를 정의한다 (#74 초안 — 사용자 결정 반영 완료, 구현은 MyDocs 구현 Issue 분할안 참조).
- `config-comparison-evaluation.md`는 불변 평가 세트와 세트 위 다중 구성 비교 작업·결과·적용 gate 관계를 정의한다 (#74 초안 — 사용자 결정 반영 완료, 구현은 MyDocs 구현 Issue 분할안 참조).
- `llm-recommendation-job.md`는 카탈로그 등록 방법 중 추천하는 LLM 비동기 작업(`recommend_methods`)의 입력 범위·결과 JSON을 정의한다 (#74 초안 — 사용자 결정 반영 완료, 구현은 MyDocs 구현 Issue 분할안 참조).
