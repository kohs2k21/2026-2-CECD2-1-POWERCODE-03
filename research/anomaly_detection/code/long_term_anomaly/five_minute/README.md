# 선택한 n시간만 보는 장기 이상탐지

입력은 기존 Process 로그의 CSV·JSON·DataFrame이다. 컬럼을 바꾸거나 외부에서 5분 집계 파일을 만들 필요가 없다. `analysis_hours=1, 4, 8, 12, 24`로 분석할 구간 길이만 고른다. 내부에서 해당 구간을 5분 단위로 집계한다.

최신 결과는 **[실행 완료 노트북](result.ipynb)** 에서 확인한다. 배포 연결에는 `reports/long_term_anomaly/five_minute_localized/models_portable.joblib` 한 개를 사용한다. 크기는 2,336,908 bytes, 약 2.23 MiB다. 다른 실험 모델 파일은 최신 전달 대상이 아니다.

## 확인용 평가 결과

모델과 설정을 고정한 뒤 새로 생성한 576개 사례에서 평가했다. 각 구간의 여섯 시나리오별 지표 중앙값이다. 모든 개별 시나리오가 65% 이상이라는 뜻은 아니다.

| 선택 구간 | Accuracy | Precision | Recall | F1 | 최대 시나리오 오탐률 |
|---:|---:|---:|---:|---:|---:|
| 1시간 | 92.90% | 80.47% | 76.39% | 76.96% | 4.10% |
| 4시간 | 94.89% | 96.73% | 69.97% | 80.78% | 0.66% |
| 8시간 | 95.52% | 98.01% | 73.12% | 83.43% | 0.45% |
| 12시간 | 95.56% | 97.98% | 73.41% | 83.64% | 0.36% |
| 24시간 | 95.23% | 98.32% | 71.23% | 82.24% | 0.32% |

다섯 구간 모두 네 지표의 중앙값 각각 65% 이상, 전체 및 각 시나리오 오탐률 6% 이하를 충족했다. 평가 단위는 원래의 한 시간이며, 정답 확장이나 point adjustment를 하지 않았다. 시나리오별 값과 통합 지표는 노트북·`confirmation_results.json`에 따로 표시한다. 점진적 악화 초기와 일주기 패턴 변화는 여전히 약하다.

이것은 **실제 5분 관측 분포를 참고한 새 반합성 평가**다. 이전 시간당 1점 평가와 같은 데이터가 아니다. 기존 평가·모델·1시간 Recall 61.01% 결과는 보존했다. 새 수치를 이전 평가셋 대비 향상 폭으로 주장하지 않는다. 공식 ADBench나 실제 운영 장애 성능도 아니다.

## 무엇을 구현했나

- Process 원본을 받아 `[window_end − n시간, window_end)`의 시작 시각을 가진 행만 집계
- `adapterType + processId`별 정상 기준 통계 조회, ID 문자열 자체를 ML 피처로 인코딩하지 않음
- 5분별 실행 횟수, 완료 실행의 평균 로그 소요시간, 평균 로그 처리 건수 계산
- 정상 기준 대비 수준 차이·변동 폭·추세·앞뒤 차이·인접 상관으로 설명되지 않는 변화 산출
- 정상 데이터로 학습한 경험적 분포의 꼬리에서 현재 값이 얼마나 드문지 점수화
- 1시간 모드에는 집단의 정상 1시간 구간 672개를 사용한 조건부 보정 적용
- 개발 평가로 피처 가중치 선택, 1시간에서는 관련성이 낮았던 처리 건수·미세 추세 등을 경보 가중치 0으로 선택
- 긴 구간에서는 전체 구간의 이상 근거와 구간 내 해당 1시간의 근거가 모두 있을 때만 그 시간에 경보

정상 기준 통계는 미리 만들어 고정한다. 감시 구간 밖의 로그·이전 경보 상태를 이어받지 않으며, 감시 데이터를 정상 기준에 자동 편입하지 않는다. 현재 구간의 이상 여부를 판단하는 모델이지 미래의 실행 시간을 예측하는 모델이 아니다.

## 기준과 하이퍼파라미터

| 항목 | 설정·의미 |
|---|---|
| 선택 구간 | 1·4·8·12·24시간, 내부 12H개 집계점 |
| 정상 참고 | 검토한 28일, 8,064개 5분 위치 |
| 학습·정상 보정 | 정상 사례 64개·48개 |
| 개발·원래 native 평가 | 288개·576개, 정상·이상 쌍은 파티션 간 분리 |
| 새 확인용 표본 | 모델 고정 후 임의 seed를 한 번 생성해 576개 사례 생성, seed 재선택 없음 |
| 정상 시간대 기준 | 288개 일주기 위치의 중앙값, 위치별 관측 수 / (관측 수 + 4)로 축소 |
| 정상 변동 폭 | 중앙값·MAD 기반, 로그 단위 최소 폭 0.05 |
| 정상 추세 | 일별 중앙값의 양쪽 14일에서 방향·크기·유의한 변화가 일관될 때만 반영 |
| 인접 상관 | 관측이 이어진 정상 구간으로만 추정, 0~0.85로 제한 |
| 후보 피처 | 3개 채널 × 10개 통계, 학습 분포는 정상 데이터로만 적합 |
| 1시간 사용 피처 | 실행 횟수 평균 증가·감소, 소요시간 평균 증가·감소, 소요시간 변동성·에너지·잔차 에너지 |
| 1시간 실제 기준 | 점수 > 4.3188439191625445, 개발 정상 오탐 예산 4.5% |
| 긴 구간 실제 기준 | `min(구간 점수 − 해당 구간 기준, 시간 점수 − 1시간 기준) > 0` |
| 설정 선택 | 정상 보정·개발 정답으로 선택, 확인용 정답으로 변경하지 않음 |

