# 시간창 검증 선택 및 6개 이상 탐지 전략 비교

`results.ipynb`를 먼저 열면 실행된 표와 그림을 볼 수 있다. 모델 학습·평가는
`evaluate.py`로 분리했고, 노트북을 다시 실행해도 재학습하지 않는다.
GitHub 업로드·Issue·PR 생성은 이 작업에서 수행하지 않는다.

## 실행

프로젝트 루트에서 기존 분석 환경을 사용한다.
numpy·pandas·scikit-learn·joblib·matplotlib·threadpoolctl과
nbformat·nbclient·ipykernel이 필요하며, 표 스타일에는 Jinja2가 필요하다.
실제 계산 환경 버전은 결과의 `protocol.json`에 기록된다.

```powershell
& .analysis-venv/Scripts/python.exe -m ensurepip --upgrade
& .analysis-venv/Scripts/python.exe -m pip install 'jinja2>=3.1.6,<4'
& .analysis-venv/Scripts/python.exe code/model_evaluation/test_evaluation.py
& .analysis-venv/Scripts/python.exe code/model_evaluation/tune_windows.py
& .analysis-venv/Scripts/python.exe code/model_evaluation/evaluate.py --window-minutes 1 --output reports/model_evaluation_v2_baseline_1m
& .analysis-venv/Scripts/python.exe code/model_evaluation/evaluate.py
& .analysis-venv/Scripts/python.exe code/model_evaluation/execute_notebook.py
```

결과: `reports/model_evaluation_v2/summary.json`, `protocol.json`,
시나리오별 `metrics.json`, 관측·정상 예측 배열 `.npz`, 새 실험 모델 `.joblib`.
진행 중 중단된 경우 `--resume`을 사용한다. 기존 결과 덮어쓰기는 금지한다.
코드·입력·설정·모델 해시가 달라졌으면 새 `--output` 위치를 지정해야 한다.
`--max-cases 20`은 개발용 표본 실행이며 전체 평가로 표시하지 않는다.
`--row-only`는 시간창 실험을 끄는 옵션이다.

## 시간창을 고르는 방법

`tune_windows.py`는 1·2·3·5·10·15분을 비교한다. 30초는 기초 정상 이력의
1분 해상도보다 짧아 제외한다. 후보는 **최근 W분을 보되 매 1분마다 판단**한다.
따라서 모든 후보의 정답·평가 시점·평가 분모가 같다. 장애 구간 라벨을 W분으로
늘리거나, 한 번 탐지했다고 전체 구간을 정답으로 바꾸지 않는다.

- 원본 정상 실행 template ID의 고정 해시로 약 25%를 검증용 예약
- 예약 실행을 시간창 학습·calibration·threshold 및 시간 기준 계산에서 모두 제외
- 예약 실행으로 기본 시나리오마다 240개의 새 검증 사례와 짝 정상 대조군 생성
- 원래 31,000개 평가 사례와 ID 중복 없음; 모델 점수로 생성 사례를 거르지 않음
- 기준: 6개 기본 시나리오 F1을 동일 비중으로 평균, 다시 6개 모델을 동일 비중으로 평균
- 동일값이면 정상 오탐률이 낮은 후보, 다시 동일하면 짧은 후보 선택
- Hard와 최종 평가 라벨·점수는 시간창 선택에 사용하지 않음
- P99·IF/K-means 설정·50:50 앙상블은 고정; 이번에는 시간창만 탐색

`reports/window_search_v2/selection.json`에 후보별 표, 선택 근거, 모델별 권장 후보,
원본 예약 수, 누수 검사 및 재현성 해시가 남는다. `training_inputs.joblib`은
독립 정상 참조, `validation_inputs.joblib`은 선택용 새 사례다.
이번 검증의 공통 선택은 **5분**이다. 평균 F1은 1분 10.24% → 5분 16.69%이지만
평균 정상 오탐률은 1.47% → 8.93%로 늘었다. 오탐률 상한을 별도 제한한 최적화는
아니므로 운영 채택 결론과 다르다. IF 통합은 15분, IF Adapter/앙상블은 5분,
K-means 세 전략은 2분을 선호했다. 이 차이는 노트북에 표시한다.

전체 최종 평가(31,000개 사례 + 31,000개 정상 대조군, 동일 2,167,766개 판단)의
F1은 아래와 같다. 왼쪽은 동일한 새 정상 참조로 재학습한 1분 대조 실험이다.

