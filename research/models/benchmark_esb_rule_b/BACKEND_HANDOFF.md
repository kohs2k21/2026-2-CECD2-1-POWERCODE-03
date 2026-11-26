# B Rule provider 연결

## 범위와 교체 지점

이번 변경은 `research/`에 독립 provider를 추가한다. `server/`, DB migration, 배포 설정, 활성 규칙을 수정하지 않는다. 기존 `rule_baseline_v3` provider를 사용하는 작업을 B라고 표시하거나, B 응답에 `v=3`만 붙여 기존 검증기에 넣지 않는다.

백엔드 담당자는 **입력 생성 → B 호출 → native 검증 → baseline 초안 변환**을 하나의 provider로 선택한다. Feature 계산·ML은 같은 피처 정의·순서·단위·결측·집계 단위·전처리 계약을 유지할 때 재사용한다. 새로운 피처를 추가하면 그 변경과 ML 재학습을 별도로 수행해야 한다.

## 기존 TRAIN 입력 생성 코드 재사용

```python
from research.models.benchmark_esb_rule_b import (
    build_service_request, validate_prediction, convert_suggestion,
)
from research.models.benchmark_esb_rule_b.inference import BRuleModel

# records/context는 기존 baseline_rule_recommendation의 TRAIN 입력 계약 사용.
# definition에는 기존 name/definition_sha/dtype/unit/expression/meaning과
# 검증된 grain/population/group_by/missing_policy/source_columns를 함께 전달.
request = build_service_request(train_records, definition, context)
result = BRuleModel(base_path).recommend(request["input"], new_output_directory)
draft = None
if result["status"] == "validated":
    draft = convert_suggestion(request["input"], result["prediction"])
```

- 기존 `baseline_rule_recommendation.build_request`의 TRAIN 전체, 거래·동시각 묶음 보존 80:20 시간 분할과 정확한 p95 계산 재사용
- 실제 수집 시각 `persisted_at`에 해당하는 timezone 포함 `event_time` 필요; CSV 순서·UUID로 대체하지 않음
- 기존 caller가 고정한 TRAIN 분할을 `context.partition`으로 전달할 수 있음
- 미확인 단위는 native에서 `null`; 현재 관측과 참조는 분리
- 피처 정의·분석 단위·모집단·원천 열은 upstream 계산·검증 결과를 사용; 이 함수가 새 의미나 관계를 추정하지 않음
- 입력이 차단되면 실패 기록; 잘못된 시간 분할로 임의 기준선을 생성하지 않음
- 라벨·원천 행·거래 ID 목록을 모델 입력으로 전달하지 않음; 기존 builder가 허용하는 라벨은 내부 참조 선정에만 사용
- 이 서비스 bridge는 수치 피처용; 일반 native 입력의 Boolean·관계 진단은 전체 명세·검토 API에서 별도 처리

## native 입력과 원문

`feature`에는 `id`, `sha`, `meaning`, `expression`, `dtype`, `unit`, `grain`, `population`, `group_by`, `missing_policy`가 필요하다. `measurement_id/measurement/source_version/scope/sources/constraints/baseline/range_proof/verified_relationship_bounds/observed_diagnostic_contract`와 `requests=["hr","observed","baseline"]`를 제공한다.

baseline에는 TRAIN 참조·관측의 정의 SHA 호환성, 단위·모집단·기간·유효 표본 수·최소 표본 수·p95·strict gt·결측·갱신·만료 정책을 함께 기록한다. 관측 최댓값을 보편적인 상한으로 추정하지 않는다. `validate_input()`은 구조와 연결을 확인하며, 제공자가 주장한 단위나 원천의 실제 업무 의미를 독립 입증하지 않는다.

원문은 다음 필드를 유지한다.

