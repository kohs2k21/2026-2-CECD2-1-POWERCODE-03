# 가공 완료 CSV 반입 계약 v1

- Issue: #62; `prepared-csv-v1`, CSV 전용; SQL/SQLite·실행 파일·모델/fit 파일 업로드 제외
- 범위: 전처리·인코딩·스케일링까지 완료한 숫자 피처 표 및 합성 이상 주입 표
- 원본 운영 관측·Detector 큐·알림·활성 구성과 분리; 외부 결과의 운영 적용 자격 항상 차단

## API·비동기 작업

- 관리자 `POST /api/prepared-datasets`, JSON `{csvText, manifest}`; `Idempotency-Key` 1~128자 영숫자/`_.:-`
- CSV UTF-8 원문 최대 5 MiB, JSON 최대 32 MiB, manifest 최대 64 KiB; 원문 로그 출력 금지
- 응답 `201 {id,jobId,status}`; 같은 관리자·키·동일 canonical manifest/CSV hash는 `200`, 다른 입력 `409 idempotency_payload_conflict`
- `GET /api/prepared-datasets/:id`: 관리자 상태·manifest·hash·검증 결과·snapshotId 조회, 파일 경로/원문 응답 제외
- 원본 `ARTIFACT_ROOT/prepared/<datasetId>/original.csv` 원자 저장 및 SHA-256; 완료 파일만 작업 접수
- DB 원본 등록과 `run_jobs(kind=import_csv,contract_version=v03,queue=training)` 및 기존 training queue 접수: 단일 트랜잭션
- 작업 `input_spec`: `{kind:'import_csv',sourceId,preparedDatasetId}`; 파일 경로·manifest는 DB 고정 입력에서 조회
- worker: 기존 lease/attempt/token 검증·재시도·최종 성공 트랜잭션 재사용; 부분 검증 결과 공개 금지
- 정규화 파일: `prepared/<datasetId>/<normalizedHash>.jsonl`, 완성 파일/hash 확인 후 ready 처리; 같은 입력 재시도 시 내용 주소 재사용
- 상태 `queued → ready|failed`; 일시 실패 시 queued 유지·기존 작업 재시도, 실패 확정 후 입력 보존·새 반입 요청으로 재실행
- ready 데이터셋·메타데이터·파일 참조 불변; API 접수 실패의 미참조 파일은 데이터셋으로 공개되지 않음

## manifest

- 정확한 키 및 타입: [prepared-csv-manifest.schema.json](prepared-csv-manifest.schema.json)
- `contractVersion:'prepared-csv-v1'`, `sourceId`, `sourceSchemaVersion`, `domain`, `rowCount`, `inputStage:'model_input'`
- `features`: 1~32개 `{name,type:'number',unit}` 순서 고정; metadata 필드와 중복 금지
- `preprocessing`: `{definitionHash,scaling:'none'|'applied',fitHash:sha256|null,fitSplit:'train'|'unknown',trainRowIdsHash:sha256|null}`
- 가공 hash/fit 출처 선언은 증거와의 대조 대상; 임의 코드·전처리 실행/복원 요청으로 사용 금지
- 완료한 feature runtime/fit 및 모델 내부 scaler 재적용 금지; prepared 전용 모델 형식·직접 IF 학습/점수 경로 사용
- 기존 모델 평가는 입력 단계·source·domain·피처 순서/타입/단위·definitionHash·scaling·fitHash 등 전체 입력 규약 일치 시만 허용
- 입력 descriptor 없는 raw/legacy 모델은 prepared 입력 호환 미확인 오류; 전처리·단위 추정으로 평가 허용 금지
- `split:{version:'provided-v1',timeBasis:'occurred_at'|'unspecified'}`; 반입 순서로 시간/분할 생성 금지
- `columns`: 필수 `rowId`; 선택 `split,label,status,isSynthetic,scenario,originRowId,groupId,occurredAt`; 값은 CSV 열 이름 또는 null(선택 열 미제공)
- sourceSchemaVersion/domain 및 설명 문자열: 비어 있거나 앞뒤 공백 있는 값 거부
- 열/피처 이름: `[A-Za-z_][A-Za-z0-9_]{0,127}`, camelCase·대소문자 보존; API source UUID는 lowercase canonical로 정규화
- `labelSource` 선택: `{kind:'manual'|'synthetic'|'unknown',reference:string|null}`; 미제공 unknown, 라벨값과 출처 미확인 사유 분리
- `synthetic` 선택: `{generator,version,seed,intensity,baselineReference}`; 미제공 생성 정보는 미확인
- hash: SHA-256 lowercase hex; 객체 key를 재귀 사전순 정렬한 compact JSON UTF-8
- manifest 작은 소수 intensity: Python JSON 표기(절댓값 0보다 크고 1e-4 미만이면 지수, 지수 최소 2자리)로 통일; API DB 저장 숫자는 JSON.stringify 정규화
- `trainRowIdsHash`: 정렬된 모든 train rowId의 compact JSON 배열 SHA-256; 실제 fit 대상·학습 범위 대조 필수

## CSV·검증

