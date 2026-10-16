# PROCESS 합성 이상 실험 — Issue #53

- 범위: 오프라인 원본 복사본 → 합성 PROCESS → 고정 모델/룰 판정 → 시나리오별 채점
- 입력: 기존 canonical CSV/별칭 지원 PROCESS + 같은 출처 TRANSACTION CSV
- 실행 환경: `research/anomaly_detection/requirements.txt` 고정 의존성
- 출력: 이 패키지의 Git 제외 `runs/` 아래 새 디렉터리; 원본·실행 결과·모델의 Git 추가 금지
- 운영 API·DB·화면·CD·장기 추세/캐시 경로 변경 없음
- 방법 검증 확장: `--config research/evaluation/process_synthetic_lab/config.method-validation.example.json`; 기존 설정/실험 보존

```powershell
python -m research.evaluation.process_synthetic_lab.run `
  --process <PROCESS.csv> --transactions <TRANSACTION.csv> `
  --config research/evaluation/process_synthetic_lab/config.example.json `
  --output research/evaluation/process_synthetic_lab/runs/experiment-01 `
  --source-commit <git-commit-and-dirty-state>
```

## 고정 규약

- 시간 분할: 학습 70%·임계값 검증 15%·시험 15%의 시작시각 분위수; 시간 경계 교차 거래 제외
- 같은 출처·거래의 모든 PROCESS/재시도: 동일 분할; 제공된 원본 계보 중복 검사
- 분할 비율 선정: 모델 결과 확인 전 일자별 데이터 편중 조사; 기존 날짜 수 기준 분할과 구분
- 학습 참조: 기존 성공·품질·건수 일관성 조건; 확인된 정상 정답으로 간주 금지
- 피처: 기존 PROCESS 수치·시간 피처 + 어댑터별 처리시간 median/MAD·빈도·기준선 부재 표시
- fit: 피처·스케일러·모델·생성 기준·기존 통계 룰은 train만; 모델 임계값은 검증만
- 모델: 단일 global IF 예비 실험; 미제공 최종 모델·기존 어댑터별 앙상블 재현 아님
- 미관측/표본 부족 어댑터: train global 통계 fallback; adapter/interface 결과로 편차 확인
- 정상 오탐률: 확인된 정상 라벨 미제공 시 null; 성공 상태 참조의 경보율만 별도 제공

## 시나리오

| 종류 | 변형 | 거부 조건 |
|---|---|---|
| P01 명시적 실패 | 확인된 F 상태·정합성 유지한 error/success count, 확인된 응답코드 | 상태 의미 미확인, 비어 있지 않은 응답코드의 실패 대응값 미확인 |
| P02 개별 처리 지연 | 기존 종료시각 + train 어댑터 중앙값 × (강도−1), 최소 기준값 1ms | 선택한 부모 정책·의존 PROCESS 시작시각 위반 |
| P03 연속 PROCESS 지연 | 같은 출처/어댑터/인터페이스의 시간순 연속 구간에 P02 적용 | 완전한 연속 구간 없음·한 행이라도 관계 검사 실패 |
| P04 연구 건수 보존 계약 위반 | 기존 totalCount=successCount+errorCount 행의 totalCount만 +1/+2/+3 | 연구 계약 미동의·기존 품질/관계 위반·정확한 정수 증분 불가 |

- 강도 1.5/2/3 × seed 11/23/47; 시도당 최대 10행, 결과 확인 후 강도 조정 금지
- 모델/SR 점수 기반 주입 행 선별 금지; 부모·의존 관계 안전성에 따른 생성 거부 수 보존
- P01 기본값 미지원; 실제 의미 근거를 `failure_semantics_evidence`에 지정한 경우만 활성화
- P01 보류 유지: F 상태여도 errorCount=0인 원본 존재; 모든 실패를 errorCount>0 또는 임의 응답코드로 치환 금지
- 주입 정답: 설정한 인위적 변화의 정답; 실제 고객 장애·운영 SLA의 정답 아님
- `parent_policy=strict_parent` 기본값: 부모 종료시각 고정, 여유 시간 부족 시 거부
- `parent_policy=propagate_parent_end` 별도 실험: PROCESS 지연에 따른 부모 완료시각 파급 포함; 부모 종료=max(원래 종료, 변경한 자식 종료), 같은 부모의 복사본 행 전체에 동일 적용
- 부모 파급 조건: 알려진 원본 형제 시간 위반이 없는 부모만 사용; 형제 PROCESS 시각·알려진 의존 PROCESS 시작시각 이동 금지
- 두 조건의 결과 분리; strict 실패 결과 보존, 모델 점수에 맞춘 강도 변경 없음
- 확인된 의존 PROCESS의 시작시각 결측: 선행 PROCESS 변형 거부; 추출에 없는 하위 로그의 존재/완전성은 검증 불가
- 상태/응답코드의 회사별 의미·부분 실패 전파 정책: 별도 확인 필요
- P04: 연구에서 명시적으로 채택한 건수 보존 계약의 양성 대조; 실제 고객 장애를 무조건 의미하지 않음
- P04 허용 변경 필드: totalCount 하나; status·응답코드·성공/실패건수·시각·관계·부모 불변, 기대 위반=count_conservation만 별도 기록/검사
- P04 범위: 기존 실험에서 사용한 시험 데이터 재사용 → 방법 개선용 탐색 실험, 미사용 holdout의 모델 성능 검증 아님
- `scenarios`: 실행할 P01/P02/P03/P04 선택; 기본값은 기존 P01/P02/P03
- C00(`run_noop_control`): 미변형 시험 CSV 저장→기존 canonical parser로 복구→같은 모델 재판정, 모든 행의 판정·미판정·점수 정확 일치 요구
- C00 parser 진단: 정규화된 결측값에서 원래 파싱 오류 복원 불가; 기록된 `_invalid_*` bool을 엄격하게 복원·원본 필드와 함께 대조
- C00 실패: 전체 실행 실패 처리; 정상 라벨 생성·오탐률 계산 없음

