# XAI 모델 연구 프로토타입 (#237)

동결된 HR·SR·ML 탐지 결과에서 **어떤 값과 조건 때문에 경고했는지** 설명하는 전달본이다.
기존 `research` 모델·데이터·평가 파일은 유지하고 이 디렉터리만 추가한다.

| 구성 | 역할 |
|---|---|
| `models/xai_summary_lora_v1/` | 실제 QLoRA SFT 완료 어댑터, 가중치 원본 SHA 보존 |
| `xai_runtime/` | 근거 선택 검증, 고정 문구, 전체 상세·판정·평가 불가 보존 |
| `service_bridge/` | 서비스 evidence v4의 HR·SR·ML 결과, category k, 심각도 연결 |
| `integration/backend.patch` | 기존 백엔드 explain 작업에 전체 근거를 전달하는 검토용 패치 |
| `examples/` | 고객 데이터가 아닌 실행 가능한 합성 예시 |
| `evaluation/` | 집계 성능·검증 결과, 공개 재현 CPU 로그 |

## 현재 완료 범위

- 저장된 근거를 대상으로 하는 XAI LoRA 학습·생성 평가 완료, 탐지 모델·규칙·임계값 변경 없음.
- LLM은 요약할 근거 ID 최대3개만 제안하며, 코드는 원래 수치·판정·등급·전체 근거와 확인 안내를 보존.
- 서비스 연결 코드는 코드 기본 설명으로 실행 가능하며 GPU를 자동 호출하지 않음.
- 서비스 API의 실제 LoRA 자동 호출, 운영 PostgreSQL 작업 큐·JWT 서버 검증과 배포는 후속 통합 범위.
- 실제 장애 원인·운영 피해·기업 유용성은 미확인. 설명이 네트워크 장애 같은 원인을 확정하지 않음.
- 추가 질의형 대화·자율 학습 기능은 제외.

## 실행

리포지터리 루트에서 사용한다. Python 3.10 환경을 검증했으며, 기존 프로젝트 환경에
`requirements-test.txt`의 의존성이 있으면 CPU 검사를 바로 실행할 수 있다.
설치가 필요하면 프로젝트의 패키지 설치 승인 규칙을 따른다.

```bash
python -B -m unittest discover -s research/xai/tests -v
python -B research/xai/run.py \
  --input research/xai/examples/service_HR.json \
  --output /tmp/xai-service-example.json
```

출력 파일은 새 경로여야 한다. `run.py`는 입력의 기존 판정을 설명하며 새 탐지를 수행하지 않는다.
연구용 `xai-evidence/1.1.0` 입력도 `examples/research_packet.json`으로 확인할 수 있다.

서비스 설명 예시는 HR 조건과 원시 입력, 최종 기여 방법, 심각도, 평가 불가 사유와 버전을 포함한다.
`causeStatus=unconfirmed`가 기본이며 LLM이 이를 실제 원인 확정으로 바꿀 수 없다.

## 실제 XAI LoRA 호출

Base는 `Qwen/Qwen3.6-27B`, 고정 revision은
`6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`다.
어댑터는95,136,344바이트이며 Base 전체 가중치는 포함하지 않는다.
[`models/model_manifest.json`](models/model_manifest.json)에 가중치 SHA와 학습·검증 계보가 있다.
Base의 라이선스·고정 파일 목록은 [`models/`](models/)에 보관한다.

`selection_runner.py`는 로컬에 준비된 Base와 어댑터를 읽고 고정 JSON 문법으로 근거 ID만 생성한다.
자동 다운로드·학습·임계값 갱신은 하지 않는다. `requirements-inference.txt`와
`environment_verified.json`을 참고하고 할당된 BF16 GPU 하나를 선택한다.
최초 연구 실행의 PyTorch는 CUDA12.9 빌드였으며, 전달 환경의 CUDA 호환성은 별도로 확인한다.

```bash
CUDA_VISIBLE_DEVICES=0 python -B research/xai/selection_runner.py \
  --base-dir "$XAI_BASE_DIR" \
  --view research/xai/examples/selection_view.json \
  --output /tmp/xai-selection-receipt.json
```

위 `0`은 현재 컨테이너에 할당되어 보이는 GPU일 때만 사용한다.
Base의 고정 파일 SHA와 어댑터 SHA를 검사하며 입력3,500토큰·출력512토큰 상한을 넘으면 자르지 않고 중단한다.
실제 생성 계획은 응답의 `selection`에 있다. 같은 입력의 ID만 후단에 전달해야 한다.
이 이식용 래퍼는 CPU 구조 검사를 마쳤으며 이번 업로드 과정에서 새 GPU 생성을 실행하지 않았다.
과거 실제41/41 결과는 원래 동결 실행기의 개발 평가이며 새 래퍼의 운영 성능으로 승계하지 않는다.

## 백엔드 전달

[`integration/BACKEND_HANDOFF.md`](integration/BACKEND_HANDOFF.md)에 입력·출력·기존 작업 API·패치 적용 범위를 정리했다.
이번 PR은 `research/`에만 추가하며 `server/`·DB·배포 설정을 바꾸지 않는다.
기본 코드 설명 경로와 명시적 LLM 선택 경로를 구분해 연결해야 한다.

## 검증과 한계

이전 연구 CPU95/95, 원래 생성 평가의 새 근거 선택 계약에서 Base41/41·기존 XAI LoRA41/41을 기록했다.
이는 동일 회사5거래·41분할의 개발 검증이다. 독립 고객 일반화·원인 진단·실무 효용을 입증한 수치가 아니다.
이 전달본의 CPU 검사 수와 실제 실행 기록은 [`evaluation/publication_validation.json`](evaluation/publication_validation.json)에 있다.
회사 담당자 평점은0건이고 실제 장애 원인 확정도0건이다.

고객 CSV·실제 거래 ID/패킷·학습 JSONL·optimizer 상태·인증정보·Base 가중치는 포함하지 않는다.
원래 연구의 전체 데이터 재평가와 SFT 재현에는 별도로 승인된 비공개 원천 자료가 필요하다.
