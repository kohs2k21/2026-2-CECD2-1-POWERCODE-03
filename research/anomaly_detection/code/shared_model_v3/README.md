# 고정 오탐률에서 F1·Recall 개선 실험

목표: `reports/shared_model_v2_release` 대비 알고리즘별 F1과 Recall을
각각 **10~20%p** 높이되 동일 정상 평가 분에서 FP를 늘리지 않기.
목표는 보장된 결과가 아니며, 실제 달성 여부는 최종 `summary.json`에 기록한다.

## 실제 채택 결과

**+10~20%p 목표는 미달**했다. 동일 2,167,766분 / 6개 시나리오+Hard 기준이다.
오탐률 증가 없이 두 지표가 모두 오른 후보만 `reports/shared_model_v3_release`에 모았다.

| 모델 | F1 전 → 후 | Recall 전 → 후 | 정상 오탐률 전 → 후 |
| --- | ---: | ---: | ---: |
| IF | 55.01 → 56.79% | 43.53 → 45.25% | 3.15 → 3.01% |
| K-means | 56.22 → 58.93% | 43.13 → 45.77% | 2.20 → 2.05% |
| LOF | 55.06 → 55.06% | 42.25 → 42.25% | 2.40 → 2.40% |

- IF: 기존 표현을 유지하고 500 trees / max_samples 32 / max_features 0.5,
  정상 availability robust 점수 보정으로 변경. F1 +1.79%p / Recall +1.72%p
- K-means: 초기 관측 대비 소요시간·누적/기억·실행량 표현, 32개 입력,
  8개 군집, 정상 Adapter/availability 점수 보정과 정상 P99.5 정책.
  F1 +2.71%p / Recall +2.64%p
- LOF: 새 후보가 고정 오탐률에서 F1·Recall을 함께 개선하지 못해 이전 모델 유지
- 모든 시나리오가 좋아진 것은 아님. 특히 일부 개별 지연/입력 중단 회귀를 노트북에 표시

### 새 seed 확인: 일반화 주의

모델 동결 후 seed 45100042 / 47,726분을 기존 모델과 동일 사례로 비교했다.
재선택에 사용하지 않았으며, 원형 생성기이므로 주 벤치마크 mild-v2와 난도 프로파일이 다르다.

- IF: F1 57.30 → 59.24%, Recall 48.07 → 49.41%, 오탐률 3.78 → 3.34%
- K-means: F1 **60.86 → 60.29%**, Recall **49.08 → 47.37%**, 오탐률 2.34 → 1.87%
- LOF: 동일 모델이므로 변화 없음

**K-means의 일반화 개선은 확인되지 않았다.** `release`는 전수 벤치마크 기준으로
동결한 연구 후보 묶음이며 운영 배포 승인/자동 교체가 아니다. 기존 v2 모델은 보존했다.

실제 전달 파일은 `../../reports/shared_model_v3_release/handoff/`,
상세 결과는 `result.ipynb`, 전수 수치는
`../../reports/shared_model_v3_release/summary.json`에 있다.

기존 모델/노트북/원본 기간 CSV/평가 데이터·정답은 수정하지 않는다.
알고리즘별 탐지기는 하나이고 Adapter 전문가 모델은 없다.
모델 학습은 정상 후보만 사용하지만 설정 선택에는 개발 라벨을 사용한다.
이미 반복 관측한 합성 연구 벤치마크이므로 외부 블라인드 평가가 아니다.

## 파일 역할

- `features.py`, `adaptive.py`, `sensitive.py`: 인과적인 지연·실행량·기억 피처 후보
- `models.py`, `sensitive_detector.py`, `evidence.py`: 피처 표현/스케일/단일 탐지기 후보
- `geometry.py`: IF 표본·피처 부분선택, K-means 거리 반경, LOF 거리/이웃 비교
- `temporal_score.py`: 동일 탐지기의 과거 점수 누적/기억 후보. point adjustment 아님
- `run*.py`, `recalibrate.py`: 기존 개발 분할의 실험. 이미 완료한 폴더는 보존
- `diagnose.py`, `observability.py`: 놓친 이상과 정상 대조군의 피처 비교
- `select.py`: 개발 F1과 Recall 증가분 중 작은 값을 우선하는 공동 선택
- `evaluate.py`: 동일 7개 데이터셋 전수 1분 평가. 모델 동결 후 threshold 변경 없음
- `conservative.py`: 앞선 후보 오탐 초과 후 새 후보 하나를 정상 calibration P99.5로 고정.
  전수 FPR을 맞추는 threshold 역산은 없으며, 반복 연구라는 선택 편향은 존재
- `release.py`: FP 증가 없음 + F1/Recall 모두 상승인 후보만 채택. 아니면 이전 모델 유지
- `audit.py`: 독립 지표 재계산, 모델 저장·재로딩, Process 입력 경로·인과성·새 Adapter 검사
- `fresh.py`: 모델 고정 후 새 seed에서 이전/채택 모델을 동일 사례로 비교. 재선택 금지
- `report.py`, `result.ipynb`: 일반 막대 그래프, 점수 분포·경보선, 탈락 이유와 한계

## 주요 주의점

- `totalCount`는 실행 한 건의 처리 레코드 수, 실행량은 분당 실행 횟수이다.
- 초반 관측 몇 개로 만든 기준은 정상임이 보장된 기준이 아니다.
- 새 정상 완료가 관측되면 지연 기억을 갱신/해제한다. 미래 완료를 보지 않는다.
- 과거 개발 정상 대조군의 일부 시작/완료 행 수가 따로 재집계되지 않아
  **`flow=True` 후보는 최종 선택에서 제외**했다. 기존 release는 이 필드를 피처로 쓰지 않는다.
- 초기 sensitive 프로토타입의 `__main__` 직렬화는 `select.load`에서 import 가능한
  `sensitive_detector.Detector`로 정규화한다. 최종 전달 파일은 `handoff/`만 사용한다.
- 새로운 시간 피처를 계산할 이력이 없는 기존 ADBench 1,000개에는 임의 시간을 붙이지 않았다.
- 실제 backend에서는 Adapter+Process별 상태, 연속 빈 분, 지연 도착 처리가 추가로 필요하다.
- `joblib`은 신뢰할 수 있는 파일만 열고 의존 `code/` 트리와 환경 버전을 함께 전달한다.
- GitHub/Notion/운영 모델은 이 실험으로 자동 변경하지 않는다.

## 실행 예

프로젝트 루트에서 실행한다. 캐시를 생성하는 전수 평가 두 개를 동시에 실행하지 않는다.
후속 평가는 `--readonly-cache`로 기존 검증 캐시만 읽는다.

```powershell
$env:PYTHONPATH = 'code'
.analysis-venv/Scripts/python.exe -m unittest shared_model_v3.test_v3 -v
.analysis-venv/Scripts/python.exe -m shared_model_v3.run
.analysis-venv/Scripts/python.exe -m shared_model_v3.run_evidence
.analysis-venv/Scripts/python.exe -m shared_model_v3.run_sensitive
.analysis-venv/Scripts/python.exe -m shared_model_v3.run_geometry
```

참고 근거:
[NIST CUSUM](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm),
[IF 설정](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html),
[LOF 설정](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html).
CUSUM은 작은 변화 누적의 설계 근거이며, 이번 변형이 해당 자료의 정확한 재현이나
성능 보장을 의미하지는 않는다.
