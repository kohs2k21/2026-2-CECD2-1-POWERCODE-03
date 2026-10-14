# 기존 프리셋 재현 범위

- 아래 항목: v3·10/13 조사 시점의 과거 3개 모델 기준. 최신 v12/캐시 업로드 범위는 하단 참조
- 대상: Issue38 inventory의 joblib 3개; 원본 파일 SHA256 일치
- 환경: Python 3.12.14·기존 연구 requirements 정확 버전; pip dependency 충돌 없음
- PROCESS: 기존 `prepare`·10개 encoder 피처·global IF/KMeans
- TIME_WINDOW: 기존 `window_features`·12개 encoder 피처·global IF/KMeans
- 1분/5분 묶음 × 두 입력 단위 × 두 알고리즘: 총 8개 직접 점수 경로
- 동일 합성 입력에서 `Detector.score - threshold`와 `SixModels.score`의 해당 global 열 대조: 절대 오차 1e-12 이내·이상 판정 동일
- 반복 점수 계산: 입력·모델 변경 없음, 유한 점수·피처 shape 확인
- 기존 회귀: 111개 통과; 원본 archive 필요한 benchmark 시험 1개 skip
- 미제공: 원본 CSV/SQLite·기존 정답 사건·학습/검증 cache·대규모 예측 파일
- 미재현: 기존 고객 데이터의 성능·분포·대규모 평가 수치·재학습 결과
- 신규 실험: 독립 합성 정상/이상으로 새 fit·정규화·임계값 설정; 기존 모델 정확도 재현과 구분
- 검증: 구현 작성자와 별도 독립 자료/환경 검수자 실행

---

## 최신 업로드·v4 재현 범위

- 고정 연구 source: `cb006179ec61531e547af3d33269cd596084fb5f`·PR47
- 최신 제공: IF/KMeans 모델2개·`training_inputs.joblib`/`validation_inputs.joblib` cache2개·`inputs.json`
- 원본 v12 smoke/회귀6개 통과·미등록 범주 fallback·완료 관측/과거3분 gating·동일 반복 점수 확인
- v12 전체 재fit/전체 성능 재현 부족: development 입력·benchmark/history·정답·과거 분기 모델/중간 frozen 산출물
- v4: 최신 estimator/score 재사용·새 균형 합성 재fit·새 고정 최종 시험. 원본 v12 expert routing/고객 성능과 구분
- `source_manifest.json`: 최초 조사 증빙, v4 실행 필수 파일 아님. 코드 필수 경로: snapshot 내부 `research/anomaly_detection/code/`

### 고정 source 복원 — 저장소 루트·PowerShell

```powershell
git fetch origin cb006179ec61531e547af3d33269cd596084fb5f
$snapshotRoot = 'research/evaluation/detector_dual_path/runs/source_snapshot/cb006179'
$snapshotTar = 'research/evaluation/detector_dual_path/runs/source_snapshot/source-cb006179.tar'
New-Item -ItemType Directory -Force -Path $snapshotRoot
git archive --format=tar --output=$snapshotTar cb006179ec61531e547af3d33269cd596084fb5f research/anomaly_detection
tar -xf $snapshotTar -C $snapshotRoot
```

