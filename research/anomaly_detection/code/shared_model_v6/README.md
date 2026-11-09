# 전체 Recall 60% 이상 + 다른 전체 지표 유지

현재 결과: `reports/shared_model_v6/release/summary.json`.
설명과 실행 결과는 `result.ipynb`를 읽는다. v5 모델·결과는 그대로 보존했다.

| 모델 | Precision | Recall | F1 | 정상 오탐 |
|---|---:|---:|---:|---:|
| IF | 87.57% | 69.58% | 77.55% | 2.05% |
| K-means | 88.83% | 66.38% | 75.98% | 1.73% |
| LOF | 84.39% | 68.49% | 75.61% | 2.63% |

Accuracy·Precision·F1은 모델별 **전체 합산 기준**으로 v5 이상이다.
Recall 60%도 **전체 합산 기준이며 모든 task 기준이 아니다**.
IF 개별 지연 F1은 64.30% -> 52.53%로 하락했다. LOF는 5개 task F1이 모두 유지/개선됐지만
task별 Precision까지 모두 유지된 것은 아니다. K-means는 폭주 퇴보를 막기 위해 기존 모델 유지.

## 주요 변경

- IF: 15개 원천 수치를 정상 기준으로 맞춰 5개 신호군으로 묶고, 최대 신호 1개를 400-tree IF에 입력
- LOF: 신호군별 최대·평균 및 연속적인 지연 동시상승 11개 피처, 이웃 800 / 참조점 4096
- IF·LOF: 가장 큰 관측 신호군별 정상 점수 참조 보정; 전문가 모델이나 task 정답에 따른 라우팅 아님
- K-means: 기존 19개 피처·8개 군집 유지; 새 후보의 장점과 퇴보를 모두 기록
- 모델별 역할: IF 집단 지연/중단, K-means 폭주, LOF 개별 지연. 점진적 악화는 IF·LOF가 비슷함

## 재검증 / 보고서

프로젝트 루트 PowerShell:

```powershell
$env:PYTHONPATH='code'
$env:PYTHONIOENCODING='utf-8'
.analysis-venv/Scripts/python.exe -m shared_model_v6.audit
.analysis-venv/Scripts/python.exe -m shared_model_v6.roles
.analysis-venv/Scripts/python.exe -m shared_model_v6.report
```

`search*.py`: 정상 후보 학습/개발 검증. `probe*.py`, `calibration_probe.py`: 재사용 연구 자료의 명시적 예비 비교.
`evaluate.py`: 동결한 후보 전체 평가. `release.py`: 전체 지표 유지/Recall/FPR 기준 채택, 완료된 모델 자동 덮어쓰기 금지.
`normal_cutoffs.py`: 별도 정상 점수에서 경보선 후보 계산. `adoption.json`: 탈락과 채택의 수치 근거.

## 전달용 모델

`reports/shared_model_v6/release/handoff/{if,kmeans,lof}_shared.joblib`와 `manifest.json`.
`code/` 의존 모듈 트리도 함께 필요. 로드된 Monitor의 `predict(native_minute_grid, completed_process_rows)` 사용.
빈 분 생성·이전 prefix·지연 도착 정책·상태 유지 worker·배포는 별도 구현 대상.
모델은 각각 하나이며 Adapter 전문가나 모델 앙상블은 없다. 신뢰하는 joblib만 로드한다.

데이터·정답·난이도·ADBench는 변경하지 않았다. 후보 선택에 이미 재사용한 연구 정답을
활용했으므로 외부 블라인드 성능으로 해석하지 않는다. Git push와 Notion 갱신은 수행하지 않았다.
