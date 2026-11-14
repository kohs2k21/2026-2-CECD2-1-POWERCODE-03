Created: 2026-11-13T18:30:00+09:00
Updated: 2026-11-14T21:00:00+09:00
Author: Claude Code(WebUI builder)
Status: current

# 평가 결과 화면·비교 평가 조회 API 구현 계약

- 범위: `/detection/evaluations`(비교 평가 목록·새 비교 평가 3단계·평가 세트 생성), `/detection/evaluations/:id`(평가 상세), 모델 교체 2·3단계와 운영 설정 교체 추천 카드가 쓰는 비교 결과. 정본은 [config-comparison-evaluation.md](config-comparison-evaluation.md) "v1 구현 범위"(Trainer 소유)이고, 이 문서는 API·화면이 그 위에서 정한 값만 적는다
- 연결: API `backend/src/services/comparisonEvaluation.ts`·`routes/comparisonRoutes.ts`, `POST /api/jobs kind=compare`(`routes/jobRoutes.ts`가 넘김). 화면 `frontend/src/features/detection/evaluations/`(데이터 접근은 `evaluationsApi.ts` 한곳, 예시 데이터 없음)
- 판단 없음: API는 접수 검증·접수 시점 값 고정·조회만 한다. 세트 고정·평가 실행·결과 기록은 Trainer. 화면은 서버 값을 옮기기만 하고 합산·순위를 만들지 않는다
- 권한: 아래 API는 모두 관리자 전용(403 `admin_required`). 저장소 실패는 503 `evaluation_set_storage_unavailable`·`comparison_storage_unavailable`(`POST /api/jobs`는 기존 `job_storage_unavailable`)

## API

| 요청 | 응답·동작 |
|---|---|
| `POST /api/evaluation-sets` `{sourceId, snapshotId, purpose: "validation"\|"test", displayName, synthetic: null}` | 201 `{id, jobId, status: "queued"}`. 한 트랜잭션: `run_jobs(kind=evaluation_set, v03 training, input_snapshot_id)` + `data_evaluation_sets(queued)` + 큐 메시지. 작업 입력은 Trainer 키 `{kind, evaluationSetId, sourceId, snapshotId, purpose}`만(이름은 세트 행). `Idempotency-Key` 같은 본문 200, 다른 본문 409 `idempotency_payload_conflict` |
| `GET /api/evaluation-sets?sourceId=&limit=&cursor=` | `{items[], pagination}` 최신순. 항목 `{id, sourceId, snapshotId, purpose, displayName, status, synthetic, generation: {rowCount, originalRowCount, syntheticRowCount, truthCount, contentHash, artifactRef: null} \| null, createdAt, readyAt}` — `generation`은 `ready`일 때만 |
| `GET /api/evaluation-sets/:id` | 위 항목 + `jobId`, `errorCode`, `syntheticProfileBinding`(+ 저장돼 있으면 `generation.trialStatuses`·`unsupportedScenarios`) |
| `GET /api/evaluation-sets/snapshots?sourceId=` | 세트 기준 후보 = 모델 버전의 학습 스냅샷(모델 산출물 `snapshot_id`) 중 `ready`·`process-v1`. `{items: [{id, sourceId, createdAt, from, until, configVersionIds[]}]}` 최신 200개. 스냅샷 목록 API가 계약에 없어 WebUI가 정한 형식 |
| `POST /api/jobs` `{kind: "compare", sourceId, evaluationSetId, configVersionIds[]}` | 201 기존 작업 응답 `{id, status, kind, inputSpec}`. 검증·`input_spec`은 정본 "비교 접수" 그대로. 정책 리비전 0이면 `detectionPolicy: null`. `request_hash`는 요청 본문(정렬한 ID)만 — 같은 키 재요청은 그 사이 운영 상태가 바뀌어도 같은 작업 200. `input_snapshot_id`·`input_config_version_id`는 NULL |
| `GET /api/comparisons?sourceId=&evaluationSetId=&modelKey=&scope=current\|all&limit=&cursor=` | 비교 **작업** 최신순(완료 전 포함). 항목 `{comparisonId(완료 전 null), jobId, evaluationSetId, baselines, activeModelSetRevision, status, errorCode, itemCount, succeededCount, failedCount, createdAt, completedAt, recommendations, recommendationsReason}`. `scope=current` = 접수 때 활성 집합 리비전이 지금과 같고 실패가 아님(화면 `현재 운영 모델 기준` 탭) |
| `GET /api/comparisons/:id` | 비교 ID 또는 비교 작업 ID. 완료 뒤 = 저장된 `summary`(작업 `resultSummary`와 같음) 전체 + `comparisonId, jobId, status, errorCode, completedAt`. 완료 전 = 접수 고정 값(`evaluationSet.id`, `baselines`, 리비전, `ruleSet`, `detectionPolicy`) + 접수 항목 `{configVersionId, modelKey, role, status: "pending"}` |

