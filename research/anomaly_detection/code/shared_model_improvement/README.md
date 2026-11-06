# Adapter 피처를 유지한 단일 모델 개선

## 목적과 비교 기준

- 알고리즘마다 단일 IF / MiniBatch K-means / LOF. Adapter 전문가와 전문가 앙상블 없음.
- 기존 실패 실험의 Adapter 파생 피처 10개를 유지하거나 `adapter_workload_z` 하나만 제외하여 **9개 이상 유지**.
- 목표: 동일 기존 전체 벤치마크에서 피처 추가 전 F1 이상. 기준은 IF 33.625%, K-means 33.713%, LOF 39.191%. 성공 여부는 전수 혼동행렬 재계산으로 확인하며 실패를 완료로 표시하지 않음.
- 기존 v18, `adapter_feature_ablation`, 원본 CSV를 보존. 배포·GitHub·Notion 변경 없음.

## 개선 내용

1. 기존 스케일 하한 0.05가 거의 상수인 정상 피처의 작은 차이를 크게 키운 문제 보완.
2. Adapter 피처에 asinh 완만한 압축, 유한 범위 clip, 학습 분포 기반 QuantileTransformer 비교.
3. LOF는 기본 피처를 함께 변환하면 개발 제약을 통과하지 못해, 기본 피처 스케일을 그대로 두고 Adapter 추가 블록만 안정화하는 hybrid 후보도 검토.
4. 거리 기반 모델에서 Adapter 추가 블록의 양수 가중치 0.1~0.5 비교. 가중치를 0으로 꺼서 유지한 척하지 않음.
5. IF 표본 수/피처 비율, K-means 군집 수, LOF 이웃 수·참고 표본 수·중복 제거 등을 개발 데이터에서 선택.

근거: [QuantileTransformer 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.QuantileTransformer.html)는 특성별 비선형 변환의 이상값 영향 감소와 학습 범위 밖 포화 특성/상관 왜곡 한계를 설명. [IF 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)는 표본 수·피처 선택·점수 정의의 기준. asinh/블록 가중치는 본 연구의 수치 안정화 후보이며 특정 논문 알고리즘의 정확한 재현이라고 주장하지 않음.

## 분할과 평가 정직성

- 기존 v15의 원본 실행 분할을 보존한 정상 train 19,793분만 탐지기/스케일러/Adapter 참고 통계에 사용.
- 독립 정상 개발 17,925분의 점수 quantile로 경보선 후보 생성.
- search 정답으로 하이퍼파라미터와 quantile 선택. **알고리즘 학습은 비지도지만 설정 선택은 라벨 활용**.
- search에서 기존 기본 모델 대비 정상 오탐률 +0.5%p 이내(최소 허용 상한 3%), Precision -2%p 이내 제약 후 pooled F1 최대화.
- train / 정상 경보 보정 / search / confirmation의 case_id는 상호 분리. 기존 confirmation은 선택 후 확인에만 사용.
- 첫 실패 실험의 최종 결과를 이미 본 뒤 구조를 개선한 연구이므로 기존 벤치마크는 외부 블라인드 테스트가 아님. 이 사실을 메타데이터/결과에 명시.
- 최종 벤치마크 정답을 threshold 설정·파라미터 탐색 루프에 입력하지 않음. 데이터 변경/난도 완화 없음.
- 6개 기존 시나리오 + Hard 전수 평가. 주요 5개 별도 집계. point adjustment 없음.
- 현재 벤치마크에는 실제 Adapter 전체 동시 Process 군집이 없어, 그러한 새 피처를 꾸며 넣지 않음.

## 파일과 실행

최종 IF 보완 경로는 `reports/shared_model_improvement_refined/`이며, 1차 개선 결과는 `reports/shared_model_improvement/`에 보존한다. 관측 상태별 경보선 후보는 개발 평가에서 더 낫지 않아 채택하지 않았다. IF는 기본 피처 중복을 줄인 full/balanced/compact view를 추가 비교했고 Adapter 피처 9개는 그대로 유지했다.

```powershell
.analysis-venv/Scripts/python.exe code/shared_model_improvement/run.py --stage tune
.analysis-venv/Scripts/python.exe code/shared_model_improvement/run.py --stage confirm
.analysis-venv/Scripts/python.exe code/shared_model_improvement/run.py --stage evaluate
.analysis-venv/Scripts/python.exe code/shared_model_improvement/refine_if.py --stage tune
.analysis-venv/Scripts/python.exe code/shared_model_improvement/refine_if.py --stage evaluate
.analysis-venv/Scripts/python.exe code/shared_model_improvement/inference.py --output reports/shared_model_improvement_refined
.analysis-venv/Scripts/python.exe code/shared_model_improvement/report.py --output reports/shared_model_improvement_refined
.analysis-venv/Scripts/python.exe code/shared_model_improvement/audit.py --output reports/shared_model_improvement_refined
$env:PYTHONPATH='code'
.analysis-venv/Scripts/python.exe -m unittest shared_model_improvement.test_models -v
```

- `models.py`: 단일 탐지기와 정상 학습 전용 인코더.
- `run.py`: 개발 탐색·동결·미사용 confirmation 확인·최종 전수 평가.
- `refine_if.py`: IF 기본 피처의 중복 제거와 작은 트리 표본 수 추가 탐색. K-means·LOF는 고정된 1차 개선 모델/전수 점수를 그대로 재사용.
- `inference.py`: 3개 연구 모델 내보내기. Process 완료 로그와 과거를 유지한 연속 1분 grid 입력 필요.
- `report.py`, `result.ipynb`: 전후 지표, 일반 막대 그림, 실제 경보선, 파라미터, 시나리오별 해석.
- `audit.py`: 저장 점수 기반 재계산, Adapter 피처 실제 사용, 보존/목표 달성 점검.
- `reports/shared_model_improvement_refined/`: 선택된 최신 모델/전수 평가/분석. selection이 생기면 재탐색 덮어쓰기 거부.

모델은 실시간 과거 상태 저장/지연 도착 처리/운영 prior 갱신을 직접 수행하지 않는다. 기존 feature processor와 Adapter 참고표도 함께 내보내며, backend 워커에서 문맥을 유지해야 한다. 자동 운영 교체 없음.
