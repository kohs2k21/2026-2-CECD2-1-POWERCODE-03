# 장기 시계열 이상탐지 (별도 오프라인 모델)

## 최신: 원본 Process 입력과 1·4·8·12·24시간 선택

현재 사용할 모델과 설명은 [five_minute/README.md](five_minute/README.md), 실행 완료 결과는 [five_minute/result.ipynb](five_minute/result.ipynb)에 있다. 기존 Process 컬럼을 그대로 받아 내부에서만 5분 집계하며, 선택한 구간 밖의 감시 로그를 사용하지 않는다. 최신 전달 모델은 `reports/long_term_anomaly/five_minute_localized/models_portable.joblib`이다.

모델 고정 후 새로 생성한 확인용 표본에서 다섯 구간 모두 지표별 시나리오 중앙값 65% 이상·전체 및 시나리오별 오탐률 6% 이하를 충족했다. 내부 해상도가 달라진 새 반합성 평가이므로 이전 시간당 1점 결과와 직접적인 향상 폭 비교는 하지 않는다. 아래 내용과 기존 루트 노트북은 초기 시간당 집계 실험 기록으로 보존한다.

기존 1분 IF·K-means·LOF 파이프라인을 수정하지 않은 별도 실험이다. 결과는 [result.ipynb](result.ipynb), 실제 수치 요약은 `reports/long_term_anomaly/summary.md`에서 확인한다.

## 무엇을 만드는가

같은 `adapterType + processId`의 시간당 집계를 받아 **정상 참고 28일과 이후 14일**을 비교한다. Process 한 행을 즉시 판정하는 모델이 아니다. 기본 일주기 후보를 STL로 분리하고, PELT로 여러 날 지속되는 평균·변동성의 변화를 구간화한다. STL + 시간창 모델과 시간대 단순 기준도 동일 데이터에서 비교한다.

원본 세 기간은 달력상 길지만, 한 집단의 정상 후보 최장 연속 관측은 159시간이다. 현재 원본만으로 수 주 연속 정상/장애의 실제 성능을 입증할 수 없다. 이에 따라 장기 신경망을 무리하게 학습하지 않고, 해석 가능한 통계 기반 비지도 방법을 먼저 검증한다. **실제 원본에 28일 연속 참고 구간이 확보되어 있다는 의미가 아니다.**

## 파일별 역할

| 파일 | 역할 |
|---|---|
| `audit.py` | 세 기간 원본을 내용 기반으로 식별하고 중복·시간·상태·연속성 점검, 시간당 집계 생성 |
| `protocol.json` | 모델 성능 확인 전에 정한 사례 수·이상 강도·기간·평가 방식 |
| `benchmark.py` | 원본 초기 정상 후보의 분포를 참고해 장기 반합성 평가셋 생성·해시 고정 |
| `models.py` | 정상 구간 STL 적합, 처리 건수 조건부 소요시간 보정, 시간창/PELT 근거 추출 |
| `persistent.py` | 구간 근거와 짧은 시간창 근거의 비중을 조절하는 장기 변화 점수 |
| `experiment.py` | 정상 학습·보정, 개발 데이터 튜닝, 모델 고정, 최종 평가 순서 강제 |
| `inference.py` | 향후 배치 worker용 검증된 DataFrame/CSV 추론 진입점 |
| `report.py` | 고정된 결과표·막대그래프·점수 분포·시계열 사례·탐지 근거·불확실성 |
| `result.ipynb` | 결과와 의미를 설명하는 읽기용 노트북 |
| `test_long_term.py` | 결측·입력 검증·저장/복원·지표 계산·정답 미사용·구간 캐시 동등성 테스트 |
| `validate.py` | 고정 해시·원본 보존·파티션 분리·독립 지표 재계산·파일 I/O 없는 추론 검증 |
| `certify_missingness_fix.py` | 최초 최종 입력 검사에서 발견한 연결 결측 처리 보완의 동일성 증명; 성능/기준 튜닝 아님 |
| `handoff.py` | 검증 후 선정 모델 한 개만 배치 worker용으로 내보내기 |
| `requirements.txt` | 재현용 의존성 |

## 데이터와 모델 위치

- 신규 평가셋: `data/evaluation_long_term/`; 기존 `data/evaluation*`의 다른 폴더 수정 없음
- 원본 점검·모델·결과: `reports/long_term_anomaly/`
- 모델 패키지: `reports/long_term_anomaly/models.joblib`
- worker 전달용 선정 모델 한 개: `reports/long_term_anomaly/handoff/long_term_model.joblib`
- 모델/기준 선택 근거: `selection.json`; 최종 점수 개봉 순서: `prediction_seal.json`
- 최종 수치: `test_results.json`, `test_metrics.csv`
- 근거 그림: `reports/long_term_anomaly/figures/`

