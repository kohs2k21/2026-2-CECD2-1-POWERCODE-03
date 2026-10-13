# Process 이상탐지 파이프라인

**입력/출력 단위는 Process 실행 1행.** 요청한 규칙 → Adapter별 Isolation Forest → 전체 Isolation Forest → 점수 앙상블을 구현했다. `transactionId`나 `processId`마다 별도 모델을 만들지 않는다. 새 Process ID라도 준비된 Adapter를 사용하면 해당 전문가를 그대로 사용한다.

## 파일과 실행

| 파일 | 역할 |
|---|---|
| `pipeline.py` | 학습, 추론, 근거 반환, 모델 저장/로드. 웹 프레임워크와 분리 |
| `rules.py` | 규칙 판정과 평가 가능/불가능 구분 |
| `data.py` | 입력 정규화, 시간 분할, 기존 세 기간 export 로딩 |
| `features.py`, `quantiles.py` | 학습 전처리와 고정 통계 임계값 |
| `config.example.json` | 변경 가능한 기본 설정 |
| `analysis.py`, `report.py`, `post_analysis.ipynb` | 전수 집계, 전후 개수·실제 판정선·사례 근거 보고서 |
| `execute_analysis.py` | 현재 Python으로 새 노트북 전체 실행 및 그림 추출 |
| `tests/test_pipeline.py`, `tests/test_analysis.py`, `tests/test_report.py` | 규칙·누수·fallback·저장/로드, 시각화·전후 개수·근거 테스트 |
| `artifacts/process_if_v1.joblib` | 이 데이터로 학습한 배포용 모델 |
| `artifacts/process_if_v1.manifest.json` | 실제 파라미터, 학습 범위, 표본 수, 버전, 임계값 |

프로젝트 루트에서 PowerShell:

```powershell
$env:PYTHONIOENCODING='utf-8'
cd code
..\.analysis-venv\Scripts\python.exe -m unittest process_anomaly_pipeline.tests.test_pipeline -v
..\.analysis-venv\Scripts\python.exe -m process_anomaly_pipeline --help
```

새 환경은 `python -m pip install -r process_anomaly_pipeline/requirements.txt`; 노트북까지 실행하려면 `requirements-analysis.txt`를 설치한다. 저장된 모델은 manifest의 학습 환경과 맞춘다. 이 실행에서는 Python 3.12, scikit-learn 1.9.1, pandas 3.0.6, numpy 2.5.3을 사용했다. 범위형 requirements는 **새로 학습할 환경의 설치 조건**이지 다른 버전에서 기존 pickle을 호환시킨다는 뜻이 아니다.

새 버전 학습 (기존 모델을 덮어쓰지 않는 별도 이름 권장):

```powershell
..\.analysis-venv\Scripts\python.exe -m process_anomaly_pipeline train --data-dir ../data --config process_anomaly_pipeline/config.example.json --model process_anomaly_pipeline/artifacts/process_if_v2.joblib --with-transaction-context --complete-context
```

세 기간 전체 Process를 읽는다. 파일명만 보고 선택하지 않고 컬럼 스키마를 확인한다. 최근 기간은 파일명과 실제 내용이 다르다. 오프라인 원본 로더만 `code/analysis/log_analysis_common.py`를 이용하며 백엔드 추론에는 이 분석 파일이나 CSV가 필요 없다. `data/ESB.SQL`은 DDL을 **읽어 제약조건을 확인했을 뿐 실행하지 않는다**.

`--complete-context`는 "제공된 거래의 모든 Process를 수집했다"는 호출자의 선언이다. 내보낸 데이터가 완전하다는 운영 확인 없이는 확정 정합성 오류로 해석하지 않는다. 이 실험은 전체 export를 제공한 조건으로 관계 후보를 검사했다. Transaction 없이 학습하려면 위 두 context 옵션을 빼면 된다.

파일 추론 (새 결과 경로; `.jsonl`이면 행별 근거, `.csv`이면 플래그/점수):

```powershell
..\.analysis-venv\Scripts\python.exe -m process_anomaly_pipeline predict --model process_anomaly_pipeline/artifacts/process_if_v1.joblib --trust-model --input my_processes.json --output my_results.jsonl
```

