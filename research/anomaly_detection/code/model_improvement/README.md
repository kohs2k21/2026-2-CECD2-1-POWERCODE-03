# Process 이상탐지 모델 개선

시작은 `results.ipynb`. 실행된 그래프, 정확한 수치, 결과 해석과 모델별 판단 근거를 함께 담는다.
Process 입력의 분 단위 집단 감시 모델이며 Transaction 피처는 쓰지 않는다. 기존 하드 룰/운영 v1은 변경하지 않았다.

## 파일

| 파일 | 역할 |
|---|---|
| `features.py` | 정상 학습 데이터만 이용한 조건부 지연 참고값, 인과적인 1/5/15분·EWMA 피처 |
| `models.py` | IF, MiniBatch K-means, 동일 지원 조건의 Adapter 모델, 전체 fallback, 독립 정상 threshold 보정 |
| `data.py` | 정상 참고 로그 재생, 학습 템플릿과 겹치지 않는 새 검증 사례, Process-only 입력 구성 |
| `run.py` | 첫 실험(v3). 실패/한계도 별도 보존 |
| `refine.py` | 개선 실험(v4). 기존/관계/시계열/축소 피처 뷰 탐색·확인·동결 평가 |
| `analyze.py` | 시나리오/Adapter 비교, 경보 겹침, 동일 오탐 예산 진단, 사례 근거, 사례 단위 bootstrap |
| `inference.py` | 신뢰된 모델 hash 검증 후 backend에서 호출할 `ProcessMonitor` 인터페이스 |
| `report.py` | 일반 막대·히스토그램·시계열 그림과 표. 모델 재학습 없음 |
| `build_notebook.py` | 설명/코드 셀을 분리한 결과 노트북 생성·실행·그림 추출 |
| `test_improvement.py` | 미래 정보 누출·에피소드 격리·fallback·threshold·거리 해석 테스트 |

## 실제 적용 내용

- 정상 fit/calibration/threshold 참고 집단과 새 라벨 검증 사례를 분리
- 1분 의사결정 단위와 기존 정답 유지, point adjustment 없음
- 기존 피처를 버리지 않고 피처 뷰를 비교. 준비된 모든 피처가 최종 모델에 들어가는 것은 아님
- 조건부 지연: Adapter+ProcessID+처리건수(log2 구간), 정상 원본 30행 미만이면 더 큰 집단으로 fallback
- 개별 최대 지연, 집단 P95, 짧은/긴 창 차이, 연속 요청 없음, EWMA 추가 후보
- IF: 희귀한 피처 조합의 분리 점수. v4 128 trees, max_samples 256/512/1024 비교
- Clustering: 정상 패턴 중심과 거리. K=6/12, raw distance/정상 군집 P95 반경 보정 비교
- 정상 calibration의 중앙값/P95로 점수 보정. 전체+Adapter 결합 자체도 정상 threshold 집단에서 보정
- 전문 모델 없는 Adapter의 세 전략은 전체 모델과 동일. IF/K-means 전문 모델 지원 집단을 맞춤

v4 실제 선택은 IF 관계 피처(16개), 128 trees/max_samples=1024, 정상 quantile=.975, 전체 가중치=.25.
Clustering은 시계열 피처(18개), K=6/반경 보정, 정상 quantile=.999, 전체 가중치=.75.
설정·점수는 `reports/model_improvement_v4/selection.json`, `summary.json`, `role_analysis.json`에서 확인한다.

## 실험과 해석

v3는 피처를 통째로 교체해 IF 민감도가 낮아졌다. v4는 이를 보존하고 기존 피처를 후보에 남겼다.
v3의 시나리오 macro 오탐 2.5% 제한과 달리 v4는 각 전략 합산 정상 오탐 5%를 탐색 제약으로 사용한다.
v4는 별도 seed의 새 검증 1,440사례+정상 짝 1,440개, 탐색/확인 각각 720사례다.
확인 결과나 기존 최종 평가 점수로 설정을 다시 고르지 않았다. 하지만 적응적 실험 설계·공유 생성기/원본 풀의 영향은 남는다.

확인 집단에서 선택 Clustering Adapter/앙상블 오탐은 약 10%로 탐색 제한 5%를 넘었다.
따라서 저장된 v4는 **연구 후보**이지 운영 승인 모델이 아니다. 기존 `process_if_v1.joblib`를 자동 교체하지 않는다.
이득이 나타나는 시나리오와 손해/오탐을 함께 보고, 별도 실제 로그로 운영점을 재검증해야 한다.

정상 템플릿은 기존 compact archive에서 선택하며 전체 371만 원본 Process를 모두 학습한 것이 아니다.
학습/참고 정상 원본은 train 7,510행, calibration 2,468행, threshold 2,452행이다.
재생 창은 train 10,825, calibration 12,987, threshold 11,572개. 반복 재생을 독립 실측 원본 표본 수로 세지 않는다.
정상 STATUS=S도 실장애가 없는 로그라고 확정하지 못한다. 주입 정답은 합성 시나리오 정답이다.
이전에 평가한 최종 6개×5,000+Hard 1,000 사례는 새 블라인드 외부 평가셋이 아니다.

