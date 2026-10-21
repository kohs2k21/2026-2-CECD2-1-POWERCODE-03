# 방법 카탈로그 계약 v1 (초안, Issue #74)

- 계약 버전: `method-catalog-v1`; 항목 스키마는 [method-catalog.schema.json](method-catalog.schema.json). 근거 코드 origin/dev `872f540`
- 상위 결정: 플러그형 설계 방향 3차 지시(시스템 내장 목록, 사용자 등록 아님, 확장 쉬운 인터페이스만), 4·5차 지시(Git 기준 = UI 제작 범위, 실행 가능 여부 별도 필드, 실행 불가 선택 시 오류 그대로), Q-B=A(시간창 SR 24종 등재), Q-H=A(IF v1 기본값 = 서버 현재값)
- 표기: **[확정]** / **★** 제안(결정 대기) / **충돌**
- 범위: HR·SR·ML 세부 방법의 시스템 내장 레지스트리. 식별자·kind·상태·UI 제작 대상 근거·실행 가능 경로·파라미터 스키마·기본값·입력 요건·출처. 구성 버전([detection-config](detection-config-version.md))·LLM 추천([llm-recommendation-job](llm-recommendation-job.md))·WebUI가 같은 식별자를 사용
- 제외: 방법의 실제 어댑터 구현, 종합 판정 규칙(Detector), 사용자 정의 방법 등록 API(목표 아님)

## 원칙

- **UI 제작 대상 = Git에 올라온 방법**(`server/` + `research/`) [확정]. 팀원 논의 수준(Git 없음)은 등재하지 않음
- **실행 가능 = `server/`에 해당 경로 어댑터가 존재** [확정]. `status`와 `executable`은 별개 필드. research이면서 실행 가능(없음)·supported이면서 특정 경로 실행 불가(KMeans의 PROCESS)가 모두 표현 가능
- 실행 불가 방법의 취급을 셋으로 구분: ① 목록·UI 제작 대상 ✓ [확정 Git 기준] ② 화면 선택은 WebUI 2.2·2.3절대로 **비활성(사유 문구 미표시)** — 사용자 확인 10/21 ③ 서버는 **저장 거부** 422 `method_not_executable:<methodId>` **[확정 — 사용자 10/21, Detector Q3-1·설계 방향 4.5절]**: 실행 불가 방법은 선택 자체 불가. 연구 폴더 항목은 개발 병행 중 화면 표시(비활성)일 뿐, 최종 버전 목록에는 없음. 학습·평가·적용 요청도 같은 코드로 거부. 목업 성공·빈 결과 대체 금지 [확정]
- 항목 추가는 `catalogVersion` 증가. 제거는 하지 않고 `status=deprecated`로 전환(기존 구성이 참조하므로)
- 기본값은 출처를 함께 기록(`defaultsSource`). 연구 보고서 미확인 값은 `defaults=null`로 두고 채우지 않음

## 항목 필드