노트북을 다시 실행하려면 `code/`에서 `..\.analysis-venv\Scripts\python.exe -m process_anomaly_pipeline.execute_analysis`. 이 명령은 새 노트북의 실행 출력과 자체 생성 그림만 갱신한다. 학습/분석은 원본 전수 로딩과 거래 관계 집계로 메모리가 필요하다. score_batch_rows는 피처 행렬의 상한일 뿐 전체 입력/결과 메모리의 상한은 아니다. backend에는 원본 CSV를 전수 로드하지 않고 제한된 요청 단위로 Process를 전달한다.

입력은 일반 Process CSV/JSON/JSONL이다. SQL export 원본 학습 로더와 일반 CSV 추론 로더는 다르다. 결과 파일 저장은 이 CLI를 명시적으로 실행했을 때만 이루어지며 노트북에는 분석 결과 CSV 저장 셀을 두지 않았다.

## 1. 규칙을 무엇으로 판정하는가

| 규칙 | 판정/근거 | 주의할 의미 |
|---|---|---|
| `status_failed` | STATUS=F, 사용자 제공 상태 정의 | 명시적 처리 실패. 공격/모델 이상 정답과는 다름. H 자체는 자동 실패 처리하지 않음 |
| `tx_success_all_process_failed` | 부모 S + processCount만큼 고유 단계가 모두 관측 + 전부 F | 전체 단계 수집, 동일 재시도/허브, 상태 집계 의미 확인 필요 |
| `process_count_mismatch` | 완료 부모의 processCount ≠ 고유 Process ID 수 | 재시도 분리. 부분 배치에서는 판정하지 않음 |
| `success_with_error` | Process S이고 errorCount>0 | 성공의 의미에 오류 허용이 있는지 업무 확인 필요 |
| `count_conservation` | S/F/H/Z, 3개 건수가 존재하고 total ≠ success+error | 진행 중 건수는 제외. 건수 합계의 업무 의미 확인 필요 |
| `final_missing_end` | S/F/H/Z이고 endTime 결측 | 로그 마감/비동기 기록 시점 확인 필요 |
| `missing_sql_required` | transactionId/processId/processHubId/retryCount 결측 | `ESB.SQL`의 Process NOT NULL 4개. 다른 nullable 컬럼 결측은 이 규칙으로 잡지 않음 |
| `negative_duration` | end−start < −time_tolerance_ms | 기본 허용 오차 0 ms. 분산 시계 오차가 있으면 운영 합의로 조정 |
| `invalid_count_domain` | 건수/재시도가 음수 또는 비정수 | 건수 도메인의 비음수 정수 가정. Oracle NUMBER 자체는 소수를 금지하지 않음 |
| `statistical_tail` | 과거 학습 S 기준집단의 duration_ms/totalCount/retryCount P99 또는 P99.9를 엄격히 초과 | 드문 값 후보이지 확정 장애가 아님. 동률은 초과 아님 |
| `invalid_input` (추가 안전검사) | 파싱 실패/무한대/정의 외 상태/입력 배치의 복합 PK 충돌 | 조용히 0이나 정상으로 바꾸지 않음. 원본 DB의 장애인지 export/입력 오류인지는 별도 확인 |

기본 통계 기준은 Adapter별 P99(비결측 기준집단 ≥1,000), P99.9(≥10,000), 부족하면 전체 기준으로 fallback한다. 둘 다 부족하면 NA. 학습 후 기준을 동결하며 추론 배치에 따라 재계산하지 않는다. 최소 지지 수는 각각 꼬리에 약 10개 관측을 확보하려는 **초기 실험 가정**이지 신뢰도 보증이 아니다. **같은 기준집단**의 P99.9 초과는 P99 초과에 포함된다. Adapter P99/GLOBAL P99.9처럼 산정 집단이 다르면 이 포함 관계나 심각도 순서를 보장하지 않는다. 각 basis를 확인하고 서로 다른 기준의 후보를 합산하지 않는다. P99.9만 탐지하고 싶으면 `quantiles: [0.999]`로 설정한다.

각 규칙은 `True=후보`, `False=평가했으나 미충족`, `NA=조건/맥락 부족으로 판단 불가`. 미평가를 정상으로 세지 않는다. 최종 `anomaly_candidate`는 **규칙 OR 모델 후보**이며 `decision`은 대표 이유의 우선순위일 뿐 다른 근거를 지우지 않는다.

## 2. Process만 입력하는데 Transaction은 어떻게 연결하는가

