# 저장·추천·학습·탐지 계약 (2026-10-15)

제공 고객 API → Collector → 안전한 설정/응답 snapshot + checkpoint + 실시간성 sample.
검증된 P 관측(CSV 등) → immutable input/job + detection 큐 → Detector 공통 변수 계산 → Rule/ML 선택 경로 → 확정 판정 DB → durable SSE.
저장된 기간/snapshot → LLM 피처·Rule 추천 → 관리자 채택 → Trainer 후보/학습·평가 → 관리자 적용 요청 → Detector 준비·CAS 적용.
확정 판정 → 인증 사용자 요청 → LLM 설명 큐 → saved facts 기반 검증 결과.

## 수집·측정

Bearer 인증, 목록 POST와 상세/진행 중 GET은 첨부 API 명세를 따른다. 현재 진행 중 응답은 `[]` 예시만 확인됐다. source execution/revision/as-of 계약을 임의로 추정하지 않는다. 고객 응답의 안전한 preview는 data_api_snapshots, 각 요청 측정은 ops_api_poll_samples에 쌓인다. 원문 BODY·토큰·adapter 접속정보는 저장하지 않는다.

Collector 실시간성은 HTTP p50/p95/p99·오류율·빈 응답 비율·DB 저장 p95·실제 조회 간격을 측정한다. 원천 발생 지연은 NULL이다. `collector.report`는 DB만 읽고 추가 고객 API 부하를 만들지 않는다. SLO 숫자는 호출자가 지정하며 샘플 부족/기준 초과는 실패로 반환한다.

공유 입력 Collector(`input_runner`)는 API 조회를 시작 기준 간격(`pollSeconds`)으로 하고, 한 응답의 행을 한 트랜잭션에서 묶음 SQL로 입고한다(행 단위로 하나씩 입고할 때와 같은 revision·중복·작업·큐 결과). 커밋된 detect 작업의 직접 알림은 조회 루프 밖에서 보내며 정본은 늘 DB 큐다([collector README](../worker/collector/README.md)).

## API 작업

`POST /api/jobs`:

- recommend_thresholds, train/evaluate: 관리자. recommend_feature·recommend_rule은 폐지 — 422 `job_kind_abolished`(역할 무관, 작업·큐 메시지 없음, [rules-ui.md](rules-ui.md) AI 규칙 추천 절).
- explain_protocol: 관리자(확정 11/14 사용자). 프로토콜 설정 상세(관측 경로)·프로토콜 알림 대상 경로 설명, `llm_explanation` 큐 — [protocol-explanation-job.md](protocol-explanation-job.md).
- explain: 인증 사용자. body `{kind:"explain",sourceId,decisionId}`. 저장 판정/config/evidence를 서버에서 고정한다. 사용자별 동일 근거·모델·prompt·schema의 진행/완료 작업을 재사용한다.
- 새 train 요청은 `detectionPolicy:{mode,deadlineMs,combination}`(v1) 필수. mode는 rule_only/ml_only/parallel, deadlineMs는 정수 100~30000, combination은 단일 모드 selected_path / 병렬 any_anomaly이다. 병렬 정책은 해당 구성에서 명시적으로 선택한다. 하드룰·소프트룰·ML 대분류의 v2 객체(`version:2`) 형식·검증·v1 구분은 [판정 구조 계약](detection-decision.md)을 따른다. 위험도는 판정 구조 계약의 위험도 정책(근거 방법 단계 중 최고값)으로만 계산하고, 점수·방법 수를 합산한 단일 severity를 만들지 않는다.
- Rule 모드를 선택하면 고정 Rule 버전이 필요하다. ML 전용의 Rule 목록은 비어 있어야 한다. Rule 전용은 IF 산출물 없이 후보를 만들고 같은 공통 평가로 검증한다.
- 추천 이후 동일 기간 조건의 rule/train은 snapshotId를 명시해 재사용할 수 있다. source·기간·ready 상태를 검증하며 재시도는 최초 job snapshot을 유지한다.
- train의 IF P99는 기존 train 내부 보정이다. validation 임계값 선택으로 바뀐 것으로 표시하지 않는다. test를 반복 튜닝에 사용하지 않는다.

