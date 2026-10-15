# 벤치마크 난이도 소폭 완화 실험

기존 `data/evaluation`에 난이도 완화를 반영했다. 현재 기본 평가 경로는
`data/evaluation`이며 별도의 활성 평가셋은 없다. 변경 전 원본은
`reports/benchmark_before_mild_20261014`에 복구·비교 근거로 보관했다.
모델/threshold는 변경하지 않았고 결과는 `reports/benchmark_mild_v1`에 있다.

6개 task의 원본 5,000개 사례를 그대로 사용한다. 정상 대비 주입 편차/추가 요청을
1.2배로 늘리고, 중단은 5/10/20분에서 6/12/24분으로 늘린다. 같은 정상 값에서 비교하며
기존 하드 룰 상한을 넘지 않는다. 강도와 무관한 컬럼/상태/업무 집단을 변경하지 않는다.
정상 대조군과 1,000개 혼합 Hard는 byte-identical 복사한다.

사례를 detector 점수로 골라내거나 난이도를 성능에 맞춰 재조정하지 않는다.
`profile.json`은 평가 전에 고정한다. 정수 올림과 상한 제한으로 실제 변화는 다를 수 있다.
20%는 신호 강화율이지 난이도 감소율 또는 F1 개선율이 아니다.

## 현재 데이터 검증과 평가

프로젝트 루트에서 실행한다. 이미 반영된 데이터는 다시 생성하거나 덮어쓰지 않는다.
변경 전 연구의 모델·학습 캐시는 이전 manifest에 고정되어 있으므로, 과거 캐시를
현재 데이터의 새 평가 결과로 취급하지 않는다. 현재 6개 경로의 평가 진입점은 아래와 같다.

```powershell
.\.analysis-venv\Scripts\python.exe code/benchmark_difficulty/test_adjust.py
.\.analysis-venv\Scripts\python.exe code/benchmark_difficulty/activate.py --stage verify
.\.analysis-venv\Scripts\python.exe code/evaluation_benchmark/validate.py data/evaluation
.\.analysis-venv\Scripts\python.exe code/benchmark_difficulty/evaluate.py
.\.analysis-venv\Scripts\python.exe code/benchmark_difficulty/audit.py
.\.analysis-venv\Scripts\python.exe code/benchmark_difficulty/report.py
.\.analysis-venv\Scripts\python.exe code/benchmark_difficulty/build_notebook.py --execute
```

CSV 내보내기는 기존 Artifact Tool writer를 재사용한다. 학습/보정 없이 v12 portable
연구 모델을 로드한다. 원본 Hard 점수는 입력/모델 동일성을 확인한 뒤 그대로 재사용한다.
정상 대조군의 공통 시간 구간에서는 원본과 예측이 동일한지 확인한다.

원본 v12가 float32 피처 NPZ를 다시 float64로 읽어 평가했으므로 이 비교도 동일한
float32 round-trip을 적용한다. 전체 float64로 바로 평가한 진단 실행에서 정상 집단
지연 구간의 K-means 판정 34개가 경계 정밀도 때문에 달랐고, 동일 정밀도 적용 시
모두 일치했다. 진단 파일은 `reports/benchmark_mild_v1_precision_diagnostic`에 보존한다.
이는 모델이나 threshold 변경이 아니라 원본 평가 조건을 맞춘 것이다.

성능 차이는 **벤치마크 난이도 변경 효과**다. 기존 결과를 대체하거나 실제 운영 성능의
개선으로 발표하지 않는다. GitHub/Notion/backend/운영 모델을 변경하지 않는다.
