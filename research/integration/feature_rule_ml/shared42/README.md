# 공통 피처 Trainer·Detector 실행 패키지 (#140)

새 T/P/M CSV를 직접 계산하는 **research용 실행 패키지**다. Git에 포함된 정의·규칙·ML 가중치만으로 동결 탐지를 실행할 수 있다. 과거 서버 경로, 피처 캐시, Qwen Base 다운로드가 탐지에 필요하지 않다. `server/` API·DB·큐·알림 배포는 별도 담당 범위다.

## 포함된 구성

| 구성 | 내용 |
|---|---|
| 피처 | 이전 AI 정의58개 + 재사용 코드지표7개, 총65개 고정 AST |
| 규칙 | HR10, 관측SR58, 수치SR42. 총110개 역할 기록, 고유 조건100개 |
| ML 입력 | T25/P13/M4, 총42개. 결측·모집단 표시를 포함하면 T41/P22/M4차원 |
| 동결 ML | T/P/M × IF·K-means·novelty LOF, 실제 모델9개 포함 |
| Trainer | 새 승인 CSV → 같은 피처 계산 → 명시적 ML 학습9개 → 저장·재로딩 검증 |
| Detector | 새 승인 CSV → 같은 피처 계산 → 고정 HR·SR와 ML9개 병렬 의미의 적용 → 거래 단위 OR |
| 결과 | T/P/M 원천행 판정, 발동/평가불가 규칙ID, ML 점수, 거래별 판정, 단독/공통 집계, 원천·버전 SHA |

두 LLM은 **계산식·규칙을 설계하는 이전 단계**에 사용됐다. 여기서는 그 검증된 산출물을 동결해 계산한다. 매 거래마다 LLM이 피처를 재발굴하거나 규칙을 다시 생성하지 않는다. 이번 Trainer는 ML을 학습하며 Feature/Rule LoRA를 재학습하지 않는다. 기존 실제 LLM 생성 CLI는 [명세 모델 패키지](../../../models/feature_rule_spec/README.md)에 보존되어 있으며, 그 다른 체크포인트가 여기의110개 규칙을 새로 생성했다고 주장하지 않는다.

기존 IF·K-means와 추가 LOF의 **추정기 가중치는 유지**했다. 전달 시 모델 컨테이너의 로컬 경로를 제거하고 상대 경로 계약으로 다시 저장했다. 따라서 파일 SHA는 옛 아카이브와 다르지만 원천 전체 시험의 점수·판정·발동 원천행은 대조한다. `release/models/model_set.json`에 원본/전달 SHA를 구분한다.

## 환경

저장소 루트에서 Python 3.10.20으로 실행한다. 이 프로젝트에서는 기존 `.venv/bin/python -B`를 사용한다. 의존성은 [requirements.txt](requirements.txt)에 고정되어 있다. 설치는 자동으로 하지 않는다. Python·패키지·계산/ML 코드·정의·모델 SHA가 다르면 탐지를 중단하며 자동 재학습으로 대체하지 않는다.

```bash
python -B -m research.integration.feature_rule_ml.shared42 inspect
python -B -m research.integration.feature_rule_ml.shared42 --help
```

## 공개 예제로 Trainer부터 Detector까지 실행

```bash
python -B -m research.integration.feature_rule_ml.shared42 demo \
  --output /your/project/new-public-demo
python -B -m unittest research.integration.feature_rule_ml.tests.test_shared42_delivery -v
```

공개 예제는 고객 로그를 사용하지 않는다. 서로 다른 날짜·거래ID의 train/calibration/threshold/test 각32거래와 연결된 P/M을 코드로 구성한다. Trainer에서 모델9개를 실제로 학습·저장하고 별도 TEST CSV를 Detector로 판정한다. 모델 기능 검사이며 장애 탐지 성능·운영 정상성 검증이 아니다.

## 새 CSV를 입력 캡처로 만들기

물리 컬럼·자료형·단위·관계는 `release/profile.json`의 고정 스키마를 따른다. 별도 고객사의 다른 필드명·단위·관계를 조용히 추정하지 않는다. 지원 스키마가 바뀌면 명세 재검토가 필요하다.

```bash
python -B -m research.integration.feature_rule_ml.shared42 capture \
  --transactions /your/T.csv --processes /your/P.csv --messages /your/M.csv \
  --start 2026-08-22 --end 2026-09-09 --split test \
  --data-class approved_real \
  --complete-family-capture --held-lineage-excluded --accept-frozen-reference \
  --confirm-same-hr-contract \
  --output /your/project/new-test-capture
```

이 명령은 원본을 보존하고 새 경로에 **메타데이터만** 복사한다. Body/payload 및 `RESPONSE_MESSAGE`는 제외하며 임의의 미등록 컬럼·라벨·주입 정보는 거부한다. 각 원본 SHA와 제외 열 이름을 기록한다. 이미 Body가 제외된 같은 스키마의 CSV도 사용할 수 있다.

플래그는 근거를 대신 만들어 주는 옵션이 아니다. 실제로 전체 거래 가족이 수집되어 있고 보류 계보가 제외된 경우에만 선언한다. `--confirm-same-hr-contract`는 기존 NOT NULL 근거가 새 입력에도 동일하게 유효함을 확인한 경우에만 사용한다. 생략하면 HR은 평가 불가이며 정상으로 대체하지 않는다. `--accept-frozen-reference`는 옛 TRAIN의 수치 SR을 이번 **오프라인 연구 비교에만** 그대로 적용한다는 선언이다. 운영 유효기간·SLA·정상 인증을 자동 연장하지 않는다.