파생변수 실행 범위는 검증된 Process 행 AST이다. T/M 관계/as-of를 임의 join하지 않는다. Rule의 F.*는 고정 feature set에 연결한다.

## 판정·SSE·설명

모드별 선택 경로만 실행한다(`detectionPolicy` v1). parallel은 Rule·ML을 각자 준비하고 실행하며 모델 부족이 raw Rule을 차단하지 않는다. deadline은 job 처리의 준비 시간을 포함한다. 완료 시각을 넘긴 계산 결과는 채택하지 않고 DB를 쓰지 않는다(중간 판정 기록 포함). 최종 판정은 작업당 1건이고, 중간 판정 기록(첫 이상·근거 추가·대체됨·최종 없음)은 최종 판정을 대신하지 않으며 설명·운영 보고·평가 대상이 아니다. 완료/부분/미확인과 이상 여부는 별개다. 선택 경로(v2는 활성 방법) 미완료 상태를 정상으로 바꾸지 않고, 확인된 Rule hit는 partial에서도 유지한다. 최종 판정 시점·중간 판정 기록 규칙은 [판정 구조 계약](detection-decision.md) 13절(10/21 확정 의미를 옮긴 정본)을 따른다. v2 조합 규칙·이상/이상 징후 구분·위험도·evidence v4도 같은 계약이 정의한다.

