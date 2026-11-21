# Process 단일 모델 개선 실험 v7

이 디렉터리는 IF·MiniBatch K-means·LOF 각각 **단일 전체 추정기**의 연구 코드다. Adapter 전문가 모델, 알고리즘 간 앙상블은 없다. Adapter·Process별 정상 비교 통계와 최근 이력은 유지한다.

## 완료된 결과

실행된 결과 노트북: `code/shared_model_v7/result.ipynb`. 원 벤치마크 1,927,972개 분 판정의 독립 감사 완료. 아래는 전체 합산 지표(%)다.

| 모델 | Accuracy | Precision | Recall | F1 | FPR |
|---|---:|---:|---:|---:|---:|
| IF | 94.007 | 90.326 | 73.001 | 80.745 | 1.626 |
| K-means | 93.663 | 92.932 | 68.383 | 78.790 | 1.081 |
| LOF | 93.809 | 89.597 | 72.441 | 80.111 | 1.749 |

- 15개 모델×시나리오의 최저 Recall **55.423%**, 최고 **95.299%**, 단순 평균 **72.041%**
- 시나리오별 최대 FPR **2.792%**, 모든 시나리오 Accuracy·Precision·F1은 v6 이상
- 관측 F1 최고: IF 개별 지연 **87.507%**, K-means 집단 지연 **92.882%**, LOF 점진적 악화 **80.826%**
- 통계 참조와 개발 정답으로 선택한 경보 보정을 포함한 파이프라인 결과. 순수 알고리즘만의 효과 또는 외부 블라인드 성능 아님

## 최신 확정 조건

- 각 모델 × 5개 시나리오 모두 Recall ≥55%
- 각 모델 × 각 시나리오 Accuracy·Precision·F1은 변경 없는 v6 이상
- 전체 합산 Accuracy·Precision·Recall·F1도 v6 이상
- **사용자 최신 요청: 각 시나리오 및 전체 FPR ≤6%**
- 기대 강점: IF 개별 지연, K-means 집단 지연, LOF 점진적 악화. 다른 모델을 고의로 떨어뜨리지 않고 관측된 F1·Precision·Recall로 비교

2026-10-19 이전의 ‘각 시나리오 FPR도 v6 이하’ 조건은 사용자 요청으로 완화되었다. 이전 평가·실패 기록은 보존하고 새 조건과 구분한다. 6%를 목표 오탐률로 맞추는 것은 아니며, 낮은 오탐을 우선한다.

## 핵심 변경과 해석

- `evidence_fusion.py`: 단일 학습 코어 + 정상 소요시간 이웃 + 과거 방향 요청 수 상태 신호 결합
- `pmf_filter.py`: 정상 시간대 요청 수 PMF와 현재까지의 관측으로 상태 신호 계산. 미래 로그/장애 종료 구간 비참조
- `adapter_calibration.py`, `switch_calibration.py`: 관측 신호·지원 이력·Adapter에 맞는 경보 기준표. 전문가 모델이 아니라 개발 단계에서 선택한 통계 보정
- K-means 유력 후보: 정상 피처의 최대·평균·결합 크기 12개 입력, OAS whitening, 8개 군집. v6의 최소 rank 19개 입력과 다른 군집 구조
- IF·LOF 유력 후보: 기존 학습 코어 유지, 조건부 정상 비교 및 개발 경보 기준 개선
- `burst_margin.py`, `gap_passthrough.py`: 별도로 실험한 보수적 운영점 정책. 최종 선택 여부는 `release/selection.json`의 실제 기록으로 확인

최종 점수 1 초과가 경보다. 점수는 확률이 아니다. **세 순수 알고리즘만의 성능이 아니라 통계·시계열 피처와 보정을 포함한 파이프라인 성능**이다. 추정기 fit은 정상 참조만 사용하지만, 개발 벤치마크 정답을 설정·피처·기준 선택에 반복 사용했으므로 블라인드 평가가 아니다.

## 실행과 파일

저장소 루트에서 가상환경을 활성화하고 `PYTHONPATH=code` 설정 후 실행한다.

```text
python -m shared_model_v7.evaluate_one --algorithm if --model <동결된 후보.joblib> --output reports/shared_model_v7/<새 시도>
python -m shared_model_v7.audit_attempt --output reports/shared_model_v7/<세 모델 평가가 모인 시도>
python -m shared_model_v7.release --attempt reports/shared_model_v7/<독립 감사 통과 시도>
python -m shared_model_v7.report
python -m shared_model_v7.finalize --visual-reviewed
python -m unittest shared_model_v7.test_v7 -q
```

- `evaluate_one.py`: 후보·경보선 동결 후 각 시나리오 5,000쌍 전체 평가. 자동 채택 없음
- `assemble_attempt.py`: 이미 완료된 세 알고리즘의 평가 결과를 해시 확인 후 모음. 점수·경보선 변경 없음, 새 독립 감사 필수
- `select_completed.py`: 모든 최신 지표 조건을 만족하는 전수 평가 후보 중 알고리즘별 목표 시나리오 F1을 독립 비교. 상대 알고리즘을 떨어뜨리는 선택 없음
- `acceptance.py`: 기본값은 이전의 엄격 조건이며, 실행기는 `protocol.py`의 최신 사용자 조건을 명시적으로 전달
- `audit_attempt.py`: sklearn 지표 독립 계산, raw Process 입력, 과거 prefix, 새 Adapter, 저장/로드, 런타임 연구 데이터 비참조 확인
- `release.py`: 모든 지표·역할 조건과 데이터 해시 검증 후 새 로컬 연구 전달물 생성. 이전 v6 및 완료된 v7 릴리스 덮어쓰기 금지
- `report.py`: 검증된 결과표, 일반 막대 그래프, 정상/이상 점수 분포·경보선, 피처·설정·해석을 실행된 `result.ipynb`로 생성
- `finalize.py`: 그림을 직접 검토한 뒤 export 점수·130개 테스트·노트북을 재검증하고 로컬 latest 기록. `--visual-reviewed`는 실제 그림 검토 이후에만 사용
- `research_diagnostics.ipynb`, `WORKLOG.md`: 미채택 실험과 한계 기록. 최신 모델 성능과 혼동 금지

완료된 전달물은 `reports/shared_model_v7/release/handoff/{if,kmeans,lof}_shared.joblib` 및 `manifest.json`이다. 의존하는 `code/` 모듈과 manifest의 라이브러리 버전도 필요하다. 신뢰하는 joblib 파일만 로드한다. `reports/shared_model_v7/latest.json`은 최종 전달물·노트북 감사 이후에만 갱신된다.

## 백엔드 입력 계약

- 입력은 Transaction이 아닌 Process 기반
- 시간순 연속 1분 감시 격자 + 그때까지 완료된 Process 행 + 유지된 과거 prefix
- 빈 분 생성·상태 보관·지연 도착·타이머 실행은 별도 worker 구현
- 원 기간 CSV나 평가 CSV는 추론 시 읽지 않음
- 이 작업은 로컬 연구 구현. Git push·Notion 갱신·운영 배포 없음