- 수(`itemCount`·`succeededCount`·`failedCount`): summary 값이 있으면 그 값(접수 뒤 없어진 구성 `config_not_found`는 항목 표에 없고 `summary.items`에만 있다), 없으면 항목 표에서 센다
- 상태: 비교 묶음이 있으면 그 `status`(succeeded·failed), 없으면 작업 상태(queued·running·failed)
- 교체 추천: `summary.replacementRecommendations`가 배열이면 항목마다 파생 `recommendationState`(정본 stale 규칙)를 붙인다. null이면 `replacementRecommendationsReason` 그대로(v1 `not_implemented_v1`), 완료 전은 둘 다 null. 목록은 같은 값을 `recommendations`·`recommendationsReason`으로

### 구현에서 정한 코드(정본에 코드가 없는 곳)

| 경우 | 값 |
|---|---|
| 세트 요청 형식(키 집합·UUID·purpose·이름 1~120자 공백 제외) | 400 `invalid_evaluation_set_request` |
| 세트 기준 스냅샷 없음·다른 출처 / 준비 안 됨 / prepared / process-v1 아님 | 404 `snapshot_not_found` / 409 `snapshot_not_ready` / 400 `prepared_not_comparable` / 409 `evaluation_set_snapshot_unavailable`(Trainer 작업 실패 코드와 같음) |
| 비교 요청 형식(키 집합·UUID·중복) | 400 `invalid_comparison_request` |
| 세트 없음 / 다른 출처 / 준비 안 됨 | 404 `evaluation_set_not_found` / 400 `evaluation_set_source_mismatch` / 409 `evaluation_set_not_ready` |
| 버전 없음 / 다른 출처 | 404 `config_not_found` / 400 `config_source_mismatch` |
| 같은 종류 운영 버전이 없는 구성 / 다른 출처 | 409 `comparison_baseline_not_found` / 409 `comparison_baseline_source_mismatch`(Trainer가 작업 실패로 끝낼 입력을 접수 때 막음) |
| 출처 없음 | 404 `source_not_found` |
| 조회 형식 | 400 `unknown_evaluation_set_filter`·`unknown_comparison_filter`·`invalid_source_id`·`invalid_evaluation_set_id`·`invalid_comparison_id`·`invalid_model_key`·`invalid_scope`·`invalid_limit`·`invalid_cursor`. 기본 `limit` 50, 최대 100 |
| 없는 세트·비교 | 404 `evaluation_set_not_found`·`comparison_not_found`(평가 세트 작업 ID는 비교가 아니므로 404) |

## 화면

