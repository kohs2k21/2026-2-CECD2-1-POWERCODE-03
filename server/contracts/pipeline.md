# 저장·추천·학습·탐지 계약 (2026-10-15)

제공 고객 API → Collector → 안전한 설정/응답 snapshot + checkpoint + 실시간성 sample.
검증된 P 관측(CSV 등) → immutable input/job + detection 큐 → Detector 공통 변수 계산 → Rule/ML 선택 경로 → 확정 판정 DB → durable SSE.
저장된 기간/snapshot → LLM 피처·Rule 추천 → 관리자 채택 → Trainer 후보/학습·평가 → 관리자 적용 요청 → Detector 준비·CAS 적용.
확정 판정 → 백엔드 코드 문장 → LLM 설명 큐 → 경량 모델 다듬기 → 코드 확인(실패면 코드 문장).

## 수집·측정

Bearer 인증, 목록 POST와 상세/진행 중 GET은 첨부 API 명세를 따른다. 현재 진행 중 응답은 `[]` 예시만 확인됐다. source execution/revision/as-of 계약을 임의로 추정하지 않는다. 고객 응답의 안전한 preview는 data_api_snapshots, 각 요청 측정은 ops_api_poll_samples에 쌓인다. 원문 BODY·토큰·adapter 접속정보는 저장하지 않는다.

Collector 실시간성은 HTTP p50/p95/p99·오류율·빈 응답 비율·DB 저장 p95·실제 조회 간격을 측정한다. 원천 발생 지연은 NULL이다. `collector.report`는 DB만 읽고 추가 고객 API 부하를 만들지 않는다. SLO 숫자는 호출자가 지정하며 샘플 부족/기준 초과는 실패로 반환한다.

공유 입력 Collector(`input_runner`)는 API 조회를 시작 기준 간격(`pollSeconds`)으로 하고, 한 응답의 행을 한 트랜잭션에서 묶음 SQL로 입고한다(행 단위로 하나씩 입고할 때와 같은 revision·중복·작업·큐 결과). 커밋된 detect 작업의 직접 알림은 조회 루프 밖에서 보내며 정본은 늘 DB 큐다([collector README](../worker/collector/README.md)).

## API 작업

`POST /api/jobs`:

- recommend_thresholds, train/evaluate: 관리자. recommend_feature·recommend_rule은 폐지 — 422 `job_kind_abolished`(역할 무관, 작업·큐 메시지 없음, [rules-ui.md](rules-ui.md) AI 규칙 추천 절).
- explain_protocol: 관리자(확정 11/14 사용자). 프로토콜 설정 상세(관측 경로)·프로토콜 알림 대상 경로 설명, `llm_explanation` 큐 — [protocol-explanation-job.md](protocol-explanation-job.md).
- explain: 인증 사용자. body `{kind:"explain",sourceId,decisionId}`. 저장 판정·구성과 코드 문장을 서버에서 고정한다(아래 판정·SSE·설명 절). 새 형식(`summary-sentences-v1`)은 요청자와 무관하게 판정마다 공유 재사용한다: `reuse_key` = SHA-256 `[decisionId, templateVersion, 정본 입력 hash, model_version, prompt_version, output_schema_version]`(요청자 없음). 같은 키의 advisory lock 안에서 ① `queued`·`running`·`succeeded` 작업이 있으면 그 작업 ② 없고 같은 키의 `failed` 작업이 끝난 지 10분 안이면 그 실패 작업(쿨다운, 새 작업 없음) ③ 그 밖은 새 작업(`max_attempts = 1`, 재시도 없음)을 돌려준다. 인증·`sourceId` 판정 소유 확인은 요청마다 그대로이고 재사용은 그 확인을 통과한 뒤에만 한다. 멱등키 확인은 요청자별 그대로다. 이전 형식(`saved-decision-v1`) 작업은 이전 키 규칙(요청자별)을 그대로 쓴다. 작업 형식·실패 표는 [llm-recommendation-job.md](llm-recommendation-job.md) AI 근거 요약 다듬기 절.
- 새 train 요청은 `detectionPolicy:{mode,deadlineMs,combination}`(v1) 필수. mode는 rule_only/ml_only/parallel, deadlineMs는 정수 100~30000, combination은 단일 모드 selected_path / 병렬 any_anomaly이다. 병렬 정책은 해당 구성에서 명시적으로 선택한다. 하드룰·소프트룰·ML 대분류의 v2 객체(`version:2`) 형식·검증·v1 구분은 [판정 구조 계약](detection-decision.md)을 따른다. 위험도는 판정 구조 계약의 위험도 정책(근거 방법 단계 중 최고값)으로만 계산하고, 점수·방법 수를 합산한 단일 severity를 만들지 않는다.
- Rule 모드를 선택하면 고정 Rule 버전이 필요하다. ML 전용의 Rule 목록은 비어 있어야 한다. Rule 전용은 IF 산출물 없이 후보를 만들고 같은 공통 평가로 검증한다.
- 추천 이후 동일 기간 조건의 rule/train은 snapshotId를 명시해 재사용할 수 있다. source·기간·ready 상태를 검증하며 재시도는 최초 job snapshot을 유지한다.
- train의 IF P99는 기존 train 내부 보정이다. validation 임계값 선택으로 바뀐 것으로 표시하지 않는다. test를 반복 튜닝에 사용하지 않는다.