`models.joblib`는 설정·정상 점수 통계·경보 기준을 저장한다. 각 입력 배치의 정상 참고 구간에서 STL 패턴을 적합하는 구조이므로, 대형 신경망 가중치 파일이 아니다. 모델 로드만으로 원본 CSV·정답 파일이 필요하지 않다. Python의 pickle/joblib은 코드를 실행할 수 있으므로 신뢰된 파일만 로드한다.

## 입력 계약

같은 집단의 배치를 `case_id`로 구분하며, 시간순으로 정확히 1시간씩 연속된 행이 필요하다. 서로 다른 Process를 하나의 시계열로 이어 붙이면 안 된다.

| 컬럼 | 정확한 의미 |
|---|---|
| `case_id` | 독립 분석 배치 식별자; 학습 피처 아님 |
| `timestamp` | 시간대가 일관된 시간순 1시간 간격 시각 |
| `log_requests` | `log(1 + 시간당 정상 후보 Process 실행 행 수)`; Transaction/API 요청 건수가 아님 |
| `log_duration_ms` | 시간당 `log(1 + Process 소요시간(ms))`의 평균; 산술평균 소요시간의 로그가 아님 |
| `log_items_per_process` | 시간당 `log(1 + totalCount)`의 평균; 실행 한 건의 처리 건수 조건 |
| `collection_observed` | 해당 시간의 수집 여부가 알려졌는지; False는 0건이 아니라 결측 |
| `latency_available` | 실행이 있어 소요시간을 관측할 수 있는지 |

원본 정상 후보는 `STATUS=S`, 유효한 시작/종료 시각·비음수 소요시간, 정수/비음수 건수·재시도, `totalCount=successCount`, `errorCount=0` 조건으로 뽑았다. **S나 이 조건들은 진짜 정상이라는 보증이 아니다.** 이 생성기는 원시 Process/Transaction CSV가 아니라 모델용 시간당 집계 CSV를 생성한다. 따라서 원시 SQL의 건수 컬럼 정수 제약을 집계 평균 값에 적용하거나 원시 하드 룰을 통과했다고 주장하지 않는다.

관측된 무실행은 `log_requests=0`, 소요시간/처리 건수는 결측, `latency_available=False`다. 수집 자체의 결측은 `collection_observed=False`, 모든 채널 결측이다. 최대 6시간의 짧은 결측은 중립 잔차/보간으로 처리하되 판정·지표에서는 제외한다. 정상 참고 구간에 긴 결측이 있으면 중단한다. 분석 구간의 긴 수집 결측은 앞뒤를 자동 분리해 시간창/PELT가 공백을 가로질러 계산되지 않게 한다. 실서비스에는 실제 수집 건강 지표가 추가로 필요하다.

## 학습과 평가 원칙

1. 모델 결과 확인 전 `protocol.json`과 입력/정답 해시 고정
2. 원본 첫 기간의 초기 60% 시각 범위만 생성 분포 추정에 사용
3. 학습 64개 정상 사례로 점수 항목의 정상 통계 학습
4. 별도 정상 보정 48개와 개발 288개로 설정·기준 선택
5. 최종 평가 576개에 모델/기준을 그대로 적용, 점수 해시 저장 후 정답 개봉
6. 시나리오별 시간 단위 Accuracy·Precision·Recall·F1과 오탐률 계산

각 사례는 1,008시간이다. 최종 평가의 각 시나리오는 주입 사례 48개 + 짝지은 정상 사례 48개다. 시간 수가 독립적인 현실 표본 수를 뜻하지 않으며, 불확실성은 짝 단위 bootstrap으로 별도 보고한다. 파티션 사이에 생성 사례·난수 시드·짝이 겹치지 않지만, 생성 가정 자체는 공유한다.

분포 추정에 충분한 초기 관측이 있던 참고 집단은 DB.READER 40개, DB.WRITER 40개, FILE.READER/FILE.WRITER 각 1개다. 이 결과를 SERVICE 등 모든 Adapter의 장기 장애 성능으로 일반화하면 안 된다.

목표는 네 지표의 **시나리오별 중앙값 각각 70% 이상**, 전체·각 시나리오 오탐률 6% 이하이다. 목표는 선정 장기 모델에 적용한다. 비교용 단순 기준까지 같은 성능을 보장하지 않는다. 목표 충족 여부는 실제 `test_results.json` 값을 보며, 결과를 맞추기 위해 최종 정답이나 이상 강도를 바꾸지 않는다. 원래 시간별 정답을 그대로 사용하며 point adjustment/정답 구간 확장/정답 기반 경보 전파를 사용하지 않는다.

