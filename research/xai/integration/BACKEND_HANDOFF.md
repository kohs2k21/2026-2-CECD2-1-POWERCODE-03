# 백엔드 연결 계약

기준 dev: `182e6831fc990d7010d92e6e5ba7c3d05a050eef`.
패치는 연구 전달 자료이며 이번 업로드에서 서비스 파일에 적용하지 않는다.

## 입력

`service_bridge.adapter.explain_detail()`은 다음 투영을 받는다.

```json
{
  "decisionId": "판정 UUID",
  "sourceId": "원본 UUID",
  "processObservationId": "판정에 사용한 P 관측 UUID",
  "transactionId": "추적용 거래 ID",
  "processId": "추적용 프로세스 ID",
  "status": "complete 또는 partial 또는 unavailable",
  "inputManifestHash": "입력 manifest SHA-256",
  "evidence": {"schemaVersion": 4, "methodResults": [], "categoryResults": {}, "composite": {}}
}
```

위 JSON은 필드 안내용이며 바로 실행할 입력은 `../examples/service_HR.json`이다.
`evidence`에는 원래 v4 전체 필드가 필요하다. 원래 저장 JSON을 그대로 전달한다.
고객 Body·인증정보는 전달하지 않는다.

같은 관측의 `processInput`을 제공하면 `observationId`, `status`, `start_time_raw`, `end_time_raw`,
`total_count`, `success_count`, `error_count`만 허용한다. 내장 HR4를 원래 코드로 대조한다.
이 값이 없으면 원시 비교값은 미확인으로 설명한다. 다른 관측 리비전의 값으로 채우지 않는다.

## 실행 경로

1. 기존 `POST /api/jobs`의 `{kind:"explain",sourceId,decisionId}`와 `Idempotency-Key`를 유지한다.
2. `backend.patch`는 v4에서 전체 evidence와 동일 관측 P 입력을 고정하도록 jobRoutes.ts를 수정한다.
3. 새 작업 계약은 `saved-detector-v4-service/1.0.0`이며 과거 v1 작업과 reuse key를 분리한다.
4. 수정 worker는 `service_bridge.worker.explain_job()`을 호출한다. CPU 기본 경로로 GPU·LLM 호출0이다.
5. 기존 `GET /api/jobs/:id`의 `resultSummary`에서 기존 facts/nextChecks와 새 `report`를 조회한다.

LLM을 사용하는 서비스 통합에서는 명시적으로 어댑터 추론을 호출하고 유효한 근거 ID 선택만
`explain_detail(item, selection)`에 전달한다. 전체 상세는 코드가 보존한다.
서비스 입력을 LLM 학습 입력과 연결하는 투영, 요청 예산·타임아웃·대체 경로와 GPU 자원 관리는 통합 담당 범위다.
임의 설명 문장·원인·등급·수치를 LLM 출력으로 덮어쓰지 않는다.

## 선택적 localhost API

현재 컨테이너에서 실행 중인 백엔드의 실제 origin을 `XAI_BACKEND_ORIGIN`에 설정한다.

```bash
python -B research/xai/serve_service.py --port 8097
```

- 읽기: `GET /v1/explanations/{decision_id}`. 기존 Bearer 토큰을 백엔드로 전달.
- 검토 기록: `POST /v1/explanations/{decision_id}/reviews`. 기존 feedback 관리자 권한 적용.
- 상태 확인: `GET /healthz`. 백엔드 연결 상태를 검사한 결과를 뜻하지 않음.

별도 검토 DB는 없다. 기존 feedback의 `{kind,reason,outcome}`을 사용한다.
원인 확인은 원문·시각·문서 SHA·근거 ID·설명 SHA에 묶은 담당자 기록이다.
기본 기업 검토자 허용 목록은 비어 있다. `XAI_ENTERPRISE_ACTOR_IDS`는 신뢰할 수 있는 배포 설정으로 관리한다.
인증된 기업 담당자 기록도 문서 진실성의 독립 검증이나 원인 추론 모델의 정답으로 간주하지 않는다.

## 적용 전 검사

- 패치는 위 고정 dev에서 `git apply --check`와 실제 Python worker10사례·legacy 호환 검사를 통과.
- Node/TypeScript 부재로 백엔드 타입 검사 미실행. 실제 PostgreSQL·작업 큐 통합도 미실행.
- localhost HTTP 검증은 백엔드 계약 대역 사용. 실제 운영 인증·수집·큐·부하 검증과 구분.
- 승인되지 않은 정상·이상 라벨, 새 HR·SR·ML·임계값·심각도 재보정 없음.
- 이번 전달의 성공 기준은 연결 가능한 연구 프로토타입이며 운영 배포 완료가 아님.
