# 이전 공유 기록 — 이슈 #38의 v12 모델

연결 이슈: [#38](https://github.com/kohs2k21/2026-2-CECD2-1-POWERCODE-03/issues/38).
이번 공유 범위는 최종 v12 모델 두 개, 전체 연구 코드·노트북, 원본 참조 아카이브와
학습/검증 캐시다. **기간별 원본 데이터와 벤치마크 CSV·정답은 업로드 보류**다.
`server/`의 backend/frontend/worker 또는 운영 모델은 변경하지 않는다. PR 대상은 `dev`다.

## 먼저 볼 파일

- [최신 모델 카드](MODEL_CARD.md): 실제 모델 두 개, 입력, 지원 Adapter, 성능 및 한계
- [서비스 연결 준비](INTEGRATION.md): 데이터 없이 모델 로드·추론 확인, 입력 계약과 주의사항
- [v12 개선 결과](code/model_research_v8/results.ipynb): v4/v7–v12 비교·특징·분포·검증
- [현재 벤치마크 난이도 비교](code/benchmark_difficulty/comparison.ipynb): 같은 v12 모델의 원본/완화 평가 비교
- [Process 분석](code/analysis/processes_analysis.ipynb), [Transaction 분석](code/analysis/transaction_analysis.ipynb), [연결 분석](code/analysis/merge_analysis.ipynb)
- [규칙·모델 전략](code/analysis/rule_and_model_strategy_analysis.ipynb), [K-means 분석](code/analysis/minibatch_kmeans_analysis.ipynb)
- [이전 시간창 실험](code/model_evaluation/results.ipynb), [초기 개선 실험](code/model_improvement/results.ipynb), [v5–v7 실험](code/model_research_v5/results.ipynb)
- [기존 IF 사후 분석](code/process_anomaly_pipeline/post_analysis.ipynb): 이전 v1 연구 기록이며 최신 추론 진입점이 아님

## 포함 및 보류 범위

| 포함 | 제외·보류 |
| --- | --- |
| 전체 `code/`의 연구 코드·시험·노트북·그림 | 기간별 원본 CSV/SQL, 환경 파일 및 의존성 폴더 |
| 최종 v12 IF·K-means portable 모델 두 개 | 이전 v1/v2 및 중간 실험 모델 바이너리 |
| 원본 그대로의 `reports/evaluation_v2_work/inputs.json` | 벤치마크 CSV·행/시간창 정답 |
| `reports/window_search_v2/training_inputs.joblib`, `validation_inputs.joblib` | 행별 예측 NPZ, 생성 중간 NDJSON |
| 중간/최신 실험의 집계 JSON·설명·저장 그림 | 벤치마크 변경 전 백업 데이터 |

`inputs.json`에는 원본에서 가져온 실행 정보가 들어 있다. 사용자 요청에 따라 익명화하지 않았다.
학습/검증 joblib 두 개는 **데이터 캐시**이지 별도의 모델이 아니다.
따라서 포함된 joblib은 네 개지만, 실제 모델은 두 개다. 모델과 캐시의 SHA-256은
`model_inventory.json`에 구분해 기록한다. 바이너리와 원본 아카이브는 원본과 동일하다.

## 모델 구조

```text
reports/model_research_v12/handoff/
  if_v12_candidate.joblib       # IF 전체 / Adapter / 앙상블
  kmeans_v12_candidate.joblib   # K-means 전체 / Adapter / 앙상블
  manifest.json                # 모델 hash·라이브러리 버전·입력 설명
  verification.json            # 원래 연구 환경의 예측 재현 기록
code/model_research_v8/inference.py  # ProcessResearchMonitor, hash 검사 로더
```

각 모델에 전체·Adapter별·같은 알고리즘의 전체+Adapter 앙상블 세 경로가 들어 있다.
IF와 K-means를 서로 섞은 앙상블은 아니다. 최신 모델은 이전 행별 v1 API와 입력 형태가 다르다.
Process에서 만든 연속 1분 grid와 완료 Process 관측을 받으며 Transaction 테이블은 ML 입력이 아니다.

## 벤치마크 없이 가능한 확인

Python 3.12와 `requirements.txt`에 기록된 환경을 사용한다.

```powershell
cd research/anomaly_detection
python inspect_models.py
python inspect_models.py --trusted --smoke
python run_tests.py
```

첫 명령은 모델·포함 캐시의 hash만 확인하며 pickle을 로드하지 않는다.
`--trusted --smoke`는 직접 만든 작은 입력으로 두 모델의 세 경로를 확인한다.
이는 탐지 정확도 평가가 아니다. 신뢰할 수 없는 joblib/pickle은 실행하지 않는다.

11개 노트북은 저장된 출력으로 열어볼 수 있다. 원본 EDA를 다시 실행하려면 기간별 원본이,
벤치마크 재평가에는 평가 입력과 정답이 필요하다. 이 자료들은 이번 공유에서 보류했다.
집계 기반 최신 v12/난이도 비교 노트북은 저장 결과를 읽는 형태로 재실행할 수 있다.

## 버전 및 재현 주의

- 대표 최신 모델은 최종 v12다. 모든 시나리오·모든 지표에서 보편적으로 최고라는 뜻은 아니다.
- v12 원본 벤치마크 결과와 현재 난이도 완화 결과를 별도로 보존한다. 완화 결과 상승은 재학습 효과가 아니다.
- 포함 학습/검증 캐시는 원래 시간창 선택 실험의 입력 hash에 고정돼 있다. 현재 완화 벤치마크의 새 학습 캐시로 간주하지 않는다.
- 이전 실험 코드는 보존하지만 해당 이전 모델 바이너리는 이번 정책상 제외했다. 이전 전체 실험의 재실행에는 별도 산출물이 필요하다.
- `code/benchmark_difficulty/evaluate.py`는 저장 모델 평가 경로지만 보류된 CSV·정답·변경 전 데이터도 필요하다.
- 하드룰 코드는 `code/process_anomaly_pipeline/`에 보존한다. v12 모델의 `predict()`가 자동으로 하드룰까지 실행하는 것은 아니다.
- 합성 과거 트래픽 기준, 지연 도착 정책, 수집 장애, 상태 유지, 점수 전달 계약은 운영 연결 전에 검토해야 한다.

`publication_checks.json`은 이번 패키지에서 실제로 수행한 검증을 기록한다.
`verification.json` 등 연구 기록과 이번 검증을 구분한다. 기존 운영 서비스 배포나 PR 병합 완료를 의미하지 않는다.