IF 학습 샘플과 수치 피처는 **오직 Process**다. Transaction은 두 관계 규칙에 필요한 선택적 메타데이터일 뿐 Transaction 모델을 따로 학습하지 않는다.

연결 키: `(source_period, transactionId, processHubId, retryCount)`. source_period는 서로 다른 export 간 충돌 방지용이며 온라인 기본값은 `runtime`. 제공된 Transaction에도 같은 값을 사용한다. 중복 부모/다른 허브/다른 재시도는 추정으로 연결하지 않는다.

backend에서 단건 Process가 들어오면 즉시 행 단위 규칙과 IF를 적용한다. 해당 거래의 완료 후 모든 단계와 부모 상태/기대 단계 수를 수집했을 때 그 거래 단위로 다시 호출해서 관계 규칙을 평가한다. 기본 `complete_context=False`이며 단건/부분 배치에 True를 붙이면 안 된다. 행만 넣고 processCount나 부모 상태를 추측하지 않는다.

**한계:** Process가 아예 하나도 없는 거래는 Process 입력만으로 결과 행을 만들 수 없다. "실행돼야 하는데 로그/데이터가 전혀 없음"이나 오래 남은 N/P는 별도 거래 수집 완전성/SLA 모니터가 필요하다. 첫 버전 IF는 완료 S/F/H/Z + 유효 시작/종료 시각을 대상으로 한다. N/P 및 무효 입력은 점수가 없고 `not_assessed` 또는 규칙 결과를 반환한다. `anomaly_candidate=False`만 보고 정상으로 해석하지 말고 `decision`, `model_eligible`, `unevaluated_rules`를 함께 확인한다.

## 3. 피처·학습·앙상블

피처: `log1p(duration_ms)`, `log1p(totalCount)`, `log1p(retryCount)`, 각 결측 표시, 시작 시간의 시간대/요일 sin·cos (총 10개). 건수 결측을 관측값 0으로 취급하지 않는다. 학습에서 fit한 중앙값 대체와 RobustScaler를 저장해 동일하게 사용한다. IF는 스케일링이 필수인 모델은 아니지만 파이프라인 변환을 고정했다.

STATUS, errorCount, successCount는 규칙에 사용하고 IF에는 넣지 않는다. 상태를 그대로 학습해서 F를 재발견하는 모델을 피하기 위한 설계다. ID 문자열 희소성/새 ID 여부는 IF 피처로 쓰지 않는다. Adapter는 라우팅에만 사용한다. 따라서 GLOBAL은 Adapter 종류를 직접 구분하지 않는 일반 수치/시간 문맥 모델이다. 이 버전은 메시지 크기, 호출 빈도, 거래 전체 지연을 학습하지 않는다.

1. 날짜 기준 과거 60% train, 다음 20% validation, 마지막 20% test. 실제 관측 날짜 수 기준이며 행 수 비율이 60/20/20이라는 뜻이 아니다.
2. 한 거래(source_period+transactionId)의 모든 Process/재시도는 같은 분할에 둔다. 경계를 넘는 거래/시간 없는 행은 학습·시간 평가에서 제외.
3. validation은 거래 키의 안정적 해시로 calibration과 threshold에 분리한다. 전자는 점수 단위를 맞추고 후자는 최종 임계값을 정한다. 두 집단에 같은 거래는 없다. 둘은 validation 안에서 날짜가 섞여 있지만 test는 그 이후다.
4. 학습 기준집단은 S + 완료/유효 시간 + 품질/정합성 규칙 미충족. 이 기준집단에서 통계 임계값을 먼저 고정하고 기본값으로 통계 꼬리도 제외하여 IF를 학습한다. **S=진짜 정상이라는 라벨 보장은 없음.** F/H/Z는 추론 가능하나 정상 후보 학습에는 넣지 않는다.
5. GLOBAL은 전체 정상 후보 중 최대 100,000행을 Adapter 균형 할당으로 뽑는다. 소량 Adapter는 가능한 수만큼, 잔여 표본은 큰 집단에 재할당. Adapter 모델은 해당 집단에서 균등 표본추출.

원시 점수 `raw = -IsolationForest.score_samples(X)` (클수록 드묾). calibration에서 모델별 `center=median(raw)`, `spread=P95(raw)-median(raw)`; 극도로 작으면 std/작은 양수로 보호한다. `normalized=(raw-center)/spread`. 0≈중앙값, 1≈P95이며 **0~1 확률이 아니다**.

