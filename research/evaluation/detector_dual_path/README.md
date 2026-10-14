# Detector 병행 실험 (#45)

## 최신 공정 비교 v4

- 진입점: [v4 비교 결과](docs/V4_RESULTS.md)·[환경/소스 복원/실행 규약](docs/REPRODUCTION.md#최신-업로드v4-재현-범위)·[봉인된 품질 실험 규약](docs/V4_PROTOCOL.md)
- A 실시간 / B 시간창 통계 Rule 추가 / C 시간창 ML 추가 / D 둘 추가; 실시간 경로 동일·전체 경보 예산 공통
- 정상 요일/시간 균형·5 seed·정상112h/주요630사건·논리 count 계약105사건 별도. 원본 v12 전체 모델/고객 정확도 재현과 구분
- 부하: 군당1논리CPU·RT1/trend1·10/100건/초·60/5초 주기·각4회. 공유 Windows 호스트이며 실제 서버/ESB SLA와 구분
- 코드: `v4.py`·`v4_core.py`·`v4_load.py`·`v4_config.json`, 회귀 `tests/test_v4.py`; 원시/생성 모델 `runs/` Git 제외
- 실행 조건과 중단/IO 보완 이력: 재현 문서의 최신 v4 절 기준. 아래 v3와 기존 `RESULTS.md`는 과거 탐색 이력 보존

## v3 탐색 실험

- [실험 결과](docs/RESULTS.md)·[운영 이식 전 합의](docs/HANDOFF.md)·[기존 프리셋 재현 범위](docs/REPRODUCTION.md)
- 독립 재검수: 합성 입력 경로 탐색용 pilot. 운영 모델 우위·최적 창/주기·두 ML 모델 필수성의 검증과 구분
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

## 폴더 구성

```text
research/evaluation/detector_dual_path/
├── README.md             # 목적·실행법·문서 진입점
├── *.py                  # 모델·재생·시나리오·평가·부하·실패 상태
├── config.json           # seed·창/주기 후보·반복 시험 설정
├── requirements.txt      # 기존 연구 의존성·측정 패키지
├── tests/                # 구현 회귀·독립 검수 시험
├── docs/                 # 결과·재현 범위·운영 이식 계약
│   ├── RESULTS.md
│   ├── REPRODUCTION.md
│   └── HANDOFF.md
└── runs/                 # manifest·CSV/JSON·생성 모델, Git 제외
```

- 새 실험 코드·설정·문서·산출물: 이 평가 패키지 안에 배치
- 기존 계산 재사용: `research/anomaly_detection/code/`; 배포 코드에서 연구 패키지 직접 import 금지
- 패키지 모듈명·기존 모델·과거 실행 manifest/산출물 보존

## 실행

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
