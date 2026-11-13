Created: 2026-11-05T15:00:00+09:00
Updated: 2026-11-13T18:00:00+09:00
Author: #128 Claude Code(WebUI builder)
Status: current

# 머신러닝 모델 화면·모델 버전 조회 구현 계약

- 범위: `/detection/models`·`/detection/models/:versionId` 관리자 화면(#128, 상위 #101 하위 5). 화면 기준 와이어프레임 #93 `section[data-view="detect-models"]`·`detect-model`. 요청·응답 정본은 [model-version.md](model-version.md) 조회 절, [trainer-service.md](trainer-service.md), [method-catalog.md](method-catalog.md)
- 제외: 활성화·교체·복원(하위 7), 평가·비교(하위 6), 입력 변수 AI 추천
- 연결: 조회 `backend/src/services/configurationReadModel.ts`, 거래량 `services/volumeReport.ts`, 카탈로그 `ruleset/methodCatalog.ts`(`contracts/method-catalog.json`을 읽기만 함), 화면 `frontend/src/features/detection/models/`
- 판단 없음: 화면·API는 조회·요청·표시만 한다. 실행 가능 여부·선정·학습은 카탈로그·Trainer 소관
- 용어: '모델 버전'은 그대로 쓴다. 판정 정책은 화면에 없다

## 화면 구성(위에서 아래)

| 영역 | 내용 |
|---|---|
| 버전 | URL 탭 `tab=active|all`(기본 현재 운영 버전) + 높이 고정 자체 스크롤. 열: 버전·모델·상태·운영·입력 변수·학습 파라미터·판정선·최근 평가·생성(`modelSummary`, 값이 없으면 `없음`). 파라미터는 고정 값을 뺀 `키=값`, 판정선은 넷째 자리까지(원값은 상세). 두 탭 모두 최신순 커서 `더 보기`, 현재 운영 버전은 `active=true`. 룰 전용 구성(`modelKey` null)은 모델 버전이 아니라 표시하지 않는다 |
| 새 학습 | 같은 화면 아래, 머리글 버튼 없음. `step=1|2|3`(이전·다음과 단계 표시는 Link push. 단계 표시는 갈 수 있는 단계만 링크이고 앞 단계가 끝나지 않은 단계는 비활성, 다른 주소값은 유지). 앞 단계가 끝나지 않은 주소는 가까운 유효 단계로 replace. `step`이 주소에 있으면 영역으로 스크롤 |
| 학습 작업 | 접수되면 주소에 `job=<작업 ID>`를 두고 영역 맨 위에 상태를 표시(새로고침해도 이어짐). 대기·실행 중만 5초 조회 |
| 버전 상세 | 모델·상태·운영·방법 버전·학습 작업 / 학습 기간·건수·선정 정책·제외 구간 / 입력 변수 / 학습 파라미터 / 산출물(파일 ID·hash·판정선). 머리 오른쪽 `이 버전으로 교체`(운영 설정 하위 7): 실행 가능·운영 중 아님·`applicationEligibility.eligible`일 때만 눌리고 모델 교체 2단계로 간다([operations-ui.md](operations-ui.md) 운영 설정·모델 교체 절) |

- 머리글 '새 학습' 버튼, 교체·비교 평가 버튼 없음

## 와이어프레임에서 뺀 항목

| 위치 | 항목 | 이유 |
|---|---|---|
| 1단계 | 데이터셋 이름·업무 범위 | 학습 요청에 대응하는 키가 없음 |
| 1단계 | 출처 선택 | 고객사 하나당 시스템 하나(사용자 11/05). 첫 출처를 쓴다 |
| 2단계 | 입력 변수 개별 선택·검색·필터·상세·선택 요약 | 세트 등록 API가 없어 등록된 세트 선택으로 대체. 개별 선택은 세트 등록 API로 돌아온다(trainer-service.md 입력 변수 세트 등록 규칙) |
| 2단계 | 분석 단위 선택·집계 간격 | 학습은 PROCESS만 지원. 표시만 한다 |
| 2단계 | AI 추천 | #128 범위 밖 |
| 3단계 | 버전 이름·전처리 선택 | 학습 요청에 키가 없음 |
| 3단계 | 다른 모델 파라미터 목록 | 선택한 모델만 표시(사용자 11/05) |
| 상세 | 평가 이력·자동 비교 평가·새 비교 평가 | 하위 6 |

## 새 학습 단계

1. **기간**: 조회 시작·종료(최대 366일, 한국 시각 날짜) → `조회` → 거래량 막대. 단위는 표시 범위로 정한다(100일 초과 월, 3일 초과 일, 4시간 초과 시간, 그 이하 분, 최소 30분). 휠 확대·축소, Shift+휠·가로 스크롤 이동, `전체 보기`. 표시 범위가 멈추면 그 단위로 재조회한다. 제외 구간은 드래그, 막대 두 번 클릭, `제외 구간 정확히 입력`이 같은 목록을 쓴다. 겹치거나 맞닿으면 합치고 최대 32개. 목록에서 눌러 입력칸으로 수정, 삭제
2. **입력 변수**: 등록된 입력 변수 세트(`GET /api/jobs/feature-sets`)에서 하나 고른다. 학습 요청이 `featureSetId` 하나를 받기 때문이다. `supported=false`는 선택 불가, `reason` 코드를 그대로 표시. 필드 개별 선택은 `ml-feature-fields.json` 목록에서 고른 키를 `POST /api/jobs/feature-sets`로 등록해 받은 `id`를 쓴다(같은 필드 조합은 같은 세트, trainer-service.md 입력 변수 세트 등록 규칙)
3. **모델·파라미터**: `methodCatalog.methods[]` 전부 표시, `executable.process=false`는 선택 불가. 파라미터 표는 선택한 모델만 `parameterSchema`·`defaults`로 만든다(`const`는 고정, 범위는 minimum·maximum, 사용자 11/05). 입력값은 모델별로 따로 보관하고 요청에는 선택한 모델의 값만 보낸다. 이름·뜻·모델 설명은 카탈로그에 없어 와이어프레임 값을 화면 표로 둔다(없는 키는 키 그대로). `준비 검사`: 기간·입력 변수·`inputRequirements.maxFeatures`·파라미터 범위·제외 구간 지원 여부(`capabilities.training.excludedIntervals`, 미지원이면 서버 코드 그대로 `확인 필요 · 제외 구간: config_schema_not_supported_yet`), 위반이면 `학습 요청` 비활성

- 요약의 `학습에 쓰는 건수`는 거래량 집계의 구간 밖 상태 S 건수이고 train 분할은 그중 약 70%다(trainer-service.md 선정 정책 v2의 사전 미리보기). 정확한 검사는 접수·실행 시. 제외 구간이 있는데 미지원이면 건수 대신 같은 코드를 표시한다. 구간 건수를 모르면(조회 중·실패) `제외`·`학습에 쓰는 건수`는 `—`다
- 초안: `draftStore`(경로별). 접수한 작업 ID와 함께 작업 성공 전까지 보관한다(나갔다 돌아오면 초안과 작업 상태가 이어진다). 앱 안 이동 확인은 접수 뒤 고친 내용이 있을 때만 뜬다(접수한 내용 그대로면 값이 보관되어 잃지 않는다). 새로고침·창 닫기·로그아웃은 메모리 초안이 사라지므로 기존 확인이 그대로 뜬다. 성공하면 접수한 내용 그대로인 초안은 비우고, 접수 뒤 고친 초안은 남기며, 결과 표시는 주소 `job`으로 남긴다. 실패면 같은 초안으로 다시 요청할 수 있다
- 표시할 작업: 초안의 작업이 주소 `job`보다 먼저다(브라우저 뒤로 가기로 옛 주소가 돌아와도 진행 중인 작업을 놓치지 않는다). 초안에 작업이 있으면 공유받은 `job` 주소도 초안의 작업으로 표시한다
- 요청 응답 전에 화면을 떠나면 끌고 오지 않는다. 주소의 `job`은 초안의 작업을 따라 지금 보이는 화면이 옮긴다(옛 `job` 주소로 돌아와도 초안의 작업으로 바뀐다)
- 요청 막음: 작업이 대기·실행 중이거나 상태를 알 수 없으면(조회 전·조회 실패) `학습 요청` 비활성. 없는 작업(404)·잘못된 ID(400)는 막지 않는다
- 초안의 저장소는 `draftStore` 하나다(화면 인스턴스가 따로 들지 않는다). 응답이 어느 인스턴스에 오든 지금 보이는 화면에 바로 반영된다
- 멱등키: `job-idempotency`로 만들어 초안(`requestKey`)에 둔다. 같은 내용은 같은 키이고, 응답을 받아도 지우지 않는다. 그래서 응답을 놓친 화면이 다시 눌러도 서버가 같은 작업을 돌려준다. 새 키는 초안의 작업(그 키로 접수한 작업)이 실패 확정이거나 내용이 바뀐 경우이고, 주소에만 있는 옛 작업의 실패는 판단에 쓰지 않는다. 화면이 초안의 작업 성공을 보면 그 작업을 접수한 키만 지운다. 끝난 작업의 키는 남을 수 있고(새로고침 등으로 초안이 없을 때), 다음 같은 내용 요청의 재전송에서 교체된다. 키를 내용으로 찾아 지우지 않는다(같은 내용의 새 요청 키까지 지워 중복 학습이 된다)
- 같은 키로 끝난 작업이 돌아오는 경우: `POST /api/jobs`는 같은 키면 끝난 작업도 200으로 돌려준다. 접수 응답이 이미 `succeeded`면 새 학습이 아니므로 그 키를 버리고 새 키로 한 번만 다시 보낸다(안내 문구 없음). 따라서 서버가 접수했는데 응답을 잃은 사이 작업이 성공까지 끝나면 같은 내용이 한 번 더 실행될 수 있다
- 요청 실패 코드는 초안에 둔다(응답을 받은 화면과 지금 화면이 달라도 표시). 다시 요청하면 지운다
- 응답은 `requestKey`가 같은 초안에만 적는다. 버린 초안이나 그 뒤 새로 쓴 초안에는 적히지 않는다

## 학습 요청 본문 — 지금 서버가 받는 형식

```json
{"kind": "train", "sourceId": "<첫 출처>", "from": "<UTC>", "until": "<UTC>", "featureSetId": "<세트>", "modelKey": "iforest",
 "parameters": {"n_estimators": 256, "max_samples": 512, "random_state": 42},
 "detectionPolicy": {"mode": "ml_only", "deadlineMs": 3000, "combination": "selected_path"}}
```

- `parameters`: 조정 가능한 키만 숫자로 보낸다. 고정 값은 워커가 기본값으로 채운다
- `detectionPolicy`: v1 형식 필수값이라 모델 단독 값을 고정해 보낸다(화면에 없음). v3 요청에는 없는 키이므로 v3 산출이 열리면 뺀다(`features/detection/models/trainDraft.ts` `LEGACY_POLICY`)
- `timeBasis`: `capabilities.snapshot.timeBasis`가 `persisted_at`이면 키 생략, 다른 값이면 그 값을 보낸다
- `excludedIntervals`: 구간이 있을 때만 UTC로 보낸다

## 지금 서버가 지원하지 않는 것

| 항목 | 현재 동작 | 풀리는 조건 |
|---|---|---|
| 제외 구간 | `capabilities.training.excludedIntervals=false`. API는 `excludedIntervals` 키가 있으면(빈 배열 포함) 형식 검사(400 `excluded_interval_out_of_range`·`excluded_interval_overlap`·`invalid_job_request`) 뒤 422 `config_schema_not_supported_yet`. 워커가 키를 무시해 제외되지 않은 채 성공하는 것을 막는다. 화면은 구간 선택은 그대로 두고 준비 검사에서 막는다 | Trainer 선정 정책 v2(#113 하위) 병합 뒤 `trainerContract.ts` `TRAINING_EXCLUSION_SUPPORTED` 값만 true로(접수·capabilities·화면이 함께 풀리고, 구간은 UTC·`reasonCode` 기본 `other`·`note`로 `input_spec`에 실린다) |
| 학습 기간 ESB 시각 기준 | Trainer 스냅샷은 `persisted_at`만 지원(`capabilities.snapshot.timeBasis`). `timeBasis=esb_occurred_at`은 422 `config_schema_not_supported_yet`. 화면 거래량 그래프는 학습이 실제로 쓰는 기준으로 조회·표시한다(`거래량 · 저장 시각 기준`). 값이 `esb_occurred_at`로 바뀌면 그래프 문구·조회·요청이 함께 바뀌고 `timeBasis`가 `input_spec`에 실린다 | Trainer `timeBasis` 지원 뒤 `snapshot.timeBasis` |
| 모델 버전 v3 | 학습 결과는 schemaVersion 1 구성이다. 조회는 v1·v2 투영과 v3 읽기를 모두 구현했다 | Trainer #113 하위 3 |
| 버전 이름 | 학습 요청에 이름 키가 없어 입력칸을 두지 않는다. 목록은 ID 앞 8자리 + `displayName`(있을 때) | 요청 계약 |
| 입력 변수 개별 선택 | 세트 등록 API가 없어 등록된 세트 선택만 가능 | 세트 등록 API(`POST /api/jobs/feature-sets`, 규칙·시험 벡터는 Trainer가 확정) |

## 후속

- 제외 구간 건수 기준: 계약은 구간 비교가 항상 ESB 시각(trainer-service.md `excludedIntervals`)이고, 화면의 구간별 건수는 그래프와 같은 `timeBasis`로 조회한다. 지금은 제외 구간 학습이 막혀 있어 차이가 드러나지 않는다. Trainer가 저장 시각 기간 + ESB 시각 제외 구간 조합을 허용하면 건수 조회를 `esb_occurred_at`로 나눈다

## 구현에서 정한 값(계약에 코드가 없는 곳)

| 경우 | 값 |
|---|---|
| `GET /api/configurations` 알 수 없는 쿼리 | 400 `unknown_configuration_filter` |
| 달력에 없는 커서 시각 | 400 `invalid_cursor` |
| 형식 오류 | 400 `invalid_source_id`·`invalid_limit`·`invalid_cursor`·`invalid_created_from`·`invalid_model_key`·`invalid_executable`·`invalid_active` |
| 기본 `limit` | 50 |
| `GET /api/pipeline/reports/volume` 형식 오류 | 400 `unknown_report_filter`·`invalid_sourceId`·`invalid_report_period`·`invalid_report_bucket`·`invalid_report_time_basis`·`invalid_report_timezone`. `bucket`·`timeBasis` 필수, `timezone` 기본 `Asia/Seoul`, 없는 원천 404 `source_not_found`, 관리자 전용. 10초 초과는 503 `pipeline_storage_unavailable`(부분 결과 없음) |
| 일광절약 시간대 | 버킷은 실제 시각 축으로 만든다. 전환일 시간 버킷은 23·25개이고 겹치는 시각은 서로 다른 버킷이다 |
| ESB 기준에서 시각 없는 행 | 저장 시각이 기간 안이면 `timeUnknownCount`로만 센다 |
| `statusCounts.other` | S·F·P·N 외 전부(Z·H·미정 포함) |
| `active`·`activeSince` | 활성 집합의 값만(`activeSince` = 포함된 리비전·시각). 활성 집합 저장 경로(하위 7) 구현 뒤에는 v1·v2 운영의 `ops_system_state.active_config_version_id`를 운영 중으로 보지 않는다(교체·복원 기준과 같게) |
| `latestComparison` | 비교 평가 조회 전(하위 6)이라 항상 null |
| 카탈로그에 없는 모델 키 | `status: "unknown"`, `executable` 전부 false, `executableReason: "method_not_in_catalog"` |
| 모델 없는 구성 | `modelKey` null, `executable` false |
| `method_not_executable` 판단 | 카탈로그 `executable.process`(워커는 어댑터 존재까지 본다. 카탈로그 시험이 둘을 맞춘다) |
| prepared 학습 | 모델은 IF 고정, 그 외 키는 카탈로그 검증 뒤 422 `unsupported_model` |