`GET /api/pipeline/decisions`는 저장 판정 조회, `/events`는 Bearer SSE다. Last-Event-ID/after cursor로 DB event를 순서대로 재전달한다. 기본은 최종 판정 이벤트만 `event: decision`(기존 필드 + `eventKind:"final"`)으로 보낸다. `?include=interims`를 주면 012 중간 판정 기록 이벤트(`eventKind` first_anomaly·evidence_added·superseded·no_final)도 같은 커서 순서로 `event: interim` `{eventId,eventKind,interimId,jobId,attemptNo,seq,evidenceVersion,sourceId,processObservationId,createdAt}`(payload 스냅샷 없음, `GET /api/analysis/decisions/:id/interims`로 읽음)로 보낸다. 그 밖의 `include` 값은 400 `invalid_event_include`(#105). event 생성 TX는 순번/commit 순서를 직렬화해 늦은 작은 ID를 놓치지 않는다. API는 risk를 재계산해 확정 판정을 덮어쓰지 않는다. 토큰 만료/사용자 변경 시 stream을 닫는다(토큰 만료는 송신마다, 계정 재확인은 약 1초 간격, [auth.md](auth.md)). 서버는 약 0.25초마다 커서 다음을 한 번에 최대 100건 읽고, 100건이 차면 같은 주기 안에서 커서를 이어 다시 읽는다(주기당 상한 있음, 남은 이벤트는 다음 주기). 응답 형식·이벤트 순서·커서 의미는 같다. 기존 anomaly ingest의 데모 risk/SSE는 별도 계약으로 남아 있다.

설명은 요청 후 생성한다. saved evidence의 정확한 문자열/hash·모델·prompt·schema·재사용 key를 고정한다. 모델은 fact ID와 허용된 점검 항목만 선택하고 output을 검증한다. 확정 원인·SHAP·계산하지 않은 기여도는 제공하지 않는다. 결과 `contributionStatus`는 근거의 ML 방법 결과 기여도(`methodResults[]` `category=ml`의 `attribution.status`)로 정한다: 하나라도 `succeeded`면 `computed`, 아니면 `failed`(Detector 계산 실패), `unreadable`(읽을 수 없는 기록), `skipped` 순, 기여도 키가 없으면(이전 근거) `not_computed`. `computed`면 워커가 저장값으로 만든 문장(요약·반사실·한계)과 쓴 수치를 사실 `ml_attribution` 하나로 더해 모델에 함께 주고, 모델이 고르지 않아도 결과 facts에 넣는다(출력 형식·prompt·reuse key는 그대로). 형식이 계약과 어긋나거나 모르는 `version`이거나 표시할 수 없는 값(아주 큰 수 등)인 기여도는 사실에서 빼고 `unreadable`로 세며 경고 `explanation_attribution_invalid`를 남긴다(설명 작업은 계속). 워커는 `savedEvidence.methodResults[]` 항목의 `methodId`·`category`·`score`·`attribution` 4키를 읽되 `methodResults` 원본은 모델 입력 사실로 넘기지 않고(기여도는 `ml_attribution` 사실로 대신), 문장의 음수·0·동률 허용 오차는 max(1e-9, 1e-9·|score|)(score = 같은 항목의 `score`, 없거나 숫자가 아니면 기여도 원점수)이며 동률은 저장된 topK·players 순서를 그대로 쓴다. 지금 접수 입력 `savedEvidence`에는 `methodResults`가 없어 API가 이 4키를 넘기기 전까지 결과는 `not_computed`다. LLM 컨테이너는 설명 전용 소비자를 따로 두고, GPU 생성 차례는 설명이 추천보다 먼저다. 설명이 대기 상한(`LLM_EXPLANATION_WAIT_SECONDS`, 기본 10초)을 넘기면 작업은 `generation_busy`로 재시도하고 최대 시도 뒤 실패한다([llm-recommendation-job.md](llm-recommendation-job.md) GPU 차례 절). 일반 사용자 UI는 이상 분석 판정 상세 `/analysis/d/:decisionId`(analysis-ui.md, #105 — 옛 `/analysis/process-decisions`는 `/analysis`로 넘김)다. 관리자 운영 UI(옛 `/detection/pipeline`)는 #100에서 옛 화면 폐지, 탐지 설정 화면에서 재연결 예정이다.

`/api/pipeline/collector`, `/collector/metrics`는 관리자 조회이며 raw 토큰/원문 BODY를 반환하지 않는다. 자세한 배포·권한·검증 경계는 [Docker 문서](../infra/DOCKER_DEPLOYMENT.md)를 따른다.

## Historical replay demo

`demo.cli` captures CSV or read-only PostgreSQL Process inputs into `data_demo_inputs` under an isolated `demo-replay` source. CPU training uses explicitly `operator_defined` numeric Process fields and the existing real Trainer. Each paced delivery atomically creates an observation, fixed manifest/config job, durable detection message, delivery record and pass checkpoint. Reusing a completed pass does not resend; interrupted passes resume with the same settings. New pass/cycle identities yield independent decisions without changing original source rows or the global active configuration. Demo evidence is labeled in saved decisions; original occurrence latency remains unavailable. Optional T/M metadata capture does not establish verified parent joins or replay message body content. See `../infra/DEMO_REPLAY.md`.


## Shared switchable input

The current `collector.input_runner` admits API and CSV through `input_contract.normalize` / `input_store.admit` under one logical source. It preserves the chosen feature/Rule/model configuration across mode switches and never skips explicit Rule adoption. CSV replay registration is separate from model preparation. `ops_input_controls` is locked at admission; a request fetched under a previous control revision cannot admit observations after a committed switch. CSV progress, the observation, its origin and the durable queue entry commit atomically. API revision numbers denote local changed-snapshot arrivals, not verified source modification versions. An input-selected validation candidate is distinct from the global approved active pointer. Pinning an input config (UI/API `POST /api/inputs/:sourceId/mode` or operator CLI `input select --config <id>`) accepts only `configuration_spec.schemaVersion` JSON number 1 or 2 (`config_schema_not_supported_yet` otherwise): pinned jobs take the v03 path, and model versions (schemaVersion 3) run only through the active model set ([model-version.md](model-version.md)). A kept config is checked the same way when starting api/csv; only a pause that keeps the existing pin is not checked. Fixed jobs retain their original config and saved origin evidence. Four streams are T/P/M/B reference; BODY values are not staged or collected. Legacy CPU-only `demo.cli` is separate and not the shared full pipeline. See `../infra/SWITCHABLE_INPUTS.md`.
