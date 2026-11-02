Created: 2026-10-21T00:00:00+09:00
Updated: 2026-11-02T18:00:00+09:00
Author: Claude Code (Trainer 세션 builder)
Status: draft

# 방법 카탈로그 계약 v2 (초안, Issue #94)

- 변경 요약(#94): 대분류를 **hard / soft / ml**로 재편(사용자 11/02). 통계 SR R00~R23 **전부 삭제**·`sr:*` 폐기(호환 표만), PROCESS SR 프로토타입(#75) 미등재 유지, HR 연구 8종·`hr:process-status`·`rule` 템플릿은 카탈로그에서 제거(하드룰은 detection-decision.md(#92)가 정의). 신규 **소프트룰 등록 필드 목록 `softRuleFields[]`**(단위·타입·원천 식). ML 카탈로그(IF 등)는 `ml:` 접두로 유지, `displayName` 유지(10/22 승인)
- 계약 버전: `method-catalog-v2`; 항목 스키마 [method-catalog.schema.json](method-catalog.schema.json)(`$id` …:v2). 근거 코드 origin/dev `7f24e03`(`worker/derived_expression.py`, `backend/src/rules/ruleContract.ts`, `worker/model_adapters.py`)
- 상위 결정: 사용자 11/02 결정(Q4 소프트룰 = 등록 필드 + 임계값, Q5 통계 SR 제외, 8.2절 룰 수정 범위), Trainer·Detector 분담 합의 11/02(등록 필드 목록·편집 제약 = Trainer, 하드룰 목록·소프트룰 실행 의미론 = Detector). 절 번호는 총괄 기록 기준
- 범위: ML 세부 방법의 시스템 내장 레지스트리 + 소프트룰이 고를 수 있는 **등록 필드**(시스템 정의, 사용자 등록 아님) + 식별자 체계. 룰 세트([rule-set.md](rule-set.md))·모델 버전([model-version.md](model-version.md))·임계값 추천([llm-recommendation-job.md](llm-recommendation-job.md))·WebUI가 같은 식별자를 사용
- 제외: 하드룰 내장 목록(id·조건·위험도 — Detector), 방법 어댑터 구현, 종합 판정 규칙, 사용자 정의 방법·필드 등록 API(목표 아님)

## 원칙(유지·조정)

- UI 제작 대상 = Git에 올라온 방법(`server/` + `research/`) [확정 10/21]. 실행 가능 = `server/`에 어댑터 존재. `status`와 `executable`은 별개. 실행 불가 방법은 화면 비활성·서버 저장 거부 422 `method_not_executable:<methodId>` [확정 10/21 Q3-1]
- 항목 추가·필드 추가는 `catalogVersion` 증가. 제거는 하지 않고 `status=deprecated`. **예외(이번 v2)**: SR 24종·HR 연구 8종은 사용자 결정으로 카탈로그에서 **삭제**(저장된 참조가 없으므로 deprecated 전환 불필요, 연구 기록은 `research/`에 보존)
- 기본값은 출처를 기록(`defaultsSource`); 미확인 값은 `defaults=null`

## 대분류·식별자 [합의 11/02 — 사용자 확인 필요 (a)]

| kind | category | 화면 표시 용어 [확정 11/02] | 탐지 결과 | 정의 주체 | 카탈로그 표현 |
|---|---|---|---|---|---|
| `hard` | RULE | **이상 규칙**(내부: 하드룰) | **이상** | Detector(detection-decision.md(#92)) 내장 목록 | `hardRules: {definedBy: "detection-decision.md", displayName: "이상 규칙"}` 참조만. 항목 없음 |
| `soft` | RULE | **이상 징후 규칙**(내부: 소프트룰) | **이상 징후** | 사용자가 등록 필드 + 임계값으로 만듦([rule-set.md](rule-set.md)) | `softRuleFields[]`(고를 수 있는 필드) + `softRule`(인스턴스 규칙, `displayName: "이상 징후 규칙"`) |
| `ml` | ML | **머신러닝 모델** | **이상 징후** | 시스템 내장 ML 방법 | `methods[]`(전부 `ml:*`, 각 항목 `displayName`은 방법 이름) |

- 대분류 표시명은 카탈로그 JSON의 `categoryDisplayNames`(`{hard: "이상 규칙", soft: "이상 징후 규칙", ml: "머신러닝 모델"}`, 3키 필수)와 `hardRules.displayName`·`softRule.displayName`(필수, 같은 값)로 제공해 화면이 문구를 하드코딩하지 않게 함. `hardRules.contractVersion`은 schema `const "detection-decision-v1"`

- 식별자 `<kind>:<key>`, key `^[a-z][a-z0-9_-]{1,63}$`. 소프트룰 인스턴스 id는 서버 생성 `soft:<fieldId>-<8 hex>`(`softRule.instanceIdPattern`)
- 호환 매핑(v1/v3 판정·구성 **읽기 전용**, 자동 변환 없음)

| 기존 | 새 체계 | 비고 |
|---|---|---|
| `hr:process-status` | `hard:process-status`(`methodVersion: process-status-hr-v1`, Detector 11/02 회신) | 상태 HR(`status_rule.py`) |
| `hr:status_failed` 등 연구 HR 8종 | 카탈로그 제거. 하드룰 후보는 Detector 내장 목록 확장(#92 후속 B)에서 판단 | `research/evaluation/process_synthetic_lab/model.py` 기록 보존 |
| `rule:<rule_key>` | 임계값형은 `soft:*` 의미. `ml_rule_versions` 행은 읽기 보존, 자동 이관 없음 | `rules.md` API 유지 |
| `sr:<key>` R00~R23 | **폐기**(등재 없음) | `research/evaluation/trend_sr_comparison/` 보존 |
| `sr:process_*`(#75 프로토타입) | 미등재 유지, 예약 이름공간도 해제 | `research/` 보존 |
| `rule:sr_<hash>`(LLM `recommend_rule` 산출) | Rule DSL 인스턴스, 읽기 보존 | `recommend_rule` 폐지([llm-recommendation-job.md](llm-recommendation-job.md)) |

## 소프트룰 등록 필드 `softRuleFields[]` [제안 — 사실 기반]

- 소프트룰은 **시스템 정의 필드**만 고름. 사용자 피처셋(`F.<name>`, `rules.md`)을 참조하지 않음 — 룰 세트가 피처셋 버전에 묶이지 않게 하기 위함. 각 필드는 원천 열(`column`) 또는 내장 파생식(`derived`, `worker/derived_expression.py`의 AST와 동일 형식)으로 정의되며 Detector·Trainer·API가 같은 식으로 값을 계산
- 원천(Rule DSL `P.*` 숫자 필드, `rules.md`·`derived_expression.py:9-11` `FIELDS`): `success_count`·`error_count`·`total_count`·`retry_count`(number→count). `P.status`·`P.response_code`·`adapter_type` 등 문자열 필드는 임계값 비교 대상이 아님(상태 조건은 하드룰 영역)
- 파생: `datetime_diff`(timestamp 2개, 단위 `s|ms|us`), `divide`(count/count → ratio), `subtract`, `convert_unit`(`derived_expression.py:12-13, 44-66`). 처리시간은 원천 필드에 없고 `datetime_diff(END_TIME, START_TIME)` 파생으로만 존재(11/02 9.1 조사와 일치)

| fieldId | displayName | valueType | unit | source | 결측 규칙(Detector 의미론과 별개로 값 계산 수준) |
|---|---|---|---|---|---|
| `total_count` | 총 건수 | integer | count | column `TOTAL_COUNT`(`P.total_count`) | 열 결측 → unavailable |
| `success_count` | 성공 건수 | integer | count | column `SUCCESS_COUNT` | 〃 |
| `error_count` | 오류 건수 | integer | count | column `ERROR_COUNT` | 〃 |
| `retry_count` | 재시도 수 | integer | count | column `RETRY_COUNT` | 〃 |
| `elapsed_ms` | 처리 시간 | number | ms | derived `datetime_diff(END_TIME, START_TIME, "ms")` | 시각 파싱 실패 → unavailable; 음수는 하드룰(`START_TIME > END_TIME`) 영역이라 소프트룰 값으로는 그대로 둠 |
| `error_ratio` | 오류 비율 | number | ratio | derived `divide(ERROR_COUNT, TOTAL_COUNT)` | `TOTAL_COUNT = 0` → unavailable(0 나눗셈 없음) |

- 공통: `operators = ["gte","gt","lte","lt"]`, `status = supported`. `displayPrecision`: count 0, ms 0, ratio 4 — 화면 반올림 자릿수(저장값은 원값)
- `valueType: integer`(건수 4종)는 **계약 수준 제약**: DB 열은 `numeric`(`002_core_erd.sql:65-66` `success_count numeric, error_count numeric, total_count numeric, retry_count numeric`)이라 정수가 보장되지 않음. 임계값 입력은 정수로 제한하고, 값 계산 시 비정수 관측은 그대로 비교(Decimal)하며 정수 강제 변환은 하지 않음
- `fieldId` 길이 ≤ 54자(스키마 `{1,53}` + 첫 글자): 소프트룰 인스턴스 id `soft:<fieldId>-<8 hex>`의 key(`<fieldId>-<8 hex>`)가 식별자 key 상한 63자를 넘지 않게 하기 위함
- 필드 추가는 카탈로그 버전 증가 + Detector 계산 경로 확인(어댑터 추가와 같은 절차). 후보(미등재): `success_ratio`, `elapsed_s`(단위 변환), `retry_ratio`
- `softRule`: `maxRules 32`(현행 Rule 상한 `jobRoutes.ts:108`·#92와 동일, 확대는 합의로), `severities [info, warning, critical]`, `thresholdSerialization fixed-decimal-string`(지수 표기 금지 — DSL 정규식 `ruleContract.ts:17`과 11/02 9.1 결함 근거)

## ML 목록 v2 — 사실 기반(v1에서 `ml:` 항목 그대로)

| methodId | displayName | status | uiTarget | executable | parameterSchema·defaults | inputRequirements |
|---|---|---|---|---|---|---|
| `ml:iforest` | Isolation Forest | supported | server `server/worker/model_adapters.py`(연구 `research/anomaly_detection/code/process_anomaly_pipeline/artifacts/process_if_v1.manifest.json`) | process ✓(`process-iforest-v1`) / window ✓(v12 번들 `if`, `window_worker.py:105`) | `n_estimators` int 16..1024, `max_samples` int 64..4096, `contamination` const `auto`, `random_state` int ≥0, `thresholdPolicy` const `train-internal-p99-v1`; defaults [확정 Q-H] `{n_estimators:128, max_samples:512, contamination:"auto", random_state:42, thresholdPolicy:"train-internal-p99-v1"}`(`model_adapters.py:121-127`) | `{unit:process_row, featureSet:required, numericOnly:true, maxFeatures:32, minTrainRows:600, fitOperations:[group_median, group_centered]}` |
| `ml:minibatch_kmeans` | MiniBatch K-Means | research | research `research/anomaly_detection/code/analysis/minibatch_kmeans_common.py` | process ✗(`server_adapter_missing`) / window ✓ | `research_declared`: `k` int 2..64, `batch_size`·`max_iter`·`n_init`·`random_state` int, `quantile` number 0..1; defaults `{k:6, batch_size:2048, max_iter:100, n_init:5, random_state:42, quantile:0.99}` | `{unit:process_row, featureSet:required, numericOnly:true}` — 서버 피처셋 호환 미확인(N13) |
| `ml:eif` | Extended Isolation Forest | research | research `research/evaluation/long_term_trend/models.py` | process ✗ / window ✗(`research_only`) | `research_declared`: `ntrees` int, `sample_size` int, `extension_level` const `dimension-1`; defaults `{ntrees:128, sample_size:256}` | `{unit:aggregated_window, featureSet:not_required}` |

- `runtime.library`: `scikit-learn`(iforest·kmeans) / `research-bundle`(eif). v1의 `scipy`·`statsmodels`는 SR 전용이라 enum에서 제거
- `resultSchema` 필드는 **삭제**(Detector 11/02 회신): 방법별 결과는 evidence v4 `methodResults[]`(DB `evidence_version` = `detector-evidence-v4`, detection-decision.md(#92))로 정의되며 카탈로그가 형식 이름을 들고 있지 않음. v1의 `method-result-v1` 자리 초안 폐기
- 시간창(WINDOW) 경로 `executable.window`는 표시용 [확정 10/21 Q-J]

## 레지스트리 인터페이스 [유지 10/21 Q-K]

- 원본 `server/worker/methods/registry.py`(Trainer 소유 제안). ML 항목 = dataclass 1개 + 어댑터 프로토콜(`prepare/fit/score`). **소프트룰 필드** = 같은 모듈의 필드 dataclass(`fieldId`·AST·단위) + 값 계산 함수(`derived_expression.evaluate` 재사용) — Detector는 읽기만
- API는 빌드 시 생성된 `server/contracts/method-catalog.json`(스키마 통과본)을 읽음. 시험: Python 산출 JSON == 커밋 JSON 일치
- 확장: ML 항목 추가 = dataclass + 어댑터 + 시험; 필드 추가 = dataclass + AST 검증 시험. 사용자 등록 API 없음

## 조회 API

- `GET /api/jobs/capabilities`에 `methodCatalog` [유지 10/21]: `{catalogVersion, generatedFrom: {gitRef}, hardRules, softRuleFields, softRule, methods}`. 별도 엔드포인트 없음. 하드룰 실제 목록(id·조건·위험도)은 `GET /api/rule-set`([rule-set.md](rule-set.md))이 Detector 정의값을 투영
- 기존 `models[{key, displayName, supported}]` 유지: `key` = `ml:*`의 `<model_key>`, `supported := executable.process`
- **충돌**: backend 시험 `trainer-contract.test.mjs:10-11`이 `models.map(key) == ['iforest']` 고정 → 항목 추가 시 갱신(API)

## 예시 카탈로그 JSON(스키마 검증 대상)

```json
{
  "catalogVersion": "method-catalog-v2",
  "generatedFrom": {"gitRef": "7f24e03", "generatedAt": "2026-11-02T09:00:00Z"},
  "categoryDisplayNames": {"hard": "이상 규칙", "soft": "이상 징후 규칙", "ml": "머신러닝 모델"},
  "hardRules": {"definedBy": "detection-decision.md", "displayName": "이상 규칙", "contractVersion": "detection-decision-v1", "idPattern": "^hard:[a-z][a-z0-9_-]{1,63}$"},
  "softRule": {"displayName": "이상 징후 규칙", "instanceIdPattern": "^soft:[a-z][a-z0-9_]{1,53}-[0-9a-f]{8}$", "operators": ["gte", "gt", "lte", "lt"],
               "severities": ["info", "warning", "critical"], "maxRules": 32, "thresholdSerialization": "fixed-decimal-string"},
  "softRuleFields": [
    {"fieldId": "total_count", "displayName": "총 건수", "valueType": "integer", "unit": "count",
     "source": {"kind": "column", "column": "TOTAL_COUNT", "ruleDslField": "P.total_count"},
     "operators": ["gte", "gt", "lte", "lt"], "displayPrecision": 0, "status": "supported",
     "sourceRef": {"path": "server/worker/derived_expression.py", "lines": "9-11", "commit": "7f24e03"}},
    {"fieldId": "success_count", "displayName": "성공 건수", "valueType": "integer", "unit": "count",
     "source": {"kind": "column", "column": "SUCCESS_COUNT", "ruleDslField": "P.success_count"},
     "operators": ["gte", "gt", "lte", "lt"], "displayPrecision": 0, "status": "supported",
     "sourceRef": {"path": "server/worker/derived_expression.py", "lines": "9-11", "commit": "7f24e03"}},
    {"fieldId": "error_count", "displayName": "오류 건수", "valueType": "integer", "unit": "count",
     "source": {"kind": "column", "column": "ERROR_COUNT", "ruleDslField": "P.error_count"},
     "operators": ["gte", "gt", "lte", "lt"], "displayPrecision": 0, "status": "supported",
     "sourceRef": {"path": "server/worker/derived_expression.py", "lines": "9-11", "commit": "7f24e03"}},
    {"fieldId": "retry_count", "displayName": "재시도 수", "valueType": "integer", "unit": "count",
     "source": {"kind": "column", "column": "RETRY_COUNT", "ruleDslField": "P.retry_count"},
     "operators": ["gte", "gt", "lte", "lt"], "displayPrecision": 0, "status": "supported",
     "sourceRef": {"path": "server/worker/derived_expression.py", "lines": "9-11", "commit": "7f24e03"}},
    {"fieldId": "elapsed_ms", "displayName": "처리 시간", "description": "END_TIME - START_TIME (ms)", "valueType": "number", "unit": "ms",
     "source": {"kind": "derived", "ast": {"op": "datetime_diff", "unit": "ms",
                "args": [{"op": "column", "table": "EC_PROCESS_MONITORING", "column": "END_TIME"},
                         {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "START_TIME"}]}},
     "operators": ["gte", "gt", "lte", "lt"], "displayPrecision": 0, "status": "supported",
     "sourceRef": {"path": "server/worker/derived_expression.py", "lines": "50-53, 89-94", "commit": "7f24e03"},
     "notes": "시각 파싱 실패 시 unavailable. 음수(START_TIME > END_TIME)는 하드룰 영역"},
    {"fieldId": "error_ratio", "displayName": "오류 비율", "description": "ERROR_COUNT / TOTAL_COUNT", "valueType": "number", "unit": "ratio",
     "source": {"kind": "derived", "ast": {"op": "divide",
                "args": [{"op": "column", "table": "EC_PROCESS_MONITORING", "column": "ERROR_COUNT"},
                         {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "TOTAL_COUNT"}]}},
     "operators": ["gte", "gt", "lte", "lt"], "displayPrecision": 4, "status": "supported",
     "sourceRef": {"path": "server/worker/derived_expression.py", "lines": "101-102", "commit": "7f24e03"},
     "notes": "TOTAL_COUNT = 0 이면 unavailable. p95가 1e-4 미만일 수 있어 고정소수 직렬화 필수"}
  ],
  "methods": [
    {"methodId": "ml:iforest", "kind": "ml", "category": "ML", "displayName": "Isolation Forest", "status": "supported",
     "methodVersion": "process-iforest-v1",
     "uiTarget": {"scope": "server", "path": "server/worker/model_adapters.py",
                  "ref": "research/anomaly_detection/code/process_anomaly_pipeline/artifacts/process_if_v1.manifest.json"},
     "executable": {"process": true, "window": true},
     "runtime": {"library": "scikit-learn", "artifactFormat": "process-iforest-v1"},
     "parameterSchema": {"type": "object", "additionalProperties": false,
       "required": ["n_estimators", "max_samples", "contamination", "random_state", "thresholdPolicy"],
       "properties": {"n_estimators": {"type": "integer", "minimum": 16, "maximum": 1024},
                      "max_samples": {"type": "integer", "minimum": 64, "maximum": 4096},
                      "contamination": {"const": "auto"},
                      "random_state": {"type": "integer", "minimum": 0},
                      "thresholdPolicy": {"const": "train-internal-p99-v1"}}},
     "parameterSchemaStatus": "verified",
     "defaults": {"n_estimators": 128, "max_samples": 512, "contamination": "auto", "random_state": 42, "thresholdPolicy": "train-internal-p99-v1"},
     "defaultsSource": "server/worker/model_adapters.py:121-127",
     "inputRequirements": {"unit": "process_row", "featureSet": "required", "numericOnly": true, "maxFeatures": 32, "minTrainRows": 600,
                           "fitOperations": ["group_median", "group_centered"]},
     "sourceRef": {"path": "server/worker/model_adapters.py", "lines": "89-127", "commit": "7f24e03"},
     "canDisable": true},
    {"methodId": "ml:minibatch_kmeans", "kind": "ml", "category": "ML", "displayName": "MiniBatch K-Means", "status": "research",
     "methodVersion": "research-minibatch-kmeans-v8",
     "uiTarget": {"scope": "research", "path": "research/anomaly_detection/code/analysis/minibatch_kmeans_common.py"},
     "executable": {"process": false, "window": true}, "executableReason": {"process": "server_adapter_missing"},
     "runtime": {"library": "scikit-learn"},
     "parameterSchema": {"type": "object", "additionalProperties": false,
       "properties": {"k": {"type": "integer", "minimum": 2, "maximum": 64}, "batch_size": {"type": "integer", "minimum": 1},
                      "max_iter": {"type": "integer", "minimum": 1}, "n_init": {"type": "integer", "minimum": 1},
                      "random_state": {"type": "integer", "minimum": 0}, "quantile": {"type": "number", "minimum": 0, "maximum": 1}}},
     "parameterSchemaStatus": "research_declared",
     "defaults": {"k": 6, "batch_size": 2048, "max_iter": 100, "n_init": 5, "random_state": 42, "quantile": 0.99},
     "defaultsSource": "research/anomaly_detection/code/analysis/minibatch_kmeans_common.py:35-46",
     "inputRequirements": {"unit": "process_row", "featureSet": "required", "numericOnly": true},
     "sourceRef": {"path": "research/anomaly_detection/code/analysis/minibatch_kmeans_common.py", "lines": "35-46", "commit": "7f24e03"},
     "canDisable": true,
     "notes": "PROCESS 경로 피처 정의는 연구 인코더 기준, 서버 피처셋 호환 미확인(N13)"},
    {"methodId": "ml:eif", "kind": "ml", "category": "ML", "displayName": "Extended Isolation Forest", "status": "research",
     "methodVersion": "research-eif-long-term-trend",
     "uiTarget": {"scope": "research", "path": "research/evaluation/long_term_trend/models.py"},
     "executable": {"process": false, "window": false}, "executableReason": {"process": "research_only", "window": "research_only"},
     "runtime": {"library": "research-bundle"},
     "parameterSchema": {"type": "object", "additionalProperties": false,
       "properties": {"ntrees": {"type": "integer", "minimum": 1}, "sample_size": {"type": "integer", "minimum": 1},
                      "extension_level": {"const": "dimension-1"}}},
     "parameterSchemaStatus": "research_declared",
     "defaults": {"ntrees": 128, "sample_size": 256},
     "defaultsSource": "research/evaluation/if_feature_comparison/models.py:19",
     "inputRequirements": {"unit": "aggregated_window", "featureSet": "not_required"},
     "sourceRef": {"path": "research/evaluation/long_term_trend/models.py", "lines": "56-58", "commit": "7f24e03"},
     "canDisable": true}
  ]
}
```

- `hardRules.contractVersion: "detection-decision-v1"`은 Detector 11/02 회신으로 **확정**. 하드룰 예시 id `hard:process-status`(`process-status-hr-v1`)·`hard:negative-duration`(kebab-case)도 Detector 회신값
- `softRule.instanceIdPattern`의 fieldId 부분은 `[a-z0-9_]{1,53}`(카탈로그 `fieldId` 패턴과 동일, 하이픈 없음) — 식별자 key 전체(`<fieldId>-<8 hex>`)는 `^[a-z][a-z0-9_-]{1,63}$` 안에 들어감

## 검증·시험(구현 Issue)

- 카탈로그 JSON은 `method-catalog.schema.json`을 통과. `defaults`는 자신의 `parameterSchema`를 통과. `softRuleFields[].source.ast`는 `derived_expression.validate`를 통과하고 `unit(ast)`가 필드 `unit`과 같아야 함(시험)
- `executable.process=true` 항목은 `server/` 어댑터 등록 실제 존재. 연구 경로 항목은 `server/`에서 `research/` import 금지
- `uiTarget.path`·`sourceRef.path`는 저장소에 실제 존재
- 이 초안 검증(실행): 위 예시 JSON을 jsonschema(Draft 2020-12)로 검증 — 결과는 #94 보고 참조

## 확정 요약·남은 의존

- 확정(11/02): 대분류 hard/soft/ml, SR 삭제, 소프트룰 = 등록 필드 + 임계값, 하드룰 = Detector 내장 목록
- 유지(10/21): Q-H IF 기본값 / Q-I `supported := executable.process` / Q-J window 표시만 / Q-K 레지스트리 + 생성 JSON / Q3-1 저장 거부 / N13 KMeans 피처셋 대응 미확정 / N18 `k` 2..64
- 결정 질문: Q-C1 초기 필드 6종 확정 여부(후보 `success_ratio`·`elapsed_s`·`retry_ratio` 추가 여부) / Q-C2 `elapsed_ms` 음수 처리(값 그대로 vs unavailable — Detector 의미론과 함께)
- 남은 의존: 하드룰 내장 목록 전체 key·조건·위험도(detection-decision.md(#92)); 대분류 키 `hard/soft/ml`·접두는 세션 합의로 사용자 확인 전(rule-set.md Q-R7)
