# 이상 탐지 연구 및 학습 모델

연결 이슈: [#38](https://github.com/kohs2k21/2026-2-CECD2-1-POWERCODE-03/issues/38)

대규모 로그 기반 비지도 이상 탐지의 분석·규칙·Isolation Forest·MiniBatch K-means·
시간창 검증 선택·평가 결과를 보관한다. `server/`의 운영 코드·설정에는 연결하지 않는다.
작업 브랜치는 `feat/38-anomaly-detection`, PR 대상은 **dev**다. main 반영은 포함하지 않는다.

## 먼저 볼 파일

- [실험 결과 노트북](code/model_evaluation/results.ipynb): 시간창 후보·선택 근거·1분/5분 비교·분포/판정선·미탐·오탐·시나리오별 결과
- [모델 카드](MODEL_CARD.md): 3개 모델 파일의 역할·입력·지원 Adapter·성능·사용 제약
- [평가셋 설계](BENCHMARK.md): 6개 기본 시나리오와 혼합 Hard의 생성 조건·논문 적용·라벨 의미
- [Transaction 분석](code/analysis/transaction_analysis.ipynb), [Process 분석](code/analysis/processes_analysis.ipynb), [연결 분석](code/analysis/merge_analysis.ipynb)
- [규칙과 모델 분리 기준](code/analysis/rule_and_model_strategy_analysis.ipynb), [K-means 실험](code/analysis/minibatch_kmeans_analysis.ipynb)
- [기존 Process IF 사후 분석](code/process_anomaly_pipeline/post_analysis.ipynb)

## 포함 및 제외

| 포함 | 제외 |
| --- | --- |
| Python 코드·시험·노트북 | 고객 원본·합성 평가 CSV/SQL |
| 집계 지표·실험 설정·그림 | 원본 실행 아카이브 `inputs.json` |
| 실제 학습 모델 `.joblib` 3개 | `training_inputs.joblib`, `validation_inputs.joblib` |
| 모델 SHA-256·환경 버전·제약 | 행별 예측/정답 NPZ, 환경파일·키·의존성 폴더 |

노트북의 개별 원본 로그 미리보기 출력과 결과 JSON의 사례 ID 목록은 공개 복사본에서
제거했다. 집계 표·그림·평가 건수는 보존했다. 모델은 원본 학습 산출물과 바이트가 같다.
모델에는 학습된 수치 기준·업무 ID별 기준 매핑이 있지만 원본 실행 행은 포함하지 않는다.
노트북의 배포 안내와 저장 그림 fallback만 추가했으며 모델 성능 수치는 다시 계산하거나
유리한 기준으로 바꾸지 않았다. 모델 해시와 출판 변환 기록은 `model_inventory.json`에 있다.

## 구조

```text
code/analysis/                    # 원본 3기간의 EDA와 모델/규칙 설계
code/process_anomaly_pipeline/    # 하드룰 + Process IF, backend용 core 추론 API
  artifacts/process_if_v1.joblib  # 기존 Process IF 실제 모델
code/evaluation_benchmark/        # 외부 데이터로 평가셋 생성·검증
code/model_evaluation/            # IF/K-means 6전략, 시간창 선택 및 평가
reports/model_evaluation_v2/      # 선택 5분 모델·집계 결과·그림
reports/model_evaluation_v2_baseline_1m/  # 동일 새 참조의 고정 1분 대조 모델
reports/window_search_v2/         # 검증 후보별 집계, 데이터 캐시 미포함
```

원래 코드의 모듈 이름과 상대 구조를 유지하므로 joblib의 클래스 경로를 해석할 수 있다.
다른 위치에서 로드할 때도 이 폴더의 `code/`를 Python import 경로에 추가해야 한다.

## 데이터 없이 가능한 확인

Python 3.12와 `requirements.txt`에 기록한 라이브러리 환경을 사용한다.
joblib은 코드를 실행할 수 있으므로 신뢰하는 저장소의 자체 학습 파일만 로드한다.

```powershell
cd research/anomaly_detection
python -m pip install -r requirements.txt
python inspect_models.py
python inspect_models.py --trusted --smoke
python run_tests.py
python code/model_evaluation/execute_notebook.py
```

`inspect_models.py`는 먼저 모델 해시를 검사한다. `--trusted`가 있어야 pickle을 로드한다.
`--smoke`는 새 합성 입력의 출력 형태·유한 점수·import 경로만 확인하며 탐지 정확도 시험이 아니다.
시험 중 사용되는 작은 임시 로그는 합성 fixture이며 고객 데이터가 아니다.
결과 노트북은 집계 JSON으로 표를 만들고 NPZ가 없는 그림은 저장 PNG를 표시한다.
원본 기반 EDA 노트북은 저장된 결과를 열어볼 수 있지만 원본 없이는 재실행할 수 없다.

## 데이터가 필요한 재현 작업

학습·시간창 재선택·31,000개 평가 사례의 재평가는 데이터 제외 정책 때문에 바로 실행할 수 없다.
별도 승인된 원본·평가셋·캐시를 이 연구 폴더의 무시되는 `data/`, `reports/` 경로에 준비해야 한다.
실행 방법은 각 코드 폴더 README를 따른다. 과거 SHA와 맞지 않는 입력은 새 실험으로 기록해야 한다.
CSV 내보내기 `author_csv.mjs`는 당시 제공된 `@oai/artifact-tool` Node 환경이 필요한 선택 경로다.
그 도구는 이 저장소에 번들하지 않았다. 기존 완료된 전체 실험 결과를 포함했지만,
데이터 없이 다시 실행한 검증과는 구분한다.

## 현재 결론

- 시간창 후보 1·2·3·5·10·15분, 공통 검증 평균 F1 기준 **5분**, 판단 주기 1분
- 원본 실행 약 25% 검증용 예약, 시간창 학습/보정/판정선과 중복 제외
- 최종 평가: 이상 사례 31,000개 + 짝 정상 사례 31,000개, 시간창 판단 2,167,766개
- 최고 전체 시간창 F1: IF 앙상블 22.54%, Recall 14.79%, 정상 오탐률 3.28%
- 같은 새 참조의 1분 IF 앙상블 F1 6.22%, 오탐률 0.70%; 오탐 증가 동반
- Hard의 6개 사건을 모두 탐지한 비율 0%; 운영 적용 수준이라는 결론 없음
- 기본 5분이 모든 모델·시나리오의 최적은 아님; 개별 지연은 짧은 시간창이 유리
- 원본 정상 패턴이 생성과 학습에 재사용된 반합성 스트레스 테스트, 독립 운영 장애 평가가 아님

backend 연결·운영 배포·추가 최적화·dev PR 병합은 별도 검토 사항이다.
