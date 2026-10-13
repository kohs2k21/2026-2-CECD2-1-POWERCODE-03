# Detector 병행 실험 (#45)

- [실험 결과](RESULTS.md)·[운영 이식 전 합의](HANDOFF.md)·[기존 프리셋 재현 범위](REPRODUCTION.md)
- 범위: PROCESS 실시간 경로·TIME_WINDOW 추세 경로의 단독/병행 비교; 운영 배포 제외
- 알고리즘: global Isolation Forest / MiniBatch KMeans 각각 두 입력 경로; 기존 연구 encoder·점수 계산 재사용
- 자료: 초 단위 신규 합성 정상/이상·짝정상; 원본 고객 로그·기존 평가 정답 미제공
- 분리: train → calibration → threshold → validation 후보 선택 → 고정 후보 test
- 출력: `manifest.json`·비교표·실제 로컬 부하 측정; 원시 실행 출력 Git 제외
- 경계: 합성 효과·가상 사건시간·실제 로컬 연산시간 구분; ESB 발생→판정 2~3초 달성 근거 아님
- 한계: 시나리오별 3개 사건·단일 합성 인터페이스·좁은 시간대/요일; 분할 간 달력 변화의 오경보 혼재 가능
- 주평가: 동일 사건의 추가 탐지·최초 탐지 시간·짝정상 공통 감시시간의 경보/시간
- PROCESS 정답: 개별 장애 기준; 폭주·입력 정지는 개별 PROCESS 양성 정답 미정의. 폭주의 건수 증가는 PROCESS에서는 FP·사건에서는 TP로 집계 가능
- 시간창 정답: 창 종료시점이 주입 활성구간인지 여부; 복구 뒤 창에 남은 이상 이력의 경보는 사건 탐지 성공으로 미집계
- 단위별 F1/FPR 직접 비교·PROCESS FPR의 운영 오경보 추정 금지
- 실행: 저장소 루트, Python 3.12, 기존 연구 모델의 동일 패키지 버전

```powershell
.venv/Scripts/python.exe -m pip install -r research/evaluation/detector_dual_path/requirements.txt
.venv/Scripts/python.exe -m research.evaluation.detector_dual_path --output research/evaluation/detector_dual_path/runs/pilot-45 --trust-published-models
```

- 동일 출력 경로 재사용 금지; 새 디렉터리로 재실행
- pickle: 저장소 inventory SHA256 일치 모델만; 외부 모델 로딩 금지
- 기준 비교: 5분 창·60초 주기 고정; 정상 경보/시간 분모는 모든 후보의 공통 준비 15분 이후
- 1분 창도 기존 5분 보조 피처 사용; 최소 5분 수집 후 준비 완료
- 연구 산출물의 `server/` 런타임 import 금지

```powershell
.venv/Scripts/python.exe -m unittest discover -s research/evaluation/detector_dual_path/tests -v
```
