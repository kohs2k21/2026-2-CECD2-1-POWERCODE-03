# 선택 구성: 이전 Feature/Rule + 42개 ML 피처 + IF/K-means/LOF

2026-11-06 사용자 선택에 따라 이전 검증본으로 돌아간 구성이다. 최신 명세 패키지나 다른 팀원의 ML 가중치를 덮어쓰지 않는다.

| 부분 | 사용하는 결과물 |
|---|---|
| Feature LLM 설정 | 2026-10-15 feature_discovery_sft의 epoch_2 어댑터 |
| Rule LLM 설정 | 2026-10-16 rule_connection_long_context_refinement의 epoch_2 어댑터 |
| 피처 정의 | 원래 AI 정의58개 + 재사용 코드지표7개 |
| 동결 규칙 | HR10·관측SR58·수치SR42, 총110개 역할 기록, 고유 조건100개 |
| ML 입력 | T25/P13/M4 정의, 총42개. 결측·모집단 표시를 더한 각 모델 벡터는 T41/P22/M4차원 |
| IF/K-means | 2026-10-18에 위 입력으로 학습·저장한 기존 T/P/M 모델6개 |
| LOF | 같은 피처·전처리 계약으로 이번에 추가 학습한 T/P/M 모델3개 |

두 LLM의 가중치·기존 피처 정의·규칙·IF/K-means·임계값은 변경하지 않았다. 새로 학습한 것은 LOF뿐이다. `selected_profile.json`에 실제 LLM 경로와 가중치 SHA를 기록한다. 이 재생 실행은 기존 정의와 규칙을 사용하므로 LLM을 다시 호출하지 않는다. 선택한 LLM 설정이 새 입력에서 동일 결과를 생성한다는 새 평가로 해석하지 않는다.

## 입력과 적용 범위

이 명령은 같은 원본 TEST 캡처의 SHA가 고정된 기존 피처 저장본을 재사용하는 오프라인 재생이다. 원천 CSV SHA, 가족 분할, 행 키, 기존195개 독립 계산 검사, 피처·전처리 계약을 확인한다. 임의의 신규 CSV나 실시간 로그를 받는 수집기·서비스는 아니다.

다른 로그에 적용하려면 같은 동결 식을 원천에서 계산하고 42개 피처·행 키·자료형·단위·모집단 계약을 맞춰야 한다. 오래된 캐시를 새 로그의 피처로 재사용하지 않는다. 이번 실행에서 원시 피처를 다시 계산했다고 주장하지 않는다.

결측값과 모집단 밖 상태는 서로 다르다. 기존 모델 계약에 따라 입력마다 결측 표시와 모집단 표시를 붙이고, TRAIN에서 학습한 중앙값으로 인코딩한다. 이는 결측을 정상0으로 판정하는 동작이 아니다. 모든 선택 원천 피처가 유효하지 않은 행은 ML 평가 불가다.

## LOF 학습

이미 저장소에 있는 `research/anomaly_detection/code/model_research_v5/models.py`의 Encoder·RawModel·Settings와 ScoreNormalizer를 재사용한다. RawModel의 LOF는 `novelty=True`다. PR98 방식처럼 TRAIN 인코딩 벡터를 소수6자리에서 중복 제거하고 최대8192개만 고정 시드42로 선택해 학습량을 제한한다. 복제 벡터나 합성 정상 벡터를 만들지 않는다.

- Encoder/LOF fit: TRAIN, 2026-08-10 이상~08-20 미만
- 점수 정규화: 2026-08-20 validation 구간
- p99 임계값: 2026-08-21 validation 구간
- TEST: 2026-08-22 이상~09-09 미만, fit·임계값 보정 제외
- 레이블 사용0, 정상 인증되지 않은 운영 참조 로그, 완료 후 T/P/M 사후 분석

## 명령

프로젝트 Python3.10 `.venv`에서 저장소 루트를 작업 폴더로 사용한다. 패키지 설치는 포함하지 않는다.

```bash
PY='/home/msko20702/#종합설계/data/.venv/bin/python'
OLD='/your/preserved/shared_feature_ml_rule_comparison/20261018T142523+0900'
MANIFEST='/your/preserved/rule_connection_long_context_refinement/20261016T124302+0900/model_manifest.json'

# 사용자가 새 LOF 학습을 명시적으로 승인한 경우에만 사용
"$PY" -B -m research.integration.feature_rule_ml restore-previous42 \
  --release "$OLD" --llm-manifest "$MANIFEST" \
  --train-lof --trust-local-model --output /your/new-training-and-evaluation

# 이후에는 저장 모델을 동결해 재생. 이 경로는 fit하지 않음
"$PY" -B -m research.integration.feature_rule_ml restore-previous42 \
  --release "$OLD" --llm-manifest "$MANIFEST" \
  --lof-models /your/new-training-and-evaluation/models \
  --trust-local-model --output /your/new-frozen-replay
```

출력 폴더는 새 경로여야 한다. 기존 결과를 덮어쓰지 않는다. `--train-lof`와 `--lof-models`는 동시에 사용할 수 없고, 어느 것도 지정하지 않으면 실행하지 않는다. IF·K-means의 재학습이나 누락 모델 자동 학습은 없다.

## 판정과 결과

`T/P/M_decisions.csv.gz`는 원천 행 키·거래ID·방법별 상태·점수·발동 규칙 ID를 보존한다. `transaction_decisions.csv.gz`는 거래 단위로 집계한다. 경고가 하나라도 있으면 경고, 적용 가능한 검사들이 전부 비경고면 비경고, 경고가 없고 일부 계산 불가이면 평가 불가다. 기존 비교의 “하나라도 계산 가능하면 assessed”보다 보수적인 집계라 평가 가능 수는 다를 수 있다. 조건·발동 행은 그대로 보존한다.

관측SR을 포함한 `OR_all`과 HR·수치SR·ML만 결합한 `OR_numeric`을 함께 남긴다. 기존 관측SR의 합집합은 전체 거래에 발동하므로 이를 유용한 장애 선별 성능으로 해석하지 않는다. 수치SR42개 역시 모두 서로 다른 유용한 업무 이상 규칙이라고 인증한 것이 아니다.

이전 IF/K-means의 모든 시험 행 점수와 판정을 대조한다. LOF는 저장·재로딩 후 같은 점수/판정을 확인한다. 실제 연결 시험에는 규칙별 발동 원천행 지문을 기존 정확한 Decimal 실행 기록과 대조한 추가 감사가 있다. 확정 장애 레이블·운영 precision/recall/F1·실시간 탐지 지연을 주장하지 않는다.

이 경로는 research의 로컬 연구 프로필이다. `server/`의 모델 선택, API, DB, 알림이나 운영 배포를 자동으로 변경하지 않는다.
