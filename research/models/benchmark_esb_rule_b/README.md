# Benchmark → ESB Rule B — Issue #251

기존 Qwen에 벤치마크 SFT를 진행한 뒤, **원본 ESB 교사 자료와 학습 방식을 유지하며 추가 SFT한 B Rule LoRA**의 연구 전달본이다. 선택한 epoch 2 가중치와 native Rule-v2 추론기, 공개 검증기, 기존 baseline 서비스 초안 형식으로의 연결을 함께 제공한다.

- provider: `rule_b_native_v2`
- Base: `Qwen/Qwen3.6-27B`, revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`
- 어댑터: `models/rule`, weight SHA256 `36589b07d9fb8ee3b955598ea652a08de1f72243ceb066fd793c62e39692c11e`
- 입력: 코드로 검증·측정한 피처 정의, 원천·단위·분석 단위·모집단, 관측 통계, TRAIN 기준선, 확인된 제약
- 출력: 피처 하나에 대한 `hr`, `observed`, `baseline`의 제안·보류·근거 요청·해당 없음
- 서비스 전달: 검증된 `proposed` baseline만 기존 `ruleKey/condition/outcome/evidenceSpec` 초안으로 변환
- HR·관측 SR: 모델 원문과 검토 기록 보존, 이 연결부에서 실행 초안으로 자동 변환하지 않음
- 기존 Feature LLM, 논리 SR 생성기, AE 기반 SR, ML 모델·전처리·임계값 및 운영 활성 규칙: 이번 전달에서 교체하지 않음

**기존 Rule-v3 provider에 가중치 경로만 넣으면 작동하지 않는다.** B는 학습한 native v2를 유지한다. `convert_suggestion()`이 검증 후 서비스 DTO를 만들며, v3 설명 필드를 모델이 생성한 것처럼 채우지 않는다. 기존 파일은 보존하고 별도 provider를 명시적으로 선택한다. 서버의 DB·큐·API·UI·XAI 연결은 [BACKEND_HANDOFF.md](BACKEND_HANDOFF.md)를 따른다.

## 실행

저장소 루트에서 프로젝트에 승인된 Python 환경을 사용한다. 아래 `<project-python>`은 이 서버의 `/home/msko20702/#종합설계/data/.venv/bin/python`에 해당한다. CPU 검사에는 `jsonschema`, 실제 로컬 추론에는 기존 검증 환경의 torch·transformers·peft·bitsandbytes가 필요하다. 자동 설치·Base 다운로드·학습은 없다.

```bash
<project-python> -B -m unittest discover -s research/models/benchmark_esb_rule_b/tests -v
<project-python> -B -m research.models.benchmark_esb_rule_b verify
<project-python> -B -m research.models.benchmark_esb_rule_b replay
```

`replay`는 공개 PFS에서 과거 실제 B가 생성한 응답 8개를 CPU로 다시 검증하고 baseline 초안으로 변환한다. 새로운 LLM 생성이나 탐지 성능 재평가를 수행하지 않는다. 전체 CSV나 고객 원본 행은 포함하지 않는다.

새 실제 생성은 별도로 다음과 같이 실행한다. 자신에게 할당된 GPU를 먼저 확인하고 `CUDA_VISIBLE_DEVICES`에 한 장만 지정한다. 입력은 `build_service_request(...)["input"]` 또는 계약을 충족하는 native 입력 JSON이다. 출력은 존재하지 않는 새 디렉터리를 사용한다.

```bash
CUDA_VISIBLE_DEVICES=<assigned-visible-gpu> <project-python> -B \
  -m research.models.benchmark_esb_rule_b recommend \
  --base-path /approved/local/qwen-snapshot \
  --input /new/run/native_input.json \
  --output /new/run/recommendation
```

Base 전체 파일 SHA와 B 어댑터를 확인한 후, NF4/bf16·greedy·thinking off·출력 1024토큰으로 생성한다. native 평가와 같이 grammar 강제는 사용하지 않는다. 공개 오류 피드백으로 최대 한 번만 수정하고 실패·잘림·문맥 초과를 보존한다. 입력 4096토큰을 넘으면 자르지 않는다. 저장 답변·다른 모델·코드 정답으로 대체하지 않는다. `baseline_draft.json`은 **추천 초안**이며 관리자 채택이나 운영 활성화를 의미하지 않는다.

## 학습 및 평가 해석

상세 설정은 [training.json](training.json), 원본 코드·가중치 출처는 [provenance.json](provenance.json), 검사 결과와 한계는 [VALIDATION.md](VALIDATION.md)를 참조한다.

1. FlowBench 1000Genome·Montage의 실제 라벨을 사용해 검증한 규칙 명세를 교사 자료로 학습: TRAIN 88 / VAL 24
2. 원래 ESB Rule 교사 자료로 추가 SFT: TRAIN 452 / VAL 298 / TEST 522, 2 epochs, LoRA r8/alpha16, AdamW 1e-4, 실효 batch 4
3. validation answer-token NLL 최소 epoch 2 선택, adapter tensor 608개 / trainable parameter 23,760,896개

ESB 교사는 AI 작성·코드 검증 silver 자료이며 기업이 확정한 장애 정답은 아니다. TEST 전체 검사 통과는 A 513/522, B 510/522로 B의 우월성을 입증하지 않았다. PFS에서 A와 B의 검증된 수치 SR은 같은 판정을 냈다. B를 추천 모델 후보로 전달하며, 탐지 성능 개선을 자동으로 주장하지 않는다.

현재 B는 단일 피처의 제공된 진단 조건과 TRAIN p95를 근거에 맞게 명세한다. 임의의 다중 피처 논리 규칙 발굴, AE 의사 라벨 Rule 생성, 실제 장애 원인 확정은 별도 모듈의 범위다. 관측 `is_missing`를 운영 장애 경고로 자동 채택하면 과다 경고가 발생할 수 있어 기본 서비스 전달에서 제외한다.