## 재현

워크스페이스 루트에서 `.analysis-venv/Scripts/python.exe`로 실행한다. `prepare`는 저장 입력을 hash 검증해 재사용,
`tune`은 기존 선택 파일을 덮어쓰지 않으며, `evaluate`는 동일 모델의 완료된 평가만 hash 검증 후 재사용한다.

```powershell
.analysis-venv/Scripts/python.exe code/model_improvement/run.py --stage prepare
.analysis-venv/Scripts/python.exe code/model_improvement/run.py --stage tune
.analysis-venv/Scripts/python.exe code/model_improvement/run.py --stage evaluate
.analysis-venv/Scripts/python.exe code/model_improvement/refine.py --stage prepare
.analysis-venv/Scripts/python.exe code/model_improvement/refine.py --stage tune
.analysis-venv/Scripts/python.exe code/model_improvement/refine.py --stage evaluate
.analysis-venv/Scripts/python.exe code/model_improvement/analyze.py --version 4
.analysis-venv/Scripts/python.exe code/model_improvement/build_notebook.py --execute
.analysis-venv/Scripts/python.exe -m unittest discover -s code/model_improvement -p test_improvement.py -v
```

이미 동결된 실험의 소스/입력 hash가 바뀌면 재사용을 중단한다. 새 튜닝은 별도 실험 버전으로 남긴다.
v4 평가는 v3에서 원본 Process로부터 만든 피처 캐시만 사용한다. v3 최종 점수/결정을 입력으로 사용하지 않는다.
원본 CSV는 재검증하되 수정/삭제하지 않는다. 보고서 NPZ·개발 입력 캐시는 데이터이므로 GitHub 공개 대상에서 제외한다.

## backend 연결

모델: `reports/model_improvement_v4/models_v4.joblib`. 실제 Python 객체이며 외부 불신 joblib는 실행 위험 때문에 로드하지 않는다.
`selection.json`의 model SHA256과 생산 환경에서 신뢰한 SHA256을 비교한다. 버전은 현재 `.analysis-venv`와 고정해야 한다.

```python
from model_improvement.inference import ProcessMonitor
from model_evaluation.windowing import PriorHistory

monitor = ProcessMonitor(model_path, trusted_sha256, prior_history)
decisions = monitor.score(minute_grid, completed_processes)
```

`minute_grid`: stream ID인 `case_id`, `adapterType`, `processId`, `window_start`, `window_end`, `execution_arrivals`.
`completed_processes`: `case_id`, `adapterType`, `processId`, `totalCount`, `duration`(ms), `end_seconds`.
ID는 집단 라우팅용이다. 생성 사례 ID를 정상/이상 피처로 쓰지 않는다.

기존 하드 룰로 입력을 점검한 다음, backend의 집단별 상태 저장소가 이전 관측 이력을 유지해야 한다.
이 구현은 재현 연구용 batch history interface이며 DB/Redis 상태 저장·실시간 유입 중복 제거·업무별 실제 이력 학습을 구현한 서비스는 아니다.
정규화된 동일 시간대의 naive timestamp/epoch 입력을 사용한다. UTC/지역 시간 변환은 호출 측에서 일관되게 수행한다.
현재 EWMA/연속 없음은 호출에 들어온 이력 처음에서 초기화한다. 재시작/요청마다 상태를 자르지 말고 이력과 초기 상태를 운영에서 관리해야 한다.
로그가 안 오는 경우에도 타이머가 빈 창을 생성해야 입력 중단을 탐지할 수 있다. 수집기 고장과 실제 업무 중단을 구분하는 상태 확인도 필요하다.
참고 트래픽 이력은 이번 평가에서 합성 14일 이력이며, 운영에서는 실제 과거 정상 시간대/업무 집단 이력으로 교체·검증한다.

## 근거와 적용 범위

- [Liu, Ting, Zhou (2008), Isolation Forest](https://cs.nju.edu.cn/zhouzh/zhouzh.files/publication/icdm08b.pdf): 분리 경로 기반 희귀성, 작은 subsample 크기 비교
- [He, Xu, Deng (2003), Discovering cluster-based local outliers](https://www.sciencedirect.com/science/article/pii/S0167865503000035): 지역 정상 집단 관점의 참고. 이번 반경 보정은 자체 실험이며 CBLOF 재현이 아님
- [Kim et al., ICLR 2022, Towards a Rigorous Evaluation of Time-series Anomaly Detection](https://arxiv.org/abs/2109.05257): point adjustment 미사용, 분/이벤트 지표 구분
- [NIST EWMA](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc324.htm): 최근 관측에 가중한 추세 피처. 정규분포 3-sigma 경계를 사용하지 않음
- [scikit-learn MiniBatchKMeans](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.MiniBatchKMeans.html): 미니배치 군집 설정

시간창·건수 구간·support·가중치·오탐 제한 숫자는 우리 실험 설계다. 논문에서 이 데이터에 대한 최적값을 가져온 것이 아니다.
