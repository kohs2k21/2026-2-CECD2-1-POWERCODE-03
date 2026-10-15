# 모델 연결 계약

Python 연결 지점은 `server/worker/model_adapters.py`이다. feature_v5·rule_v2를 내부 GPU 생성 서비스로 연결하고 검증한 Process 행 AST를 실행한다. 해당 변수셋으로 IF를 새로 학습하며 최신 v12는 별도 1분 시간창 워커로 실행한다. 서버 설정은 `server/worker/DEPLOYMENT.md`에 있다.

| 함수 | 입력 | 반환/검증 |
|---|---|---|
| `recommend_features(rows)` | 허용된 P 필드만 담은 train 행, 최대 10,000개 | `{definitions,reviews,stopReason,provider}`. 변수 이름, 필드, 연산, 깊이, 개수를 안전 DSL로 검증한다. `F.*` 직접 참조·임의 코드 실행은 허용하지 않는다. |
| `recommend_rules(rows, feature_set_id, definitions)` | 버전 고정 변수값과 결측 목록이 있는 train 행 | `{suggestions,reviews,provider}`. suggestions에는 측정·조건·모델의 evidenceSpec을 보존한다. `P.*` 또는 해당 변수셋의 `F.*`만 허용한다. 추천은 초안이며 사용자 채택 후 버전을 생성한다. |
| `train_iforest(rows, feature_names, output_directory)` | 스냅샷의 train 분할과 고정 열 순서 | 임시 디렉터리의 `modelPath`, `fitPath`, `formatVersion`, `runtimeVersion`, `thresholdSpec`. 반환 파일은 허용 경로로 원자 이동하고 해시·크기·참조를 기록한다. |
| `score_iforest(model_reference, features)` | 검증된 model/fit 참조와 같은 열 순서의 수치 변수 | 유한한 점수 하나. 결측·스키마 불일치는 판정 불가로 기록한다. 점수 방향과 임계값은 학습 산출물에 명시한다. |
| `prepare_iforest(model_reference)` | 활성화할 model/fit 경로와 해시 검증 결과 | 로드·호환성 확인에 성공하면 반환한다. 실패하면 활성 포인터를 바꾸지 않는다. 운영 구현은 같은 프로세스에서 로드된 모델을 재사용해야 한다. |

신뢰되지 않은 `.joblib`/pickle 파일은 로드하지 않는다. 모델, fit, 버전, 학습 스냅샷, 변수셋, 열 순서가 모두 일치해야 한다. 데이터 전처리 통계는 train 분할로만 적합시키고 validation/test에는 고정 변환만 적용한다. 원천 BODY·인증정보·원문 response message는 모델 입력에 포함하지 않는다.

현재 파생변수 DSL은 P 계층의 `success_count`, `error_count`, `total_count`, `retry_count`와 덧셈·뺄셈·비율이다. 원천의 T/M 관계와 시각 의미가 검증되면 새 DSL/스키마 버전으로 확장한다. `scikit-learn` Isolation Forest는 CPU 구현이므로 GPU 활용을 전제로 모델 포맷이나 컨테이너를 구성하지 않는다.

추천 작업은 관리자 요청이며 ERD v03의 llm_spec과 run_llm_results를 사용한다. 선언한 요청 기간의 해시와 실행 후 고정 스냅샷·실제 측정의 해시를 구분한다.

feature_v5 AST는 Process의 뺄셈·비율·비교·Boolean·결측·ISO 시각 차이·시간 단위 변환을 지원한다. T/M 관계·그룹 집계는 실행되지 않고 보류된다. Boolean은 IF 입력에서 0/1, 결측은 NULL이다.

v12 margin은 0~1 확률이 아니며 margin>0이 이상 후보다. /api/pipeline/windows와 /api/pipeline/window-monitors에서 판정·수집 상태를 조회한다.
