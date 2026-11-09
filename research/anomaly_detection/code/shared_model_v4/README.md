# F1 60% 이상 + 기존 정상 오탐 수 상한 유지

최신 확인 결과는 `result.ipynb`에서 확인한다. 기존 v3 코드·모델·결과 및 실패 실험은 보존했다. GitHub push, Notion 수정, 운영 자동 배포는 하지 않았다.

## 결과: 기존 Process 로그

동일한 2,167,766개 1분 판정, 정상 1,785,918 / 이상 381,848. Hard 포함, 정답·데이터·난이도 변경 및 point adjustment 없음. FP/(TN+FP)가 정상 오탐률이다.

| 모델 | 이전 F1 | 최종 F1 | Precision | Recall | 이전 오탐률 → 최종 | FP 상한 → 최종 |
|---|---:|---:|---:|---:|---:|---:|
| IF | 56.79% | 64.40% | 81.90% | 53.06% | 3.01% → 2.51% | 53,829 → 44,773 |
| K-means | 58.93% | 71.36% | 88.30% | 59.88% | 2.05% → 1.70% | 36,546 → 30,282 |
| LOF | 55.06% | 68.53% | 88.31% | 56.00% | 2.40% → 1.59% | 42,848 → 28,312 |

전 시나리오 F1이 60% 이상이라는 뜻은 아니다. IF의 개별 지연·컬럼 관계 TP=0, LOF의 폭주 성능 저하 등이 남았다. 분 수가 많은 점진적 악화·입력 중단의 영향이 큰 전체 F1 외에 notebook의 유형별 결과와 macro F1을 함께 봐야 한다.

## ADBench: 반드시 두 프로토콜 구분

ADBench 공식 생성기 기반 기존 1,000개 수치 표: 정상 800 / 이상 200, global/local/cluster/dependency 각 250개. ADBench 전체 공개 실제 데이터셋 종합 성능이 아니다.

| ADBench 전용 재학습 모델 | F1 | Precision | Recall | 오탐률 | 이전 FP 상한 → 최종 |
|---|---:|---:|---:|---:|---:|
| IF | 69.49% | 87.79% | 57.50% | 2.00% | 22 → 16 |
| K-means | 66.67% | 94.50% | 51.50% | 0.75% | 6 → 6 |
| LOF | 64.31% | 90.09% | 50.00% | 1.375% | 11 → 11 |

위 표는 **ADBench 전용으로 새로 학습한 모델**이다. 서비스 모델 그대로의 무학습 전이는 IF F1 8.22%, K-means 33.33%, LOF 0%로 목표 미달이며 K-means는 정상 800개 전부 경보했다. 실제 시간축이 없는 표에 서비스 시계열 피처가 있다고 가정하면 안 된다. 두 결과와 모델 파일을 바꿔 부르면 안 된다.

전용 학습 자료는 원래 공식 생성기의 모집단을 정확히 재구성해 기존 평가 1,000개를 제외하고 만들었다. 정상 train 3,916 / 정상 calibration 844 / 개발 2,240(정상 840 + 이상 1,400). 학습은 정상만, 설정 선택에 개발 라벨 사용. 수치적으로 동일한 행의 분할 중복 0건. 생성 모집단은 공유하므로 외부 일반화 증거가 아니다. 평가 1,000개는 변경하지 않았다.

## 실제로 사용한 모델·피처

- Process: `rank_model.py` / `rank_context.py`. 과거 완료 관측 대비 최대 소요시간 순위·연속 증가·회복·짧은 기억, 정상 시간대별 요청 수 분포의 꼬리·무입력 지속. 미래 관측이나 정답 이벤트 시작/끝은 사용하지 않음.
- Process IF: 28개 피처, 300 trees, max_samples 1024, asinh, 정상 관측 상태별 robust 점수 보정, 정상 calibration P99.25.
- Process K-means: 19개 간결 피처, 8 clusters, 정상 공분산 whitening, 군집 반경 P95 거리 점수, 관측 상태별 정상 CDF 보정.
- Process LOF: 28개 피처, novelty=True, n_neighbors 800, 최대 참조점 4096, 정상 관측 상태별 robust 보정, 정상 calibration P99.5.
- Adapter 전문가 없음. IF·LOF는 기존 Adapter 파생 수치 9개 직접 사용. K-means는 이 9개를 직접 사용하지 않지만 기존 집단 참조 기반 source 피처는 사용.
- ADBench: `adbench_quantile.py`의 정상 분위수 Gaussian 변환과 공분산 잔차. IF 400 trees / max_samples 256, LOF neighbors 800. K-means는 `adbench_cluster.py`, 128 clusters / 가장 가까운 중심 거리 / 정상 calibration P99.75. 실패한 GMM 전처리 분기는 최종 모델에 사용하지 않음.
- 소요시간 초기 참조는 최대 6개 완료 관측이며 정상이라고 보증하지 않음. 시간대 트래픽 참조는 기존 합성 prior이므로 운영 이력으로 다시 구축해야 함.

