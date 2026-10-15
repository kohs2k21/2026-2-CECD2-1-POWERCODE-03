# 추가 성능 개선 연구

시작 파일: `results.ipynb`. 원본 CSV·라벨·기존 운영 모델은 변경하지 않았다.
모든 결과는 합성 벤치마크 연구 성능이며 실제 운영 일반화 성능은 아니다.

## 핵심 결과

### 최종 v12 결과

관측 이력이 짧을 때는 조건부 지연에 강한 v8, 이력이 쌓이면 IF v10 / K-means v4를 사용한다.
경로 선택은 **직전 비어 있지 않은 실행 분 3개**에만 근거한다. 시나리오/정답/총 사례 길이를 보지 않는다.
v12는 새로운 확인 사례를 만들기 전에 동결했다. v11의 정상 proxy 보정은 사용하지 않는다.

| 모델 | Accuracy | Precision | Recall | F1 | 정상 오탐 |
|---|---:|---:|---:|---:|---:|
| IF 전체 | 85.35% | 74.30% | 18.00% | 28.98% | 1.24% |
| IF Adapter | 84.73% | 73.29% | 12.72% | 21.68% | 0.92% |
| IF 앙상블 | 85.11% | 69.13% | 18.74% | 29.49% | 1.67% |
| K-means 전체 | 85.09% | 62.59% | 25.41% | 36.15% | 3.02% |
| K-means Adapter | 84.06% | 54.66% | 23.47% | 32.84% | 3.88% |
| K-means 앙상블 | 84.63% | 57.59% | 28.20% | 37.86% | 4.14% |

기준 IF v7 대비 전체 IF의 네 지표 모두 상승. 기준 K-means v4 대비 Adapter/앙상블의 네 지표 모두 상승.
IF 앙상블 및 K-means 전체는 Precision/오탐 tradeoff가 있다. 모든 경로의 전면 개선이라고 주장하지 않는다.
IF 전체 개별 지연 F1 0%→88.51%, 관계 이상 0%→22.37%. 점진적 악화/혼합 Hard는 여전히 약하다.
K-means 앙상블 집단 지연의 정상 오탐은 12.18%라 평균 4.14%만 보고 운영 준비를 선언하지 않는다.

### 이전 v10/v11 결과와 한계 (보존)

최근 IF 전체(v7) 대비 네 지표 모두 상승한 후보를 확보했다.

| 지표 | 기존 v7 | 분리 정상 참고 집단 v10 | 대표 정상 보정 v11 |
|---|---:|---:|---:|
| Accuracy | 84.89% | 85.28% | 85.25% |
| Precision | 69.81% | 76.35% | 73.39% |
| Recall | 15.93% | 16.47% | 17.58% |
| F1 | 25.95% | 27.09% | 28.36% |
| 정상 오탐 | 1.37% | 1.02% | 1.27% |

증가 폭은 작고 Recall은 여전히 낮다. 모든 경로/모델의 개선은 아니다.
새 K-means는 이전의 강한 v4를 넘지 못해 v4를 보존한다.
v11은 이전 실험 정상 대조 사례를 학습 보정에 사용한다. calibration/test 원본 템플릿 공유가 생겨 v10과 같은 프로토콜이 아니다.

## 접근과 실험

- `features.py`: 정상 train의 Adapter+ProcessID+정확한 처리량별 경험적 꼬리. 최소 20행, 부족하면 상위 집단 fallback.
- 최대 꼬리 위치로 단건 지연을 보존하고 평균/변화/추세로 집단 변화를 시험.
- 단건 Process 지연은 과거 분 관측 없이 정상 참고 집단이 있으면 평가.
- 직전 비어 있지 않은 실행 분 3개 이상에서만 기존 안정 패턴 분기 평가. 빈 창/미평가 행도 최종 분모에 포함.
- v8: 트래픽/지연 채널. 단건 지연 IF 전체 F1 88.51%지만 중단/종합 성능 손실.
- v9: compact 피처와 보수적 정상 두 집단 envelope. 지연은 좋아져도 종합 Recall 손실.
- v10: 기존 IF v7/K-means v4 분기 동결+조건부 지연 분기. IF 전체의 네 지표 개선.
- v11: 트리/중심은 v10과 동일. 이전 합성 정상 대조 자료로 보정만 변경. IF F1/Recall 추가 상승, K-means 실패.
- 군집 공분산 후보(radius/diagonal/full), IF sample 크기, 피처 view, 결합 mean/max, 분위수, 가중치 비교.

조건부 CDF는 학습된 피처/통계 참고값이다. 확정 이상 하드 룰, 보장된 p-value, COPOD/ECOD 재현은 아니다.

## 파일