### 환경·품질 실험

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r research/evaluation/detector_dual_path/requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s research/evaluation/detector_dual_path/tests
.\.venv\Scripts\python.exe -m research.evaluation.detector_dual_path.v4 --output research/evaluation/detector_dual_path/runs/v4_balanced_20261014
```

- 실행 환경: Python3.12.14·정확한 연구 패키지 버전·psutil7.2.2. Python 패치/OS/BLAS·source 코드 hash도 기록
- 출력: 새 폴더 필수; 이전 결과 덮어쓰기 금지. 같은 PC 재실행은 새 출력명을 지정
- declaration/manifest·역할별 날짜/달력 지원·모델 hash·validation selection seal·최종 결과 보존
- 최종 시험을 보고 seed/임계값/후보 변경 금지; 예산 실패 그대로 보고
- 결과: [V4_RESULTS.md](V4_RESULTS.md), 전체 과학 규약: [V4_PROTOCOL.md](V4_PROTOCOL.md)

### 실제 부하 — 품질 실험 완료 후 별도

- 용어: 준비 연산(warmup)은 측정 전 모델·연산을 예열하는 단계, 잔여 작업 처리(drain)는 입력 종료 뒤 이미 제출한 작업을 마무리하는 단계. 처리 불가로 버린 작업(drop)·측정 끝에도 미완료인 작업(pending) 별도 기록
- 두 snapshot 구분: 고정 연구 source snapshot은 특정 Git 커밋의 코드 사본, PROCESS snapshot은 특정 시점까지 확인된 로그 관측값

- 이번 반복 조건: IF·seed451·RT1/trend1·군당1논리CPU·BLAS/OMP1·추론 n_jobs1·큐1,000
- 준비900초·연산 warmup10Hz600건·측정10/100Hz600초·추세60/5초·고정 종료 후drain최대60초
- Windows 물리코어/SMT topology 확인 후 군별 서로 다른 물리코어의 논리CPU 배정; `[4,6,8,10]`은 이번 PC값·다른 PC에 무조건 복사 금지
- 코어 배정4회 cyclic·A/B/C/D 동시; config 기본3회와 실행4회 override/실완료 횟수 구분
- 조건 순서 고정: 10Hz/60초 → 10Hz/5초 → 100Hz/60초 → 100Hz/5초, 각4회. 군 간 같은 블록의 paired 비교, 조건 간 절대 차이는 시간/온도 교란 제한
- 각 child 예시: `<...>`는 실제 경로/UTC epoch로 대체

```powershell
.\.venv\Scripts\python.exe -m research.evaluation.detector_dual_path.v4_load --run-dir <품질출력절대경로> --output <해당군새출력절대경로> --group A --algorithm if --seed 451 --rate 100 --stride 5 --cpu 4 --repeat 0 --executed-repeats 4 --warmup-epoch <공통UTC초> --measurement-epoch <초기예정UTC초> --measurement-release <공통release.json절대경로>
```

- coordinator: 같은 trace/ready/model/선택/code hash 확인·4개 `ready.flag` 확인 후 공통 미래 epoch 발행. release JSON 임시 저장 후 원자 교체
- 공유 재현 범위: worker 실행 명령·release 형식·조건/코어 배정 규약. 이번 외부16블록 coordinator·재개 wrapper는 Git 미포함
- peer 준비 검증: clean warmup 후 `ready.flag` 전에 한 번 발행한 `ready_contract.json` 사용. 각 프로세스가 이후 갱신하는 `contract.json`과 구분
- release 형식: `measurement_epoch_utc` 숫자와 `ready_outputs`의 A/B/C/D 절대 출력 경로. 발행 후 최소5초 여유·이미 지난 epoch 사용 금지
- warmup drop/error/pending 발생 시 실측 시작 금지. 실측 drop/미제출/pending은 전체 예정 실패 분모로 기록
- 원시 예정/제출/시작/끝·큐 지연·추론·drain 완료·CPU/RSS·실제 affinity/threadpool/n_jobs 기록
- Windows venv 실행 PID가 launcher stub일 수 있음: 실제 interpreter/하위 owned tree 계측·종료 검증 필요; stub 합계만으로 메모리 주장 금지
- 측정 범위: 준비 trace 생성/모델 로드/결과 직렬화 제외, worker 피처 준비·추론 및 측정 생산자 비용 포함. 부모/호스트 계측 범위 별도
- 공유 호스트 affinity는 독점 자원/quota 보장 아님; 단독2CPU·멀티코어 가속·고객 ESB 전체 SLA로 일반화 금지
- raw runs·source archive·모델·audit·개인 기록 Git 업로드 제외

### 이번 부하 실행의 중단·재개 이력

- 원 완료1~5·첫 재개 완료6 보존. 원6 외부 중단은 원인·종료 코드 미확인; 첫 재개7은 실측 전 Windows 계약 파일 교체 접근거부
- 두 partial은 완료 표본에서 제외·원본 보존. 이미 완료된 조건 재실행·유리한 결과 선택 없음
- 완료 raw의 분모와 전체 실행 시도 수 구분. 중단 시도의 데이터 미저장·실행 안정성까지 성공한 것으로 해석 금지
- Windows 보완: peer 검증 대상만 immutable 준비 계약으로 변경. 동일 모델·입력·추론·측정 슬롯·큐·CPU·주기 유지
- 수정 전후 입력/추론 동등·4프로세스 준비 계약 읽기/갱신·66시험 독립 검증 통과. 외부 프로그램의 파일 잠금까지 방지한다는 보증과 구분
- 잔여 original7~16은 `resume2/` 별도 출력에 완료. 원 `full/` 유효1~5·`resume/` 유효6과 합산해16블록·64trial 독립 대조 완료
- 전체18시도·partial2개 보존, raw RT2,112,000/추세3,120 예정 분모 유지. 전체 예정 실패 또는3초 초과1,914,469건은 과부하 실측 결과이며 실행 검증 PASS와 구분
- 최종 독립 합산: `runs/v4_load_20261014/io_control_tester/final_raw64/`의 조건/군/trial/paired CSV·JSON. 원시·실행 선언·중단 로그·파일 해시 보존, 공유 결과는 [V4_RESULTS.md](V4_RESULTS.md)