준비된 Adapter: `ensemble=0.5×GLOBAL_normalized + 0.5×Adapter_normalized`. 새/부족/누락 Adapter: GLOBAL_normalized만 사용. Adapter 경로는 해당 Adapter의 threshold 기준집단 ensemble P99로 고정하고, GLOBAL-only 경로는 **전체 Adapter의 threshold 기준집단** GLOBAL 점수 P99로 고정한다. GLOBAL 그림의 fallback 행 분포와 GLOBAL 판정선 산정 집단은 서로 다르다. `ensemble_score > model_threshold`일 때 후보. `score_margin > 0`도 같은 뜻이다. 개별 raw score threshold는 참고이고 **최종 판정선은 경로별 ensemble threshold**다. 원시 점수 두 개를 그대로 평균하지 않는다.

## 4. 하이퍼파라미터 설정과 근거

| 설정 | 기본값 | 선택 이유/한계 |
|---|---:|---|
| n_estimators | 128 | 백엔드 비용과 랜덤 분산 사이의 초기 절충. 최적화 결과 아님 |
| max_samples | 512/트리 | 총 행 수와 무관하게 트리 비용 제한. 큰 표본 크기도 비교할 가치 있음 |
| max_features | 1.0 | 10개 피처를 모두 사용 |
| bootstrap | False | 트리 내 복원 추출 중복을 줄임 |
| n_jobs / random_state | 2 / 42 | 학습 병렬 작업 수 제한, 재현 가능한 초기 실험. score_samples 병렬화 설정과는 별개 |
| max_train_rows | 100,000/모델 | 대규모 로그의 학습 시간/메모리 제한. 전수 학습 아님 |
| max_calibration_rows | 100,000/모델 | 정규화 계산 상한; threshold 계산은 해당 기준집단 전수 |
| score_batch_rows | 50,000 | 추론 feature matrix 메모리 상한. 관계 규칙은 전체 거래 맥락에서 먼저 계산 |
| score_quantile | 0.99 | threshold 정상 후보 기준집단의 상위 약 1%를 검토하겠다는 정책. 정확도/실제 이상 비율을 뜻하지 않음 |
| global_weight | 0.5 | 두 모델을 동등 반영하는 시작점. 평가 전 최적 가중치라고 주장하지 않음 |
| Adapter 최소 train/calibration/threshold/days | 500 / 100 / 100 / 3 | 전문가와 독립 기준선을 최소 데이터로 지원하려는 가드레일. 꼬리 1%는 100행에서 약 1행이라 작고 불안정; 필요 시 상향 |
| 전체 최소 train/calibration/threshold | 200 / 200 / 200 | 불가능한 fit을 조기에 차단 |
| quantiles / tail exclusion | P99,P99.9 / True | 요청한 극단값 규칙. 정상 장시간 업무를 제거할 위험이 있어 False 실험과 비교 필요 |
| time_tolerance_ms | 0 | 역전 시각을 즉시 표시; 운영 시계 허용 오차는 별도 합의 |

`contamination="auto"`는 sklearn 생성자 설정이지만 본 구현은 `predict()`/`decision_function()`의 내부 offset 판정을 사용하지 않는다. 위 별도 threshold 집단에서 정한 경로 기준이 최종 정책이다. API 정의는 [scikit-learn IsolationForest 공식 문서](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)를 참고한다.

## 5. backend 연결 예시

```python
from process_anomaly_pipeline import ProcessAnomalyPipeline

# 프로세스/worker 시작 때 1회 로드. 요청마다 fit/load하지 않는다.
detector = ProcessAnomalyPipeline.load("process_if_v1.joblib", trusted=True)

records = [{
    "transactionId": "new-execution-1", "processId": "new-process-1",
    "processHubId": "HUB01", "retryCount": 0, "adapterType": "DB.READER",
    "status": "S", "startTime": "2026-10-08T10:00:00+09:00",
    "endTime": "2026-10-08T10:00:01+09:00",
    "totalCount": 10, "successCount": 10, "errorCount": 0,
}]
json_results = detector.predict_records(records)  # 1입력→1출력. 관계 규칙은 미평가.

# 완료 후, 해당 거래의 전체 Process와 부모 Transaction을 수집했을 때만:
# detector.predict_records(all_steps, transactions=parents, complete_context=True)
```