| 파일 | 역할 |
|---|---|
| `features.py` | 조건부 꼬리·분 집계·과거 기반 실행 주기 |
| `data.py` | 동일 정상 재생 재구성, 새 v8 사례, 입력/코드 hash |
| `models.py` | 두 채널 IF/K-means, 군집 거리 후보, 정상 보정과 정확한 fallback |
| `run.py` | v8 학습/탐색/확인/평가 |
| `refine.py` | v9 피처 축소와 보수적 보정 |
| `bridge.py` | v10 기존 분기+조건부 분기 결합 |
| `recalibrate.py` | v11 대표 정상 보정, source 공유 명시 |
| `compat.py` | 신뢰된 예전 script-pickle 로드 시에만 임시 class alias. 기존 파일 보존 |
| `audit.py` | 정답/시각/행 동일성, 재계산, 트리·중심 동일성, 사례 단위 bootstrap |
| `ablate.py` | 동일 설정 고정, 새 확인 집단의 분기 제거/보정 제거 실험 |
| `inference.py` | portable handoff, Process 입력 전처리/점수 interface |
| `verify_handoff.py` | 새 Python 프로세스에서 모델 로드·600개 예측 재현(각 알고리즘) |
| `report.py`, `build_notebook.py` | 읽기 전용 표/일반 막대/히스토그램, 설명과 실행된 노트북 |

## Handoff 모델

### 최종 v12 handoff

`reports/model_research_v12/handoff/`:

- `if_v12_candidate.joblib`: 11,773,977 bytes.
- `kmeans_v12_candidate.joblib`: 605,016 bytes.
- `manifest.json`: trusted SHA256/패키지 버전, `verification.json`: 각 알고리즘 600개 저장 예측 재현.
- 코드 `regime.py`: 이전 모델을 그대로 보존한 관측 이력별 경로 선택과 새 확인 사례 평가.
- 코드 `audit_regime.py`: 원래 라벨/시각/행 동일성·정확한 fallback·지표 재계산·paired-case bootstrap.

기존 운영 v1/backend는 변경하지 않았다. v12는 연구 후보이며 실제 과거/미래 로그 검증과 traffic prior 교체 후 배포 검토가 필요하다.

### 이전 v11 handoff (연구 기록 보존)

`reports/model_research_v11/handoff/`:

- `if_v10_candidate.joblib`: 6,146,070 bytes. 정상 보정 DataFrame/사례 ID 제거. 배포 완료가 아닌 연구 후보.
- `kmeans_v4_preserved.joblib`: 242,734 bytes. 기존 더 강한 K-means를 보존한 것이며 새 성능 개선으로 주장하지 않음.
- `manifest.json`: trusted SHA256와 패키지 버전. `verification.json`: 저장 예측과 일치 여부.

`ProcessResearchMonitor.predict(native, completions)`는 연속 분 grid와 완료된 Process 로그를 받는다.
Transaction 테이블은 입력에 쓰지 않는다. 실행 식별은 Process의 transactionId를 쓸 수 있지만 평가 case_id와 혼동하지 말 것.
caller는 각 stream의 과거를 유지하고 비어 있는 분을 보존해야 한다. 입력 없이 중단을 탐지할 수는 없다.
현재 synthetic traffic prior를 실제 운영 이력으로 검증/교체하고 미평가·수집 장애를 별도 표시해야 한다.
임의 외부 joblib은 로드하지 않는다. trust manifest hash로 검증한다.

## 재실행

```powershell
.analysis-venv/Scripts/python.exe code/model_research_v8/audit.py
.analysis-venv/Scripts/python.exe code/model_research_v8/audit_regime.py
.analysis-venv/Scripts/python.exe code/model_research_v8/ablate.py
.analysis-venv/Scripts/python.exe code/model_research_v8/inference.py
.analysis-venv/Scripts/python.exe code/model_research_v8/verify_handoff.py
.analysis-venv/Scripts/python.exe code/model_research_v8/build_notebook.py --execute
```

동결된 버전의 tune은 덮어쓰기를 거부한다. 새 실험은 새 버전/새 확인 사례로 분리한다.
v9/v10 초기 research joblib에 `__main__` class가 있었고 trusted alias로 호환 로드한다. 새 handoff/v11은 canonical class 경로로 저장한다.
수정 전 초기 실패 및 preparation의 preselection bugfix 기록은 유지했다. 기존 동결 소스/모델을 수정하지 않았다.

## 해석 한계

- 정상 fit은 비지도. 주입 라벨로 설정을 선택했으므로 전체 튜닝 절차를 완전 비지도라고 부르지 않음.
- 원래 평가셋은 이전에 여러 번 봤고 설계가 적응적으로 바뀜. 새 blind/external 검증이 아님.
- v11 보정용 정상은 별도 synthetic 사례지만 원본 템플릿/생성기를 공유. 원래 final normal CSV를 보정에 사용하지는 않음.
- 현재 학습은 정상 샘플 아카이브/재생 기반. 수백만 원본 행 전체 재학습 아님.
- bootstrap은 합성 사례 샘플링 오차만 반영. 원본 공유/선택 편향/운영 도메인 차이를 해결하지 못함.
- 6종 혼합 Hard와 약한 점진적 악화는 미해결. 전체·Adapter·앙상블 어느 경로가 항상 더 좋은 것은 아님.
- 운영 v1/backend/GitHub/Notion은 변경하지 않음. 실패한 K-means 포함 연구 묶음을 자동 배포하지 말 것.
