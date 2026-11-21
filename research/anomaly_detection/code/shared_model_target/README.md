# 단일 모델 + Adapter 파생 피처

Adapter별 전문가 모델 없이 IF / MiniBatch K-means / LOF 각각 하나로 탐지한다.
세 알고리즘을 합치는 앙상블도 없다. 기존 구현·모델·벤치마크는 유지한다.

## 최신 결과 위치

- 실행된 설명·표·일반 막대 그래프: `result.ipynb`
- 실제 전수 지표: `../../reports/shared_model_target_lof/summary.json`
- 결과 요약: `../../reports/shared_model_target_lof/RESULTS.md`
- 재계산·원본 파일 해시·추론 감사: `../../reports/shared_model_target_lof/audit.json`
- 개별 전달 모델: `../../reports/shared_model_target_lof/handoff/`
- 정확한 파라미터·threshold: 위 폴더의 `selection.json`, `handoff/manifest.json`

기존 전체 6개 시나리오 + Hard, 2,167,766분 판정. 정답·CSV 수정 없음.
모든 Process가 S인 합성 평가이며 정상 1,785,918분, 이상 381,848분이다.
정상 오탐률은 FP / (TN + FP). point adjustment는 사용하지 않는다.

| 모델 | Accuracy | Precision | Recall | F1 | 정상 오탐률 |
| --- | ---: | ---: | ---: | ---: | ---: |
| IF | 87.46% | 74.70% | 43.53% | 55.01% | 3.15% |
| K-means | 86.94% | 76.64% | 37.20% | 50.09% | 2.42% |
| LOF | 87.44% | 77.66% | 40.27% | 53.04% | 2.48% |

## 입력과 변경 이유

입력은 Process 로그에서 만든 **연속 1분 grid와 완료 Process 행**이다.
현재·과거 특징 17개 + Adapter 통계 9개 = 26개를 각 모델에 사용한다.
`adapterType` / `processId` 문자열을 임의 숫자로 인코딩하지 않는다.
해당 문자열은 스트림 구분·정상 참고 통계 조회에만 사용한다.

- 소요시간: 현재 1분 **완료** 소요시간, 정상 참고 분포 대비 편차·꼬리
- 실행량: 현재 1분 Process 실행 횟수와 과거 실행 주기 대비 입력 공백
- 변화: 먼저 관측한 기준·최근 과거 잔차 대비 지연, 양수 기울기
- Adapter: 같은 Adapter·`totalCount` 구간 대비 지연·드문 정도·평소 실행 횟수
- `totalCount`: 실행 한 건에서 처리한 레코드 수. 분당 요청 수와 다름

과거 5~15분 지연 피처가 복구 후에도 남아 정상 구간을 경보하는 문제를 줄였다.
현재 지연이 사라지면 과거 지연·추세 피처의 영향도 연속적으로 줄어든다.
현재 완료가 없는 분은 소요시간을 0초 정상 사례로 해석하지 않으며 가용성 플래그를 사용한다.
과거만으로 실행 주기를 계산하고, 새 Adapter는 전체 참고 통계를 사용한다.

Adapter P95 통계는 현재 완료 1분 P95 기준이다. `totalCount` 조건은 최근 5분 평균의
log2 구간이므로 정확히 동일한 처리 건수만 비교하는 것은 아니다. 참고 표본 부족 시
Adapter 단독 → 전체 통계로 확장한다. Process 스트림 실행량을 Adapter 기준으로
비교하는 것이지 Adapter 전체 동시 트래픽을 집계한 피처는 아니다.

## 모델과 파라미터

- 공통: seed 42, hybrid 압축/안정화, Adapter 피처 가중치 .25
- IF: 300 trees, max_samples 256, OAS 공분산 안정화 + 고정 공통 회전
  - EIF의 축 편향 문제를 참고했지만 **EIF 전체 구현은 아님**
- MiniBatch K-means: 16 clusters, batch_size 2048, n_init 10, max_iter 150
  - 군집 중심 거리 / 정상 군집 P95 반경의 최솟값
- LOF: novelty=True, neighbors 500, Euclidean, 중복 제거 후 정상 참조 최대 4096개
- 점수 보정: 정상 참고 점수 통계, 현재 실행량/소요시간 평가 가능 여부로 조건화
  - Adapter 전문가 없음. IF/K-means는 경험적 꼬리 보정, LOF는 중앙값/P95 폭 보정
  - 고정 경보선 초과 시 탐지. 점수는 확률이 아니며 목표 오탐률 보장은 없음

## 학습 및 선택