| 필드 | 의미 |
|---|---|
| `methodId` | `<kind>:<key>` ([detection-config](detection-config-version.md) 식별자 절) |
| `kind` | `hr` / `sr` / `ml` (`methods[]` 항목). `rule`은 `instanceTemplates[]`에만 있고 `methods[]`에는 오지 않음(schema `$defs.method.kind` enum에서 제외) |
| `category` | 대분류 `HR` / `SR` / `ML`(rule은 HR — **[확정 10/21]** Detector Q2 A) |
| `displayName` | 표시 이름(한글 가능) |
| `status` | `supported`(운영 코드·시험 있음) / `research`(연구 코드만 또는 연구 결론 미확정) / `deprecated` |
| `methodVersion` | 실행 코드·산출물 형식 버전 문자열(ML은 `ml_artifacts.format_version`과 동일 값) |
| `uiTarget` | `{scope: "server"|"research", path: "<Git 경로>", ref?: "<보조 경로>"}` — UI 제작 범위 근거 |
| `executable` | `{process: boolean, window: boolean}` 경로별 실행 가능. `process`=PROCESS 판정(Detector `detector/`), `window`=시간창(`window_worker.py`) |
| `executableReason` | `{process?: string, window?: string}` 실행 불가 사유 코드: `server_adapter_missing` / `input_unit_mismatch` / `research_only` |
| `runtime` | `{library: "scikit-learn"|"scipy"|"statsmodels"|"server-python"|"research-bundle", artifactFormat?: string}` |
| `parameterSchema` | JSON Schema draft 2020-12 부분집합. 최상위 `type:"object"`·`additionalProperties:false` 필수, `properties`·`required`·`allOf`; 속성별 `type/enum/const/minimum/maximum/exclusiveMinimum/exclusiveMaximum/items/minItems/maxItems/description`(schema.json `$defs.parameterProperty`와 동일 목록) |
| `instanceTemplates` | 선택. 인스턴스형 방법(현재 `rule`)의 템플릿 `{kind, category, displayName, status, uiTarget, executable, instanceSource, instanceIdPattern, inputRequirements, sourceRef}` 배열 ★. `methods[]`에는 템플릿을 두지 않고 구성의 `methods[]`에는 인스턴스 `rule:<rule_key>`만 들어감(등록 Rule 목록은 Rule API에서 조회) |
| `parameterSchemaStatus` | `verified`(서버 코드와 대조) / `research_declared`(연구 코드 선언값) / `unconfirmed`(파라미터 미확인, 빈 schema) |
| `defaults` | `parameterSchema`를 만족하는 객체 또는 null |
| `defaultsSource` | 출처 문자열(경로:행 또는 보고서) |
| `inputRequirements` | `{unit: "process_row"|"aggregated_window", featureSet: "required"|"optional"|"not_required", numericOnly?, maxFeatures?, minTrainRows?, fitOperations?, fields?, aggregationMinutes?}` |
| `sourceRef` | `{path, lines?, commit}` 근거 코드 위치 |
| `resultSchema` | 세부 방법별 결과 형식 버전(`method-result-v1` ★, 아래 절) |
| `canDisable` | 항상 true(HR 포함) [확정] |
| `instanceSource` | `instanceTemplates[]` 항목만: `ml_rule_versions`(인스턴스는 등록 Rule 버전). `methods[]` 항목에는 없음 |
| `notes` | 제약·주의 |

## 세부 방법별 결과 형식 `method-result-v1` — Detector evidence v4에 정렬 [확정 Q8 A, 10/21]