행 순서와 시간순은 다르다. 거래의 기간 배정은 T.START_TIME으로 선언하고 모든 관련 P/M을 함께 제공한다. `__SOURCE_ROW_ID`가 없으면 현재 CSV 레코드 순번을 원천 좌표로 생성하며 ML 입력에는 사용하지 않는다. `__RETRY_LEXICAL`은 P/M RETRY_COUNT의 정확한 원시 문자열을 보존한다. T/P/M을 자의적으로 `(거래ID, PROCESS_ID)`만으로 중복 제거하지 않는다.

## 동결 모델 탐지

```bash
python -B -m research.integration.feature_rule_ml.shared42 detect \
  --capture /your/project/new-test-capture/capture.json \
  --trust-local-model --output /your/project/new-detection
```

기본값은 포함된 모델9개다. 직접 관리하는 로컬 모델만 신뢰 플래그로 로드한다. 이 경로는 fit·임계값 보정·LLM 호출을 하지 않는다. 과거 피처 캐시를 새 CSV의 계산 결과로 사용하지 않는다. 65개 AST를 현재 원천에서 계산하고 별도의 구현으로 전수 비교한다. 규칙은 정확한 Decimal 값, ML은 float64 변환값과 명시적 결측/모집단 지표를 사용한다.

완료 후 T/P/M이 함께 도착한 **완전한 배치 캡처**를 대상으로 한다. `group_count`는 그 캡처의 빈도이므로 입력 크기·기간도 분석 의미에 포함된다. 작은 배치를 원래 장기간 캡처와 같은 기준선으로 해석하면 안 된다. 부분 거래의 진행 중 탐지·과거 시점 as-of 계산·watermark·실시간 장애 예측을 구현했다고 주장하지 않는다.

## ML 재학습

같은 피처42개를 유지하고 세 참조를 각각 `capture` 명령으로 준비한다. 날짜와 거래 가족이 겹치지 않는 train/calibration/threshold로 선언해야 한다. test/application을 학습 역할로 넘길 수 없다.

```bash
python -B -m research.integration.feature_rule_ml.shared42 train \
  --train /your/train/capture.json \
  --calibration /your/calibration/capture.json \
  --threshold /your/threshold/capture.json \
  --output /your/project/new-training

python -B -m research.integration.feature_rule_ml.shared42 detect \
  --capture /your/test/capture.json \
  --models /your/project/new-training/models --trust-local-model \
  --output /your/project/new-retrained-model-test
```

Encoder 중앙값·q90−q10 스케일과 IF/K-means/LOF fit은 train만 사용한다. 점수 정규화는 calibration, p99 임계값은 threshold에서 얻는다. 기존 IF/K-means의 global/adapter/ensemble 정책과 두 스레드 상한을 유지한다. LOF는 novelty=True, TRAIN 벡터 round6·중복 제거·최대8192개·시드42로 학습한다. TEST는 이 과정에 들어가지 않는다. 원본 운영 로그는 라벨이 없는 참조이며 정상으로 인증된 자료가 아니다.

ML 입력 모두가 계산 불가인 행은 학습에서 제외하고 탐지에서도 평가 불가로 남긴다. 일부 결측은 원래 TRAIN 중앙값·명시적 결측/모집단 지표로 인코딩한다. 새 학습에서 특정 열 전체가 미관측이면 기존 Encoder의0 대체와 그 입력 상태가 사용된다. 이0은 정상 판정이 아니며, 참조 적합성은 별도 검토해야 한다. 최소8행은 기능적 방어 조건일 뿐 충분한 표본 수라는 기준이 아니다.

HR·SR 조건과 LLM 가중치는 이 Trainer에서 바꾸지 않는다. 새로운 ML 모델은 별도 경로에 저장하고 자동 활성화하지 않는다. 고정 규칙과 새 ML의 조합 역시 별도 시험 후 채택해야 한다.

## 결과 해석

- `summary.json`: 방법별·거래별 상태와 수치 SR 기준 단독/공통 집계
- `T/P/M_decisions.csv.gz`: 원천 행 키·거래ID·발동 및 계산 불가 규칙ID·IF/K-means margin·LOF 점수
- `transaction_decisions.csv.gz`: 거래 가족 단위 HR/수치SR/관측SR/IF/K-means/LOF와 OR 결과
- `feature_audit.json`, `*_features.npz`: 원천별 계산·독립 검증과 이번 계산값
- `execution.json`, `manifest.json`: 원천·모델·규칙·코드·환경의 버전과 파일 SHA

IF/K-means margin은0보다 크면 경고다. LOF 정규화 점수는 클수록 이상이며 저장된 임계값보다 클 때 경고다. 확률이 아니다. 원천행의 경고는 거래로 올려도 남고, 경고가 없으면 적용 가능한 검사가 모두 비경고일 때만 최종 비경고다. 일부 계산 불가이면 평가 불가다. T와 연결되지 않는 P/M도 원천행 결과와 미연결 수에 남긴다.

주 집계 `OR_numeric`은 HR·수치SR·ML이다. 관측SR까지 포함한 `OR_all`도 보존한다. 기존 관측SR은 시험 거래 전체에 발동하므로 **기본 선별 성능을 설명할 때 수치 SR과 구분**해야 한다. 어떤 입력에서 모든 방법이 계산 가능하다고 가정하지 않는다. 완성된 연구 프로그램과 운영 이상탐지 품질의 입증은 다른 평가다.

## 실제 검증과 제한

[VALIDATION.md](VALIDATION.md)를 확인한다. 기존 시험 T46,815/P46,928/M103,792행을 새 CSV 경로로 다시 계산해 피처65개·규칙110개 발동 원천행·모델9개 점수/판정을 대조한다. 배치 시간은 실시간 거래당 지연이 아니다. 실제 장애 정답, 독립 기업의 미사용 스키마, 운영 precision/recall, API·DB·알림 배포 검증은 포함하지 않는다.
