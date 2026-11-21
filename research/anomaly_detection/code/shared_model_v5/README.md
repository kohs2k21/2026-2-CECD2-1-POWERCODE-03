# 현재 5-task 모델

현재 결과: `reports/shared_model_v5/release/summary.json`.
실행 결과가 포함된 `result.ipynb`를 먼저 읽는다. 예전 v4 노트북은 당시 7-task 기록이다.

목표는 모델별 **task F1 최저 ≥30%, 중앙값 ≥60%, 모든 task/전체 정상 오탐 <5%**이다.
현재 모두 통과했다. 전체 F1이 모두 상승한 것은 아니다. IF 복원과 LOF 재선정은
취약 task를 회복하는 대신 일부 task와 전체 합산 F1이 하락한 교환이며 K-means는 유지했다.
IF 최대 오탐 4.9971%는 기준에 매우 가까우므로 운영 보장은 아니다.

## 파일

- `protocol.py`, `protocol.json`: 현재 다섯 task, 원본 해시, 수치 목표
- `search_existing.py`: 정상 학습 후보의 5-task 개발 검증 재선정; 탈락 후보 보존
- `evaluate.py`: 동결된 첫 시도 전체 평가; task·행 정렬 검사; `attempt01` 보존
- `release.py`: 정상 분위수 경보선 후보와 재사용 연구 평가의 투명한 채택 판단
- `audit_scope.py`: 남은/보관 CSV 63개, ADBench 파일 해시 검증
- `audit.py`, `test_v5.py`: 독립 TP/FP/FN/TN 및 목표, 추론 경로 검증
- `report.py`: 일반 막대 그래프, 성능표, threshold 분포, 해석이 섞인 노트북 생성/실행

## 검증·보고서 재실행

프로젝트 루트 PowerShell:

```powershell
$env:PYTHONPATH='code'
$env:PYTHONIOENCODING='utf-8'
.analysis-venv/Scripts/python.exe -m shared_model_v5.audit_scope
.analysis-venv/Scripts/python.exe -m shared_model_v5.audit --output reports/shared_model_v5/release
.analysis-venv/Scripts/python.exe -m shared_model_v5.report
```

완료된 선택/모델을 자동 덮어쓰지 않는다. 실험을 새로 하려면 별도 attempt 경로를 사용한다.

## 백엔드 전달

`reports/shared_model_v5/release/handoff/`의 세 `*_shared.joblib`와 `manifest.json`.
`code`의 의존 모듈도 함께 필요하다. 로드된 Monitor는
`predict(native_minute_grid, completed_process_rows)`를 지원한다.
모델별 한 개의 전체 추정기이며 Adapter 전문가·모델 앙상블은 없다.
입력은 Process 로그에서 생성하지만 빈 분과 과거 prefix가 필요하다. worker 실시간 상태 관리,
지연 도착 정책·운영 배포는 별도 구현 대상이다. 신뢰하는 joblib만 로드한다.

반복 사용한 반합성 연구 벤치마크이며 외부 블라인드 테스트가 아니다.
Task 제외와 모델 개선 효과는 분리해서 비교한다. ADBench 자료는 변경하지 않았다.