JSON 결과에 거래/Process/허브/재시도 식별자, 규칙명/실측값, 미평가 규칙, 모델 경로/fallback 이유, 점수·판정선·초과폭, 과거 수치 분포와의 비교를 반환한다. 대량 배치는 `.predict(DataFrame)`의 표를 사용하고, 상세 설명 `.explain(..., result=existing_results)`은 선택한 행에 사용한다. 전수 관계 결과를 먼저 계산하고 사례만으로 단계 수를 다시 세지 않는다.

NaN/NA는 JSON null이다. `feature_evidence`는 학습 분포의 P01/중앙값/P99와의 비교이며 **IF 특징 기여율/SHAP/인과 설명이 아니다**. 점수가 판정선을 넘은 것은 정확한 모델 판정 근거지만 장애의 원인 확인은 별도 검토가 필요하다.

pickle/joblib는 로드 시 임의 코드를 실행할 수 있으므로 신뢰하는 자체 생성 파일만 사용한다. `trusted=True`는 검증 장치가 아니라 호출자의 명시적 신뢰 선언이다. 환경도 일치해야 한다. [공식 모델 저장 가이드](https://scikit-learn.org/stable/model_persistence.html). 본 코드는 sklearn 버전 불일치를 거부하며 다른 의존성도 manifest에 맞춰 고정해야 한다.

## 6. 노트북 읽는 법과 평가의 한계

`post_analysis.ipynb`는 결과를 먼저 읽는 순서로 구성했다. **1절: 규칙 전후 개수·규칙별 추가 분리·기간/Adapter별 감소 → 2절: 전체 입력 전후 분포와 실제 고정 P99/P99.9 → 3절: 전체 IF 초과폭 분포와 경로별 판정선 → 4절: 중복 제거 결과·STATUS/일별 비교 → 5절: 실제 입력값과 판정 조건을 나란히 표시한 사례 → 6절: 결론**이다. 학습 설정·모델 지원·기간별 상세는 부록으로 옮겼다. 현재 추론은 규칙 후보도 삭제하지 않고 IF 가능한 행에 점수를 계산한다. 전후 표는 후보를 별도 분리했을 때 남는 수이지 detector 실행 방식을 바꾼 것이 아니다.

전후 표에서 규칙별 독립 탐지 수와 앞 규칙 중복을 뺀 추가 분리 수를 구분한다. 규칙 미탐지의 IF 미평가를 정상으로 합산하지 않는다. 학습 과정의 S 전용 선택은 추론 필터와 별도 표로 설명한다. 전체 입력·IF 초과폭 그림은 전수이고, 기존 경로별 threshold/test 비교와 시간 산점도는 그림의 표본 수를 표시한다. 모든 사례는 전수 결과를 유지한 채 설명하므로 사례 몇 행만으로 관계를 다시 집계하지 않는다. IF 피처 분포 비교는 원인 기여도가 아니다.

55셀 중 코드 27셀을 전부 실행해 표·13개 그림·규칙 사례 6행·IF-only 사례 2행을 저장했다. 결과는 재실행 없이 읽을 수 있다. 실제 IF-only 사례에는 DB.WRITER 처리시간 0.05초·처리건수 3건처럼 과거 P01(2.036초·10,000건)보다 작은 값도 있다. 드문 패턴이 항상 지연/대량 처리를 뜻하지 않으며 정상 소량 업무인지 장애인지 확인이 필요하다.

분포는 **채워진 일반 막대**로 표시한다. 입력 피처는 기간별 칸, 처리시간·IF 점수는 학습/검증과 미래 test를 좌우 칸으로 분리해 겹치지 않는다. 시간·건수는 실제 단위 구간이며 구간 폭이 같지 않으므로 밀도가 아닌 구간별 비중이다. IF 점수 히스토그램의 빨간 점선/옅은 붉은 영역은 실제 판정선/후보 영역이다. 분포·상태·규칙·일별 비교에는 log/symlog 축을 쓰지 않는다. 날짜와 점수의 관계를 보는 산점도만 별도로 유지했다. 학습 피처의 log1p 변환 자체는 변경하지 않았다.

**이상의 정답 라벨이 없어 정확도·재현율·오탐률은 미측정이다.** STATUS 비교는 업무 상태별 모델 반응을 보는 것이지 정답 평가가 아니다. 기간 드리프트, Adapter 내 서로 다른 업무 혼합, 부족한 전문가 판정선 지원, S에 잠재한 이상, 꼬리 제거 영향을 검토해야 한다. 후보 리뷰와 담당자 확인 라벨로 가중치·score_quantile·학습 기준을 검증한다. 미지 Adapter의 GLOBAL fallback은 처리 지속 방법이지 정확도 보장이 아니다.

입력의 무효값을 유지하고 미평가를 구분하며, 전수 통계와 그림 표본을 분리하여 수집 누락이나 표본 편향을 정상으로 오해하지 않도록 했다.

### 이번 실데이터 실행 요약

2026-10-08 실행, 원본 Process 3,714,199행 / Adapter 24개. GLOBAL과 DB.READER·DB.WRITER·SERVICE.EXECUTOR 전문가 3개를 학습했다. GLOBAL의 정상 후보 학습 집단은 2,582,379행이고 그중 균형 표본 100,000행을 실제 fit에 사용했다.

**Adapter가 24개인데 모델은 왜 3개인가?** 원본 총량이 아니라 정상 후보의 과거 학습/독립 검증 지원을 모두 요구한 결과다. 최소 500/100/100행·학습 3일은 이번 구현의 초기 가드레일이지 IF 알고리즘의 최소 요구량이나 검증된 최적값이 아니다.

| Adapter | 정상 후보 학습 | 점수 정규화 검증 | 판정선 검증 | 학습 일수 | 결과 |
|---|---:|---:|---:|---:|---|
| DB.READER | 1,266,772 | 72,653 | 72,480 | 28 | 생성 |
| DB.WRITER | 1,266,649 | 72,575 | 72,404 | 25 | 생성 |
| SERVICE.EXECUTOR | 2,621 | 35,846 | 35,973 | 19 | 생성 |
| FILE.READER | 24,151 | 45 | 45 | 14 | 검증 두 집단 부족 |
| FILE.WRITER | 22,136 | 35 | 41 | 10 | 검증 두 집단 부족 |
| DB.EXECUTOR | 2 | 2,285 | 2,188 | 2 | 과거 학습 행/날짜 부족 |

FILE 계열은 학습 자체가 불가능한 것이 아니다. 공통 시간 분할에서 후속 정상 후보가 적어 별도 점수 정규화와 판정선을 지원하지 못했다. 부족한 Adapter는 GLOBAL로 처리한다. 모델을 늘리려면 미래 test를 섞거나 기준을 임의로 낮추기보다 추가 수집 또는 Adapter별 과거 시간 분할을 별도 실험으로 비교해야 한다. 노트북에는 전체 24개 Adapter의 지원 수·제외 이유를 함께 표시한다. 시각화 변경으로 모델·가중치·판정 기준을 바꾸지 않았다.

미래 test 111,549행 중 IF 평가 111,469행, IF 후보 485행(0.435%). 같은 test에서 F는 74,386행이었다. 즉 **명시적 실패와 수치 패턴의 희귀성은 다르다**. IF가 F 전체를 재발견해야 한다는 가정으로 평가하지 않으며 F는 규칙으로 별도 처리한다.

전체 기간 규칙 후보 167,310행, IF 후보 7,110행, 중복 제거 OR 후보 171,147행. 단계 수 불일치는 완료 부모/완전 수집 선언 조건에서 고유 거래 시도 4,876건(연관 Process 5,479행), Tx S/기대 Process 전부 F는 1건이었다. 조건을 바꾸면 분모·건수도 바뀐다. SQL 필수 결측·역전 시각·음수/소수 건수·입력 오류는 이 원본에서는 0건이었다. 0건이라고 미래에도 발생하지 않는다는 뜻은 아니다.

새 파이프라인/시각화/보고서 테스트 54개(막대·일반 축·구간 경계·미평가·지원 수·기간별 피크 잘림·전후 합계·중복 제거·실제 판정선·사례 근거·큰 건수 표기 검증 포함)를 통과했다. 이전 실행에서는 기존 분석 모듈 테스트 32개와 backend형 JSON 호출도 확인했다. 보고서의 새 그림은 `artifacts/figures/post_analysis_v2/`에 추출한다. 기존 그림은 보존하며 원본 데이터·저장 모델·가중치·최소 지원 조건·threshold는 변경하지 않는다.