| 전략 | 고정 1분 F1 | 선택 5분 F1 |
| --- | ---: | ---: |
| IF 통합 | 4.78% | 22.01% |
| IF Adapter | 7.08% | 19.29% |
| IF 앙상블 | 6.22% | 22.54% |
| K-means 통합 | 11.41% | 14.95% |
| K-means Adapter | 14.52% | 17.52% |
| K-means 앙상블 | 14.30% | 17.44% |

최고 F1인 IF 앙상블도 Recall 14.79%, 정상 오탐률 3.28%이며, 대조 모델의
정상 오탐률 0.70%보다 높다. Hard의 6개 사건을 모두 탐지한 사례는 모든 모델에서
0%다. 개선은 확인되었으나 운영 성능이 충분하다는 결론은 아니다.
`evaluate.py`는 선택된 시간창으로 모델을 **다시 학습**한 뒤 31,000개 사례와
31,000개 정상 대조군을 평가한다. `--window-minutes 1` 실행은 동일한 새 참조와
동일 집계 방식의 1분 대조 실험이다. 이전 v1과의 차이를 시간창 효과로 오인하지
않도록 노트북의 변경 전·후 비교에는 이 대조 실험을 사용한다.

최근 W분 요청 수와 완료 소요시간 P50/P95를 집계하며, 분위수는 실제 Process 값에서
다시 계산한다. 과거 요청량 기준도 과거 정상 이력의 rolling W분 요청 수에서 계산한다.
기존처럼 1분 P95를 더하거나 평균내서 W분 P95라고 부르지 않는다.
최근 5분 요청량 피처는 별도로 유지하고 겹친 W분 요청량을 다시 누적하지 않는다.
사례 시작 직후에는 W분이 다 차지 않으므로 확보된 분량만 사용하며 과거 기준도
같은 길이로 맞춘다. 중단 이후 로그가 없어도 정해진 분 단위의 0건 입력은 유지한다.

이 선택은 **후보·검증 생성기·고정 P99 설정 안에서의 최선**이지 모든 업무에서의
보편적 최적값이 아니다. 검증 예약은 해시 기반이며 실제 미래 시계열 검증은 아니다.
최종 평가셋은 v1에서 이미 평가한 적이 있어 새롭게 블라인드인 외부 테스트도 아니다.
이전 1분 결과와 실행 노트북은 `reports/model_evaluation_v1/`에 보존한다.

## 6개 비교 대상

1. Isolation Forest 통합
2. Isolation Forest Adapter별 라우팅
3. Isolation Forest 통합·Adapter 앙상블
4. MiniBatch K-means 통합
5. MiniBatch K-means Adapter별 라우팅
6. MiniBatch K-means 통합·Adapter 앙상블

Adapter별 전략의 미지원·신규 Adapter는 통합 모델로 fallback한다.
순수 Adapter 지원 데이터만의 성능도 별도 표에 제시해 fallback 효과와 구분한다.
알고리즘끼리 지원 집단이 달라지는 불공정 비교를 방지하기 위해 공통 지원 집단을 사용한다.

## 평가 단위

- Process 행: 개별 지연·집단 지연·컬럼 관계·Hard의 point 이상 평가.
  `row_is_anomaly`를 정답으로 사용한다. collective-only 행은 정상으로 잘못 처리하지 않고 제외한다.
- 선택된 최근 W분 시간창, 1분마다 판단: 6개 시나리오와 Hard 전체 평가. 폭주·입력 중단·누적 악화에
  필요한 문맥을 표현하며, 0건 시간창도 존재한다. 행 성능과 합산하지 않는다.
- 사건: 이상 구간에서 처음 탐지된 시간과 사건 탐지율. 한 번 탐지했다고
  해당 구간의 모든 정답을 맞힌 것으로 바꾸는 point adjustment를 하지 않는다.
- Hard: 유형별 사건 탐지율과 한 사례의 6개 사건을 모두 탐지한 비율을 분리한다.

Accuracy=(TP+TN)/전체, Precision=TP/(TP+FP), Recall=TP/(TP+FN),
F1=2TP/(2TP+FP+FN). 이상 예측이 없을 때 Precision=0으로 정의한다.
정답 이상이 없는 집단의 Recall/F1은 N/A다. 다수 클래스 기준의 높은 Accuracy를
오해하지 않도록 항상 정상 예측 기준선과 정상 오탐률을 함께 제시한다.

## 학습·임계값·특징

원본 3개 기간에서 미리 확보한 13,314개 완전한 정상 실행 템플릿
(`reports/evaluation_v2_work/inputs.json`)의 18,837개 Process를 사용한다.
행 모델은 원본 실행 단위의 시간 분할과 검증 구간 내 Transaction 해시 분할을 유지한다.
시간창 모델은 이 참조에서 별도 검증용 예약 실행을 추가로 제외한다.
학습·점수 정규화용 calibration·임계값용 threshold는 겹치지 않는다.
원본 후반 test 분할은 정상 참조 학습에 사용하지 않는다.

