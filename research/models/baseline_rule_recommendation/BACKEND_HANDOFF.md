# 백엔드 전달 계약 — baseline 수치 SR

이번 GitHub 전달 범위는 `research/`다. 모델·연구 코드·입출력 계약·검증 예시를 제공하며, `server/`의 로컬 참고 수정은 포함하지 않는다. 실제 DB·큐·API·UI·탐지기 연결과 배포는 백엔드 개발자가 담당한다. 현재 백엔드가 자동으로 새 모델로 전환된 상태는 아니다.

## 사용할 진입점

| 목적 | 경로 | 범위 |
|---|---|---|
| 새 수치 SR 추천 | `research.models.baseline_rule_recommendation` | 미리 계산한 피처 → TRAIN 요약 → Rule LLM → 검증·초안 |
| 기존 전체 명세 연구 | `research/models/feature_rule_spec` | Feature silver51·고정 Rule LoRA·공식 CLI·검증·공개 예시 |
| 기존 피처/Rule/ML 오프라인 연결 | `research/integration/feature_rule_ml` | 동결 피처 재계산, HR·SR·IF/K-means/LOF 연결 |
| 최신 Process 행 ML 연결 | `research/integration/feature_rule_ml/PROCESS_EVENT_INTEGRATION.md` | PR #98 동결 모델·별도 Python 3.12·같은 원천 Process의 HR/SR 결합 |

위 진입점은 서로 다른 실행 프로필이다. 새 baseline 경로는 `requests=["baseline"]`만 생성하며 HR·관측 SR은 생성하지 않는다. 기존 HR·규칙을 삭제·수정하지 않는다. 로컬 서비스 시험에서는 Feature v5를 유지했지만, 전체 연구 패키지의 기본 Feature는 silver51이다. Feature 모델까지 바꿀지는 별도 결정해야 한다.

## 모델과 버전

| 항목 | 고정 값 |
|---|---|
| provider 식별자 | `rule_baseline_v3` |
| 입력 계약 | `baseline-rule-recommendation1.0` |
| 출력·서비스 계약 | `rule3.0-purpose-baseline1.0` |
| 요청·프롬프트 버전 | `baseline-rule-service-v1` |
| Base | `Qwen/Qwen3.6-27B`, 실제 클래스 `Qwen3_5ForConditionalGeneration` |
| Base revision | `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9` |
| Rule 경로 | `research/models/feature_rule_spec/models/rule` |
| Rule weight SHA256 | `d816cf6a1f264149fb3d4db9c7cc929a76b12ce6f04f0407a4c4b459dbd573c9` |
| Rule config SHA256 | `1a04f51652f28f85da78939ece45fdf69178155aeda894977d9e0be74b96219b` |

기존 Rule 가중치를 재사용한다. 새 SFT·ML 재학습이나 가중치 복제는 없다. Base 가중치는 GitHub에 추가하지 않으며 승인된 로컬 경로를 명시한다. 로딩 전에 `feature_rule_spec/models/base_files.json`의 전체 SHA와 Rule weight/config SHA를 확인한다. 다른 모델이나 저장 응답으로 자동 대체하지 않는다.

## 1. 백엔드가 준비할 입력

같은 고정 snapshot의 TRAIN 전체를 준비한다. validation/test를 섞거나 임의의 1만 행 접두부를 먼저 잘라 쓰지 않는다. 로컬 참고 구현의 상한은 100만 행이며, 상한 초과 시 조용히 샘플링하지 않고 별도 분할 정책을 정해야 한다.

```json
{
  "observation_id": "내부의 고정된 Process 관측 ID",
  "transaction_key": "동일 거래를 연결하는 안정적인 거래 ID 해시",
  "event_time": "2026-01-01T00:00:01+00:00",
  "features": {"total_count": "12"},
  "missing": [],
  "label": null
}
```

- `event_time`: 확인된 DB 수집 시각 `persisted_at`, timezone을 명시한 ISO 문자열. 처리 시각으로 주장하지 않는다. 정보가 없으면 UUID·CSV 행 순서·추정 시각으로 대신 나누지 않는다.
- `transaction_key`: 같은 거래에 연결된 Process는 같은 값. ID는 관계·계보에만 사용하며 탐지 피처로 사용하지 않는다.
- `features`: 고정된 피처 정의로 코드가 정확히 계산한 값. 계산 실패·결측은 `null` 및 `missing`에 보존한다.
- `label`: 확인된 `anomaly`만 참조 계산에서 제외한다. 미라벨을 정상으로 확정하지 않는다. 현재 관측에서는 anomaly도 유지한다.
- 원문 BODY·인증정보·메시지 본문은 입력에 포함하지 않는다. 라이브러리는 모델에 행·ID 목록 대신 통계·묶음 해시를 전달한다.