```json
{
  "v": "2",
  "feature": "exact_feature_id",
  "sha": "exact_definition_sha",
  "decisions": [
    {"request":"hr","role":"HR","state":"no_applicable","basis":"none","condition":null,"evidence":[],"needed":[],"reason":"No confirmed constraint supplied."},
    {"request":"observed","role":"SR","state":"proposed","basis":"observed_diagnostic","condition":{"op":"is_missing"},"evidence":["measurement_id"],"needed":[],"reason":"Calculated unavailability only; incident meaning unconfirmed."},
    {"request":"baseline","role":"SR","state":"proposed","basis":"reference_p95","condition":{"op":"gt","value":418.0},"evidence":["measurement_id","baseline_id"],"needed":[],"reason":"Use the supplied TRAIN p95."}
  ]
}
```

위 값은 형식 설명용이며 418을 새 데이터에 고정 적용하지 않는다. 실제 임계값은 각 입력에서 검증된 TRAIN 기준을 사용한다.

## 서비스 초안·탐지 연결

`convert_suggestion()`은 전체 native 응답이 유효할 때만 실행한다. baseline이 `proposed`이면 기존 DTO 이름인 `ruleKey`, `displayName`, `condition`, `outcome`, `evidenceSpec`을 반환한다. `condition`은 `F.<name>`, `gt`, 모델에 제공·생성된 같은 경계의 고정소수점 문자열이다. 정밀값으로 조용히 치환하거나 40자 경계를 자르지 않는다.

내부 feature ID와 서비스 필드명이 다르면 다음 바인딩을 명시한다.

```python
draft = convert_suggestion(native_input, model_prediction, field_binding={
    "feature_id": registered_feature_id,
    "definition_sha": registered_definition_sha,
    "unit": registered_unit,
    "field_name": registered_service_field_name,
})
```

- `evidenceSpec`: native 입력·원문 판정, feature SHA, baseline/measurement ID, 참조·관측 기간, 표본 수, 단위, 입출력 SHA, 필드 바인딩 보존
- `deferred/request_evidence/no_applicable`: 초안 `None`; 기존 규칙 삭제·비활성화·정상 판정의 뜻 아님
- 검증 실패: 초안 생성 금지, 오류와 최초·수정 응답 보존
- HR 고정 정책: 이 converter는 새 HR 실행 규칙을 등록하지 않음
- 관측 SR: 원문 검토 기록 유지; 결측 진단을 자동으로 장애 경고에 합치지 않음
- AE 기반 SR, 별도 논리 SR, 기존 수치 SR 및 ML: B 생성물과 구분되는 출처·버전 유지
- 탐지에서 결측·타입·단위·정의 불일치: 평가 불가; 기존 시스템의 결합 정책 유지
- SR 수치 조정: 관리자 검토와 새 규칙·기준선 버전 등록; 탐지 중 기존 버전 덮어쓰기 금지

`evaluate_draft()`는 계산된 스칼라를 대상으로 경계·결측·정의·단위 및 초안 무결성을 확인하는 연구용 실행 검사다. DB 활성화·운영 Detector·승인 정책·실시간 API를 대신하지 않는다.

## XAI와 인수 검사

현재 XAI 서비스 어댑터는 Detector evidence-v4와 고정된 Detector 코드 SHA를 확인한다. B의 native JSON을 XAI 입력으로 직접 전달하지 않는다. 백엔드가 채택한 규칙에 대해 실제 발동 규칙 ID, 관측값, 비교 기준, 단위, 정의 SHA, 규칙 세트 버전과 원천 근거를 기존 evidence 계약으로 저장해야 한다.

최소 인수 흐름은 고정 snapshot → 동일 피처 계산 → B 추천 → 검증 → 관리자 채택 → 새 규칙 버전 → 동일 새 로그 탐지 → evidence-v4 → XAI다. 이 전달에서는 CPU 계약·실행 검사와 과거 실제 응답 재생을 수행했다. 서비스 DB·큐·API·UI 및 XAI의 새 provider E2E는 실행하지 않았다. 서버 담당자의 명시적 연결 전에는 기존 provider가 계속 사용된다.
