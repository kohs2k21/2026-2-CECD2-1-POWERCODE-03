# Hybrid SR·ML 1.1.0 실행·인수 안내

동결 모델은 `research/models/hybrid_sr_ml/1.1.0/`에 있다. 기존 server 모듈을
교체하지 않는다. 백엔드는 이 연구 CLI의 입력·출력 계약을 검토해 별도로 연결한다.

## 환경과 공개 실행 검사

Python 3.10.20 및 모델의 `requirements.cpu.txt` 환경을 사용한다. 설치·Base 다운로드는
자동 실행하지 않는다. 현재 프로젝트에서는 승인된 `.venv/bin/python`을 직접 지정한다.

```bash
export HYBRID_PYTHON=/path/to/approved/.venv/bin/python
sh research/integration/hybrid_sr_ml/run.sh inspect
"$HYBRID_PYTHON" -B research/integration/hybrid_sr_ml/smoke_test.py \
  --output /path/to/NEW_smoke_results
```

공개 검사는 가상 CSV 32거래만 생성한다. 원본 고객 데이터·GPU·LLM·모델 fit 없이
SHA 확인, CPU 검사, 새 CSV 캡처, 기본·strict 탐지, TEST 학습 거부를 실행한다.
새 출력 폴더가 필요하며 기존 결과를 덮어쓰지 않는다.

## 새 ESB CSV의 탐지

거래 완료 후 T/P/M 가족이 함께 들어온 완전한 배치가 필요하다. 동일 스키마·관계·단위와
동결 참조의 적용 가능성을 확인한 후 다음과 같이 실행한다. Body는 모델 입력에서 제외된다.

```bash
sh research/integration/hybrid_sr_ml/run.sh capture-esb \
  --transactions /path/to/T.csv --processes /path/to/P.csv --messages /path/to/M.csv \
  --start 2026-07-04 --end 2026-07-05 --split application --data-class approved_real \
  --complete-family-capture --held-lineage-excluded --accept-frozen-reference \
  --output /path/to/NEW_capture

sh research/integration/hybrid_sr_ml/run.sh detect-esb \
  --capture /path/to/NEW_capture/capture.json --output /path/to/NEW_detection
```

기본값은 실제 분모 0 대체 평가다. 비교가 필요하면 `--ratio-policy strict`로 별도 실행한다.
동결된 과거 HR10·관측 SR58은 원래 출력에 보존되지만 새 최종 판정에는 포함하지 않는다.
현재 주 판정은 수치 SR·C3 논리 SR·ML이다.

## FlowBench

각 workflow에는 전용 8개 피처·기준선·ML 모델이 있다. JSON run list는 원본 CSV의
절대 경로 문자열 배열이다. 정답 라벨은 피처에 전달하지 않는다.

```bash
sh research/integration/hybrid_sr_ml/run.sh detect-flowbench \
  --workflow 1000genome --run-list /path/to/test_runs.json \
  --output /path/to/NEW_flowbench_results
```

## 출력과 운영 연결 범위

- `decisions.csv.gz`: 원천 ID, 방법별 경고·비경고·평가 불가, 최종 OR, 발동·보류 규칙
- `numerical/`, `logical/`: 실제 계산 피처·점수·연결 원천·규칙 근거
- `ratio_fallback/`: 대체 전 판정, 적용 바인딩, 분자·분모·실제 대체 검사
- `summary.json`, `execution.json`, `manifest.json`: 집계·실행 상태·버전·SHA·배치 시간
- OR: 하나라도 경고이면 경고, 모두 비경고이면 비경고, 나머지는 평가 불가
- 부분 실패는 종료코드와 근거로 노출, 결측·실패를 정상으로 대체하지 않음

서버 DB·큐·API·알림·실시간 상태 유지·실제 수집 지연은 이번 범위에 포함되지 않는다.
IF/K-means margin과 FlowBench 정규화 점수는 이상 확률이 아니다. 입력·스키마·기준선·모델
변경 시 새 버전과 별도 평가가 필요하다.

명시적 Trainer·선택적 논리 명세 생성 계약은 [동결 README](../../models/hybrid_sr_ml/1.1.0/README.md)에 있다.
이번 업로드 검증에서는 재학습과 GPU/LLM 생성을 실행하지 않는다.
