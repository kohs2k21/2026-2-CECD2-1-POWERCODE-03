# 합성 평가 서비스 계약 v1

## 범위·의미

- 기존 `POST /api/jobs`의 `kind=evaluate` 선택 확장, PostgreSQL training queue·멱등키·권한·재시도 재사용
- 기존 IF·피처 순서·train 전용 fit·임계값 고정; 합성 평가 중 모델/fit 재학습·운영 구성 변경 없음
- ESB: 원본 복사본의 허용 필드 변경 → 같은 파생 피처 재계산 → 같은 Detector 계산 경로
- ADBench: 고정 피처 공간의 수치 행 생성 → 동일 모델 평가; PROCESS 식별자·부모 관계·ESB 장애 정답 생성 없음
- 근거: `research/evaluation/process_synthetic_lab`의 Issue #53 방법 검증; 운영 코드의 research import 없음
- 프로필·시나리오는 연구용 제한 지원. 합성 변형 탐지율은 고객 장애 진단 성능·모델 채택 근거와 구분

## 지원 프로필·등록

- `GET /api/jobs/capabilities`: `syntheticProfiles`의 버전·생성 위치·단위·허용 변경·지원/보류 시나리오 조회
- `PUT /api/jobs/sources/:sourceId/synthetic-profiles`: 관리자 명시 등록
- 등록 본문: `{profileId,profileVersion:"1",sourceSchemaVersion}`; 서버의 실제 source schema와 일치 필수
- 저장: 기존 `ops_sources.field_catalog`의 다른 키를 보존한 `syntheticProfiles[profileId]`
- binding: `profileVersion/sourceSchemaVersion/staticSpecHash`; 정적 명세 hash는 서버 계산
- 같은 등록은 멱등 처리, 같은 source·프로필 버전의 다른 binding은 충돌; ESB/ADBench 동시 등록 가능
- source schema 이름과 snapshot `process-v1` 별도 검증. schema 이름만으로 고객 코드 의미·로그 완전성 보장 없음
- 관리자 등록과 행별 타입·단위·관계 검증 모두 필요. 미등록·버전 불일치·정의 변조는 명시적 거부

| 프로필 | 평가 단위 | 지원·제약 |
|---|---|---|
| `esb-process-research-v1` | 주입 PROCESS | P02 개별 지연·P03 연속 지연·P04 연구 count 계약 변형; P01 업무 실패 의미 미확정 보류 |
| `adbench-numeric-v1` | 생성 수치 피처 행 | 고정된 ADBench local/cluster/global; 분포 소속 라벨, ESB 정상/장애 라벨 아님 |

- P02/P03: train 전용 어댑터 기준선·관측된 부모/의존 시간 제약 필요, 원본 종료시각 외 임의 필드 변경 없음
- P04: `total_count=success_count+error_count`가 성립하는 행의 total_count만 변경하는 연구 계약 대조
- 실제 상태 HR·등록 Rule·ML 결과 사용. 연구 count 검사와 운영 상태 HR의 별칭 처리 금지
- 미지원·의미 미확정·생성 불가능을 정상/성공 데이터로 대체하지 않음

## 평가 요청

```json
{
  "kind": "evaluate",
  "sourceId": "<source UUID>",
  "snapshotId": "<ready snapshot UUID>",
  "configVersionId": "<immutable config UUID>",
  "purpose": "validation",
  "syntheticEvaluation": {
    "profileId": "esb-process-research-v1",
    "profileVersion": "1",
    "scenarios": [{"scenario": "P04", "severity": 1}],
    "seed": 42,
    "repetitions": 2,
    "targetRows": 10
  }
}
```

- 기존 인증·관리자 권한·멱등키 동작 유지, `purpose=validation/test`만 허용
- seed: 정수 0~2147483647; repetitions: 1~5; targetRows: 1~100; 시나리오 항목 1~4개·반복×항목 최대 20
- 현재 실행 상한: 평가 cohort 2000행·학습 참조 10000행·부모/의존 문맥 10000행·산출물 64MiB. 초과 시 명시적 실패, 무통보 표본 추출 없음
- ESB severity: 1/2/3, 지연 배수 1.5/2/3·count 증가 1/2/3; ADBench severity=1
- 알 수 없는 키·중복 항목·지원 외 모델/프로필/시나리오·source/config/snapshot 불일치 거부
- 내부 source binding은 서버 고정, 클라이언트의 내부 binding 주입 거부
- snapshot 분할·회원/원본 행·선정 정책 불변; 생성 통계는 train만, 변형은 선택 validation/test 복사본만

## 결과·재현

- 조회: 기존 `GET /api/jobs/:id`의 `resultSummary.evaluationEnvelope.synthetic`
- 스칼라 지표: 기존 [평가 봉투](trainer-evaluation-envelope.schema.json)의 value/unit/분자/분모/coverage/reason 규약
- 상세표·측정 범위·manifest 참조: `details`; ESB와 ADBench 결과·분모 합산 금지
- ESB: HR/SR/ML/종합의 시나리오·반복별 주입/탐지/미판정·탐지율·주입 전후 신규/기존 경보
- 주입 미판정도 주입 PROCESS 분모에 포함, 반복 합계와 고유 원본 수 구분
- 비주입 원본·S를 정상 정답으로 생성하지 않음; 정상 정답 없는 Precision/FPR은 null+사유
- SR 실제 미연결은 null+사유, HR·사용자 Rule로 대체 금지
- 정적 합성 시험의 실제 ESB 발생→판정 지연은 null+사유. 실제 계산 비용과 별도
- manifest: source/snapshot/hash·분할·프로필/정의 hash·seed/반복·생성/피처 버전·모델/fit/hash·행 정답·변경/부모 계보·파일 hash 고정
- 합성 산출물은 `evaluations/<job UUID>/<content SHA>.json`으로 저장, 임시 파일은 임의 UUID로 격리·완성 후 원자 이동
- 같은 내용의 재시도는 hash 검증 후 재사용, 실패·lease 상실의 미게시 파일을 성공 결과로 공개하지 않음
- DB 평가 결과·job 성공·queue ack는 기존 소유권 재검사 뒤 같은 트랜잭션 확정
- 생성 거부·미지원·실패·측정 완료 별도 상태. 재시도·lease 상실·중복 실행 시 결과의 중복 확정 차단
- 일부 생성 완료는 모든 시도의 성공/거부/실패·부분 측정 범위 명시, 전부 미생성은 작업 실패
- 합성 측정 완료만으로 운영 적용의 원본 validation 검증을 대체하지 않음; 별도 원본 평가·관리자 적용 유지

## 제외

- 최종 시나리오/모델 선정·SR 연구·장기 추세 필수 평가·외부 CSV/SQL 반입
- 프론트 레이아웃·자동 승인·운영 서버/DB 적용·실고객 지연·대규모 처리량 보장
