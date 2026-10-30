# Feature → measurement → HR/SR specification package

승인된 Body 제외 메타데이터에서 로컬 Qwen이 계산 계획을 생성하고, 코드가 정확히 그 계획을 검증·측정한 뒤 고정 Rule LoRA가 HR/SR 또는 구체적 보류를 작성합니다. 과거 응답 재생이나 수동 정답 대입은 GPU 실행의 대체 경로가 아닙니다. 이상탐지·IF 실행과 운영 승인은 포함하지 않습니다.

기본 구성은 **Qwen/Qwen3.6-27B + Feature silver51 LoRA / 고정 Rule LoRA**입니다. 두 어댑터는 별도이며 한 Base에서 전환합니다. 선택은 이번 동일 입력 비교의 계산 5/5 동률, 완결된 근거 내 설명 Feature51 5/5·Base 4/5에 근거합니다. 이 단계의 추가 SFT는 0회입니다. 정확한 버전·SHA는 `model_manifest.json`, 검증 범위와 과거 실패는 `EVALUATION.md`를 확인합니다.

## 환경과 모델 준비

현재 컨테이너의 기존 Python 3.10 환경과 `requirements-verified.txt` 버전을 사용합니다. 검증 장치는 BF16 지원 RTX A5000 24GB 한 장입니다. NF4/double quantization, BF16 연산, CPU embedding offload를 사용합니다. 공유 서버에서는 할당된 GPU 한 장만 지정합니다. 패키지·모델을 자동 설치하거나 다운로드하지 않습니다.

Base는 일반 Git에 포함하지 않습니다. 승인된 환경에서 아래의 정확한 공개 스냅샷을 준비하고, `models/base_files.json`에 있는 파일 SHA와 일치시킵니다. CLI는 다른 버전이나 누락된 모델을 자동 대체하지 않습니다.

```python
from huggingface_hub import snapshot_download
snapshot_download(repo_id="Qwen/Qwen3.6-27B",
    revision="6a9e13bd6fc8f0983b9b99948120bc37f49c13e9",
    local_dir="/your/models/qwen-base")
```

Feature·Rule 어댑터는 각각 `models/feature`, `models/rule`에 포함합니다(각 95,136,344 bytes). Base 원가중치·Vision 가중치는 포함하지 않습니다. 공개 설정의 Base 디렉터리만 repo ID로 바꿨으며, 어댑터 weight SHA는 원본과 동일합니다. 민감한 원천·교사 입력·인증정보는 배포하지 않습니다.

## 공식 실행 명령

이 디렉터리를 임의 위치에 복사한 뒤 실행합니다. 모든 output 경로는 새 디렉터리여야 합니다.

```bash
python -B verify_package.py
export SPEC_BASE_PATH=/your/models/qwen-base
export CUDA_VISIBLE_DEVICES=0  # 컨테이너에 할당된 장치 한 개

# 공개 통제 입력 8개: 실제 Feature·Rule 모델 생성
python -B design_cli.py batch --suite examples/delivery/suite.json \
  --base-path "$SPEC_BASE_PATH" --output ./runs/public-demo --max-model-calls 32

# 저장 원문과 입력 SHA 확인 + CPU 재측정 (새 모델 생성 0회)
python -B design_cli.py replay --run ./runs/public-demo \
  --suite examples/delivery/suite.json --output ./runs/public-demo-replay

# 사용자 승인 원천의 단일 실행
python -B design_cli.py pipeline --input-manifest /your/approved/capture.json \
  --request /your/request.json --rule-plan /your/rule-plan.json \
  --reference-manifest /your/approved/train-reference/capture.json \
  --base-path "$SPEC_BASE_PATH" --output ./runs/your-run --max-model-calls 4

python -B -m unittest discover -s tests -v
```

참조 비교를 요청하지 않으면 `--reference-manifest`를 생략합니다. `inspect`, `feature`, `validate-feature`, `rule`, `pipeline`, `batch`, `replay`의 `--help`에서 인수를 확인합니다. `validate-feature`는 사용자가 제공한 정의의 CPU 검사이며 모델 생성으로 집계하지 않습니다. `rule` 단독 실행도 동일 원천의 검증된 Feature bundle이 필요합니다.

