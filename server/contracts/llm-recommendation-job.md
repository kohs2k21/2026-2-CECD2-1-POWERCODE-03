Created: 2026-10-21T00:00:00+09:00
Updated: 2026-11-02T18:00:00+09:00
Author: Claude Code (Trainer 세션 builder)
Status: draft

# 소프트룰 임계값 추천 계약 v1 (초안, Issue #94 — #74 `method-recommendation-v1` 개정)

- 변경 요약(#94): LLM 추천을 **소프트룰(화면 용어: 이상 징후 규칙) 임계값 추천**으로 재정의. (1) `recommend_thresholds` — 켜진 소프트룰 대상 필드 전체를 한 번에 다루는 LLM 비동기 작업(일괄, R5 b). 1차 연결은 **(가) 코드 p95 + LLM 근거**(현재 코드 동작과 일치, 11/02 9.1 실행 조사); **(나) LLM 숫자 제안**은 `source: ai_proposal`과 확장 필드로만 예약. (2) 규칙별 **수식 추천**은 LLM 없는 동기 계산 API(p95 등 통계) — 작업 큐가 아니라 API 계약. `recommend_rule` DSL 생성은 **폐지 유지**, `recommend_methods`(ML 선택 추천)는 **보류**
- 계약 버전: `threshold-recommendation-v1`. 근거 코드 origin/dev `7f24e03`: `worker/research_design.py:36-44`(summary·p95 `np.quantile` linear), `:154-199`(`rule_request` — 기준선 = train 분할·anomaly 라벨 제외 p95, `minimum_n 20`, 단위 `count|ratio|ms|s|us`), `:202-234`(`recommend_rules` — 변수당 LLM 1회 순차, 검증 탈락은 변수만 제외, `str(request["baseline"]["value"])`로 DSL 값 생성), `worker/generation_client.py:54-55`(`TimeoutError`를 `generation_service_unavailable`로 묶음), `backend/src/rules/ruleContract.ts:17`(DSL 십진수 정규식, 지수 표기 거부)
- 상위 결정: 사용자 11/02 결정 — 임계값 산출 주체(8.1), AI 추천 단위 일괄 + 규칙별 수식(R5), 현재 코드 실행 조사(9.1), 화면 설계(9.2: (가)(나) 모두 담고 (가)로 먼저 연결). 절 번호는 총괄 기록 기준
- 범위: 작업 요청·입력 범위·추천 1건 형식·상태·실패/시간 초과 구분·임계값 직렬화 규칙·수식 추천 API·마이그레이션 영향. 제외: 프롬프트·어댑터 연구(LLM 담당 팀원), GPU 배포, 자동 적용(추천은 입력칸 채우기까지, 운영 반영은 [rule-set.md](rule-set.md) 저장)
- 표기: **[확정]** 사용자 확정·코드 사실 / **[제안]** 이 초안이 정한 값

## 기존 추천과의 관계

| 작업 종류 | 상태 | 비고 |
|---|---|---|
| `recommend_rule` | **폐지 유지** [확정 10/21·11/02] | 새 Rule DSL 초안(`rule:sr_<hash>`, `SR_DIAGNOSTIC`) 생성 경로. 기존 `run_llm_results`·`adopt` Rule은 읽기 보존. 신규 요청 차단 시점(422)은 LLM 세션. **내부 계산 경로(`rule_request`·`recommend_rules`의 p95·검증)는 `recommend_thresholds`가 재사용** — 산출물만 Rule 초안이 아니라 임계값 추천으로 바뀜 |
| `recommend_feature` | 폐지 유지 [확정 10/21] | 변경 없음 |
| `recommend_methods` | **보류** [제안] | #74 "등록 방법 중 선택 추천"의 ML 선택 부분. 모델 카탈로그·레지스트리·IF 파라미터화 Issue 뒤 재검토. `job_kind` 값(011)·`run_llm_results.kind` CHECK는 유지, API 접수·worker 분기는 구현하지 않음. 폐지가 아니라 "현 단계 범위 밖" |
| `recommend_thresholds` | **신규** | 이 문서 |
| `explain` | 변경 없음 | — |

## 산출 주체 — (가) 코드 p95 + LLM 근거 [확정 11/02 9.2, 1차 연결]

- 숫자(임계값)는 **코드가 계산**: 대상 기간 데이터 중 `status=S`·명시 anomaly 라벨 제외 표본의 p95(`research_design.summary` `np.quantile(…, .95)` linear). LLM은 **제안 여부(상태)와 근거 문장**만. LLM이 숫자를 직접 내면 검증(`validate_rule`·`expected_target`)에서 탈락 → 해당 필드 `rejected`(작업은 계속)
- (나) LLM 숫자 제안 [확정 11/02 8.1 "우선 시도", 9.2 "LLM 팀원 변경 후 같은 화면에 붙임"]: 응답 `source: "ai_proposal"`과 `aiProposal{…}` 확장 필드로 **예약만**. 필요한 LLM 범위 변경(프롬프트·출력 스키마 숫자 허용, 범위·단위 검증, LoRA 재학습 또는 베이스+프롬프트, `adapterSha` 고정)은 11/02 9.1 기록. 이 계약은 (나)가 켜져도 응답 형식이 바뀌지 않게 필드를 미리 둠

## 작업 — `POST /api/jobs kind=recommend_thresholds` [신규, 관리자, 비동기]

```json
{
  "kind": "recommend_thresholds",
  "sourceId": "<source UUID>",
  "ruleSetRevision": 13,
  "scope": {"softRuleIds": null},
  "data": {"snapshotId": "<ready snapshot UUID>", "from": null, "until": null, "timeBasis": "esb_occurred_at"},
  "mode": "data_p95_with_llm_rationale"
}
```

| 키 | 규칙 |
|---|---|
| `ruleSetRevision` | 추천 기준 룰 세트 리비전([rule-set.md](rule-set.md)). 현재 리비전과 다르면 409 `rule_set_revision_conflict`(화면 새로 불러오기). 작업은 이 리비전의 **켜진 소프트룰** 대상 필드를 입력으로 고정(`input_spec`에 `ruleSetContentHash`·대상 목록 기록) |
| `scope.softRuleIds` | null = 켜진 소프트룰 전체(R5 b 일괄). 배열이면 그 중 부분집합(화면 선택). 같은 필드를 쓰는 룰이 여러 개면 **필드당 1건** 계산하고 응답에서 룰 id 목록으로 묶음 |
| `data.snapshotId` | 집계 출처(ready, `purpose` 무관하게 **train 분할**만 사용 — 현행 `rule_request` scope). 생략 시 `from/until`(+`timeBasis`)로 snapshot 생성하는 현행 추천 경로 재사용 [유지 10/21]. 둘 다 없으면 400 |
| `mode` | `data_p95_with_llm_rationale`(가, 기본·유일 구현) / `ai_proposal`(나, 예약 — 어댑터 없으면 422 `recommendation_mode_not_available`) |
| 권한·멱등키·재시도·상태 | 기존 `/api/jobs`(queued/running/succeeded/failed, `attemptCount`·`maxAttempts`·`nextRunAt`). 큐 `llm_recommendation`([queues.md](queues.md)) |
| `llm_spec` | `evidence_version: 'soft-rule-threshold-v1'`, `input_evidence_hash: sha256(입력 집계 JSON)`, `model_version: '<어댑터 식별자>'`(현행 `rule_v2`), `prompt_version: '<번들 프롬프트 hash>'`, `output_schema_version: 'threshold-recommendation-v1'`, `reuse_key: sha256([actor, input, idempotencyKey, prompt_version])` — 011 트리거 필수 6키 그대로 |
| 접수 거부 | 켜진 소프트룰 0개(또는 `softRuleIds` 전부 꺼짐·미존재) 400 `soft_rules_empty`; 필드가 카탈로그에 없음 422 `soft_rule_field_unknown:<field>` |

## 입력 범위 — GPU에 넘기는 것 [유지 10/21 Q-M: 집계값만]

- 필드마다 현행 `rule_request` 형식(`feature-evidence-bundle-4.0.0`) 그대로: `feature{id, dtype, unit, expression(카탈로그 AST)}`, `measurement{row_count, valid_count, min, max, p95, …}`, `baseline{state: verified|unavailable, value, value_exact, n, minimum_n: 20, method}`, `scope`, `reference_policy{operator: gt}`. 카탈로그 `softRuleFields[].source`를 `definition`(`{name: fieldId, expression: {op: "derived", ast, outputType: "number", unit}}`)으로 변환해 기존 함수에 넘김. 단 `rule_request`는 `row["features"][name]`(`research_design.py:163`)을 읽으므로 **Trainer가 카탈로그 AST로 행마다 값을 먼저 계산해 `features[fieldId]`에 넣는 작은 경로가 필요**(`derived_expression.evaluate` 재사용; 피처셋 materialization은 쓰지 않음). 그 뒤 LLM 호출·검증 경로는 변경 없음
- 추가 컨텍스트(요청 크기 256,000바이트 안): 현재 소프트룰 `{id, name, field, operator, threshold, severity}`(사람이 정한 현재값 — LLM 근거 문장이 "현재 5000 → 제안 7210" 식으로 참조 가능)
- 원문 BODY·토큰·관측 원문·고객 식별자 금지. 판정 결과·evidence 제외

## 결과 JSON `threshold-recommendation-v1` — `GET /api/jobs/:id` `resultSummary`

```json
{
  "recommendationContractVersion": "threshold-recommendation-v1",
  "mode": "data_p95_with_llm_rationale",
  "ruleSet": {"revision": 13, "contentHash": "9c40…"},
  "data": {"snapshotId": "<snapshot UUID>", "split": "train", "timeBasis": "esb_occurred_at",
           "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained", "rowCount": 47120},
  "provider": "rule_v2",
  "adapterSha": "…",
  "items": [
    {"field": "elapsed_ms", "softRuleIds": ["soft:elapsed_ms-3f9a2c1d"], "currentThreshold": 5000,
     "operator": "gt", "threshold": "7210.5", "displayThreshold": "7211", "unit": "ms",
     "source": "data_p95", "status": "proposed",
     "evidence": {"n": 46980, "min": 12.0, "max": 184233.0, "p95": 7210.5, "quantileMethod": "linear_(n-1)*p",
                  "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
                  "measurementId": "m_…", "baselineId": "b_…"},
     "rationale": "The 95th percentile of 46,980 successful process durations is 7,210.5 ms; durations above it are rare in the reference period.",
     "aiProposal": null, "reviews": {"schema": true, "facts": true, "expectedTarget": true}},
    {"field": "error_ratio", "softRuleIds": ["soft:error_ratio-5d2e0b19"], "currentThreshold": 0.01,
     "operator": "gt", "threshold": "0.00003125", "displayThreshold": "0.0000", "unit": "ratio",
     "source": "data_p95", "status": "proposed",
     "evidence": {"n": 46980, "min": 0.0, "max": 0.5, "p95": 0.00003125, "quantileMethod": "linear_(n-1)*p",
                  "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
                  "measurementId": "m_…", "baselineId": "b_…"},
     "rationale": "…", "aiProposal": null, "reviews": {"schema": true, "facts": true, "expectedTarget": true}},
    {"field": "retry_count", "softRuleIds": ["soft:retry_count-91ab77e0"], "currentThreshold": 3,
     "operator": null, "threshold": null, "displayThreshold": null, "unit": "count",
     "source": "data_p95", "status": "no_applicable",
     "evidence": {"n": 46980, "min": 0.0, "max": 0.0, "p95": 0.0, "quantileMethod": "linear_(n-1)*p",
                  "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
                  "measurementId": "m_…", "baselineId": null},
     "rationale": "All reference values are 0; a percentile threshold has no discriminative value.",
     "aiProposal": null, "reviews": {"schema": true, "facts": true, "expectedTarget": true}},
    {"field": "total_count", "softRuleIds": ["soft:total_count-0a1b2c3d"], "currentThreshold": 10000,
     "operator": null, "threshold": null, "displayThreshold": null, "unit": "count",
     "source": "data_p95", "status": "rejected", "rejectedReason": "invalid_generation_output",
     "evidence": {"n": 46980, "min": 1.0, "max": 25100.0, "p95": 9800.0, "quantileMethod": "linear_(n-1)*p",
                  "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
                  "measurementId": "m_…", "baselineId": "b_…"},
     "rationale": null, "aiProposal": null, "reviews": {"schema": false, "facts": false, "expectedTarget": false}}
  ],
  "counts": {"targets": 4, "proposed": 2, "requestEvidence": 0, "deferred": 0, "noApplicable": 1, "rejected": 1}
}
```

### 추천 1건(`items[]`) 필드

| 필드 | 규칙 |
|---|---|
| `field` | 카탈로그 `fieldId`. `softRuleIds[]` = 이 필드를 쓰는 켜진 소프트룰(화면이 행마다 [적용] 가능) |
| `currentThreshold` | 요청 시점 룰의 임계값(참고 표시). 룰이 여러 개면 첫 룰 값(id 사전순)·나머지는 화면이 룰 세트에서 읽음 |
| `operator` | 1차는 항상 `gt`(코드 `reference_policy.operator`, `>p95`). 화면 조건과 다르면 사람이 조정. `status≠proposed`면 null |
| `threshold` | **원값, 고정소수 문자열**(`Decimal` → `format(d, "f")`, 지수 표기 금지, 40자 이내·필드 `valueType=integer`면 정수). 11/02 9.1 결함 대응: p95가 1e-4 미만이어도 `"0.00003125"` 형태로 직렬화. 변환 실패(비유한·40자 초과 → 필드 `displayPrecision+6` 자리로 반올림 후 재시도, 그래도 실패)는 그 필드만 `rejected`(`threshold_serialization_failed`), **작업은 성공** |
| `displayThreshold` | 카탈로그 `displayPrecision`으로 반올림한 표시 문자열(원값 툴팁) |
| `unit` | 카탈로그 단위 |
| `source` | `data_p95`(가, 코드 계산) / `ai_proposal`(나, 예약) |
| `status` | `proposed`(적용 가능) / `request_evidence`(근거 부족 — LLM 상태) / `deferred`(보류 — LLM 상태) / `no_applicable`(해당 없음 — 기준선 `unavailable`: 표본 < 20·값 전부 결측·단위 미검증·분산 없음) / `rejected`(검증 탈락·형식 오류·직렬화 실패, `rejectedReason`) — 현행 `rule_v2` 출력 `state` 5종과 대응. 화면은 `proposed`만 선택 가능, 나머지는 사유와 함께 회색 |
| `rejectedReason` | `invalid_generation_output`(스키마 위반·`truncated`) / `expected_target_mismatch`(LLM이 숫자를 직접 냄 등) / `factual_review_failed` / `threshold_serialization_failed` / `generation_failed:<code>`(그 필드 호출만 실패, 아래 실패 절) |
| `evidence` | `n`(기준 표본 `valid_count`)·`min`·`max`·`p95`(number 원값)·`quantileMethod`·`exclusionRule`·`measurementId`/`baselineId`(`m_…`/`b_…`, 입력 집계 ID — 근거 추적) |
| `rationale` | LLM 근거 문장(현재 영문, 한글은 LLM 팀원 범위). ≤ 500자. `rejected`는 null |
| `aiProposal` | (나) 예약. 켜지면 `{threshold: "<고정소수>", displayThreshold, operator, validation: {inRange: boolean, unitMatched: boolean}, rawText}`; 1차는 항상 null |
| `reviews` | 서버 검증 단계 결과 `{schema, facts, expectedTarget}`(현행 `validate_rule`·`factual_review`·`expected_target`) |
| (없음) `severity` | **응답에 없음** [확정 11/02 9.2]: 위험도는 사람이 고름(화면 기본 `warning`) |

- 상한: `items` ≤ 32(소프트룰 상한 32와 동일, 현행 변수당 LLM 호출 최대 32건과도 일치), `rationale` ≤ 500자
- 저장: 성공 결과는 `run_llm_results`(kind `recommend_thresholds`) + `run_jobs.result_summary`(현행 `_finish` 경로). 항목별 `rejected`가 있어도 저장
- 적용: 화면 [적용] → 선택한 값만 룰 입력칸에 채움 → [저장]([rule-set.md](rule-set.md) `PUT /api/rule-set`)해야 운영 반영. 서버 자동 적용 없음 [확정]

## 실패·시간 초과 — 작업 전체 vs 필드 격리 [제안]

| 상황 | 처리 |
|---|---|
| 생성 서비스 미설정·연결 불가(`generation_service_not_configured`·`generation_service_unavailable`) — 첫 호출에서 | 작업 `failed` + 그 코드(재시도 규칙 기존) |
| 호출 시간 초과 | 작업 `failed` + **`generation_timeout`** [확정 10/21 N20·11/02 9.2 "시간 초과 별도 표시"]. 현행 `generation_client.py:54-55`는 `TimeoutError`를 `generation_service_unavailable`로 묶으므로 **변경 제안**: `TimeoutError`·`socket.timeout`·`URLError.reason`이 timeout이면 `GenerationUnavailable("generation_timeout")`(기존 `recommend_rule`·`explain`에도 같은 코드 — LLM 세션 조율) |
| 일부 필드 처리 뒤 서비스 장애·시간 초과 | 작업 `failed`(부분 결과 없음, 현행과 동일) — 화면 [다시 시도]. **대안**(Q-L1): 처리된 항목을 `partial`로 저장하고 나머지 `rejected:generation_failed`로 격리해 성공 처리 |
| 한 필드의 출력 형식 깨짐·검증 탈락·직렬화 실패 | 그 필드만 `rejected` + `rejectedReason`, 나머지 계속, 작업 **성공** — 현행 "검증 탈락은 변수만 제외"를 "제외"가 아니라 "사유와 함께 표시"로 확장(11/02 9.1: `ValueError`로 작업 전체 failed였던 지수 표기 경우를 격리) |
| 모든 필드 `no_applicable`(데이터 사실: 표본 부족·분산 없음) | 작업 **성공**(항목마다 사유가 있으므로 빈 성공이 아님). 화면은 "제안 0건" + 사유 표. 대상 0건은 접수 단계 400 |
| 모든 필드가 **생성계 사유**로 `rejected`(`invalid_generation_output`·`expected_target_mismatch`·`factual_review_failed`·`generation_failed:*`) | [제안, reviewer 권고 4 — Q-L1에 묶음] 작업 `failed` + `recommendation_all_rejected`(항목별 사유는 `resultSummary`에 보존해 화면이 표로 표시). 생성 서비스 전체가 깨진 상황을 "성공"으로 보이게 하지 않기 위함. `no_applicable`·`threshold_serialization_failed`가 섞여 있으면 성공 |
| 입력 집계 부족(snapshot 행 0) | 작업 `failed` + `recommendation_input_empty` |

## 규칙별 수식 추천 — `POST /api/rule-set/threshold-stats` [신규, 관리자, 동기, LLM 없음]

- 용도: 규칙 행 [수식 추천] — 선택 기간 데이터의 p95를 바로 계산해 값·표본 수를 보여 주고 [적용]으로 입력칸에 채움(11/02 9.2). 작업 큐·LLM 호출 없음
- **구현 주체: API(TypeScript) + PostgreSQL SQL** [제안]: `percentile_cont(0.95)`(선형 보간 — numpy `linear`와 같은 정의)를 `data_processes ⋈ data_observations`에 적용. 파생 필드(`elapsed_ms`·`error_ratio`)는 카탈로그 AST를 SQL 식으로 번역(`datetime_diff` = `parsed_times`의 end−start, `divide` = NULLIF 분모). Trainer(Python)는 비동기 워커라 동기 API에 부적합. 두 경로의 p95가 같은 표본에서 일치해야 함(통합 시험 항목)

```json
{
  "field": "elapsed_ms",
  "statistic": "p95",
  "period": {"from": "2026-10-01T00:00:00+09:00", "until": "2026-11-01T00:00:00+09:00", "timeBasis": "esb_occurred_at"},
  "sourceId": "<source UUID>",
  "excludedIntervals": []
}
```

- `statistic` ∈ `p95`(기본) / `p99` / `median` / `max`. `period` 최대 366일. `excludedIntervals[]`는 학습 요청과 같은 형식([trainer-service.md](trainer-service.md) 변경 제안 절) — 장애 구간을 빼고 계산할 때
- 표본 규칙은 LLM 작업과 동일: `status=S`·명시 anomaly 라벨 제외·결측 제외. snapshot 분할은 쓰지 않음(기간 전체) — `evidence.scope: "period"`로 구분

```json
{
  "field": "elapsed_ms", "statistic": "p95",
  "operator": "gt", "threshold": "7210.5", "displayThreshold": "7211", "unit": "ms",
  "source": "data_p95", "status": "proposed",
  "evidence": {"n": 51230, "min": 12.0, "max": 184233.0, "p95": 7210.5, "quantileMethod": "percentile_cont",
               "exclusionRule": "status=S, explicit anomaly labels excluded, excludedIntervals=0",
               "scope": "period", "period": {"from": "…", "until": "…", "timeBasis": "esb_occurred_at"}},
  "rationale": null
}
```

- `status`: `proposed` / `no_applicable`(n < 20 또는 분산 없음 — `minimum_n 20` 현행과 동일) / `rejected`(직렬화 실패). `rationale`은 항상 null(LLM 없음). 위험도 없음
- 오류: 400 `invalid_threshold_stats_request`, 422 `soft_rule_field_unknown:<field>`, 400 `period_too_long`
- 응답 시간 목표 ≤ 3초(인덱스 `(source_id, source_log_occurred_at)` 요구 — [trainer-service.md](trainer-service.md) 거래량 API와 공유)

## 작업 상태 ↔ 화면(11/02 9.2)

| 화면 | API |
|---|---|
| [AI 임계값 추천] 실행 중(진행 표시) | `status` queued/running; 같은 멱등키 재요청은 `reuse_key`로 기존 작업 |
| 결과 표(선택/규칙/현재/추천(반올림, 원값 툴팁)/출처/조건/단위/상태/근거) | `items[]` 그대로. 출처 배지 `source`(데이터 p95 / AI 제안) |
| [적용] | 서버 호출 없음 — 입력칸 채움, 위험도 기본 주의, 저장해야 운영 반영 |
| 실패 + [다시 시도] | `failed` + `errorCode`(부분 결과 없음). 새 멱등키로 재요청 |
| 시간 초과 | `failed` + `generation_timeout` — 별도 문구 |
| [수식 추천](규칙 행) | `POST /api/rule-set/threshold-stats` 동기 응답 |

## 마이그레이션·코드 영향(구현 Issue, 사실 기반)

| 대상 | 변경 |
|---|---|
| `job_kind` enum | `recommend_thresholds` 추가(`002:7`, 011에서 `recommend_methods` 등 추가 방식과 동일) — PostgreSQL |
| `run_jobs_v03_contract` CHECK(`011:13-32`) | `llm_recommendation` 분기 kind 목록에 `recommend_thresholds` 추가(PG 누락 1) |
| `erd_v03_validate_job`(`011:60, 76-77`)·`run_llm_results_kind_check`(`011:36-37`)·`run_llm_results_check`(`011:39-41`, `decision_id IS NULL` 분기) | llm_spec 필수·완료 결과 필수·kind CHECK 2개에 `recommend_thresholds` 추가(PG 누락 2) |
| `jobRoutes.ts` | `KINDS`(`:15`)·허용 키(`:47`)·관리자 검사(`:132-134`)·`llmSpec` 분기(`:219-228`)·큐 선택에 `recommend_thresholds`; 룰 세트 리비전 검사; `POST /api/rule-set/threshold-stats` 신설(SQL p95) |
| `trainer/runner.py` | `_execute`에 `recommend_thresholds` 분기: 룰 세트 스냅샷 → 카탈로그 필드 → `definitions` 변환 → 현행 `recommend_rules` 재사용 → 결과를 추천 항목으로 변환(Rule 초안 생성 제거); `_finish` kind 집합에 추가 |
| `research_design.py:222-224` | DSL 값 `str(request["baseline"]["value"])` → `Decimal` 고정소수 직렬화(공용 함수), 실패 시 항목 `rejected` |
| `generation_client.py:54-55` | `generation_timeout` 분리(위 실패 절) |
| `llm_worker.py`·`generation_service.py` | `DesignModel.generate("rule", …)` 어댑터 그대로(입력 형식 불변). (나) 모드 어댑터는 LLM 팀원 |
| `queues.md` 7행 | `recommend_thresholds` 추가([queues.md](queues.md) 변경 제안 절) |

- GPU·외부 생성 서비스 호출은 사용자 승인 환경에서만. 이 계약은 호출을 요구하지 않음(11/02 9.1 조사도 가짜 LLM으로 서버 수용 범위만 확인)

## 확정 요약·결정 질문

- 확정: `recommend_rule`·`recommend_feature` 폐지 경로 / 집계값만 입력(Q-M) / `generation_timeout` 분리(N20) / 임계값 1차 = 코드 p95 + LLM 근거(11/02 9.2) / 위험도는 사람이(9.2) / 일괄 + 규칙별 수식(R5)
- 제안: 작업 종류 이름 `recommend_thresholds` / 추천 1건 필드·상태 5종·`source` 2종 / 필드 단위 격리·작업 성공 규칙 / 수식 추천 API 경로·구현 주체 API+SQL / `recommend_methods` 보류

| # | 질문 | 제안 |
|---|---|---|
| Q-L1 | (a) 일부 처리 뒤 서비스 장애: 작업 전체 failed(현행) vs partial 저장 (b) 전 항목이 생성계 사유로 rejected: 성공 vs `recommendation_all_rejected` failed | (a) 전체 failed(부분 결과 없음, 화면 설계 9.2와 일치) (b) **failed `recommendation_all_rejected`**(reviewer 권고 4). `no_applicable`만인 경우는 성공 유지 |
| Q-L2 | 수식 추천 구현 주체 API+SQL vs Trainer 동기 경로 신설 | API+SQL(동기, 큐 없음). p95 일치 시험 필수 |
| Q-L3 | `recommend_methods` 보류 vs 폐지 | 보류(모델 카탈로그 Issue 뒤 재검토) |
| Q-L4 | 1차 `operator` 항상 `gt` | 유지(코드 `reference_policy`). `lte/lt` 방향 필드가 생기면 카탈로그에 `recommendedOperator` 추가 |
