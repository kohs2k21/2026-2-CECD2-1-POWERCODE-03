# 단일 모델 개선 + ADBench 평가

최종 선택은 **reports/shared_model_v2_release**에 모았다.
이전 연구·모델·기존 벤치마크는 그대로 보존했다. GitHub/Notion 업로드나 운영 배포는 하지 않았다.

## 최종 성능

기존 전체 6개 시나리오 + Hard, **2,167,766개 1분 판정**이다.
정상 1,785,918개 / 이상 381,848개. 정상 오탐률은 FP/(TN+FP)이다.
평가 정상 대조군뿐 아니라 이상 사례의 정상 배경 구간도 포함한다. point adjustment 없음.

| 모델 | Accuracy | Precision | Recall | F1 | 정상 오탐률 |
| --- | ---: | ---: | ---: | ---: | ---: |
| IF | 87.46% | 74.70% | 43.53% | 55.01% | 3.15% |
| K-means | 88.17% | 80.72% | 43.13% | 56.22% | 2.20% |
| LOF | 87.85% | 79.01% | 42.25% | 55.06% | 2.40% |

- IF: 기존 F1 55.01% 모델 유지. 새 후보가 전수 F1을 넘지 못했으므로 교체하지 않음
- K-means: F1 50.09% → 56.22%, 정상 오탐률 2.42% → 2.20%
- LOF: F1 53.04% → 55.06%, 정상 오탐률 2.48% → 2.40%

최종 세 모델은 모두 F1 55% 이상이고 정상 오탐률이 직전보다 증가하지 않는다.
모든 시나리오가 동시에 개선된 것은 아니다. 특히 K-means의 개별 지연/시간대 폭주,
LOF의 시간대 폭주 등의 회귀도 노트북에 그대로 표시했다.

## 실제 바뀐 내용

탐지기는 알고리즘별 하나이며, Adapter 전문가 모델이나 알고리즘 간 앙상블은 없다.
모두 Adapter 통계 피처 9개를 사용한다. 별도의 문자열 ID 인코딩은 하지 않는다.

- IF: 현재/과거 17 + Adapter 9 = **26개**. 300 trees, max_samples 256,
  정상 공분산 안정화/고정 공통 회전, 기존 정상 점수 보정 유지. EIF 전체 구현은 아님
- K-means: 위 26 + 기억/트래픽 9 + 관계 6 = **41개**. 8 clusters,
  batch_size 2048, n_init 10, max_iter 200. 정상 군집 P95 반경으로 거리 정규화
  - 정상 피처 공간의 극단적 거리 상위 1%를 **중심 적합에서만** 제외
  - 평가 데이터/정답을 삭제하지 않았고 정상 calibration 전체는 그대로 사용
  - 완전한 Trimmed k-means 구현이 아닌 한 번의 강건한 학습 자료 선택
- LOF: 현재/과거 17 + Adapter 9 + 관계 6 = **32개**.
  novelty=True, 이웃 **400**, 정상 참조 **2,048**, Euclidean,
  중복 제거, Adapter 블록 가중치 **0.55**
- 공통 seed 42. Adapter 블록/시간 문맥의 실제 가중치·경보선·점수 보정은 manifest 참조

관계 피처는 지연·정상 꼬리·증가 추세가 함께 높아지는 정도, 실행량/입력 공백의
동시 변화 등이다. 기억 피처는 과거에 확인한 지연을 최대 3/8분 감쇠시키며 새 완료가
들어오면 갱신한다. 미래 완료를 읽지 않고 새 하드 룰로 확정 이상을 판단하는 것도 아니다.
PCA/화이트닝/점수 평활 등은 비교했지만 **최종 K-means/LOF에 PCA·점수 필터는 채택하지 않았다**.

추가한 관계 피처의 실제 이름과 의미는 다음과 같다. 모두 숫자 피처이며 확정 이상 규칙이 아니다.

| 피처 | 의미 |
| --- | --- |
| coherent_prefix_source | 초기 관측 대비 지연과 정상 참고 분포 대비 지연이 함께 커지는 정도 |
| coherent_tail_agreement | 전체 조건부 소요시간 꼬리와 Adapter·처리건수별 꼬리가 함께 드문 정도 |
| coherent_self_tail | 초기 대비 지연이 있을 때 현재 소요시간 꼬리의 크기 |
| coherent_drift | 초기 대비 지연과 현재까지의 증가 기울기가 함께 커지는 정도 |
| coherent_burst | 현재 실행량 초과와 초기 관측 실행량 대비 증가가 함께 큰 정도 |
| coherent_gap | 초기/최근 실행 주기 두 기준에서 입력 공백이 함께 긴 정도 |

`totalCount`는 실행 한 건의 처리 레코드 수이며 분당 실행 횟수와 다르다.
Adapter 조건부 P95는 현재 완료 1분 기준, 처리건수 구간은 최근 5분 평균 totalCount 기준이다.
참고가 부족하면 Adapter 단독 → 전체 정상 통계를 사용한다.

## 결과와 파일

- `result.ipynb`: 실행된 결과, 일반 막대 그래프, 전수 점수 분포/threshold, 시나리오별 회귀, ADBench
- `../../reports/shared_model_v2_release/RESULTS.md`: 요약
- `../../reports/shared_model_v2_release/summary.json`: 전수 지표
- `../../reports/shared_model_v2_release/audit.json`: 독립 재계산·원본 해시·추론 감사
- `../../reports/shared_model_v2_release/handoff/`: 전달할 학습 모델 3개와 manifest
- `../../data/adbench_1000/`: ADBench 피처/정답 CSV 1,000개와 재현 배열·생성 근거