파생변수 실행 범위는 검증된 Process 행 AST이다. T/M 관계/as-of를 임의 join하지 않는다. Rule의 F.*는 고정 feature set에 연결한다.

## 판정·SSE·설명

모드별 선택 경로만 실행한다(`detectionPolicy` v1). parallel은 Rule·ML을 각자 준비하고 실행하며 모델 부족이 raw Rule을 차단하지 않는다. deadline은 job 처리의 준비 시간을 포함한다. 완료 시각을 넘긴 계산 결과는 채택하지 않고 DB를 쓰지 않는다(중간 판정 기록 포함). 최종 판정은 작업당 1건이고, 중간 판정 기록(첫 이상·근거 추가·대체됨·최종 없음)은 최종 판정을 대신하지 않으며 설명·운영 보고·평가 대상이 아니다. 완료/부분/미확인과 이상 여부는 별개다. 선택 경로(v2는 활성 방법) 미완료 상태를 정상으로 바꾸지 않고, 확인된 Rule hit는 partial에서도 유지한다. 최종 판정 시점·중간 판정 기록 규칙은 [판정 구조 계약](detection-decision.md) 13절(10/21 확정 의미를 옮긴 정본)을 따른다. v2 조합 규칙·이상/이상 징후 구분·위험도·evidence v4도 같은 계약이 정의한다.

