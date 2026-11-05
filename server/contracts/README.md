# Runtime contracts

Reserved for versioned request, response, event, and worker contracts shared by deployable server components. Keep definitions dependency-light and independent of `research/` experiments.

- `auth.md` defines login, Bearer `/me`, and disabled registration responses.
- `anomaly.md` defines authenticated risk evaluation, admin ingest, and SSE events.
- `queues.md`는 PostgreSQL 변수·탐지·학습 큐의 전달 계약을 정의한다.
- `rules.md`는 버전별 Rule 초안 CRUD와 파생변수 참조 규칙을 정의한다.
- `model_adapters.md`는 추후 제공할 변수 생성·Rule 추천·IF 모델의 연결 함수를 정의한다.
- `operations-ui.md`는 운영 현황(`/operations`) 읽기 API(`/api/operations/*`)·위젯 배치와 이상 근거 상세·피드백 UI 경계를 정의한다.
- `analysis-ui.md`는 이상 분석(`/analysis`, `/analysis/d/:decisionId`) 판정 목록·상세·중간 판정 기록·Process Flow 읽기 API와 처리 상태·읽음·조사·조치 기록 쓰기 API(`/api/analysis/*`, 014 표), 판정 결과별·위험도별·분류함 분류 매핑(저장 등급·위험도만), 실시간 반영(중간 판정 이벤트 포함)·설명 요청 화면 규칙을 정의한다(#105).
- `rules-ui.md`는 룰 설정(`/detection/rules`) 화면 구성·저장·되돌리기·수식 추천 화면 규칙과 룰 세트 API 구현에서 정한 값, 화면 표시 용어(버전과 숫자)를 정의한다(#109).
- `pipeline.md`는 저장, 기간 학습, 승인, 실시간 탐지의 현재 구현 경계를 정리한다.
- `detection-decision.md`는 Detector 판정 구조(하드룰·소프트룰·ML 분류와 식별자, `detectionPolicy` v2 조합 규칙, 이상/이상 징후 등급, 위험도, 룰 세트 리비전 기록, evidence v4, 중간 판정 기록)를 정의한다 (#92 초안, Detector 소유 — 사용자 결정 U1~U12 11/03 반영).
- `rule-set.md`는 룰 세트(하드룰 켜기/끄기 + 소프트룰 등록 필드·조건·임계값·위험도)의 현재 상태·저장 즉시 적용·리비전(정수 + 내용 hash)·스냅샷 이력·되돌리기(모든 이력 항목, 삭제된 규칙 재생성 허용)·동시 편집 409를 정의한다 (#94 초안, 룰은 버전 관리 없음; #116 정정 — 하드룰 내장 목록 4개 중 `hard:process-status`만 위험도 편집, 하드룰 `severity`를 포함하는 `contentHashVersion` 2, 스냅샷에 없는 내장 규칙은 꺼짐).
- `model-version.md`는 `ml:*` 1개를 담는 불변 모델 버전(configuration_spec v3 재정의, 모델 1개 = 버전 계열)·목록, 활성 모델 집합(0..N, 활성화·운영에서 빼기·교체·복원·리비전 이력), 운영 설정 `detectionPolicy` 전역 값(리비전, r0 = 내장 기본), 판정 시 일관 읽기 3요소를 정의한다 (#94 초안, #74 `detection-config-v1` 대체; 단위는 사용자 11/02 확정 — 모델 1개 = 버전 계열·같은 modelKey 1개).
- `detection-config-version.md`는 #74 묶음 구성 계약이 위 두 계약으로 분리되었음을 안내하는 호환 문서다 (#94).
- `method-catalog.md`·`method-catalog.schema.json`은 대분류 hard/soft/ml 식별자 체계, ML 방법 카탈로그(`ml:*`), 소프트룰 등록 필드 목록(`softRuleFields[]`, 단위·타입·원천 식·원천 열 누락 정책 `sourceMissingPolicy` — END_TIME 누락은 이상 아님·표시 자릿수 모드)을 정의한다 (#94 v2 초안, 통계 SR 삭제).
- `config-comparison-evaluation.md`는 불변 평가 세트와 세트 위 다중 모델 버전 비교 작업·결과·교체 추천(같은 `modelKey`끼리, `replacementRecommendations[]`)·자동 비교 평가·적용 gate 관계를 정의한다 (#94 초안, 모델 단위로 축소, 룰 세트·정책 리비전 고정).
- `llm-recommendation-job.md`는 소프트룰 임계값 추천 — LLM 일괄 작업(`recommend_thresholds`, 저장된 리비전의 켜진 소프트룰 대상)과 LLM 없는 수식 추천 API(기준 기간: 운영 모델 학습 시작~현재 / 최근 1·3개월 / 직접 지정) — 의 입력 범위·결과 JSON·실패 구분을 정의한다 (#94 초안, `recommend_rule` 폐지·`recommend_methods` 보류). `llm-recommendation-job.example.json`은 API 시험과 워커 시험이 함께 읽는 접수 예시, `threshold-values.example.json`은 워커 `threshold` 출력과 룰 세트 저장 검증을 잇는 값 예시다 (#121).
- 종합 판정(`detectionPolicy` v2 `k:{hard,soft,ml}`의 의미·조합 규칙·이상/이상 징후·evidence v4·하드룰 내장 목록·소프트룰 실행 의미론)은 Detector 소유 `detection-decision.md`(#92)가 정의한다. 정책 값의 저장 위치·리비전은 `model-version.md` 운영 설정 절.
