# Rule 초안 API 계약

`ml_rule_versions`의 `rule_spec`, `input_spec`, `evidence_spec` JSONB에 규칙을 버전별로 저장한다. 이 API는 초안만 관리한다. 규칙 승인, `ml_config_rules` 연결, 활성 구성 전환, Detector 실행은 이 API의 범위에 포함되지 않는다. 추천 모델의 결과도 사용자가 확인한 뒤 같은 생성 API로 등록할 수 있다.

모든 요청에는 기존 JWT Bearer 토큰이 필요하다. 모든 로그인 사용자가 모든 규칙의 초안을 조회·생성·수정·삭제할 수 있다. `createdByActorId`는 기존 JSON 계정의 ID를 기록하며 PostgreSQL 사용자 FK가 아니다.

| 동작 | 요청 | 결과 |
| --- | --- | --- |
| 최신 초안 목록 | `GET /api/rules?limit=50&offset=0` | 키마다 최신 버전 하나. 기본값으로 삭제 표시된 규칙 제외 |
| 삭제 표시 포함 목록 | `GET /api/rules?includeRetired=true` | 삭제 표시된 규칙 포함 |
| 최신 버전 조회 | `GET /api/rules/:ruleKey` | 규칙과 `ETag: "N"` |
| 버전 이력 | `GET /api/rules/:ruleKey/versions` | 최신순, 최대 100개씩 |
| 특정 버전 조회 | `GET /api/rules/:ruleKey/versions/:version` | 불변 버전 하나 |
| 생성 | `POST /api/rules` | 버전 1, HTTP 201 |
| 수정 | `PUT /api/rules/:ruleKey`, `If-Match: "N"` | 새 버전 N+1 |
| 삭제 | `DELETE /api/rules/:ruleKey`, `If-Match: "N"` | 상태가 `retired`인 새 버전 N+1 |

생성 본문 예시:

```json
{
  "ruleKey": "process_errors",
  "displayName": "프로세스 오류 수",
  "description": "오류 수가 0보다 큰 프로세스",
  "condition": { "field": "P.error_count", "operator": "gt", "value": "0" },
  "outcome": { "decisionCode": "PROCESS_ERROR", "severity": "warning" }
}
```

수정 본문은 `ruleKey`를 빼고 같은 형식을 사용한다. `If-Match`의 버전이 최신과 다르면 HTTP 412를 반환한다. 헤더가 없거나 형식이 잘못되면 HTTP 428이다. 이미 존재하는 키 생성 또는 내용이 같은 수정은 HTTP 409다. 삭제는 DB 행을 제거하지 않으며, 기존 운영 구성에서 참조하는 과거 버전에도 영향을 주지 않는다. 삭제 표시 후 수정하면 새 `draft` 버전으로 다시 사용할 수 있다.

조건은 `all`/`any` 묶음과 단일 비교를 지원한다. 원천 필드는 `P.success_count`, `P.error_count`, `P.total_count`, `P.retry_count`, `P.status`, `P.response_code`, `P.adapter_type`, `P.channel_id`, `P.process_hub_id`, `P.process_id`다. 파생변수는 `F.<변수이름>` 숫자 필드로 참조하며 이때 `featureSetId`가 필수다. DB 저장 시 해당 변수셋의 출력 이름인지 확인하고 학습/운영 구성의 변수셋 버전과 일치시킨다. 숫자는 `eq/ne/gt/gte/lt/lte`와 십진수 문자열, 문자열은 `eq/ne/in`을 지원한다. 조건 깊이 3, 노드 32, 묶음 자식 8개로 제한한다. BODY, `response_message`, 인증 정보, 임의 SQL/Python 표현식은 입력으로 받지 않는다. 입력값이 누락되면 `unavailable`로 처리한다. Rule 실행기는 `server/worker/detector/rules.py`에 구현되어 있다.

서버 DB에 이미 `002_core_erd.sql`이 적용됐다면 `003_rule_versions_immutable.sql`을 검토 후 적용한다. 이 migration은 기존 규칙 버전의 UPDATE/DELETE를 금지한다. `002`에 없는 DB에는 `003`을 단독 적용할 수 없다.