## 파일 위치: 운영 후보와 ADBench 전용을 섞지 않기

운영 연결 **연구 후보**:

```text
reports/shared_model_v4_final/
  models.joblib                 # 세 탐지기 + 기존 피처 참조/컨텍스트
  selection.json                # 실제 설정·경보 정책·반복 평가 한계
  summary.json                  # 전체·시나리오별 지표
  audit.json                    # 지표 재계산·단위/원시 입력 테스트
  fresh_comparison.json         # 정책 동결 후 새 seed 확인
  adbench_transfer.json         # 서비스 모델 무학습 전이: 미달 공개
  handoff/
    if_shared.joblib            # 원시 Process 인터페이스 Monitor
    kmeans_shared.joblib
    lof_shared.joblib
    manifest.json               # 의존 버전·크기·해시
```

별도 수치 피처 표 평가 전용:

```text
reports/shared_model_v4_adbench_release/
  if_adbench_specific.joblib
  kmeans_adbench_specific.joblib
  lof_adbench_specific.joblib
  results.json
  selection.json
  audit.json
```

운영 후보 Monitor 사용 예:

```python
import joblib
monitor = joblib.load('reports/shared_model_v4_final/handoff/if_shared.joblib')
result = monitor.predict(native_minute_grid, completed_process_rows)
# case_id, adapterType, processId, window_start, score, threshold, anomaly
```

`PYTHONPATH=code`와 manifest의 버전이 필요하다. joblib는 신뢰하는 파일만 로드한다. 입력은 기존 인터페이스의 Adapter+Process별 **연속 1분 grid와 완료 Process 행**이다. Transaction 테이블은 요구하지 않는다. worker의 빈 분 생성, 초기 참조 및 최근 이력 보관, 재시작 상태 복원, 늦게 도착한 로그 처리는 별도 구현 대상이다. 한 행을 무상태로 넣는 실시간 API가 완성되었다는 뜻이 아니다. 원시 모델 내부 코드가 실행 시 데이터 파일을 읽지 않는지, 저장/로드 일치, 미지 Adapter, prefix 인과성을 audit에서 검사했다.

## 검증·노트북 재생성

PowerShell 예시:

```powershell
$env:PYTHONPATH='code'
.analysis-venv/Scripts/python.exe code/shared_model_v4/audit.py
.analysis-venv/Scripts/python.exe code/shared_model_v4/report.py
.analysis-venv/Scripts/python.exe code/shared_model_v4/audit_adbench.py
```

탐색 스크립트는 완료 모델 덮어쓰기를 막기 위해 기존 산출물이 있으면 중단한다. `run_rank.py` → `finalize_rank.py` → `evaluate_log.py`가 최초 후보, `conservative_log.py`가 보수적 정책 실험이다. ADBench는 `adbench_training.py` → `run_adbench_specific.py` / `run_adbench_quantile.py` / `run_adbench_cluster.py` → `finalize_adbench.py`. 나머지 manifold/profile/expanded/mixture 분기는 실패 또는 미채택 연구 기록이다.

## 평가 해석과 한계

- 기존 전체 평가를 본 뒤 경보 정책을 재설계한 적응적 연구. 개별 시도 전 cutoff는 정상 calibration에서 고정했고 테스트 ROC/점수 분위수로 직접 역산하지 않았지만, **기존 평가 결과가 후속 정책에 영향을 주었다**. 블라인드 테스트나 운영 보장으로 표현하지 않기.
- 최종 정책 동결 후 새 seed 48600042, 47,608개 분 판정에서 IF/K-means/LOF F1 68.20% / 72.60% / 71.05%. 동일 새 사례의 이전 모델 대비 오탐 수도 감소. 이 결과로 재선택하지 않았지만 같은 원본 pool이므로 외부 검증은 아님.
- 정상 후보의 STATUS=S가 성능 정상의 절대 보증은 아님. 정답/시나리오/문자열 ID를 모델 입력 피처로 넣지 않음. 상태 P/N, SQL·카운트 하드 룰 변경 없음.
- 모델 후보를 바로 서비스에 교체하기보다 필수 장애 유형 최소 Recall, 원본 정상 로그 오탐량, drift/콜드스타트/장기 미완료를 추가 검증해야 함.

참고: [ADBench 공식 생성기](https://github.com/Minqi824/ADBench/blob/main/adbench/datasets/data_generator.py), [ADBench 논문](https://arxiv.org/abs/2206.09426), [NIST CUSUM](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm), [QuantileTransformer](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.QuantileTransformer.html). 구현은 CUSUM의 누적 변화 관측 아이디어를 변형한 파생 피처이며 해당 관리도의 정확한 재현은 아님.