정상 로그의 긴 꼬리와 자기상관 때문에 STL 적합 잔차의 MAD만으로 미래 변동 폭을 추정하면 과소평가될 수 있다. 이번 구현은 정상 시간대 패턴을 제거한 변동 폭을 사용하고, 최근 정상 수준을 평균 기반으로 추정한다. 처리 건수는 소요시간 보정용 공변량으로만 사용하며, 정상 대량 처리가 단독 경보 근거를 지배하지 않게 한다. 장기 구간 근거와 짧은 시간창 근거의 비중은 개발 데이터에서 조절한다. 모든 집단·시나리오에 같은 가중치와 경보 기준을 적용한다.

## 실행

프로젝트 루트 PowerShell 기준:

```powershell
$env:PYTHONPATH='code'
$env:PYTHONIOENCODING='utf-8'
./.analysis-venv/Scripts/python.exe -m pip install -r code/long_term_anomaly/requirements.txt
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.audit
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.benchmark
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.experiment tune
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.experiment evaluate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.report
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.validate
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.handoff
./.analysis-venv/Scripts/python.exe -m pytest code/long_term_anomaly/test_long_term.py -q
```

고정된 평가셋이 있으면 `benchmark`는 덮어쓰지 않고 중단한다. 최종 정답을 개봉한 뒤 `tune`도 중단한다. 이미 생성된 결과를 확인할 때는 노트북이나 보고서만 읽는다. 재평가 명령은 기존 결과를 출력할 뿐 기준을 바꾸지 않는다.

배치 추론:

```powershell
./.analysis-venv/Scripts/python.exe -m long_term_anomaly.inference --model reports/long_term_anomaly/models.joblib --input data/evaluation_long_term/test_hourly.csv.gz --output reports/long_term_anomaly/batch_predictions.csv
```

Python API:

```python
import joblib
from long_term_anomaly.inference import score_frame

package = joblib.load('reports/long_term_anomaly/models.joblib')
model = package['models'][package['primary']]
predictions = score_frame(model, hourly_dataframe)
```

`score`, `threshold`, `is_anomaly`, `assessable`, `reason`, `retrospective`를 반환한다. `reason`은 점수 항목과 채널이지 장애의 원인 확정이나 시나리오 분류가 아니다. PELT/중심 시간창은 분석 구간 내 뒤쪽 시간도 쓰므로, 결과를 실시간 경보 시점이나 조기 탐지 성능으로 해석하면 안 된다. 정상 참고 구간의 검토·주기적 기준 갱신은 운영 연결 시 별도 정책이 필요하다.

이번 경보 기준을 보정·검증한 분석 구간 길이는 336시간(14일)이다. 임의의 길이로 바꿔 운영하려면 해당 길이로 경보 기준과 성능을 다시 검증해야 한다.

## 논문·공식 문서와 사용 범위

- [Cleveland et al. (1990), STL: A Seasonal-Trend Decomposition Procedure Based on Loess — 공식 구현/문헌](https://www.statsmodels.org/stable/generated/statsmodels.tsa.seasonal.STL.html): 정상 패턴 분해; 이상 구간을 포함해 분해하면 추세로 흡수될 수 있어 참고 구간에만 적합
- [Killick, Fearnhead & Eckley (2012), Optimal detection of changepoints with a linear computational cost](https://arxiv.org/abs/1101.1438), [ruptures 공식 PELT 문서](https://centre-borelli.github.io/ruptures-docs/user-guide/detection/pelt/): 벌점 기반 오프라인 구간 분할; 탐지 기준은 별도 정상 보정
- [Han et al. (NeurIPS 2022), ADBench 공식 저장소](https://github.com/Minqi824/ADBench): 기본 표 형식 생성기/임의 분할을 장기 시간순 데이터로 오인하지 않기 위해 이번 생성에는 직접 사용하지 않음
- [Kim et al. (AAAI 2022), Towards a Rigorous Evaluation of Time-series Anomaly Detection](https://arxiv.org/abs/2109.05257): point adjustment 없이 시간 단위로 평가
- [Zeng et al. (AAAI 2023), DLinear 공식 구현](https://github.com/cure-lab/LTSF-Linear), [Nie et al. (ICLR 2023), PatchTST](https://arxiv.org/abs/2211.14730): 충분한 연속 장기 이력 확보 후 후속 후보; 이번에 구현·평가했다고 주장하지 않음