정상 train 19,793분에서 탐지기·스케일러·Adapter 기준을 학습했다.
정상 calibration 17,925분에서 점수 분포와 threshold 후보를 만들었다.
기존 개발 두 분할 35,782 / 71,618분의 오탐률이 각각 8% 미만인 후보에서 합산 F1을
선택했다. 기존 confirmation도 이미 여러 번 확인하여 **개발 집단으로 재분류**했다.
비지도 모델 학습과 달리 하이퍼파라미터/threshold 선택에는 개발 정답을 사용했다.

모델 동결 후 seed 41200042로 새 120사례/시나리오 + 정상 쌍을 생성하여 별도 확인했다.
이 확인 결과로 재선택하지 않았다. 같은 생성기·원본 pool을 공유하므로 외부 블라인드
평가나 실제 장애 일반화 증거는 아니다. 기존 최종 결과가 구조 개선에 영향을 준 점도 기록했다.
정상 S는 성공 상태이지 성능 정상 보장이 아니다. 학습 자료는 기존 정상 archive 기반
재생이며 모든 수집 기간 원본 행 전체를 직접 학습한 것은 아니다.

## 파일 역할 / 재현

프로젝트 루트에서 기존 `.analysis-venv`와 `code/`의 이전 연구 패키지를 함께 사용한다.
joblib에는 사용자 정의 클래스가 포함되어 **모델 파일만 다른 폴더로 복사하면 안 된다**.
pickle/joblib은 신뢰할 수 있는 파일만 로드한다.

```powershell
$env:PYTHONPATH = 'code'
.analysis-venv/Scripts/python.exe -m unittest shared_model_target.test_target -v
.analysis-venv/Scripts/python.exe code/shared_model_target/evaluate_refined.py
.analysis-venv/Scripts/python.exe code/shared_model_target/confirm.py --output reports/shared_model_target_lof --seed 41200042
.analysis-venv/Scripts/python.exe code/shared_model_target/inference.py --output reports/shared_model_target_lof
.analysis-venv/Scripts/python.exe code/shared_model_target/report.py
.analysis-venv/Scripts/python.exe code/shared_model_target/audit.py
```

- `features.py`: 과거 실행 주기/공백 계산과 첫 실험 피처. 현재 모델은 `CURRENT` allowlist만 사용
- `current_features.py`: 현재 1분 중심 피처 + Adapter 통계, 실제 채택
- `models.py`: 공통 전처리와 단일 sklearn 탐지기
- `calibration.py`: 정상 점수 참고 분포·고정 경보선, 추가 탐지 모델 아님
- `run_current.py`: 현재 피처 구성/개발 파라미터 탐색. 이미 선택된 폴더는 재학습 덮어쓰기 금지
- `refine_lof.py`: IF/K-means를 유지한 LOF 이웃·참고 표본 실험
- `evaluate_refined.py`: 검증된 읽기 전용 피처 캐시 재사용·전수 LOF 평가
- `confirm.py`: 동결 후 새 seed 사례 확인, 정답은 case_id+window_start로 정렬
- `inference.py`: 저장된 Monitor와 전달 모델 export
- `report.py`: 결과 그림/노트북 생성 및 실행
- `audit.py`: 전수 confusion matrix 재계산·해시·저장 모델 추론·인과성 점검
- `run.py`, `run_calibration.py`: 이전 실패 실험도 보존, 최종 모델로 사용하지 않음

`Monitor.predict(native_windows, completed_rows)`는 과거 문맥을 포함한 minute grid를 받는다.
backend에서는 Adapter+Process별 상태와 누락 분의 빈 grid를 유지해야 한다.
상태 저장 워커·지연 도착 처리·운영 배포는 이번 구현에 포함하지 않았다.
평가 정답/벤치마크 CSV는 학습된 모델의 runtime 추론 입력이 아니다.

## ADBench에 대한 판단

[ADBench 공식 저장소](https://github.com/Minqi824/ADBench),
[ADBench: Anomaly Detection Benchmark (NeurIPS 2022)](https://arxiv.org/abs/2206.09426).

표형 데이터의 global/local/cluster/dependency 이상 유형과 오염 강건성 평가를
보완하는 데 유용하다. 다만 기본 GMM/분포 기반 생성기는 시간 순서, 입력 중단,
점진적 악화, SQL 정합성이나 현재 하드 룰 통과를 자동 보장하지 않는다.
현재 로그 시나리오를 대체하기보다 **별도 보조 벤치마크**로 쓰는 편이 적합하다.
같은 원본/재생 시계열을 random split으로 섞지 말고 분할을 사전에 고정해야 한다.
이번 작업에서는 ADBench 설치/실행·기존 벤치마크 변경은 하지 않았다.

[EIF](https://arxiv.org/abs/1811.02141),
[OAS](https://scikit-learn.org/stable/modules/generated/sklearn.covariance.OAS.html),
[LOF](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html).

GitHub/Notion에 자동 업로드하지 않았고 기존 운영 모델을 교체하지 않았다.
