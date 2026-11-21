# 이상 탐지 연구 — 최신 모델과 새 데이터 실행

연결 이슈: [#80](https://github.com/kohs2k21/2026-2-CECD2-1-POWERCODE-03/issues/80).
최신 단기 모델은 **단일 IF·MiniBatch K-means·LOF 각 1개**다. Adapter 전문가 모델은 없고,
Adapter·Process별 정상 참고 통계와 최근 이력 피처를 사용한다. 장기 모델은 1·4·8·12·24시간을 지원한다.

## 여기서 시작

| 목적 | 파일 |
|---|---|
| 최신 성능·모델 한계 | [모델 카드](MODEL_CARD.md), [단기 결과](notebooks/latest_short_results.ipynb), [장기 결과](notebooks/latest_long_results.ipynb) |
| 저장 모델로 새 데이터 예측·평가 | [실행 안내](INTEGRATION.md), `code/research_workflow/` |
| 새 정상 데이터로 재학습 | [재학습·재현 범위](REPRODUCING.md), `configs/*_core.json` |
| 무결성·데이터 없는 실행 확인 | `python inspect_latest.py --trusted --smoke`, `python run_latest_tests.py` |
| 원래 피처·보정·벤치마크 설계 | `code/shared_model_v7/`, `code/evaluation_benchmark/`, `code/long_term_anomaly/` |
| 피처 중요도 | `code/shared_model_v7/feature_importance.ipynb`, `code/shared_model_v7/FEATURE_IMPORTANCE.md` |

Python 3.12에서 `python -m pip install -r requirements.txt`로 설치한다.
모든 명령의 작업 디렉터리는 이 폴더(`research/anomaly_detection`)다.

## 최신 모델 4개

```text
reports/shared_model_v7/release/handoff/
  if_shared.joblib
  kmeans_shared.joblib
  lof_shared.joblib
reports/long_term_anomaly/five_minute_localized/
  models_portable.joblib
```

네 파일 합계 약 15.72 MiB. [latest_inventory.json](latest_inventory.json)에 해시와 경로를 기록했다.
원래 모델 바이트·경보선은 변경하지 않았다. 신뢰하는 joblib만 로드해야 한다.

## 포함 범위와 데이터

- 활용 가능한 전처리·피처·하드룰·학습·평가·생성·진단 코드, 테스트, 연구 노트북과 집계 결과 보존
- 이전 버전 모듈도 포함: 최신 joblib이 이전 모듈의 클래스를 참조하므로 임의 삭제 금지
- 최신 결과 노트북 2개는 집계 JSON·히스토그램만으로 재실행 가능
- 원래 노트북의 저장 출력은 열람 가능하지만 모든 과거 실험을 데이터 없이 재실행할 수 있다는 뜻은 아님
- 기간별 원본, 벤치마크 CSV·정답, 새 학습 캐시, 중간 모델 바이너리·행별 예측 NPZ는 추가하지 않음
- #38에서 이미 공유한 모델·참조 아카이브·캐시는 삭제하지 않음. 최신 추론·새 데이터 학습의 필수 파일이 아님

사용자가 새 Process 관측과 검토한 정상 참고 자료를 제공해야 한다. 평가 지표에는 정상/이상 정답도 필요하다.

## 주요 한계

- 단기 결과는 개발 벤치마크를 반복 사용한 결과이며 외부 블라인드 성능이 아님
- 정상 통계·시계열 피처·개발 단계 경보 보정까지 포함한 파이프라인 결과임
- STATUS=S는 정상 후보 조건일 뿐 정상 정답을 보장하지 않음
- 입력 중단에는 수집이 정상인 빈 분을 생성하는 worker·과거 상태가 필요함. CSV 범위만 보고 빈 분을 만들면 안 됨
- 장기 모델은 별도 검토한 28일 정상 참고와 수집 확인이 필요함. 미래 예측·실시간 탐지 모델이 아님
- 새 데이터 baseline 재학습은 기존 v7의 개발 정답 기반 선택 전체를 재현하지 않으며 기존 성능을 승계하지 않음
- ADBench 결과는 버전을 명시한 과거 실험 기록이며 최신 v7의 새 평가로 간주하지 않음
- `server/`·운영 모델·배포 설정 변경 없음. 기존 서비스가 자동으로 새 모델로 교체되지 않음

이전 #38 자료는 [이전 공유 기록](LEGACY_ISSUE38.md), [이전 모델 카드](LEGACY_MODEL_CARD_ISSUE38.md),
[이전 연결 안내](LEGACY_INTEGRATION_ISSUE38.md)를 참고한다.