- UTF-8 CSV, 쉼표·RFC4180 따옴표·CRLF/LF; 중복/알 수 없는 header·형식 오류·빈 피처·비유한값·열 수 불일치 거부
- manifest의 전체 mapping 및 피처 이름과 정확히 일치하는 header; 피처의 상대 순서는 features 순서와 일치
- 최대 10,000행·64열·검증 20초; manifest.rowCount와 실제 행 수 대조
- `rowId`: 비어 있지 않은 고유 문자열; `split`: train/validation/test 또는 null(미제공·빈값); 동일 원본/거래/재시도 group의 split 교차 거부
- `label`: anomaly/normal/unknown; 미제공 unknown; 상태 S를 normal 정답으로 변환 금지
- `status`: S/F/P/N/unknown; 미제공 null; `isSynthetic`: true/false, 미제공 null
- 합성 표시와 scenario 보존; scenario·synthetic 선언과 flag 모순은 경고/학습 제외; 합성 anomaly 라벨의 자동 추정 금지
- `occurredAt`: 제공 시 엄격 UTC 시각; 등록 시각과 별도. 시간 미제공은 unspecified로 명시
- 정규화 JSONL 행: `{rowId,originalRowNumber,split,label,status,isSynthetic,scenario,originRowId,groupId,occurredAt,features:[number...],lineage:{}}`
- 원본 행 번호: header 제외 record 순서의 2부터 시작; quote 내부 개행은 별도 행으로 계산하지 않음
- PG `row_metadata`: 같은 행에서 features 제외한 배열; `validation_result`: counts·split·누수/계보·학습 자격·미측정 사유
- 원본/fit/split/time 증거 미제공·검증 불가: leakage 검증 unverified 명시; 전체 데이터 fit은 재분할로 정상화 금지
- 학습: train split 중 status=S·label≠anomaly·isSynthetic=false·행의 합성 scenario 표시 없음만 허용; 자격 정보 누락/적격 0행은 학습 오류. validation/test는 학습 필터 제외
- manifest.synthetic는 합성 방법 정보; 원본+주입 혼합 CSV의 모든 원본 행을 합성으로 취급하지 않고 행별 자격 검사
- split 미제공 반입은 허용·검증 미확인; 명시된 대상 split 부재 시 학습/평가 불가 사유 반환
- 피처 행 평가와 PROCESS 평가 분리; v1 숫자 행 HR/SR 미측정·정적 ESB 지연 미측정·미라벨 정확도 미측정

## Trainer·저장 경계

- ready 시 `data_snapshots(schema_version='prepared-features-v1')` 고정 헤더 및 dataset.snapshot_id 연결; observation/member 생성 금지
- snapshot selection/spec에 external dataset 참조, split.version/timeBasis 및 hash 기록; 외부 reader에서만 정규화 파일 로드
- 기존 `/api/jobs` train: `{kind:'train',sourceId,snapshotId,detectionPolicy:{mode:'ml_only',deadlineMs,combination:'selected_path'},modelKey?:'iforest'}`
- 외부 train 요청의 from/until/featureSetId/ruleVersionIds 금지; 외부 dataset에서 worker가 고정 FeatureSet 생성
- evaluate: 기존 `{kind:'evaluate',sourceId,configVersionId,snapshotId,purpose:'validation'|'test'}`; 외부 입력의 syntheticEvaluation 금지
- 외부 학습 후보 configuration에 prepared 계보 및 `operationalEligibility:{eligible:false,reason:'prepared_detector_reproduction_unverified'}` 보존
- 외부 평가 성공·accuracy/분할 증거와 운영 적용 분리; 관리자 승인·기존 evaluation/Detector gate 우회 금지
- 원천 DB snapshot 학습·평가 경로 및 기존 API 입력 hash 보존

## 최소 내보내기 예시

- 고객 원본 없는 3행 반입 전용 예시; 실제 학습은 기존 20% 내부 calibration 정책 유지, S-only·비합성 적격 최소 625행(fit 500행 + calibration 125행) 필요
- sourceId는 사전 등록된 테스트 source UUID, sourceSchemaVersion은 해당 source.schema_version으로 치환
- identity 정의 문자열 `identity:v1`의 SHA-256 사용; 외부 fit 없음·사건 시각/그룹 증거 없음으로 누수 검증 미확인·운영 적용 차단
- 아래 CSV 원문을 JSON csvText 문자열로 인코딩하고 아래 manifest를 함께 POST; 분할/라벨/상태/합성 여부는 metadata 열

```csv
row_id,split,label,status,is_synthetic,value
r1,train,normal,S,false,0.2
r2,validation,normal,S,false,0.3
r3,test,anomaly,F,true,9.8
```

```json
{
  "contractVersion": "prepared-csv-v1",
  "sourceId": "10000000-0000-4000-8000-000000000001",
  "sourceSchemaVersion": "process-v1",
  "domain": "controlled-example",
  "rowCount": 3,
  "inputStage": "model_input",
  "features": [{"name": "value", "type": "number", "unit": "count"}],
  "preprocessing": {
    "definitionHash": "da67fa0bce484fa75e4bceefaae506dfb6e5a58919fc3cd8158c92471de0f08d",
    "scaling": "none",
    "fitHash": null,
    "fitSplit": "unknown",
    "trainRowIdsHash": null
  },
  "split": {"version": "provided-v1", "timeBasis": "unspecified"},
  "columns": {"rowId": "row_id", "split": "split", "label": "label", "status": "status", "isSynthetic": "is_synthetic"},
  "labelSource": {"kind": "manual", "reference": "controlled-example-v1"}
}
```

- 합성 r3 생성기/seed 등 미제공 정보는 미확인으로 보존; CSV 반입 성공과 적격 학습 최소 행 수/모델 호환 성공은 별도 결과