- 목록: 탭 `현재 운영 모델 기준`(`scope=current`)·`전체 평가`, 최신순 20개씩 `더 보기`. 행 링크는 `comparisonId ?? jobId`. 교체 추천 칸은 추천 배열 표시, 없으면 `미연결 · <사유 코드>`(완료 전 `—`)
- 새 비교 평가 1단계(평가 세트): `ready` 세트만 고를 수 있다. `새 평가 세트 생성` 양식: 이름 · 생성 방식(`원본 분할만` 기본, 합성 프로필은 선택은 되지만 `확인 필요 · 평가 세트 생성: synthetic_set_unsupported`로 생성 막음) · 기준 저장 데이터셋(`<버전들> 학습 데이터 (시작 ~ 끝)`). 기본 스냅샷 = 고른 후보의 학습 스냅샷 → 현재 운영 버전의 학습 스냅샷 → 첫 항목. 접수되면 양식을 닫고 초안이 새 세트를 고른다. 대기 세트가 있으면 목록을 5초마다 다시 읽고, `ready`가 되면 바로 다음 단계로 갈 수 있다. 실패는 `평가 세트 생성 실패 · <코드>`
- 2단계(비교 모델): 모델 버전 목록(`GET /api/configurations` 최신 100개, 모델 있는 버전만) + 카탈로그 이름. 같은 종류 운영 버전은 서버가 넣는 값이라 표시만 한다
- 3단계(요청): 접수되면 초안을 저장 상태로 두고 평가 상세(작업 ID 주소)로 간 뒤 초안을 지운다. 상세는 대기·실행 중 5초마다 다시 읽는다. 실패는 `비교 평가 요청 실패 · <코드>`, 초안 유지
- 상세: 모델 단독 혼동행렬·지표(`categoryResults.ml`) + 항목별 `평가 정책`(`구성 정책 · <mode>`·`운영 정책(접수 때 고정)`, 룰이 있으면 `· 룰 n개`). 정본 v1이 내지 않는 룰+모델 종합·실행 비용이 하나도 없으면 그 표를 그리지 않는다. 룰 조건은 `운영 룰 버전 n 고정 · 항목별 평가 정책`(v1은 룰 세트 버전을 기록만 하고 v1·v2 항목은 구성 룰로 평가). 교체 추천이 null이면 `모델 교체 추천 미연결 · <사유>`. 판정 가능 기준(`decidable`)은 서버가 줄 때만 행·문구를 그린다(v1에는 없음). 정밀도 null은 사유가 `verified_normal_labels_unavailable`일 때만 `정상 확정 라벨 없음`, 그 밖은 `미측정 · <사유>`. 비교 실패(전 항목 실패)도 항목마다 `<버전> 실패 · <코드>`
- 상세 11/14 필드(정본 "평가 제외 행·무라벨 지표·버전 간 판정 일치", 표시 이름은 정본 확정 표 그대로): 항목 카드에 `이상 판정 건수`(n건)·`이상 판정 비율`·`이상 점수 중앙값 / 95분위 / 99분위`·`행당 처리 시간 중앙값`·`행당 처리 시간 P95`(`executionCost`), 이어서 `판정 실패 행`(= `ml.unavailableCount`, 기존 "제외 행" 이름 변경. `지표 비교 · 모델 단독` 표의 같은 값 행도 `판정 실패 행` · `n행`으로 카드와 같은 이름·단위, 사용자 11/14)과 `평가 제외 행`(`n행 · <사유 이름> n행`)을 두 줄로, `이 모델로 교체` 버튼 바로 위에 둔다. ML 미측정 카드도 이 새 줄은 보인다. null은 `미측정 · <사유 코드>`. 작업 단위 `버전 간 판정 일치`는 쌍마다 표 하나(칸 `둘 다 이상`·`{A}만 이상`·`{B}만 이상`·`둘 다 정상`·`판정 일치율`, {A}/{B} = 8자 버전 ID) + 안내문 한 줄. 키가 없는 예전 결과와 `pairwiseAgreement: []`는 그 줄·표를 그리지 않는다
- 운영 설정 교체 추천 카드: 최근 완료 비교에 추천 배열이 없고 사유가 있으면 `모델 교체 추천 미연결 · <사유>`
- 모델 교체 2단계: 기존 규칙 그대로(현재 운영·선택 버전이 같은 비교에서 모두 성공·ML 측정). 지표 표에 `평가 정책` 행, `판정 실패 행(완료 기준)`, 값이 있을 때 `평가 제외 행` 행. 3단계 요약에도 값이 있으면 교체할 버전의 `평가 제외 행`. 같은 세트 비교가 없을 때 `+ 새 비교 평가`는 교체할 버전을 넘겨 비교 초안의 후보로 넣고 세트 양식을 연다
- 3단계 근거: 비교가 만든 `purpose=validation` 평가 행은 기존 적용 gate를 그대로 만족한다(정본 v1). 화면은 `applicationEligibility.evaluationId`를 그대로 쓴다

## 이탈 확인 판정

- 앱 안 이동 확인(`NavigationGuard`)은 이동 시점의 초안 저장소 값으로 판단한다(같은 처리에서 초안을 저장 상태로 바꾼 뒤 바로 떠나는 비교 평가 접수 때문)
