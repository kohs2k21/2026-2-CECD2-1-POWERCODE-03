# Runtime contracts

Reserved for versioned request, response, event, and worker contracts shared by deployable server components. Keep definitions dependency-light and independent of `research/` experiments.

- `auth.md` defines login, Bearer `/me`, and disabled registration responses.
- `anomaly.md` defines authenticated risk evaluation, admin ingest, and SSE events.
- `queues.md`는 PostgreSQL 변수·탐지·학습 큐의 전달 계약을 정의한다.
- `rules.md`는 버전별 Rule 초안 CRUD와 파생변수 참조 규칙을 정의한다.
- `model_adapters.md`는 추후 제공할 변수 생성·Rule 추천·IF 모델의 연결 함수를 정의한다.
- `pipeline.md`는 저장, 기간 학습, 승인, 실시간 탐지의 현재 구현 경계를 정리한다.