순수한 무라벨 모델 선택까지 주장하지 않는다. **정상 분포 학습은 비지도 방식이고, 피처·기준 선택에는 정답이 있는 개발 평가를 사용했다.** 원래 native 평가에서 경보 번짐 문제를 확인해 구조를 수정했으므로 해당 평가는 더 이상 블라인드가 아니다. 그 후 구조와 수치를 고정해 별도의 새 확인용 표본을 평가했다. 정상 생성 방식은 같으므로 외부 실장애 검증을 대신하지 않는다.

## backend에서 사용

```python
from long_term_anomaly.five_minute.bank import load_bank
from long_term_anomaly.raw_windows import ProcessWindowService, CollectionCoverage

bank = load_bank("reports/long_term_anomaly/five_minute_localized/models_portable.joblib")
service = ProcessWindowService(bank)

# 별도 검토한 정상 Process 로그를 한 번 통계화하고 재사용한다.
references = service.prepare_reference(
    reviewed_normal_processes,
    end=normal_end,
    coverage=collector_coverage,
)
result = service.predict_processes(
    process_records,
    references=references,
    window_end=current_end,
    analysis_hours=4,
    coverage=collector_coverage,
)
decisions = result["decisions"]
```

`collector_coverage`는 실제 수집기 상태나 완전 수집이 확인된 구간으로 만든 `CollectionCoverage`다. CSV의 첫·마지막 시각으로 만들면 안 된다. 등록한 집단에 로그가 전혀 없어도 수집이 정상으로 확인되면 실행 0건을 판단한다. 수집 상태를 모르면 판단 불가이며, 소요시간 0을 만들지 않는다. 집단별 수집 상태가 다르면 `(adapterType, processId)`를 키로 하는 coverage 사전을 전달한다.

시각은 KST 기준이다. timezone 정보가 있으면 KST로 정규화한다. 완료 실행의 소요시간은 선택 구간 종료까지 확인된 값만 사용한다. 같은 SQL 기본키 `(transactionId, processId, retryCount)`의 중복은 자동 합산하지 않는다. 누락·잘못된 시작 시각, 미등록 집단 등은 명시적으로 오류를 내며 임의의 정상 통계를 만들지 않는다. 같은 Adapter·Process의 여러 Hub는 현재 합산하므로 Hub별 이질성이 큰 경우 별도 검토가 필요하다.

CLI는 `python -m long_term_anomaly.five_minute --help`로 확인한다. `--processes`와 `--reviewed-normal-processes`에는 기존 컬럼의 CSV/JSON을 그대로 전달한다. `--coverage`는 `source`와 `certified_intervals`를 담은 별도 운영 메타데이터 JSON이다. 출력 JSON은 `decisions`, `fine_bins`, `window_features`, `diagnostics`이며 판단 불가는 `null`로 보존한다.

## 파일과 검증

| 파일 | 역할 |
|---|---|
| `../raw_windows/aggregation.py`, `service.py` | 원본 Process 입력, 선택 구간 내부 집계·추론 연결 |
| `model.py`, `conditional.py` | 정상 시계열 통계·집단별 정상 창 보정 |
| `bank.py`, `localized.py` | 구간 선택, 실제 판정 및 시간별 경보 번짐 방지 |
| `benchmark.py`, `confirmation.py` | 실제 native 분포 기반 반합성 자료와 모델 고정 후 확인용 표본 |
| `run.py`, `refine.py`, `feature_study.py` | 정상 학습·개발 선택 과정의 보존된 실험 코드 |
| `serialization.py` | CLI 내부 모델의 수치 상태를 바꾸지 않고 별도 backend 프로세스에서 로드 가능한 모델로 직렬화 |
| `validate.py` | 두 평가집단의 70개 혼동행렬 재계산, 5,760개 공개 API 재생, 원본·모델·정답 해시 확인 |
| `report.py`, `notebook.py` | 최신 결과의 일반 막대그래프·점수 분포·설명 노트북 생성 |

노트북과 검증은 재학습 없이 읽을 수 있다. 재학습·평가 명령은 기존 평가나 파일을 덮어쓰지 않도록 되어 있다. 중간 실험은 최신 배포 모델이 아니다. `.joblib`은 신뢰할 수 있는 파일만 로드해야 한다.

원본 로그에는 실제 연속 28일 정상 참고 자료가 확보되지 않았다. 이번 결과는 모델 설계와 반합성 검증 완료를 의미한다. 실제 운영 연결 시 정상 참고 자료·수집 상태·새 집단 처리·기준 재검토 정책을 확보해야 한다.
