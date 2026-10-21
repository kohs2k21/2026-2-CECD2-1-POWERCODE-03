# 저장·추천·학습·탐지 계약 (2026-10-15)

제공 고객 API → Collector → 안전한 설정/응답 snapshot + checkpoint + 실시간성 sample.
검증된 P 관측(CSV 등) → immutable input/job + detection 큐 → Detector 공통 변수 계산 → Rule/ML 선택 경로 → 확정 판정 DB → durable SSE.
저장된 기간/snapshot → LLM 피처·Rule 추천 → 관리자 채택 → Trainer 후보/학습·평가 → 관리자 적용 요청 → Detector 준비·CAS 적용.
확정 판정 → 인증 사용자 요청 → LLM 설명 큐 → saved facts 기반 검증 결과.

## 수집·측정

Bearer 인증, 목록 POST와 상세/진행 중 GET은 첨부 API 명세를 따른다. 현재 진행 중 응답은 `[]` 예시만 확인됐다. source execution/revision/as-of 계약을 임의로 추정하지 않는다. 고객 응답의 안전한 preview는 data_api_snapshots, 각 요청 측정은 ops_api_poll_samples에 쌓인다. 원문 BODY·토큰·adapter 접속정보는 저장하지 않는다.

Collector 실시간성은 HTTP p50/p95/p99·오류율·빈 응답 비율·DB 저장 p95·실제 조회 간격을 측정한다. 원천 발생 지연은 NULL이다. `collector.report`는 DB만 읽고 추가 고객 API 부하를 만들지 않는다. SLO 숫자는 호출자가 지정하며 샘플 부족/기준 초과는 실패로 반환한다.

## API 작업

`POST /api/jobs`:

- recommend_feature/rule, train/evaluate: 관리자.
- explain: 인증 사용자. body `{kind:"explain",sourceId,decisionId}`. 저장 판정/config/evidence를 서버에서 고정한다. 사용자별 동일 근거·모델·prompt·schema의 진행/완료 작업을 재사용한다.
- 새 train 요청은 `detectionPolicy:{mode,deadlineMs,combination}` 필수. mode는 rule_only/ml_only/parallel, deadlineMs는 정수 100~30000, combination은 단일 모드 selected_path / 병렬 any_anomaly이다. 병렬 정책은 해당 구성에서 명시적으로 선택한다. 단일 severity 합산 규칙을 임의로 만들지 않는다.
- Rule 모드를 선택하면 고정 Rule 버전이 필요하다. ML 전용의 Rule 목록은 비어 있어야 한다. Rule 전용은 IF 산출물 없이 후보를 만들고 같은 공통 평가로 검증한다.
- 추천 이후 동일 기간 조건의 rule/train은 snapshotId를 명시해 재사용할 수 있다. source·기간·ready 상태를 검증하며 재시도는 최초 job snapshot을 유지한다.
- train의 IF P99는 기존 train 내부 보정이다. validation 임계값 선택으로 바뀐 것으로 표시하지 않는다. test를 반복 튜닝에 사용하지 않는다.

파생변수 실행 범위는 검증된 Process 행 AST이다. T/M 관계/as-of를 임의 join하지 않는다. Rule의 F.*는 고정 feature set에 연결한다.

## 판정·SSE·설명

모드별 선택 경로만 실행한다. parallel은 Rule·ML을 각자 준비하고 실행하며 모델 부족이 raw Rule을 차단하지 않는다. deadline은 job 처리의 준비 시간을 포함한다. 완료 시각을 넘긴 계산 결과는 채택하지 않고 DB를 쓰지 않는다(중간 판정 기록 포함). 완료 시각 전에는 현재 시도가 lease를 가진 동안, 종합 결과가 처음 이상일 때와 이상 근거가 늘 때만 중간 판정 기록을 남길 수 있다. 중간 판정 기록은 최종 판정을 대신하지 않으며 설명·운영 보고·평가 대상이 아니다. 최종 판정은 작업당 1건으로, 선택 경로(v2는 활성 방법)가 모두 끝나거나 완료 시각에 도달한 시점의 결과다. 같은 시도의 최종 판정이 그 시도의 기록을 닫고, 이전 시도의 기록은 새 시도가 첫 이상 또는 최종을 기록할 때 '대체됨'으로, 작업이 판정 없이 실패하면 '최종 없음'으로 닫힌다. 완료/부분/미확인과 이상 여부는 별개다. 선택 경로 미완료 상태를 정상으로 바꾸지 않고, 확인된 Rule hit는 partial에서도 유지한다.

`GET /api/pipeline/decisions`는 저장 판정 조회, `/events`는 Bearer SSE다. Last-Event-ID/after cursor로 DB event를 순서대로 재전달한다. event 생성 TX는 순번/commit 순서를 직렬화해 늦은 작은 ID를 놓치지 않는다. API는 risk를 재계산해 확정 판정을 덮어쓰지 않는다. 토큰 만료/사용자 변경 시 stream을 닫는다. 기존 anomaly ingest의 데모 risk/SSE는 별도 계약으로 남아 있다.

설명은 요청 후 생성한다. saved evidence의 정확한 문자열/hash·모델·prompt·schema·재사용 key를 고정한다. 모델은 fact ID와 허용된 점검 항목만 선택하고 output을 검증한다. 확정 원인·SHAP·계산하지 않은 기여도는 제공하지 않는다. 일반 사용자 UI는 `/analysis/process-decisions`, 관리자 운영 UI는 `/detection/pipeline`이다.

`/api/pipeline/collector`, `/collector/metrics`는 관리자 조회이며 raw 토큰/원문 BODY를 반환하지 않는다. 자세한 배포·권한·검증 경계는 [Docker 문서](../infra/DOCKER_DEPLOYMENT.md)를 따른다.

## Historical replay demo

`demo.cli` captures CSV or read-only PostgreSQL Process inputs into `data_demo_inputs` under an isolated `demo-replay` source. CPU training uses explicitly `operator_defined` numeric Process fields and the existing real Trainer. Each paced delivery atomically creates an observation, fixed manifest/config job, durable detection message, delivery record and pass checkpoint. Reusing a completed pass does not resend; interrupted passes resume with the same settings. New pass/cycle identities yield independent decisions without changing original source rows or the global active configuration. Demo evidence is labeled in saved decisions; original occurrence latency remains unavailable. Optional T/M metadata capture does not establish verified parent joins or replay message body content. See `../infra/DEMO_REPLAY.md`.


## Shared switchable input

The current `collector.input_runner` admits API and CSV through `input_contract.normalize` / `input_store.admit` under one logical source. It preserves the chosen feature/Rule/model configuration across mode switches and never skips explicit Rule adoption. CSV replay registration is separate from model preparation. `ops_input_controls` is locked at admission; a request fetched under a previous control revision cannot admit observations after a committed switch. CSV progress, the observation, its origin and the durable queue entry commit atomically. API revision numbers denote local changed-snapshot arrivals, not verified source modification versions. An input-selected validation candidate is distinct from the global approved active pointer. Fixed jobs retain their original config and saved origin evidence. Four streams are T/P/M/B reference; BODY values are not staged or collected. Legacy CPU-only `demo.cli` is separate and not the shared full pipeline. See `../infra/SWITCHABLE_INPUTS.md`.