핵심 구현은 `features.py`(기억/트래픽), `models.py`(관계 피처·단일 탐지기),
`calibration` 관련 코드(정상 점수 기준), `inference.py`(모델 전달),
`adbench_eval.py`(공식 생성기와 평가), `report.py`, `audit.py`이다.
`run.py`/`refine_*.py`/`calibrate_lof.py`/`assemble.py`는 탐색 기록이며 동결 폴더 덮어쓰기를 거부한다.
`score_filter.py`는 미채택 비교 후보이다. 최신 모델을 고를 때 중간 실험 폴더를 사용하지 않는다.

## ADBench는 무엇을 테스트했나

공식 **adbench 0.1.11**의 `generate_realistic_synthetic` 메서드를 수정 없이 호출했다.
ADBench 전체 RunPipeline 실행이나 공개 데이터셋 순위 비교는 아니다.
정상 800개 + 이상 200개, global/local/cluster/dependency별 250개이다.
생성은 정상 train의 관측/준비된 피처 1,600개에만 적합했다.
최종 모델·threshold를 고정하고 동일 1,000개로 전후를 비교했으며 이 정답으로 재튜닝하지 않았다.

**숫자 피처 공간 평가이며 원시 Process 로그가 아니다.** 시간 순서/입력 중단/점진적 악화나
SQL/STATUS 하드 룰 통과를 검증하는 데이터가 아니다. 물리적 피처 관계와 분포 겹침 한계가 있다.
정상/이상 정의와 재현 seed·copulas/pandas 호환 처리는 data/adbench_1000/README.md 및 manifest 참조.

## 재현 명령

프로젝트 루트에서 실행한다. 기본 분석 환경의 pandas/numpy/scikit-learn은 유지했다.
최종 manifest에 정확한 버전이 있다. ADBench 생성만 추가 패키지가 필요하며,
실제 모델 추론에는 ADBench/torch/tensorflow가 필요하지 않다.

```powershell
$env:PYTHONPATH = 'code'
.analysis-venv/Scripts/python.exe code/shared_model_v2/adbench_eval.py --stage evaluate --output reports/shared_model_v2_release
.analysis-venv/Scripts/python.exe code/shared_model_v2/report.py --output reports/shared_model_v2_release
.analysis-venv/Scripts/python.exe code/shared_model_v2/audit.py --output reports/shared_model_v2_release
```

기존 `data/adbench_1000`이 있으면 생성기는 덮어쓰기를 거부한다.
추가 생성 패키지는 adbench 0.1.11 / copulas 0.14.1이다. 기존 환경을 대규모 DL 의존성이나
pandas 다운그레이드로 바꾸지 않도록 공식 생성 메서드만 로드한다.

## 백엔드 추론

```python
import sys
import joblib

sys.path.insert(0, "code")
model = joblib.load("reports/shared_model_v2_release/handoff/kmeans_shared.joblib")
result = model.predict(native_minute_grid, completed_process_rows)
```

joblib은 신뢰할 수 있는 파일만 로드한다. 사용자 정의 클래스가 포함되므로 모델 파일과
`code/`의 의존 모듈, manifest의 라이브러리 버전을 함께 전달해야 한다.
manifest의 모듈 목록은 핵심 모듈 안내이며 독립 배포 패키지의 전체 의존성 목록이 아니다.
별도 serving 패키지로 분리하기 전에는 기존 `code/` 트리를 함께 유지한다.
위 추론은 평가 CSV·정답·training_inputs.joblib을 읽지 않는다.

입력은 Adapter+Process 스트림의 **과거 문맥을 포함한 연속 1분 grid + 완료 Process 행**이다.
빈 분 생성, 정상 역사 집계, Adapter+Process별 상태 보관, 지연 도착 처리는 backend 워커의
별도 책임이다. 원시 Process 한 행만 보내면 모든 시계열 피처가 자동 생성되는 것은 아니다.
ADBench 피처 CSV는 이 raw Process 추론 API에 직접 넣는 파일이 아니라 별도의 `.score` 평가 입력이다.

## 엄밀하게 읽을 한계

- 모델/참고 통계는 정상 후보 19,793분, 점수 기준은 정상 calibration 17,925분에 기반
- 하이퍼파라미터/경보선 선택에는 개발 정답을 사용. 비지도 학습과 무라벨 설정 선택은 다름
- 기존 벤치마크를 보며 반복 개선하고 IF 유지 여부도 판단했으므로 외부 블라인드 평가가 아님
- 최종 모델 동결 뒤 seed 42400042 새 사례를 별도 확인. 같은 원본 pool/원형 생성기를 공유
- 모든 수집 기간 원본 전 행을 직접 학습하거나 실제 운영 장애 전반을 검증한 것은 아님
- S는 성공 상태일 뿐 성능 정상 보장이 아님. 시간대 prior도 합성이며 운영 오탐률 보장 없음
- ADBench의 '정상'도 생성 분포상 정상이지 실제 운영 정상 보장이 아님

참고: [ADBench](https://github.com/Minqi824/ADBench),
[NeurIPS 2022 논문](https://arxiv.org/abs/2206.09426),
[MiniBatch K-means](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.MiniBatchKMeans.html),
[LOF](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html).