`GET /api/pipeline/decisions`는 저장 판정 조회, `/events`는 Bearer SSE다. Last-Event-ID/after cursor로 DB event를 순서대로 재전달한다. 기본은 최종 판정 이벤트만 `event: decision`(기존 필드 + `eventKind:"final"`)으로 보낸다. `?include=interims`를 주면 012 중간 판정 기록 이벤트(`eventKind` first_anomaly·evidence_added·superseded·no_final)도 같은 커서 순서로 `event: interim` `{eventId,eventKind,interimId,jobId,attemptNo,seq,evidenceVersion,sourceId,processObservationId,createdAt}`(payload 스냅샷 없음, `GET /api/analysis/decisions/:id/interims`로 읽음)로 보낸다. 그 밖의 `include` 값은 400 `invalid_event_include`(#105). event 생성 TX는 순번/commit 순서를 직렬화해 늦은 작은 ID를 놓치지 않는다. API는 risk를 재계산해 확정 판정을 덮어쓰지 않는다. 토큰 만료/사용자 변경 시 stream을 닫는다(토큰 만료는 송신마다, 계정 재확인은 약 1초 간격, [auth.md](auth.md)). 서버는 약 0.25초마다 커서 다음을 한 번에 최대 100건 읽고, 100건이 차면 같은 주기 안에서 커서를 이어 다시 읽는다(주기당 상한 있음, 남은 이벤트는 다음 주기). 응답 형식·이벤트 순서·커서 의미는 같다. 기존 anomaly ingest의 데모 risk/SSE는 별도 계약으로 남아 있다.

설명(`explain`, AI 근거 요약 `evidence-summary-v1`)은 요청 후 생성한다. 백엔드가 저장 근거로 만든 코드 문장(`sentences`)과 판정 낱말 순서열·원천 값 토큰을 접수 입력으로 고정하고, `llm_spec`은 `evidence_version` `summary-sentences-v1`·`prompt_version` `evidence-polish-v4`·`output_schema_version` `evidence-polish-result-v1`·`model_version`(생성 서비스 `/health`의 `polishModel`)이다. 워커는 원천 근거를 읽지 않는다: 입력 형식을 확인하고 코드 문장에서 가림 토큰(값·시각·이름표·식별자)·판정 낱말·`(숫자, 단위)`·순서 낱말을 직접 뽑아 접수 목록과 대조한 뒤(다르면 `explanation_input_rejected`), 생성 서비스 `polish_explanation`(따로 적재한 경량 다듬기 모델, 평문 출력)에 코드 문장만 넘겨 한 문단으로 다듬고, 출력을 확인 규칙 10개(형식 → 이름 → 숫자 → 판정 → 방향 → 금지 순, 첫 실패 코드만)로 검사한다. LLM은 다듬기에만 쓰고 문장·근거·확인할 것은 고르지 않는다. 통과면 결과 문단 `source=llm`, 확인 실패면 코드 문장(`source=code` + 실패 코드), 출력 잘림이면 코드 문장(`check.status=not_run`·`generation_incomplete`)이고 셋 다 작업 `succeeded`다. 생성 실패는 재시도 없는 `failed`(`generation_service_unavailable`·`generation_busy`·`generation_timeout`·`llm_model_mismatch` 등)다. 입력 형식·확인 규칙·결과 형식·실패 표는 [llm-recommendation-job.md](llm-recommendation-job.md) AI 근거 요약 다듬기 절, 통과·실패·기대 대체 예는 공용 벡터 [evidence-polish-check-vectors.json](evidence-polish-check-vectors.json)이다. 확정 원인·SHAP·계산하지 않은 기여도는 문단에 넣지 않는다. 이전 형식(`saved-decision-v1` — 저장 사실 ID와 허용 점검 항목만 고르기, 결과 `facts`·`nextChecks`·`causeStatus`·`contributionStatus`)으로 이미 쌓인 작업은 워커가 이전 동작으로 끝내고(ML 기여도 사실 `ml_attribution`은 없앴고 `contributionStatus`는 `not_computed`), 저장된 이전 결과 행은 지우지 않는다. LLM 컨테이너는 설명 전용 소비자를 따로 두고, GPU 생성 차례는 설명(다듬기 포함)이 추천보다 먼저다. 설명이 대기 상한(`LLM_EXPLANATION_WAIT_SECONDS`, 기본 10초)을 넘기면 새 형식 작업은 `generation_busy`로 바로 실패하고, 이전 형식 작업만 재시도 뒤 실패한다([llm-recommendation-job.md](llm-recommendation-job.md) GPU 차례 절). 일반 사용자 UI는 이상 분석 판정 상세 `/analysis/d/:decisionId`(analysis-ui.md, #105 — 옛 `/analysis/process-decisions`는 `/analysis`로 넘김)다. 관리자 운영 UI(옛 `/detection/pipeline`)는 #100에서 옛 화면 폐지, 탐지 설정 화면에서 재연결 예정이다.

`/api/pipeline/collector`, `/collector/metrics`는 관리자 조회이며 raw 토큰/원문 BODY를 반환하지 않는다. 자세한 배포·권한·검증 경계는 [Docker 문서](../infra/DOCKER_DEPLOYMENT.md)를 따른다.

## Historical replay demo

`demo.cli` captures CSV or read-only PostgreSQL Process inputs into `data_demo_inputs` under an isolated `demo-replay` source. CPU training uses explicitly `operator_defined` numeric Process fields and the existing real Trainer. Each paced delivery atomically creates an observation, fixed manifest/config job, durable detection message, delivery record and pass checkpoint. Reusing a completed pass does not resend; interrupted passes resume with the same settings. New pass/cycle identities yield independent decisions without changing original source rows or the global active configuration. Demo evidence is labeled in saved decisions; original occurrence latency remains unavailable. Optional T/M metadata capture does not establish verified parent joins or replay message body content. See `../infra/DEMO_REPLAY.md`.


## Shared switchable input

The current `collector.input_runner` admits API and CSV through `input_contract.normalize` / `input_store.admit` under one logical source. It preserves the chosen feature/Rule/model configuration across mode switches and never skips explicit Rule adoption. CSV replay registration is separate from model preparation. `ops_input_controls` is locked at admission; a request fetched under a previous control revision cannot admit observations after a committed switch. CSV progress, the observation, its origin and the durable queue entry commit atomically. API revision numbers denote local changed-snapshot arrivals, not verified source modification versions. An input-selected validation candidate is distinct from the global approved active pointer. Pinning an input config (UI/API `POST /api/inputs/:sourceId/mode` or operator CLI `input select --config <id>`) accepts only `configuration_spec.schemaVersion` JSON number 1 or 2 (`config_schema_not_supported_yet` otherwise): pinned jobs take the v03 path, and model versions (schemaVersion 3) run only through the active model set ([model-version.md](model-version.md)). A kept config is checked the same way when starting api/csv; only a pause that keeps the existing pin is not checked. Fixed jobs retain their original config and saved origin evidence. Four streams are T/P/M/B reference; BODY values are not staged or collected. Legacy CPU-only `demo.cli` is separate and not the shared full pipeline. See `../infra/SWITCHABLE_INPUTS.md`.
