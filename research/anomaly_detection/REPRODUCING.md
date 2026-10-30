# 새 데이터 학습과 과거 실험 재현

## 새 정상 데이터로 재학습 — 기존 캐시 불필요

`research_workflow train`은 새 입력에서 소요시간 조건부·Adapter 기준, 정상 이력,
피처 스케일과 IF/K-means/LOF 한 개를 다시 적합한다. 더 나중인 정상 calibration의
기본 P99 점수를 경보선으로 고정한다. case_id 중복·시간 역전은 오류다.
STATUS=S만 보고 정상이라고 단정하지 말고 검토한 정상 입력을 제공해야 한다.

```powershell
$env:PYTHONPATH = "$PWD/code"
python -m research_workflow train --algorithm if --normal-native YOUR/normal_native.csv --normal-completions YOUR/normal_completions.csv --calibration-native YOUR/cal_native.csv --calibration-completions YOUR/cal_completions.csv --reviewed-normal --settings configs/if_core.json --quantile 0.99 --output outputs/my_if.joblib
```

K-means/LOF는 algorithm과 `configs/kmeans_core.json`/`lof_core.json`을 바꾼다.
새 모델은 같은 predict/evaluate 명령으로 사용한다. 공개 모델은 덮어쓰지 않는다.
이는 학습 코어 설정을 재사용하는 **새 정상-only baseline**이며 원래 v7의 개발 정답 기반
점수 혼합·세부 경보 기준표 선택 전체를 자동 재현하지 않는다. v7 성능을 새 모델에 표시하면 안 된다.

정상 참고에서 관측되지 않은 요일·시간대는 제공한 정상 집단의 pooled count로 대체하고
provenance에 기록한다. 여러 주의 정상 참고와 별도 시간대 검증이 권장된다.
최소 30행 검사는 통계적 신뢰성 보장이 아니다. 데이터 없는 전체 경로 테스트:

```powershell
python -m unittest research_workflow.test_short -v
```

## 공개 v7 성능을 처음부터 재현

학습·개발 평가·탐색·감사 코드는 버전별 폴더에 보존했다. 이 경로는 보류한 원본·벤치마크와
중간 산출물의 해시를 확인한다. 파일이 없다고 다른 자료로 대체하거나 hash 검사를 끄면 같은 실험의 재현이 아니다.
정확한 과거 재현에는 원래 자료를 별도 확보해야 한다. 새 사용자는 위 baseline 경로를 사용한다.

- 원본/벤치마크 준비: `code/evaluation_benchmark/{prepare,generate,validate}.py`
- 전처리·초기 학습: `code/process_anomaly_pipeline/`, `code/model_evaluation/`
- 최신 피처·정상 참고·보정: `code/shared_model_target/`, `code/shared_model_v4/`~`shared_model_v7/`
- 동결 모델 재평가·감사: `shared_model_v7.evaluate_one`, `audit_attempt`, `release`
- 기존 inputs.json과 training_inputs/validation_inputs는 #38 자료이며 새 데이터 학습의 필수 파일이 아님

## 장기 학습·평가

`code/long_term_anomaly/`의 정상 참고, window feature fit, conditional 보정, localized 판정,
벤치마크 생성·학습·개발 선택·확인 평가·직렬화 코드를 보존했다.
`five_minute.run`, `refine`, `feature_study`, `localized`, `confirmation`, `serialization`은
단계별 입력/산출물을 요구한다. 임의 CSV 하나로 원래 학습 전체를 실행하는 단일 명령은 아니다.
`FineWindowModel.fit(normal_features)` 등 API를 새 자료에 적용할 수 있고 입력 구조는
`five_minute/benchmark.py` 및 테스트에 있다. 정상 참고 준비와 모델 재학습은 구분한다.

## 벤치마크·ADBench·노트북

생성/평가 코드·논문 근거·집계 결과를 포함했다. 생성된 CSV·정답은 추가하지 않았다.
ADBench는 원시 Process 시계열이 아닌 별도 숫자 피처 벤치마크이며 최신 v7 새 평가 결과가 아니다.
과거 모델을 요구하는 비교 실험은 해당 파일을 별도 확보해야 한다.
`notebooks/latest_*_results.ipynb`는 집계 결과만 읽어 데이터 없이 재실행 가능하다.
`code/`의 원래 노트북은 저장 출력을 보존하지만 EDA/전체 재평가에는 별도 자료가 필요할 수 있다.