## 입력

`examples/capture.json`과 `examples/delivery/`가 공개 예시입니다. manifest에는 표별 상대 파일 경로·SHA·논리 자료형·결측 토큰·원래 행 키·실제 자식→부모 연결 키·기간·TRAIN/적용 구분·승인 계보·범위가 확인된 근거를 제공합니다. 원시 CSV 전체를 임의로 승인하는 importer가 아닙니다. Body 컬럼과 보류 9/30~10/2 또는 그 병합 계보는 입장 단계에서 차단합니다.

모델은 계산 AST·분석 단위·모집단·필터·그룹 및 `result_use`를 선택합니다. `value`는 값 자체, `unavailable`은 계산 결과 가용성, `unavailable_or_zero`는 수치 결과의 계산 불가 또는 0 진단입니다. 모델에게 nullable OR로 원인을 나열하도록 요구하지 않습니다. 코드가 선택된 연산을 계약에 따라 컴파일하며, 잘못된 AST를 정답으로 바꾸지 않습니다.

## 출력과 해석

각 사례에 `feature_bundle.json`, `feature_values.json`, `official_feature_semantics.json`, `rule_input.json`, `rule_bundle.json`, `validated_rules.json`, `pending_decisions.json`, `counts.json`, `run_manifest.json`을 저장합니다. 후보 0개 또는 실패면 해당 단계의 기록을 남기며 정상 결과를 만들어 채우지 않습니다. `model/*/input.json`, `output.json`은 실제 모델 입력과 수정 전후 원문입니다.

`batch`는 루트 `validation_report.json`, `counts.json`과 `feature_values_by_grain/`도 저장합니다. 행 값은 `value` / `unavailable` / `not_selected`를 구분합니다. 정밀한 Decimal은 JSON 문자열로 직렬화될 수 있으며, 해당 열의 dtype을 함께 확인합니다. 서로 다른 원천 버전의 행을 자동으로 섞지 않습니다.

AI 원문·설명은 그대로 보존합니다. `official_semantics.author=compiler`, `not_AI_generated=true`는 코드가 확인한 타입·단위·행 범위·결측·관측·조건 분모입니다. 이를 AI가 작성한 문장이라고 표시하지 않습니다. `description_validation`은 자동으로 검증 가능한 주장 범위이며 모든 자연어 의미의 증명이 아닙니다. 공개 화면은 계산 사실과 AI 설명 승인 범위를 따로 보여 줍니다.

최초 생성과 최대 1회 수정은 따로 기록합니다. 설명만 수정하면 정상 AST·모집단·근거를 보존합니다. AST가 바뀌면 새 정의로 재측정하고 Rule도 새 정의·측정에 바인딩합니다. 실행 오류를 null/0/false로 숨기지 않습니다. `replay`는 저장 파일을 다시 읽고 원천·계산·설명·측정·조건을 대조하며 새 모델 생성과 별도로 표시합니다.

## 사용 범위

명시적 키와 물리 관계를 가진 승인 오프라인 메타데이터, 지원 AST, 한 요청의 정확한 모집단/그룹에서 검토용 명세를 만드는 도구입니다. `child_count/conditional_count/child_sum`은 부모 행, `parent_match_count`는 자식 행을 반환합니다. 서로 다른 행 단위를 암묵적으로 합치지 않습니다. 부모 속성 조회 같은 미지원 요청은 구체적으로 보류합니다.

HR은 확인된 범위의 DDL/계약에 한정합니다. 관측 SR은 측정 가능한 진단이며 장애 라벨이 아닙니다. 수치 SR은 호환되는 별도 승인 TRAIN 참조의 코드 통계에 한정합니다. 참조 없는 그룹에 임의 fallback이나 현재 p95 대체를 하지 않습니다. 온라인 갱신·SLA·장애 탐지 정확도·새 기업 일반화는 승인하지 않습니다. Rule 어댑터의 과거 교사 오류 2건 노출 이력도 유지합니다.

이 제출물은 검토 후 커밋할 로컬 패키지입니다. Git stage·commit·push·PR·병합은 수행하지 않았습니다.
