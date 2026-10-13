# 포함된 학습 모델

모델의 경로·정확한 바이트 수·SHA-256은 `model_inventory.json`에 기록한다.
모델 파일은 수치가 들어 있는 실제 학습 산출물이며 다운로드 링크나 LFS 포인터가 아니다.

| 파일 | 크기 | 역할 |
| --- | ---: | --- |
| `code/process_anomaly_pipeline/artifacts/process_if_v1.joblib` | 약 4.39 MiB | 기존 하드룰 + Process IF 파이프라인 |
| `reports/model_evaluation_v2/comparison_models.joblib` | 약 8.50 MiB | 새 행 비교 모델 + 선택 5분 시간창 모델 |
| `reports/model_evaluation_v2_baseline_1m/comparison_models.joblib` | 약 10.17 MiB | 같은 새 정상 참조의 고정 1분 대조 모델 |

`training_inputs.joblib`·`validation_inputs.joblib`은 모델이 아니라 데이터를 담은 캐시이므로 제외했다.
이전 v1 비교 모델·개발용 smoke 모델·오류 수정 전 백업도 최신 산출물과 혼동하지 않도록 제외했다.

## 기존 Process IF

`ProcessAnomalyPipeline.load(path, trusted=True)`로 로드한다. 입력은 Process 레코드이며
Transaction 문맥은 두 연결 정합성 룰의 선택 입력이다. 문맥이 없으면 미평가 상태를 반환하며
정상으로 단정하지 않는다. IF 지원 Adapter는 DB.READER·DB.WRITER·SERVICE.EXECUTOR다.
미지원 Adapter는 전체 모델을 사용한다. 이 모델은 최근 시간창 비교 실험 모델과 다르다.

## 비교 모델 파일의 내부 구성

`joblib.load(path)` 결과는 `row`, `window`, `baselines` dict다.

- `row`: 같은 정상 표본으로 학습한 IF와 MiniBatch K-means의 Process 행 비교
- `window`: 최근 W분 집계를 매 1분 판단하는 IF/K-means 비교
- `baselines`: 학습 정상 원본에서 구한 Process·Adapter·전체 처리시간 기준

각 `SixModels`는 다음 6개 전략의 margin·판정·공통 Adapter 지원 여부를 반환한다.

1. IF 전체
2. IF Adapter별, 미지원이면 전체 fallback
3. IF 전체+Adapter 정규화 점수 50:50
4. K-means 전체
5. K-means Adapter별, 미지원이면 전체 fallback
6. K-means 전체+Adapter 정규화 점수 50:50

앙상블은 별도 학습된 분류기가 아니라 점수 결합과 별도로 보정한 임계값이다.
IF와 K-means를 서로 섞은 앙상블은 아니다. 행 전문가 3종, 시간창 전문가는 위 3종과
FILE.READER·FILE.WRITER의 총 5종이다. 기본 IF 128 trees/max_samples=512,
K-means K=6, seed=42이며 데이터의 고유 벡터 수가 적으면 실제 K는 줄어든다.

행 입력은 처리시간·처리량·재시도 log1p와 결측·시간대/요일 sin/cos 10개 피처다.
시간창은 요청 수·과거 대비 편차·별도 5분 요청 수·완료 시간 P50/P95·평소 대비 지연·
결측·시간대/요일의 12개다. `prepare`/`window_features` 등의 동일 전처리가 필요하다.
CSV 한 행을 시간창 모델에 직접 넣을 수 없으며 감시 대상의 이력과 0건 구간 집계가 필요하다.

IF 점수는 `-score_samples`, K-means는 표준화 공간의 최근접 중심 거리다.
calibration으로 규모를 조정하고 별도 정상 threshold 집단의 P99를 판정선으로 사용한다.
`margin > 0`이 이상 후보이며 margin은 확률·확정 장애·원인 기여도가 아니다.

## 최종 시간창 성능

같은 새 정상 참조의 1분 대조와 5분 선택 모델을 동일한 정답·판단 시점에서 비교했다.

| 전략 | 1분 F1 | 5분 F1 | 5분 Recall | 5분 정상 오탐률 |
| --- | ---: | ---: | ---: | ---: |
| IF 전체 | 4.78% | 22.01% | 14.22% | 2.99% |
| IF Adapter | 7.08% | 19.29% | 12.17% | 2.79% |
| IF 앙상블 | 6.22% | 22.54% | 14.79% | 3.28% |
| K-means 전체 | 11.41% | 14.95% | 9.48% | 3.46% |
| K-means Adapter | 14.52% | 17.52% | 11.77% | 4.50% |
| K-means 앙상블 | 14.30% | 17.44% | 11.61% | 4.29% |

모두 정상으로 예측해도 Accuracy는 83.39%다. 이 표는 연구 평가이며 배포 승인 기준이 아니다.
Hard의 6종을 모두 탐지한 사례는 0%다. 데이터·시나리오 분포·임계값·오경보 비용이 바뀌면
결과도 달라질 수 있다. 실제 운영에서는 수집 정상 여부, 요청 도착 이벤트, 지연 도착 정책,
업무별 이력, 시간창 상태 초기화, 모델/룰 버전 연결 및 경보 처리 계약을 추가로 정해야 한다.

## 보안과 호환성

- 신뢰할 수 없는 joblib/pickle 파일 로드 금지, 사용 전 해시 확인
- 모델 학습 환경의 scikit-learn 1.9.1 사용, 나머지 기록 버전은 `requirements.txt` 참고
- 외부 저장소에서 모델만 내려받지 말고 같은 모듈 코드와 모델 카드를 함께 사용
- 원본 실행 행·정상 참조 아카이브·평가 정답·행별 예측 배열은 미포함
- 운영 서버 자동 로드·기존 모델 교체·API/worker 연동 미수행
