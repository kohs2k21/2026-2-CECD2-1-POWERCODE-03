Created: 2026-10-21T16:05:00+09:00
Updated: 2026-10-21T16:40:00+09:00
Author: Claude Code (sr_prototype_builder)
Status: prototype — 메커니즘 검증 완료, 실데이터 미검증, 운영 채택 아님

# PROCESS 행 단위 Soft Rule 프로토타입 — Issue #75

사용자 10/21 지시 "PROCESS 단위로 쓸 수 있는 Soft Rule을 몇 개만 조사하고, 돌려보고, 프로토타입으로"의 결과를 scratchpad에서 저장소로 이식한 것(Issue #75). 연구 전용: `server/`는 이 폴더를 import하지 않으며, 운영 반영은 별도 작업이다.

- 입력 단위: **PROCESS 행 1개 + 같은 `adapter_type` 그룹의 학습 기간(train) 기준선**. 시간창 SR 24종(`research/evaluation/trend_sr_comparison/methods.py`, 1분 집계 입력)과 입력 단위가 다르다.
- 학습 fit: train 분할의 **status=S + 연구 reference 규칙 통과 행만**(`train-success-only-v1`에 맞춤). S는 정상 정답 보장 아님.
- 인터페이스: `fit(train_df, params) -> fitted(JSON)` / `score(df, fitted) -> flag·score·reason·basis·fallback_reason` / `score_row(key, row_dict, fitted) -> dict`.
- 카탈로그(Issue #74 SR 카탈로그 연결): 식별자 `sr:<key>`, 항목별 `parameterSchema`·`defaults`·`defaultsSource`·`inputRequirements`·`fitStatistics`·`limitations`, 공통 `reasonCodes`·`reasonSuffix`·`basisValues`·`fallbackReasonCodes` → `results/catalog.json`(`methods.catalog()` 출력).
- 의존: **numpy·pandas만**. scikit-learn·scipy는 방법 코드에서 미사용(결정 사항 D7).

## 1. 조사 요약 — 후보 6종

| key | 판정식(행 1개) | fit 통계(train only) | 파라미터(기본값) | 기본값 근거 | 한계 |
|---|---|---|---|---|---|
| `robust_z` | z = 0.6745·(x − med_g)/MAD_g, z > k | 그룹별 n·median·MAD·meanAD(+global) | field=duration_ms, k=3.5, side=upper, minGroupRows=30, fallback=none, madZeroPolicy=unjudged | k=3.5: Iglewicz & Hoaglin(1993) 수정 z 관례. fallback=none: Trainer `feature_fit` group_median 계약(미학습 그룹 → missing, 전체 대체 없음) | MAD=0(이산·상수 그룹) → 미판정; 상한만 봄; 작은 이동(+1~3건)은 k=3.5에 안 걸림 |
| `iqr_fence` | (x − Q3_g)/IQR_g > k | 그룹별 n·Q1·Q3·IQR(+global) | k=1.5(inner)/3.0(far-out), 나머지 robust_z와 동일 | Tukey(1977); 시간창 SR R03 variants {1.5, 3.0}과 동일 | IQR=0 → 미판정; 꼬리 두꺼운 duration은 1.5에서 과다 경보 가능 |
| `quantile_tail` | x > Q_q,g (train P99) | 그룹별 n·threshold(+global) | q=0.99, minGroupRows=1000, fallback=global | 연구 `process_anomaly_pipeline.QuantileBook`(p99, min_p99_support 1000, GLOBAL fallback, 값≥0) 그대로 | 설계상 정상 행의 ~1%를 경보(오탐 예산); 소표본 어댑터는 global 또는 미판정; 이산 건수는 임계=최댓값이 되어 +1 이동 일부만 보임 |
| `centered_tail` | c = x − med_g; c > Q_q(pooled c) | 그룹별 median + pooled centered 분위·MAD | q=0.99, minGroupRows=30, minPooledRows=200 | centering = Trainer DSL `group_centered`; pooled 분위는 소표본 어댑터가 임계를 공유하게 하려는 **프로토타입 제안** | 그룹 간 편차 스케일이 다르면 큰 어댑터가 임계를 지배; 미학습 그룹 미판정 |
| `ratio_rule` | error/total > e_max 또는 retry/total > r_max | train error·retry 비율의 P99·max(진단), r_max 미지정 시 train P99로 대체 | errorRatioMax=0.0, retryRatioMax=null(→fit), fitQuantile=0.99, minTrainRows=200 | e_max=0: Trainer 합성 reference 조건(error_count==0)·연구 `success_with_error` 규칙. r_max는 제안 | total=0·결측 → 미판정; 한쪽 임계만 있으면 **부분 판정**(D); status=F 행과 HR 의미 중첩(HR/SR 경계 미결) |
| `count_conservation` | \|total − (success+error)\| > tol | 없음(train 위반 수만 진단 기록) | tolerance=0 | 연구 전용 계약(`rules.count_conservation`, 합성 lab P04) — 고객 장애 의미 미확인 | 학습 통계 없는 수식 규칙 → HR에 둘지 SR에 둘지 결정 필요 |

- 공통 입력 요건: Trainer raw 필드명 `adapter_type, status, total_count, success_count, error_count, retry_count` + 파생 `duration_ms`(end − start). 결측·그룹 없음·표본 부족은 전부 **미판정(flag=NA, reason 코드)** 로 남기고 성공 대체 없음.
- 출력 열: `flag`(boolean, NA=미판정) / `score`(float) / `reason` / `basis` / `fallback_reason`.
  - `reason` 코드(16종): `above_threshold / within_threshold / value_missing / value_negative / group_unseen / group_missing / group_support_insufficient / scale_zero / pooled_support_insufficient / threshold_unavailable / total_zero_or_missing / error_ratio_exceeded / retry_ratio_exceeded / error_and_retry_ratio_exceeded / conservation_violated / conserved`.
  - `ratio_rule` 부분 판정 접미사: `<reason>;<error|retry>_unjudged:<threshold_unavailable|value_missing>` (예: `error_ratio_exceeded;retry_unjudged:threshold_unavailable`).
  - `basis`: `group / global / group+pooled / formula / unavailable`; ratio_rule은 `error:<parameter|train_quantile_<q>|unavailable>,retry:<same>`.
  - `fallback_reason`: `basis=global`일 때만 채움 — 그룹을 못 쓴 사유(`group_unseen / group_missing / group_support_insufficient`). 그 외 None. 판정 결과(`above/within_threshold`)와 fallback 사유를 함께 보존하기 위한 열.

## 2. 파일

| 경로 | 내용 |
|---|---|
| `research_imports.py` | 저장소 연구 코드를 **복사 없이** import하는 설정(§2.1) |
| `sr_prototype/methods.py` | 방법 6종 + 레지스트리 `METHODS` + `catalog()`·`fit()`·`score()`·`score_row()` + 출력 어휘 상수 |
| `sr_prototype/data.py` | 기본 코호트(lab 테스트 fixture 원문 복사)·시간순 분할·S-only reference·주입 호출 |
| `sr_prototype/evaluate.py` | 행 단위 지표(연구 lab 규약: 미판정 분모 포함, 라벨 없는 행은 정상 아님, FPR=null) |
| `run.py` | 한 번에 재실행 |
| `tests/test_methods.py` | 인터페이스·미판정 경로·부분 판정·fallback 사유 14개 시험 |
| `results/` | 아래 결과 표 원본(csv·json·`run_stdout.txt`); Git 포함(작은 파일만) |

### 2.1 연구 코드 재사용 방식(vendor 복사본 없음)

| 재사용 모듈 | 용도 | 로드 방식 |
|---|---|---|
| `research/anomaly_detection/code/process_anomaly_pipeline/{config,data,rules,quantiles}.py` | `prepare`, 연구 reference 규칙, QuantileBook 기본값 근거 | 원본 파일 그대로. 단 패키지 `__init__.py`가 sklearn IF `pipeline.py`를 import하므로, `research_imports.setup()`이 `sys.modules`에 `__path__`만 가진 **shim 패키지**를 등록해 `__init__`을 우회(원문 미수정) |
| `research/evaluation/process_synthetic_lab/{config,data,scenarios}.py` | `ExperimentConfig`, `row_ids`·`reference_mask`, P01~P04 주입기·truth | 원본 패키지 그대로 import. `__init__`→`trainer_lab`(sys.path 설정)·`data.py`→고객 CSV 로더 **함수 import만** 발생, 호출 없음 → 추가 shim 불필요(scratchpad의 `data.py` 발췌 shim은 폐기) |
| `research/evaluation/process_synthetic_lab/evaluate.py` | 채점 규약 참조(미판정 분모·FPR null) | import 안 함; `sr_prototype/evaluate.py`가 규약만 따름 |

- 재사용 기준 commit: origin/dev `872f540`(`run_manifest.json`의 `originCommit`; `sourceCommit`은 실행 시 `git rev-parse HEAD`).
- `research/evaluation/process_synthetic_lab/tests/test_core.py::fixture`는 `sr_prototype/data.py::fixture`로 **원문 복사**(테스트 모듈 import 회피). 복사 출처는 `data.FIXTURE_SOURCE`.

## 3. 데이터 — 무엇을 썼고 무엇을 못 썼나(고객 데이터 없음)

- `research/evaluation/feature_rule_design_v0_2/feature_inputs/*.json`: **LLM 피처 발굴 프롬프트의 스키마 프로파일**(테이블·열 통계·단위 증거)이며 PROCESS 행 데이터도 이상 라벨도 없음 → SR 평가 입력으로 **사용 불가**.
- `research/evaluation/process_synthetic_lab/`(Issue #53): 생성기(`scenarios.py`, P01~P04 주입 + truth)·채점 규약(`evaluate.py`)은 Git에 있으나 **기본 코호트는 고객 PROCESS/TRANSACTION CSV**(Git 제외, 이 작업에서 접근 금지).
- 따라서 기본 코호트는 Git에 있는 유일한 행 단위 PROCESS 자료인 **lab 테스트 fixture**(지어낸 행)를 사용: n=3000, adapter A/B 교대, status 전부 S, duration = 100 + i%19 ms, total=success = 10 + i%7, error=retry=0, 거래당 PROCESS 1개.
  - 시간순 70/15/15 분할 → train 2100 / threshold 450 / test 450. train reference 2100행(S 2100, 연구 reference 규칙 전부 통과).
  - **한계**: 분포가 극도로 규칙적(A·B 동일, test=train 분포)이라 오탐 0·지연 탐지 100%는 **자료의 성질**이지 방법의 성능이 아니다. 방법 간 변별력 비교 불가.
- 주입: 저장소 `scenarios.generate` 그대로. P02 개별 지연(train 어댑터 median×(배수−1), 배수 1.5/2/3), P03 연속 지연, P04 totalCount +1/+2/+3, seed 11/23/47, 시도당 10행 → 시나리오당 9시도·90행(독립 표본 90개 아님).
  - **P01**은 lab 기본값에서 미지원(실패 의미 미확인). 이 프로토타입은 `failure_status='F'`에 증거 문자열 "PROTOTYPE FIXTURE ONLY…"를 넣어 **fixture 한정으로 켬**. 고객 실패 의미 확인이 아니며, lab의 P01 보류 결론을 바꾸지 않는다.
- 라벨: 주입 행 label=1(통제된 변형, 실제 장애 정답 아님). 그 외 행은 **라벨 없음** → 오탐률 미산출, `unlabeled_alert_rate`(경보 건수/라벨 없는 행)만 표기.

## 4. 결과 (`results/run_stdout.txt`, 2026-10-21 저장소 위치에서 재실행, 14.6초)

이식 후 재실행 결과는 scratchpad 실행과 **표 7개 파일 바이트 동일**(`trials.csv`, `summary_by_instance_scenario.csv`, `detection_rate_pivot.csv`, `control_unmodified_test.csv`, `control_reasons.json`, `generation_trials.csv`, `fitted_params.json`). 달라진 것은 `catalog.json`(출력 어휘 메타·ratio_rule 한계 문구 추가), `run_manifest.json`(issue·sourceCommit·researchImports·sklearn/scipy null), `timing.csv`(벽시계)뿐. 권고 1(부분 판정)은 fixture에서 retry 임계가 train P99로 확보되므로 결과에 영향 없음.

### 4.1 탐지율 — 방법 인스턴스 × 시나리오 (severity 3 × seed 3 합산, 주입 90행 기준, 미판정 0)

| instance | P01 실패(F) | P02 개별 지연 | P03 연속 지연 | P04 건수 보존 위반 |
|---|---|---|---|---|
| robust_z[duration_ms] | 0.000 | 1.000 | 1.000 | 0.000 |
| robust_z[total_count] | 0.000 | 0.000 | 0.000 | 0.000 |
| robust_z[total_count,meanad] | 0.000 | 0.000 | 0.000 | 0.000 |
| iqr_fence[duration_ms] | 0.000 | 1.000 | 1.000 | 0.000 |
| iqr_fence[duration_ms,k=3] | 0.000 | 1.000 | 1.000 | 0.000 |
| iqr_fence[total_count] | 0.000 | 0.000 | 0.000 | 0.000 |
| quantile_tail[duration_ms] | 0.000 | 1.000 | 1.000 | 0.000 |
| quantile_tail[duration_ms,min30,nofallback] | 0.000 | 1.000 | 1.000 | 0.000 |
| quantile_tail[total_count] | 0.000 | 0.000 | 0.000 | 0.367 |
| centered_tail[duration_ms] | 0.000 | 1.000 | 1.000 | 0.000 |
| centered_tail[total_count] | 0.000 | 0.000 | 0.000 | 0.367 |
| ratio_rule | 1.000 | 0.000 | 0.000 | 0.000 |
| count_conservation | 0.000 | 0.000 | 0.000 | 1.000 |

- 모든 인스턴스에서 `baseline_alert_anomaly_rows`=0 → 탐지된 행은 전부 **주입 후 신규 탐지**(주입 전부터 경보였던 행 없음).
- 읽는 법: 지연(P02·P03)은 duration 계열 4종이 전부 잡고 건수 계열은 못 잡으며, P04(건수)는 수식 규칙만 전부 잡는다. 즉 각 방법은 **자기 입력 필드의 변형만** 본다. 이것이 "SR 여러 개를 동시에 켜는" 구조가 필요한 직접 근거.
- P04 severity별(quantile_tail[total_count]·centered_tail[total_count] 동일): +1 → 7/30, +2 → 12/30, +3 → 14/30. train P99(=최댓값 16)·pooled P99(=3)을 넘는 행만 잡힘 → **이산 건수에 분위 임계는 큰 값 근처 행만 본다**.
- robust_z[total_count]: median 13·MAD 2 → +3이라도 z=(19−13)/(2·1.4826)=2.0 < 3.5. iqr_fence[total_count]: (19−15)/4=1.0 < 1.5. 작은 정수 이동은 k 기본값으로 안 잡힘(기본값 조정 여부는 결정 사항 D1).
- P01(status F·error>0·success 감소): duration·total 불변이라 통계 SR 전부 0, ratio_rule만 90/90. 상태 F는 HR 영역이므로 SR이 못 잡는 것이 결함은 아님.

### 4.2 미변형 test 450행 (라벨 없음, C00 유사)

| instance | rows | judged | unjudged | unlabeled_alert_rows | unlabeled_alert_rate |
|---|---|---|---|---|---|
| 13개 인스턴스 모두 | 450 | 450 | 0 | 0 | 0.000 |

- 주입 시도 36개의 라벨 없는 행(3960행/시나리오)도 경보 0. **오탐률 0이라는 뜻 아님** — fixture의 test 분포가 train과 동일해서 생기는 결과. quantile_tail은 설계상 실데이터에서 ~1% 경보가 나와야 정상.

### 4.3 fit 통계 요약(`results/fitted_params.json`)

- duration: A·B 모두 median 109, MAD 5, Q1/Q3 104/114, P99 118; centered pooled P99 9.
- total_count: median 13, MAD 2, Q1/Q3 11/15, P99 16; centered pooled P99 3.
- ratio: train error·retry 비율 P99=max=0 → retryRatioMax 0.0(train P99 대체, basis `train_quantile_0.99`). count_conservation train 위반 0.

### 4.4 실행 시간(`results/timing.csv`, Windows·Python 3.11, 순수 pandas; 벽시계라 실행마다 다름)

| instance | fit 2100행 ms | 일괄 450행 ms | 행 1개 평균 ms | 행 1개 p99 ms |
|---|---|---|---|---|
| robust_z[duration_ms] | 1.66 | 2.17 | 2.16 | 3.27 |
| iqr_fence[duration_ms] | 2.43 | 2.33 | 2.20 | 3.54 |
| quantile_tail[duration_ms] | 1.59 | 1.97 | 2.06 | 3.06 |
| centered_tail[duration_ms] | 2.28 | 1.84 | 1.90 | 2.84 |
| ratio_rule | 1.16 | 4.40 | 3.42 | 4.91 |
| count_conservation | 0.76 | 1.15 | 1.46 | 2.06 |

- 행 1개 지연 ~2 ms는 1행 DataFrame 생성 오버헤드가 대부분. Detector에 넣을 땐 dict 기반 순수 산술로 바꾸면 더 내려가나 미측정.

### 4.5 단위 시험

`tests/test_methods.py` 14개 통과: 카탈로그 완전성(+reason 어휘에 `error_and_retry_ratio_exceeded` 포함·`ok` 미노출), 미지 파라미터 거부, **fit 결과 == 전달 프레임 수동 계산(median/MAD/meanAD/Q1/Q3/P99, 2차 fit에 오염 없음)**, 행/일괄 결과 일치(fallback_reason 포함), 출력 reason 어휘 검사, 미학습 어댑터 미판정↔global fallback, **fallback_reason 보존(unseen/missing/insufficient)**, 표본 부족 미판정, MAD=0 미판정, 상한/양측, centered 임계, ratio total=0 미판정·동시 초과, **ratio 부분 판정**(retry 임계 미확보·양측 미확보·값 결측), 보존식.

## 5. 한계

1. 평가 자료가 지어낸 fixture라 **수치는 메커니즘 검증(생성→fit→판정→채점 경로가 맞게 도는지)** 이상을 말하지 않는다. 탐지율 1.000·경보 0은 실데이터 성능 추정치가 아니다.
2. 정상 라벨 없음 → 오탐률(FPR) 미산출(연구 lab 규약 유지).
3. 실데이터(고객 PROCESS CSV) 미검증. P01은 fixture 한정으로 켰을 뿐 고객 실패 의미 미확인.
4. 시간순 분할은 fixture 특성(거래당 PROCESS 1개)에 맞춘 단순 분할; lab의 거래 경계 purge·계보 검사는 고객 CSV 로더에 묶여 있어 재현 안 함.
5. `adapter_type` 외 그룹 키(interface, channel)는 미구현(`groupBy` enum에 adapter_type만).
6. Python: 지시된 3.12가 이 PC에 없어 **3.11.2**로 실행(numpy 2.4.6·pandas 3.0.6). 3.12 환경 미검증.
7. `research_imports`의 shim은 `process_anomaly_pipeline/__init__.py`가 바뀌어도 알아채지 못한다(서브모듈 원문만 로드). 원본 lab의 `__init__`·`data.py`는 그대로 import하므로 그쪽이 무거운 의존을 추가하면 이 프로토타입 import가 깨진다 — 그때 다시 shim 결정.

## 6. 다음 연구 과제

- 실데이터 평가: Trainer 세션이 `research/evaluation/process_synthetic_lab` 실행 환경(고객 CSV, 오프라인)에서 같은 방법을 돌려 어댑터별 경보율·P02/P03/P04 탐지율·global fallback 비율 측정. `methods.py`는 lab `PATHS`에 경로 하나로 끼울 수 있게 `score(df, fitted)` 형태를 맞춰 둠.
- 이산 건수 필드 전용 규칙(예: 그룹별 train 최댓값+여유, 또는 Poisson/음이항 꼬리 — 시간창 SR R11/R12의 행 단위 변형) 검토.
- 미학습·소표본 어댑터 정책: `fallback=none`(Trainer 계약) vs `global`(연구 QuantileBook) 중 운영 기본값 결정 근거 수집.
- 종합 판정은 Detector 담당(미정)이므로 여기서는 방법별 결과만 냄; k-of-n 등 조합은 미평가.
- 행 1개 판정 경로를 dict 산술로 재구현해 ms 이하 지연 확인.

## 7. 결정 사항

| # | 항목 | 현재 프로토타입 값 | 비고 |
|---|---|---|---|
| D1 | robust_z k / iqr_fence k / quantile q | 3.5 / 1.5 / 0.99 | 문헌·연구 관례값. 프로젝트 데이터로 조정한 적 없음 |
| D2 | 그룹 최소 표본·fallback | robust_z·iqr·centered 30 + none, quantile_tail 1000 + global | Trainer 계약(대체 없음)과 연구 QuantileBook(global)이 서로 다름. **`fallback=global`이면 `adapter_type` 결측(`group_missing`) 행도 global 통계로 판정**하고 `fallback_reason=group_missing`을 남긴다(`fallback=none`이면 미판정). 결측 행까지 global로 판정할지는 운영 결정 필요 |
| D3 | `count_conservation`·`ratio_rule`의 소속 | SR 카탈로그에 등재 | 학습 통계 없는 수식 규칙 → HR 목록으로 옮길지 |
| D4 | retryRatioMax 기본 | null → train P99 | retry_count가 실행 단위 필드라 total 대비 비율이 의미 있는지 미확인 |
| D (★) | `ratio_rule` **부분 판정 허용** | error·retry 중 한쪽 임계만 확보돼도 그쪽만으로 판정(flag 확정), 미확보 쪽은 `reason` 접미사 `;<side>_unjudged:<cause>`로 표기. 양쪽 모두 미확보면 `threshold_unavailable` 미판정 | reviewer 권고 반영(10/21). 한쪽만 본 판정을 "정상"으로 읽지 않도록 소비자는 접미사를 확인해야 함 |
| D5 | 미판정(flag=NA)의 Detector 표기 | reason 코드 그대로 | `not_ready`와 구분할지(판정 불가 사유 코드 2개 분리 제안과 맞물림) |
| D6 | 평가 세트 | fixture | 고객 CSV 기반 lab 실행 승인 여부 |
| D7 | **scikit-learn 미사용** | SR 방법 6종은 numpy/pandas 순수 함수 | 사용자 지시의 scikit-learn은 ML(IF 등) 대상이고 SR에는 학습 모델이 없다는 총괄 판단. 실행 환경도 numpy·pandas만 요구 |

## 8. 재실행

저장소 루트(워크트리)에서. `.venv`는 Git 제외.

```powershell
py -3.11 -m venv .venv            # 3.12도 가능(미검증). numpy·pandas만 필수
.venv\Scripts\python.exe -m pip install numpy pandas
.venv\Scripts\python.exe research\evaluation\process_sr_prototype\run.py        # 기본: --rows 3000 --target-rows 10 --seeds 11 23 47 --out results
.venv\Scripts\python.exe -m unittest discover -s research\evaluation\process_sr_prototype\tests -v
```

- 산출(`results/`): `trials.csv`(시도×인스턴스), `summary_by_instance_scenario.csv`, `detection_rate_pivot.csv`, `control_unmodified_test.csv`, `control_reasons.json`, `generation_trials.csv`(주입 메타·거부 사유), `timing.csv`, `catalog.json`, `fitted_params.json`, `run_manifest.json`(버전·분할·설정·재사용 경로), `run_stdout.txt`(`run.py > results/run_stdout.txt`로 저장).
- 결정적 실행(seed 고정, fixture 결정적) → 같은 환경에서 재실행 시 `timing.csv`·manifest의 시간/commit 외 표는 동일해야 함.
- 운영 코드(`server/`)에서 이 폴더를 import하지 않는다(`research/` 런타임 import 금지 규칙).
