# 피처·HR/SR 자동 명세 연구 패키지 v0.2 (#40)

Qwen3.6-27B에 연결하는 **피처 생성 v5**와 **규칙 생성 보완 v2**의 실제 QLoRA 어댑터, 연구 코드와 평가 자료를 보관한다. 기존 [v0.1](../rule_generator_v0_1/README.md)은 그대로 보존한다.

- [모델 카드](MODEL_CARD.md), [모델·어댑터 SHA](model_manifest.json)
- [평가 결과와 원문](../../evaluation/feature_rule_design_v0_2/README.md)
- [개발·평가 시나리오](../../scenarios/feature_rule_design_v0_2/README.md)
- [기존 이상탐지 연구와의 연결 관계](CONNECTIONS.md)
- [원본 코드와 공개 변환 기록](source_snapshot.json)

## 코드와 모델의 역할

피처 모델은 스키마·관계·통계에서 계산 계획을 제안한다. 공통 연구 코드는 실제 원천·AST·타입·단위·모집단을 검증하고 정확히 측정한다. 의미 검토를 통과한 정의·측정·근거를 규칙 모델에 전달하면 HR/SR 제안 또는 보류·근거 요청·적용 불가 판단을 작성한다.

두 어댑터는 각각 95,136,344 bytes이며 원본 가중치와 SHA가 같다. Base 가중치는 포함하지 않는다. 어댑터 설정의 로컬 Base 경로를 모델 ID와 revision으로 옮겼으며 학습 파라미터를 변경하지 않았다.

`source_snapshots/v5/`에는 당시 피처 계약·검증·SFT·완전 체크포인트·규칙 연결 코드가 있다. `source_snapshots/calculation/`에는 실제 계산기의 의존 코드가 있다. 이들은 보존용 원본 연구 스냅샷이며 원래 실험 디렉터리와 승인 원천·교사 자료를 필요로 한다. 공개 패키지에서 자동 실행하지 않는다.

`design_cli.py`는 공개 집계 자료의 SHA 확인·저장 응답 확인·12개 고정 규칙 입력의 코드 검증·고정 입력 한 건의 제한된 모델 생성 도구다. **원본 CSV 재측정·임의 스키마의 전체 자동 실행·IF 전달은 포함하지 않는다.** 공개 입력에서 원천 경로를 제거했으므로 원래 프롬프트와 바이트가 같지 않으며 이번 통합에서 GPU 재평가하지 않았다.

## CPU 확인

저장소 루트에서 프로젝트 Python을 사용한다. GPU·Torch·IF 모델을 로드하지 않는다.

```bash
python -B research/models/feature_rule_design_v0_2/design_cli.py inspect --verify-weights
python -B research/models/feature_rule_design_v0_2/design_cli.py replay --case eval_A_actual_0
python -B -m unittest discover -s research/models/feature_rule_design_v0_2/tests -v
```

`replay`는 저장된 원문·집계·검토를 읽고 규칙 계약을 검사한다. 원천 재측정이나 새 후보 발굴 성공으로 세지 않는다. `validate-rule`의 코드 통과도 신규 응답의 의미 승인을 대신하지 않는다.

## 한 건의 모델 생성

할당된 GPU와 로컬 Base를 준비한 환경에서만 명시적으로 실행한다. 자동 다운로드·설치·학습은 없다.

```bash
export RULE_MODEL_BASE_PATH='/path/to/pinned/Qwen3.6-27B/snapshot'
export CUDA_VISIBLE_DEVICES='assigned-visible-gpu-index'
python -B research/models/feature_rule_design_v0_2/design_cli.py generate \
  --kind feature --case eval_B_actual_0 \
  --output research/models/feature_rule_design_v0_2/runs/new_feature.json
```

기존 출력 경로는 거부한다. 고정 공개 입력만 사용하고 응답당 출력 상한은 피처2048·규칙512토큰이다. 새로운 피처 응답은 실제 원천 측정과 의미 검토 전 보류이며, 신규 규칙 응답도 별도 의미 검토가 필요하다. 공개 경로에서의 생성은 이번 통합 작업에서 실행하지 않았다.

## 사용 범위

오프라인 연구용 후보·규칙 명세 도구다. 피처 v5의 기존 평가14/20, 자유 발굴6/8은 같은 회사·통제 합성의 소규모 평가다. 실자료의 새 정의4개 중 변동1개·상수3개이며 운영 유용성과 IF 승인은0이다. 규칙 어댑터의 과거 교사 문맥 오류 노출 이력도 유지한다. 서버 연결·실시간 탐지·운영 승인은 별도 작업이다.