- 계측(#66)·종합 판정·방법별 결과는 **Detector evidence v4 한 계약**으로 묶임(사용자 Q8 A). 아래 필드는 v4 확정 전의 **자리 초안**이며, 확정 시 v4 필드 정의를 그대로 따름(이 문서는 참조만)
- Trainer 평가·Detector evidence가 공통으로 쓰는 **방법 1개의 결과**. 조합은 Detector 계약
- `{methodId, kind, methodVersion, status: "succeeded"|"not_ready"|"failed"|"skipped", anomalous: true|false|null, score: number|null, code: string|null, severity: string|null, reason: string|null, evidence: object[]}`
- `score`는 ML·통계 SR만(점수 척도는 방법마다 다름, 비교·합산 금지). `code` 형식(`PROCESS_STATUS_*`, Rule `outcome.decisionCode`, `SR_<KEY>`, `<KEY>_ANOMALY`)은 **Detector D12 제안이며 Q4(등급) 보류에 묶여 미확정**
- 현행 `checkers.{hr,sr,ml}{status,anomalous,evidence}`(`detector/runner.py:88-89`)는 대분류 결과로 유지, 방법별 배열은 evidence v4에서 추가(Detector)

## 레지스트리 인터페이스 ★ Q-K=A

- 원본: `server/worker/methods/registry.py`(Trainer 소유 제안). 항목은 dataclass 1개 + 어댑터 프로토콜(`prepare(reference)` / `fit(rows, parameters) -> artifacts` / `score(reference, values) -> MethodResult`). ML은 `model_adapters.py`의 `*_iforest` 함수를 첫 어댑터로 등록
- API(TypeScript)는 빌드 시 생성된 `server/contracts/method-catalog.json`(스키마 검증 통과본)을 읽음. 수동 중복 정의 금지. 시험: Python 산출 JSON == 커밋된 JSON 일치 검사(현행 `synthetic-catalog-parity.mjs` 방식 재사용)
- Detector는 같은 모듈을 읽기만(구현 Issue에서 같은 파일 동시 수정 금지)
- 확장: 항목 추가 = dataclass 인스턴스 1개 + 어댑터 1개 + 시험. 사용자 등록 API 없음

## 조회 API

- `GET /api/jobs/capabilities`에 `methodCatalog` 추가 ★: `{catalogVersion, generatedFrom: {gitRef}, methods: [...]}`. 별도 엔드포인트는 만들지 않음
- 기존 `models[{key, displayName, supported}]` **유지**: `key` = `ml:*`의 `<model_key>`, `supported := executable.process` ★ Q-I=A. 현재 WebUI의 `supported=false` 비활성 처리(`PipelineOperations.tsx:467`)는 최신 사용자 확인(WebUI 문서 2.2·2.3절: 실행 불가 방법은 버전 구성 화면에서 비활성, 사유 미표시)과 **일치**하므로 유지. WebUI 세션에는 "`status`(supported/research) 표기 추가, 비활성 근거는 `executable.process`"만 통지
- **충돌**: backend 시험 `trainer-contract.test.mjs:10-11`이 `models.map(key) == ['iforest']` 고정 → `minibatch_kmeans`·`eif` 추가 시 갱신(API 세션)
- `contractVersion`은 `trainer-service-v1` 유지, `methodCatalog.catalogVersion`으로 별도 버전

## 초기 목록 v1 — 사실 기반

### HR

| methodId | status | uiTarget | executable | 비고 |
|---|---|---|---|---|
| `hr:process-status` | supported | server `server/worker/detector/status_rule.py` | process ✓ / window ✗ | `methodVersion=process-status-hr-v1`; `parameters={}`(매핑 S→no_hit·F→critical·P/N→warning 고정, `status_rule.py:7, 13-16`); `inputRequirements={unit:process_row, featureSet:not_required, fields:[status]}`; defaults `{}` |
| `rule` 템플릿(`instanceTemplates[]`, `methods[]` 아님 ★) | supported | server `server/worker/detector/rules.py`, `server/backend/src/rules/ruleContract.ts` | process ✓ / window ✗ | `instanceSource=ml_rule_versions`, `instanceIdPattern=^rule:[a-z][a-z0-9_]{2,63}$`(`rule_key` 정규식 `ruleContract.ts:20`); 구성 `methods[]`에는 인스턴스 `rule:<rule_key>`만; 파라미터 없음(조건은 Rule DSL); `inputRequirements.featureSet=optional`(`F.*` 사용 시 required). 승인 상태 없음(`rules.md:3`) — HR 등록 목록 경계는 Detector D9-3 후속 |
| `hr:status_failed`, `hr:success_with_error`, `hr:count_conservation`, `hr:final_missing_end`, `hr:missing_sql_required`, `hr:negative_duration`, `hr:invalid_count_domain`, `hr:invalid_input` | research ★ Q-G=A | research `research/evaluation/process_synthetic_lab/model.py:15-16` | process ✗(`server_adapter_missing`) / window ✗ | 파라미터 미확인(`parameterSchemaStatus=unconfirmed`, defaults null); `hr:status_failed`는 `hr:process-status`의 F와 의미가 겹침 → 등재하되 `notes`에 중복 표기, 동시 활성은 허용(k=1에서 결과 동일) |

### SR

- 24종 모두 `status=research`, `uiTarget.scope=research`, `uiTarget.path=research/evaluation/trend_sr_comparison/methods.py`(registry `methods.py:17-22`), `executable={process:false, window:false}`, `executableReason={process:"input_unit_mismatch", window:"server_adapter_missing"}` [확정 Q-B=A]
- `inputRequirements={unit:"aggregated_window", aggregationMinutes:[1,5,15], featureSet:"not_required"}` — 입력이 1분 집계 시간창(`features.py:72-73`)이며 PROCESS 행 단위 연구 코드는 Git에 없음(사실)
- 공통 `parameterSchema`(연구 `Configuration` dataclass `methods.py:35-45`, `parameterSchemaStatus=research_declared`): `window`(정수 ≥1), `aggregation`(enum 1/5/15), `baseline`(enum `B0`/`B1`/`B2`, `references.py:86-106`), `window ≥ aggregation`. 방법별 변형(`methods.py:25-31`, registry 번호 → `METHODS` enumerate 순서 `methods.py:17-22`로 대응): R03 `sr:iqr` `k` enum 1.5/3.0; R07 `sr:finite_cusum` `k` enum 0.25/0.5/1.0; R18 `sr:mahalanobis` `ridge` enum 0.01/0.1; R21 `sr:holt_winters` `trend` enum none/damped; R23 `sr:arima_calendar` `order` enum [1,0,0]/[1,0,1]
- `defaults`: 공통 `{window: 60, aggregation: 1, baseline: "B1"}` ★(연구 기본 `baseline='B1'`, `aggregation=1`은 코드 기본값 `methods.py:37-39`; `window=60`은 초안 가정 — 연구 보고서 대조 필요, `defaultsSource`에 "미확인" 표기). 변형 파라미터 기본값은 각 enum의 첫 값 ★
- `methodVersion`: `research-trend-sr-R<nn>`

| registry | methodId | registry | methodId |
|---|---|---|---|
| R00 | `sr:typed_sr` | R12 | `sr:negative_binomial` |
| R01 | `sr:z` | R13 | `sr:binomial` |
| R02 | `sr:mad` | R14 | `sr:beta_binomial` |
| R03 | `sr:iqr` | R15 | `sr:pearson` |
| R04 | `sr:empirical_tail` | R16 | `sr:spearman` |
| R05 | `sr:excess_ratio` | R17 | `sr:linear_residual` |
| R06 | `sr:ewma` | R18 | `sr:mahalanobis` |
| R07 | `sr:finite_cusum` | R19 | `sr:ks` |
| R08 | `sr:front_back_median` | R20 | `sr:wasserstein` |
| R09 | `sr:ols_slope` | R21 | `sr:holt_winters` |
| R10 | `sr:known_zero` | R22 | `sr:stl_template` |
| R11 | `sr:poisson` | R23 | `sr:arima_calendar` |

- 번호↔이름 대응은 `METHODS = {f'R{i:02}': name for i, name in enumerate((...))}`(`methods.py:17-22`) 순서 그대로이며 카탈로그 JSON 생성 시 코드에서 읽어 채움(수기 표 아님)
- PROCESS 단위 SR 프로토타입(사용자 Q-B 추가 지시, 별도 SR 서브에이전트 진행 중): 이름공간 `sr:process_*` 예약, **추가 예정(이 버전 미등재)**. 등재 시 `inputRequirements.unit=process_row`·`executable.process`는 서버 어댑터 유무로 결정
- LLM이 만드는 `sr_<hash>` Rule(`research_design.py:228`, 코드 `SR_DIAGNOSTIC`)은 Rule DSL 인스턴스(`rule:sr_<hash>`)이며 SR 방법이 아님 — Detector D9-3 이름 정리 대상

### ML

| methodId | status | uiTarget | executable | parameterSchema·defaults | inputRequirements |
|---|---|---|---|---|---|
| `ml:iforest` | supported | server `server/worker/model_adapters.py` (연구 `research/anomaly_detection/code/process_anomaly_pipeline/artifacts/process_if_v1.manifest.json`) | process ✓(`process-iforest-v1`) / window ✓(v12 번들 `if`, `window_worker.py:105`) | `n_estimators` int 16..1024, `max_samples` int 64..4096, `contamination` const `auto`, `random_state` int ≥0, `thresholdPolicy` const `train-internal-p99-v1`; **defaults [확정 Q-H=A]** `{n_estimators:128, max_samples:512, contamination:"auto", random_state:42, thresholdPolicy:"train-internal-p99-v1"}`; `defaultsSource` `model_adapters.py:121-127`(연구 manifest와 동일값) | `{unit:process_row, featureSet:required, numericOnly:true, maxFeatures:32, minTrainRows:600, fitOperations:[group_median, group_centered]}`(`model_adapters.py:23-24, 89-108`) |
| `ml:minibatch_kmeans` | research | research `research/anomaly_detection/code/analysis/minibatch_kmeans_common.py`, `model_research_v8/models.py` | process ✗(`server_adapter_missing`) / window ✓(v12 번들 `kmeans`, `005_research_runtime.sql:5`) | `parameterSchemaStatus=research_declared`: `k` int 2..64 ★(상한 64는 초안 가정; 연구 후보 `candidates=(2,4,6,8,12)` 병기, 연구 코드 필드명은 `selected_k` `minibatch_kmeans_common.py:36-37`), `batch_size` int, `max_iter` int, `n_init` int, `random_state` int, `quantile` number 0..1; defaults `{k:6, batch_size:2048, max_iter:100, n_init:5, random_state:42, quantile:0.99}`(`KMeansConfig` `minibatch_kmeans_common.py:35-46`; v8 `Settings.k=6` `model_research_v5/models.py:21-28`) | `{unit:process_row, featureSet:required, numericOnly:true}` — PROCESS 경로 피처 정의는 연구 인코더 기준, 서버 피처셋 호환 미확인 |
| `ml:eif` | research | research `research/evaluation/long_term_trend/models.py:56-58`, `if_feature_comparison/models.py:71-76` | process ✗ / window ✗(`research_only`) | `research_declared`: `ntrees` int, `sample_size` int, `extension_level` const `dimension-1`; defaults `{ntrees:128, sample_size:256}`(`SETTINGS` `if_feature_comparison/models.py:19`) | `{unit:aggregated_window, featureSet:"not_required"}`(장기 추세 연구, 피처는 연구 인코더 고정) |

- `runtime.library`: `ml:iforest`·`ml:minibatch_kmeans`는 `scikit-learn`(사용자 4절 "scikit-learn 기반" 방향과 일치); `ml:eif`는 저자 구현 `third_party` 의존이라 `research-bundle`로 표기 — 서버 이식 시 scikit-learn 대체 구현이 없으면 `executable.process`는 계속 false
- 시간창(WINDOW) 경로 포함은 ★ Q-J=A: 구성 버전 v3는 PROCESS 경로만 다루고, `executable.window`는 표시용

## 검증·시험(구현 Issue)

- 카탈로그 JSON은 `method-catalog.schema.json`을 통과해야 함. `defaults`는 자신의 `parameterSchema`를 통과
- `executable.process=true`인 항목은 `server/`에 어댑터 등록이 실제로 있어야 함(레지스트리 시험이 import·호출로 확인). 연구 경로 항목은 `server/`에서 `research/` import 금지(배포 경계 유지)
- `uiTarget.path`는 저장소에 실제 존재(시험으로 확인)

## 결정 대기

1. Q-G HR 연구 8종 등재: ★A 등재
2. Q-I 기존 `supported` 의미: ★A `executable.process`로 유지(WebUI 비활성 처리는 최신 확인과 일치해 유지, `status` 표기 추가만 통지)
3. Q-J 시간창 경로 포함: ★A 포함(`executable.window` 표시만)
4. Q-K 레지스트리 단일 정의: ★A Python 원본 + 생성 JSON
5. SR 공통 기본값(`window=60`)·변형 기본값 — 연구 보고서 대조 후 확정
6. `ml:minibatch_kmeans` PROCESS 경로 입력 요건 — 연구 인코더와 서버 피처셋 대응 미확인