피처 정의는 다음 형태다. `definition_sha`는 백엔드의 버전 고정 정의를 canonical JSON(`sort_keys=True`, `separators=(",", ":")`, UTF-8, `ensure_ascii=False`, `allow_nan=False`)로 직렬화한 SHA256이다. 단위·계산식·모집단·결측 정책 등 정의 문맥이 바뀌면 새 버전과 SHA가 필요하다.

```json
{
  "name": "total_count",
  "definition_sha": "64자리 소문자 SHA256",
  "dtype": "number",
  "unit": "count",
  "expression": {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "TOTAL_COUNT"},
  "meaning": "해당 Process 원천 행의 전체 처리 건수"
}
```

이 라이브러리는 원천에서 위 계산식을 실행하거나 업무 단위를 확인하지 않는다. 그 작업은 피처 생성·계산 경로의 책임이다. 단위를 확인하지 못하면 `null` 또는 `unknown`으로 넘긴다. fitted feature는 별도의 참조 전용 fit 계약과 버전 연결을 마련하기 전까지 적용을 보류한다. TRAIN 전체로 fit한 뒤 참조/관측을 나누면 누출이 생길 수 있다.

## 2. 요약 생성과 모델 호출

```python
from research.models.baseline_rule_recommendation import (
    split_train, build_request, output_schema, validate_prediction,
    convert_suggestion, NEEDED_LABELS,
)

partition = split_train(train_records)  # 모든 피처에서 한 번 만든 같은 분할 재사용
record = build_request(train_records, definition, context={
    "partition": partition,
    "source_id": source_id,
    "snapshot_id": snapshot_id,
    "feature_set_id": feature_set_id,
    "time_basis": "persisted_at",
    "split_policy": "train-internal-80-20-chronological-v1",
})
# record["messages"]와 output_schema()를 실제 로컬 Rule 추론기에 전달한다.
# 추론기의 실제 parsed JSON을 prediction으로 받는다.
errors = validate_prediction(record, prediction)
draft = convert_suggestion(record, prediction) if not errors else None
```

TRAIN 내부를 시간순 약 80:20으로 나누고 같은 거래·동일 시각 묶음을 보존한다. 참조 종료는 관측 시작보다 앞서야 한다. 안전한 경계가 없으면 근거 요청으로 남긴다. 유효 수치 참조 20개 이상에서 코드가 선형 보간 p95를 계산한다. 모델은 주어진 수치·측정·근거에 맞는 단일 피처의 상측 비교를 제안한다. 업무 SLA나 새로운 업무 규칙을 발견한 것으로 해석하지 않는다.

모델에 제공한 JSON 숫자 경계와 정확한 Decimal 통계를 구분해 기록한다. 서비스 초안은 모델에 제공·생성된 경계를 그대로 사용하며 `value_exact`로 몰래 치환하지 않는다. 40자를 초과하는 고정소수점 조건값은 잘라서 적용하지 않는다.

### 추론기에 적용할 설정

- NF4 4-bit, double quantization, bf16, greedy, `enable_thinking=False`, Rule 출력 상한 1536 토큰, 입력 상한 12000 토큰. 입력을 넘기면 자르지 말고 요청을 나눈다.
- `contracts/baseline_rule_system.txt` 프롬프트와 `output_schema()` 또는 동등한 `contracts/baseline.schema.json`을 함께 사용한다. `rule.schema.json`은 generic v3 기반이며 이 프로필의 최종 스키마가 아니다.
- `xgrammar==0.2.3`의 `compiler.compile_json_schema(schema, any_whitespace=False, separators=(",", ":"))`와 HF `LogitsProcessor`를 사용한다. 자유 공백 문법은 실제로 탭을 반복해 토큰을 소진한 이력이 있다.
- 공유 GPU 할당과 메모리를 확인하고, 로컬 시험과 같은 환경에서는 노출 GPU 1장·bf16·여유 메모리 20GB 이상을 요구했다. 임의의 다른 GPU나 프로세스를 조작하지 않는다.
- 메모리 절약을 위한 embedding CPU offload를 재사용한다면, **PEFT로 감싸기 전 Base의 `get_input_embeddings()`로 접근**한다. 입력 ID를 CPU로, embedding 출력을 할당된 GPU로 옮기는 hook이 필요하다. PEFT 래핑 후 `model.model.language_model` 경로를 그대로 사용하면 로딩 오류가 난 이력이 있다.
- 검증된 GPU 환경 버전은 기존 `feature_rule_spec/model_manifest.json`의 `environment_verified`를 참조한다. 이 문서는 설치·새 학습 실행을 지시하지 않는다.

## 3. 오류 피드백과 출력

