# 파생 변수·HR/SR 생성 모델 v0.1

Qwen3.6-27B와 실제 SFT v2 LoRA 어댑터로 피처 정의·측정·근거를 받아 HR/SR 명세를 제안하는 연구용 데모다. 기존 64개 피처의 카탈로그와 실제 모델 결과를 공유하며, 피처 원천은 모델 제안 13개·코드 구성 51개다. 이번 공개 작업에서 새 피처 발굴이나 학습을 실행하지 않았다.

- [결과 화면](../../evaluation/rule_generator_v0_1/demo.html): 브라우저에서 파일 직접 열기, 별도 웹 서버 불필요
- [평가 결과·입력](../../evaluation/rule_generator_v0_1/README.md)
- [사례 구성](../../scenarios/rule_generator_v0_1/README.md)
- [모델 카드](MODEL_CARD.md): 범위·학습·평가 한계
- [모델 manifest](model_manifest.json): 모델 revision·어댑터 SHA·고정 자료

## 제공 범위

`adapter/adapter_model.safetensors`는 실제 학습된 SFT v2 가중치다. 크기는 95,136,344 bytes이며 원본 연구 어댑터와 SHA-256이 같다. `adapter_config.json`의 로컬 Base 경로만 모델 ID와 고정 revision으로 이식했다. Qwen 원본 가중치·토크나이저는 포함하지 않는다. 로컬 Base를 별도로 준비해야 한다.

AI는 조건·역할·상태·근거·설명을 제안한다. 코드는 피처·측정·기준선 연결과 계약·타입·근거를 검사한다. 직접 의미 검토는 별도 단계이며 새 실행의 코드 통과만으로 최종 승인하지 않는다. 모델 답안을 코드가 정답으로 교체하지 않는다.

이 실행기는 이미 검증한 32개 집계 입력만 받는다. 기간·측정·원천·계산식·기준선이 바뀐 입력은 거부한다. 원본 CSV를 받아 자동으로 피처를 계산하거나 새로운 스키마에서 후보를 발굴하는 범용 파이프라인은 이 패키지에 포함하지 않았다. `feature_catalog.json`은 정의·측정 결과이며 실시간 변환 실행기가 아니다. 서버 실행 코드와 연결하지 않는다.

## CPU로 결과 확인

저장소 루트에서 프로젝트 Python 환경을 사용한다. 아래 명령은 GPU 모델을 로드하지 않는다. CPU 의존성은 `jsonschema`이며 확인한 버전은 `requirements-cpu.txt`에 있다. 새 설치는 프로젝트 승인 규칙을 따른다.

```bash
python -B research/models/rule_generator_v0_1/demo_cli.py inspect
python -B research/models/rule_generator_v0_1/demo_cli.py --help
python -B -m unittest discover -s research/models/rule_generator_v0_1/tests -v
```

저장된 모델 응답의 코드 검증:

```bash
python -B research/models/rule_generator_v0_1/demo_cli.py validate \
  --input-json research/evaluation/rule_generator_v0_1/examples/real_01_f004.json \
  --response research/scenarios/rule_generator_v0_1/confirmed_hr_response.json
```

위 응답은 이미 생성된 HR 응답의 복사본이다. 새 모델 생성이나 수동 보충 성공으로 세지 않는다.

## GPU로 한 입력 재생성

원본 연구의 확인 환경은 Python 3.10.20·torch 2.11.0+cu129·Transformers 5.19.0·PEFT 0.21.2·bitsandbytes 0.50.2다. 전체 정보는 `environment.json`에 있다. 현재 Docker와 할당된 GPU를 사용하고, 실행 직전 사용 상태를 확인한다.

```bash
export RULE_MODEL_BASE_PATH='/path/to/local/Qwen3.6-27B/pinned-snapshot'
export CUDA_VISIBLE_DEVICES='assigned-visible-gpu-index'
python -B research/models/rule_generator_v0_1/demo_cli.py run \
  --input-json research/evaluation/rule_generator_v0_1/examples/real_01_f004.json \
  --max-repairs 1
```

Base는 manifest의 `Qwen/Qwen3.6-27B` revision을 사용한다. 모델 metadata SHA를 검사하며, 전체 Base 가중치의 재해시는 하지 않는다. 자동 다운로드·설치·학습은 하지 않는다. 검증한 생성 설정은 NF4 double quantization·bf16·greedy·thinking off·출력 최대 512 tokens·문법 강제 없음이다. 정확히 한 개의 노출 GPU가 필요하다. 원본 실행의 peak reserved memory는 약 22.2 GiB이며 환경별 메모리 여유는 별도 확인해야 한다.

새 결과는 `research/models/rule_generator_v0_1/runs/<새 실행시각>/`에 저장한다. 기존 결과를 덮어쓰지 않는다. 최대 최초 32개·수정 16개, 입력별 수정 최대 1회다. 새 응답은 직접 의미 검토 전 `awaiting_meaning_review` 상태다. `review` 명령은 응답 SHA에 연결된 검토만 반영하며 코드 실패를 승인으로 바꾸지 않는다.

## 결과와 공개 복사본

기록된 개발 결과는 모델 HR 5개·SR 15개, 보류 1개·근거 요청 5개·적용 불가 5개·실패 1개다. 수동 예제는 0개다. 최초 의미 승인 28/32, 수정 4건 중 3건 승인으로 최종 판단 승인 31/32다. 독립 평가 정확도나 규칙 31개를 의미하지 않는다.

고객 원본 CSV/SQL·행별 값·거래 ID·학습 자료·환경·체크포인트는 배포하지 않는다. 공개 입력은 피처 정의와 집계 통계이며 원본 artifact 경로를 제거했다. 통계·스키마 이름·기간·근거 SHA는 연구 결과 설명을 위해 유지했다. 모델 출력 원문과 숫자는 바꾸지 않았다. 원래 프롬프트의 경로를 지운 공개 입력은 원본과 byte 단위로 같지 않으며, 공개 입력의 GPU 재생성은 이번 작업에서 실행하지 않았다.

공개 source gate는 동결 집계 자료의 SHA·Body 제외·보류 계보 제외·기간을 확인한다. 원본 CSV 입수·원천 재검증은 수행하지 않으며 원본 연구 실행의 사전 검증 기록을 참조한다. 변경한 자료를 기존 자료로 조용히 대체하지 않는다.

`f015`·`f017`의 `비율 > p95=1`은 계산 범위 0~1에서 발동할 수 없다. `f006` 상태 빈도는 오프라인 표본 집계이며 실시간 시간창 정의가 없다. 계약 검증 결과와 진단 효용·운영 승인을 구분한다. [효용 검토](../../evaluation/rule_generator_v0_1/rule_utility_review.json)에 해당 사항을 기록했다.