## 판정과 결과

- HR_existing: 기존 `evaluate_rules`의 PROCESS 자체 논리/품질 규칙; 미제공 거래 전체 문맥 규칙 제외
- SR_existing: 기존 `QuantileBook`의 P99/P99.9; 기존 최소 표본 1,000/10,000 유지, 표본 부족 시 미판정
- ML_provisional: 기존 IF 설정 기반 단일 모델 + 실험용 어댑터 피처
- 예비 ML 피처에는 status/errorCount/successCount/responseCode 미포함; P01 변형은 ML 입력 불변. 해당 탐지율은 실패 신호 학습 능력의 검증으로 해석 금지
- 최종 운영 HR/SR 정책·종합 정책 검증 아님; 임의 종합 판정 없음
- 분모: 실제 생성·검사 통과한 이상 PROCESS 전체; 미판정도 분모 포함
- 결과: 전체/시나리오/어댑터/인터페이스 탐지율·미판정; 반복별 생성 거부·시간
- 보조 paired 표: 같은 주입 PROCESS의 변경 전/후 판정 비교; 기존 경보·신규 탐지·유지 경보·사라진 경보·미판정 구분
- 주입 전부터 경보였던 행의 높은 주입 후 탐지율: 합성 변화 탐지 성공으로 단독 해석 금지; 기존 recall 표와 신규 탐지 수 함께 확인
- 성공 원본의 모델 경보율: 정답 없는 관측치; 정상 오탐률과 분리
- 지연 실험과 통계 룰의 목적 중첩: 높은 SR 탐지율만으로 ML 불필요/우수 결론 금지
- ADBench: 별도 수치 피처 스트레스 실험; 같은 고정 IF scorer 사용, PROCESS 결과표에 합산 금지

## 산출물

- `manifest.json`: 입력·코드 전후 SHA·설정·환경·상태; 실행 중 변경 시 결과 무효 처리
- `data-audit.json`, `*-membership.csv`: 분할·부모 조인·표본/계보
- `frozen-model.joblib`, `model-manifest.json`, `generator-fit.json`: 고정 모델·규칙·생성 통계
- `test-base.csv`, `base-predictions.csv`, `control-report.json`: 미변형 시험 기준선
- `noop-control.json`, `noop-predictions.csv`: 선택한 C00 무변형 왕복 결과·원본 필드/진단 보존 검사
- 시도별 `process.csv`, `truth.csv`, `changes.csv`, `parent-overlay.csv`, `rejects.csv`, `predictions.csv`, `generation.json`
- 부모 복원: 원본 TRANSACTION SHA 확인 → canonical JOIN_KEYS(source_period/transactionId/processHubId/retryCount)로 overlay 결합 → before 일치 확인 → endTime만 after로 교체; 나머지 부모/필드 보존
- 부모 변경은 PROCESS 정답에 추가하지 않음; 원본 SHA·정확한 부모 overlay와 SHA로 평가 부모 데이터 재구성
- `results.csv/json`, `trials.json`: 시나리오·반복·세부 그룹 결과
- `paired-results.csv/json`: 주입 label=1 행만 원본 판정과 같은 row_id로 비교; 정상 정답/오탐률 추정 아님
- `reproducibility.json`: 경로·실행 시간 제외한 의미적 결과 해시; 입력/코드/환경 고정 재실행 대조
- `adbench/`, `adbench-summary.json`: 보조 생성 실험·별도 가정·미지원/실패 사유

- 저장 결과에 paired 보고만 추가: 새 출력 폴더 사용, 원래 결과·모델·주입 데이터 불변

```powershell
python -m research.evaluation.process_synthetic_lab.paired_report `
  --run research/evaluation/process_synthetic_lab/runs/experiment-01 `
  --output research/evaluation/process_synthetic_lab/runs/paired-01
```

## 검증

```powershell
python -m unittest discover -s research/evaluation/process_synthetic_lab/tests -v
```

- 고객/합성 원본·실제 행 식별자 포함 결과: 로컬 `runs/`에만 보관
- 완료·품질 결론: 독립 검수 결과 확인 후 별도 보고