두 알고리즘은 동일 참조 집단·동일 피처·동일 RobustScaler 방식으로 새로 학습한다.
기존 `process_if_v1.joblib` 모델의 성능을 그대로 재측정한 것이 아니다.
기존 모델·하드룰 정책·원본 CSV는 변경하지 않는다.

- IF: 128 trees, max_samples=512, max_features=1, bootstrap=False,
  contamination=auto, random_state=42. `predict()`의 기본 -1 판정은 사용하지 않음.
- K-means: K=6, n_init=5, batch_size=2048, max_iter=100, seed=42.
  학습 벡터가 6종 미만이면 실제 K를 줄여 기록. 클러스터 번호 자체는 정상·이상 라벨이 아님.
- 점수: IF는 `-score_samples`; K-means는 표준화 공간의 최근접 중심 거리.
- 정규화: 별도 calibration 집단의 중앙값과 P95-중앙값으로 점수 규모 조정.
- 판정: 별도 정상 threshold 집단의 점수 P99 초과, 엄격한 `>`.
- 앙상블: 정규화된 통합 점수 50% + Adapter 점수 50%,
  해당 조합의 정상 threshold 집단에서 별도 P99 판정선 설정.
  **다른 알고리즘인 IF와 K-means를 섞은 앙상블이 아님.**
- Adapter 지원: 학습 500행·calibration 100행·threshold 100행,
  행 모델 학습 3일 이상. 시간창 모델은 각 원본 분할별 독립 실행 20개 이상도 요구.
  동일 원본을 반복 재생한 수량만으로 지원 충분하다고 판단하지 않음.

행 피처는 기존 pipeline의 처리시간·처리건수·재시도 log1p, 결측 지표, 시간대·요일 sin/cos다.
STATUS·ID·정답·정상 대조군·주입 강도는 모델 피처가 아니다.
시간창 피처는 최근 W분 실행 수, 이전 정상 시간대 대비 편차, 현재까지의 5분 실행 수,
완료 로그 시간 P50/P95, 학습 원본의 시간 기준 대비 편차, 결측·시간대·요일이다.
5분 집계는 사례별로 초기화하고 미래 시간창을 사용하지 않는다.

## 시간창 정상 참조와 대조군

시간창 모델은 최종 평가셋의 정상 대조군을 학습에 쓰지 않는다.
각 원본 분할의 서로 다른 실행 템플릿에서 별도 seed의 정상 에피소드를 생성한다.
기본 90분, 각 Adapter·각 분할당 8개 에피소드다. 같은 과거 합성 정상 이력에서
요청 수를 추출하며, 장애 주입 함수는 호출하지 않는다.
가상 날짜는 참조/평가 에피소드끼리 겹칠 수 있고 실제 미래 시계열 holdout은 아니다.

평가용 정상 시간창은 `normal_processes.csv`의 실제 시각·소요시간에서 다시 계산한다.
입력 중단으로 관측 입력에 사라진 실행은 events에 기록된 ID로 대조군에 복구한다.
정답 파일의 정상 요청 수는 계산 검증에만 쓰고 모델 입력으로 복사하지 않는다.

## 해석의 한계

- 이 결과는 반합성 시나리오 스트레스 테스트다. 실제 운영 장애 정확도나
  독립 외부 데이터 성능의 증거가 아니다. 원본 정상 패턴은 생성·학습에 재사용된다.
- TARGET 시간창은 사전에 감시 대상 업무 집단을 정했다는 가정이다.
  실제 서비스에서는 모든 업무 집단을 감시해야 하며 장애 정답으로 대상을 선택하면 안 된다.
- 시간창은 오프라인 event-time 기준이다. 완료 로그만 뒤늦게 전달되는 시스템에서는
  실제 요청 도착 이벤트 또는 watermark·지연 도착 정책이 필요하다.
- 하드룰 P99는 원시 값의 고정 정책이고, 여기의 P99는 모델 **점수**의 정상 참조 분위수다.
- 서로 다른 평가 단위·분모·양성 비율을 섞거나 test F1을 보고 임계값을 다시 맞추지 않는다.
- `.joblib`은 신뢰하는 자체 생성 파일만 로드한다. 자동 backend 연결은 포함하지 않는다.

## 공식 참고 자료

- [IsolationForest](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)
- [MiniBatchKMeans](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.MiniBatchKMeans.html)
- [분류 성능 지표](https://scikit-learn.org/stable/modules/model_evaluation.html)