모델 응답의 adapter SHA가 위 값과 같은지 확인하고 raw JSON·parsed JSON·토큰 수·잘림 여부·입출력 해시·정의·snapshot·분할 버전을 기록한다. 잘린 응답은 채택하지 않는다. 최초 응답 오류가 있으면 원문을 보존하고 최대 한 번만 수정 호출한다. 수정 입력은 원래 입력의 복사본에 `repair={previous_output, errors, maximum_additional_attempts:1}`를 더한다. 원래 정의·통계·수치를 바꾸지 않는다. 다시 실패하면 실행 초안을 만들지 않고 실패 기록을 반환한다.

원문 v3는 `v`, `feature`, `sha`, `decisions`를 반환한다. 각 decision의 전체 필드는 다음과 같다.

```text
request, role, state, basis, condition, evidence, needed, reason,
purpose, warning_meaning, population_ref, precondition_refs,
required_evaluation_basis, unavailable_action, unavailable_reason,
business_scope, baseline_use_ref
```

| state | condition | needed | unavailable_action | `convert_suggestion` |
|---|---|---|---|---|
| proposed | 알고 있는 조건 | 빈 배열 | preserve_unassessed | 실행 초안 |
| deferred | 알려진 조건 유지 | 현재 유효 관측 필요 | preserve_unassessed | `None` |
| request_evidence | null | 구체적인 부족 근거 | no_rule | `None` |
| no_applicable | null | 빈 배열 | no_rule | `None` |

`no_rule`은 이번 요청에서 **새 실행 규칙을 만들지 않음**을 뜻한다. 기존 규칙 삭제·비활성화·정상 판정의 뜻이 아니다. `deferred`의 조건은 검토 기록에 보존하되 현재 관측에 적용하지 않는다.

검증된 proposed만 다음 서비스 초안으로 변환된다. 전체 실제 입출력은 [공개 제어 예시](examples/actual_service_generation.json)에 있다.

```json
{
  "ruleKey": "baseline_<안정적인 해시>",
  "displayName": "TRAIN 기준 초과: total_count",
  "condition": {"field": "F.total_count", "operator": "gt", "value": "4.0"},
  "outcome": {"decisionCode": "SR_BASELINE", "severity": "warning"},
  "evidenceSpec": {"kind": "train_reference_p95", "meaningReviewRequired": true}
}
```

위 예시의 `evidenceSpec`은 축약 표시다. 실제 반환값은 입력·원문 판정·기준선/측정 ID·기간·표본수·단위·정의 SHA도 포함한다. 백엔드는 원래의 `feature_set_id`·등록된 피처 버전과 초안을 연결하고, 관리자 검토·채택 뒤 버전을 생성한다. 추천만으로 운영 활성화하지 않는다.

## 4. UI·저장·버전 연결

`NEEDED_LABELS`가 다음 고정 API 키의 한글 표시를 제공한다. 키 자체를 한글로 바꾸지 않는다.

```text
need_reference, need_reference_minimum_n, need_compatible_reference,
need_verified_unit, need_valid_observations, need_confirmed_constraint,
need_business_contract
```

기존 `evidence_spec`, 작업 결과 JSON, 규칙·구성 버전 참조에 감사 정보를 연결할 수 있다. 추천→규칙 관계를 강제하는 새 테이블·FK·migration은 요구하지 않는다. 기존 `rule_v2` 작업·입력·스키마와 새 모델 결과를 혼합하지 않는다. 모델·프롬프트·입력 생성·스키마·검증·DTO 변환을 함께 연결한다.

HR 고정 편집 정책, SR 수치만 조정하는 UI/권한 제약, 신규·삭제 최소화, SR/ML 동시 탐지는 백엔드의 별도 운영 정책이다. 이 연구 라이브러리가 DB에서 강제하지 않는다.

## 5. 검증 상태와 인수 확인

이전 전체 로컬 프로토타입에서 CPU33/33, 실제 생성 최초1/3→각 사례 최대1회 수정 후3/3을 기록했다. 그 CPU33개에는 이번에 올리지 않는 서버 연결 시험도 포함된다. `research/` 전달본의 독립 시험·원문 재검증 결과는 [검증 문서](VALIDATION.md)에 구분해 기록한다. 새 GPU 생성·SFT·ML fit은 이번 전달 정리에서 실행하지 않는다.

백엔드는 DB snapshot·TRAIN 전체 선택, 피처 값/단위/SHA, 수집 시각과 거래 묶음, actual adapter SHA, compact 문법, 최대1회 수정, `None`/실패/평가 불가 처리, 관리자 채택을 연결한 뒤 자사 환경의 통합 시험을 수행해야 한다. DB→큐→추천→채택→탐지 E2E와 웹 빌드는 현재 연구 환경에서 검증하지 않았다. 일부 설명 필드의 품질 문제도 남아 있으므로 코드 검사 통과를 설명 전체 의미 승인이나 운영 장애 탐지 성능으로 주장하지 않는다.
