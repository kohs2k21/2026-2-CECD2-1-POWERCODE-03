# 연구 모델 연결 검증 기록

2026-10-14 로컬 검증. 실제 GPU 서버에 접속하지 않았다.

| 범위 | 결과 |
|---|---|
| 워커 단위 시험 | 20개 통과: 기존 수집/임대, IF, 생성 AST·타입·단위·버전, 결측·0 분모, 시간창 빈 분·revision·늦은 입력 |
| 백엔드 빌드 | 통과 |
| 프런트엔드 빌드·전체 시험 | 통과. 기존 bundle 크기 경고 있음 |
| 작업 API 통합 | 통과: 관리자 권한, idempotency, v03 LLM spec, Rule 근거 보존, 시간창 조회·권한 |
| 변수 추천 경계 통합 | 통과: 고정 스냅샷, 현재 임대, 결과 감사 기록 |
| 기존 실제 IF 통합 | 통과: 학습·평가·구성 적용·새 관측 판정 |
| 새 전체 연구 연결 통합 | 통과: 합성 P 1,000행, 생성 HTTP 계약, 파생변수 계산, grounded SR, 실제 IF, 평가·적용·판정 |
| 실제 v12 IF/K-means | 두 저장 모델 hash 검사·적재·추론 통과. 빈 분을 포함한 8분 grid에서 각각 8개 판정 |
| 모델 캐시 초기화 후 재실행 | 동일 DB 이력을 복원하고 새 중복 판정 0개 |
| 최신 DB 계약 | 기존 001/002, ERD v03, Rule/Feature 보호, 005 연구 연결 SQL을 새 PostgreSQL 17 DB에 모두 적용 |

생성 모델의 GPU 실행은 수행하지 않았다. 통합 시험에서는 tests/research_fixture.py의 명시적 계약 시험 응답을 localhost 생성 서비스에 사용한다. 이 응답은 Qwen의 실제 생성 품질을 의미하지 않는다. IF 학습과 v12 추론에는 실제 sklearn 코드·저장 모델을 사용한다.

운영 데이터의 탐지 정확도·전체 지연·GPU 메모리·새 Qwen 응답 의미 검토·ESB API 수집은 이 로컬 시험 결과에 포함하지 않는다. 관계·as-of 계약이 확인되지 않은 T/M 집계는 현재 Process 행 실행기로 변환하지 않고 보류한다.

재현은 DEPLOYMENT.md의 전체 migration을 적용한 독립 localhost DB와 연구 실행 번들이 필요하다.

```bash
cd server/worker
python -m unittest discover -s tests -p 'test_*.py' -v
# 아래 DB는 이름까지 전용 시험 DB여야 한다.
DATABASE_URL='postgresql://TEST_USER:TEST_PASSWORD@127.0.0.1:TEST_PORT/qa_research_pipeline' \
RESEARCH_BUNDLE_ROOT='/path/to/generated/research-runtime' \
python tests/integration_research_pipeline.py
```

각 통합 시험은 서로 다른 DB에서 실행한다. training 큐를 공유하면 시험이 다른 작업을 먼저 소비할 수 있다.
