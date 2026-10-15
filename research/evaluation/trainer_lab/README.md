# Trainer lab — Issue #44

- 목적: 기존 IF·MiniBatchKMeans 코드 재사용 가능성 및 데이터·산출물 검증
- 범위: 오프라인 연구, `server/` 연결·ERD/API 변경 없음
- 학습 도구 기준: PR #39의 PROCESS 전처리·IF/MiniBatchKMeans 코드; 기존 모델·결과 덮어쓰기 금지
- 최신 공개 모델: PR #47의 v12 시간 구간 모델. 아래 inspect_models 명령은 공개 모델 검사이며 이 도구의 PROCESS 학습 결과 재현과 별개
- 피처 수식·가용 시점·보류 사유: [FEATURE_CATALOG.md](FEATURE_CATALOG.md)
- 독립 검증 결과: [RESULTS.md](RESULTS.md)

## 준비

- Python 3.12, 기존 연구의 고정 의존성 사용
- 이번 확인 환경: Python 3.12.13; 기존 연구 기록 3.12.14와 패치 버전 차이
- 작업 위치: 저장소 루트

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r research/anomaly_detection/requirements.txt
.venv/Scripts/python.exe research/anomaly_detection/inspect_models.py --trusted --smoke
.venv/Scripts/python.exe research/anomaly_detection/run_tests.py
.venv/Scripts/python.exe -m unittest discover -s research/evaluation/trainer_lab/tests -v
```

## 실행

- 입력: 기존 PROCESS 컬럼명·API 별칭을 지원하는 CSV, 또는 기존 `inputs.json` 템플릿 아카이브
- 한 파일에 한 출처 권장; 출처 혼합 시 `source_period` 명시
- 원본 불변; 날짜 수정·자동 샘플링·잘못된 행의 자동 삭제 없음
- 한도: 100만 행·512 MiB; 초과 시 거부. 대용량은 출처·추출 기준·원본 SHA를 기록한 별도 입력 필요
- CSV 전체를 메모리에 보관; 스트리밍 학습 아님
- 출력: Git 제외 대상 `research/evaluation/trainer_lab/runs/`의 새 디렉터리
- 소스 변경이 있는 실험: `--source-commit`에 기반 커밋과 변경 상태 식별자 명시; 최종 커밋에서 재실행 권장

```powershell
# 데이터 검사만 수행
.venv/Scripts/python.exe -m research.evaluation.trainer_lab.run --input <PROCESS.csv> --output research/evaluation/trainer_lab/runs/audit-01 --source-commit <commit>

# 기존 기본 하이퍼파라미터, global PROCESS IF/KMeans 순차 학습
.venv/Scripts/python.exe -m research.evaluation.trainer_lab.run --input <PROCESS.csv> --output research/evaluation/trainer_lab/runs/train-01 --source-commit <commit> --train
```

- 원본 아카이브: `--kind archive`; 원래 `policy.config` 유지
- 모델별 독립 프로세스 계측: `--algorithm if` / `--algorithm kmeans`, 각각 다른 출력 폴더
- `run.json`: 환경·시간·OS 프로세스 최대 RSS·실행 상태
- `quality.json`, `splits.json`: 품질·분할·계보·시점 검사, 원본 행/식별자 미출력
- `if-result.json`, `kmeans-result.json`: 보류 시험셋 판정·학습 시간·재로딩 비교
- `<algorithm>/`: `manifest.json` + `model.joblib` + `fit.joblib`

## 분할·학습 규약

- 원래 시간순 분할 재사용: 출처+거래 단위, train / calibration / threshold / test
- 기간 경계 거래·시작시각 누락 거래: 분할 제외 수 기록
- 학습 참조: 기존 성공·품질·논리 일관성 규칙, 통계 꼬리 제거 제외
- 시험 입력: 기존 모델 적격 규칙; 부적격 행은 미판정 수로 기록
- 성공 상태 `S`를 정상 정답으로 간주하지 않음
- 학습 시점: 전처리·모델=train, 점수 보정=calibration, 판정선=threshold, test는 평가 전용
- 거래/계보 중복 또는 관측시각 이후 값 사용이 확인되면 학습 거부
- 계보·관측시각 미제공: `unknown`; 실시간 가용성·합성 원본 중복 부재 보장 불가
- 관측값 품질이 정상이어도 정상 장애 라벨을 보장하지 않음
- 정답 미제공: 판정 비율·점수 분포만 출력; 정확도·오탐률 생성 금지
- 명시적 `row_is_anomaly` 정답 제공 시 이진 지표 계산; 라벨 타당성은 별도 검수

## 저장·재로딩 규약 초안

- manifest: 입력 SHA, 소스 버전, 분할/fit 역할, 설정, 피처 순서, 환경, 임계값, 파일 SHA, 묶음 ID
- fit: 학습된 전처리·점수 보정·판정선, model: estimator
- 파일 SHA·묶음 ID·환경·단위·피처 순서·provenance 대조 후 조합
- 비교: 피처·점수 허용 오차 `rtol=1e-7, atol=1e-9`, 판정 완전 일치
- `joblib` 로드: 신뢰하는 자체 생성 파일만, `trusted=True` 필수
- 해시는 무결성 검사; 출처 신뢰·임의 pickle 실행 방지의 대체 수단 아님
- 운영 manifest/API 규약 확정 전 연구용 초안

## 기존 전체 평가 재현 제한

- 최신 dev 제공: `reports/evaluation_v2_work/inputs.json`, `reports/window_search_v2/training_inputs.joblib`, `validation_inputs.joblib` (research/anomaly_detection 기준)
- 미확보: 기존 전체 평가에 필요한 평가 CSV·정답·벤치마크 manifest; 제공 캐시와 원래 평가 데이터의 대응 확인 필요
- 원래 31,000 사례·6전략·시간창 선택 결과의 완전 재현 미완료
- 원본으로 새로 학습한 global PROCESS 결과와 기존 시간창 F1의 직접 비교 금지
- 원래 재현 명령: [기존 연구 안내](../../anomaly_detection/README.md), [모델 평가 안내](../../anomaly_detection/code/model_evaluation/README.md)
- 원본 benchmark 수령 후 SHA·환경 대조 → 원래 실행 경로 재현 → 지표 차이 비교

## 이식 가능 범위 / 후속 경계

- 재사용 후보: 입력 검증, 분할 감사, 피처 카탈로그, 산출물 묶음 검증
- 이식 전: 운영 PROCESS/시간창 공통 규약·이벤트 수정/지연 처리·피처 선택 계약 확정
- Detector 비교 실험 완료: 결과는 Issue #45 참조. 운영 적용 전 실시간/주기 추론 역할·최종 창/주기·미완료 PROCESS 정책 및 공통 입력 계약 확정 필요
- 별도 실험: SHAP·모델별 설명, 모델/임계값 선택, 합성 평가셋 타당성
- 계측: 프로세스 생애 최대 RSS; 두 모델 순차 실행 시 누적 최대치, 모델별 증가분 아님
- 이 도구의 학습 시간·메모리는 운영 부하·ESB 발생→판정 지연 보장 아님
