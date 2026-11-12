# 선택 모델과 제한

기본 Feature는 원본 Qwen/Qwen3.6-27B 위의 **기존 silver51 Feature LoRA**이며, Rule은 별도 고정 LoRA이다. Base revision은 `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`, 실제 클래스는 Qwen3_5ForConditionalGeneration이다. 로더·manifest·화면의 기본값이 같다. 누락/잘못된 어댑터에서 Base로 자동 대체하지 않는다.

선택 근거: 새 result-view 계약의 동일한 공개 입력·토큰·문법·NF4/BF16·greedy/thinkingFalse·1536 상한에서 양성 5개 계산은 Base/Feature51 모두 5/5였다. 완결된 근거 내 AI 설명은 Base4/5, Feature51 5/5였다. 별도 적절한 보류는 Base0/1→수정1/1, Feature51 최초1/1이다. 이는 좁은 지원 범위의 선택이며 수학적 우월성·자유 발굴 완성·새 기업 일반화의 증명이 아니다. 이 단계의 추가 SFT는 0회이다.

선택 Feature의 역사적 학습: 새 Base 시작, silver51건, 1epoch, optimizer13steps, 언어 LoRA 학습 파라미터23,760,896, 750.95초. rank8/alpha16/dropout0.05, batch1/accumulation4, LR1e-4, seed42, NF4/double-quant/BF16, assistant+EOS만 loss. 실제 최대2667tokens, 절단·누락0, 샘플별1회 소비. adapter/optimizer/RNG/order/exposure 체크포인트와 Base 동결·유한 gradient·LoRA 변경이 원래 실험에 보존된다. 개인정보가 포함된 실제 교사 입력은 배포하지 않으며 SHA·구성은 `training_recipe.json`에 남긴다.

51건은 지정30·수정19·보류2, bound정의36·추상계산구조31이다. 독립 계산방법51개가 아니다. 실제 모델 수정쌍4건과 통제 삽입 오류를 구분한다. validation2건은 일부 통제 원천 행을 공유하므로 NLL을 독립 선택 근거로 쓰지 않는다. 현재 result_use 계약으로 재학습하지 않았으며 실제 생성·검증으로 호환성을 확인했다.

과거 strict nullable OR 기반 중첩 가용성 실패와 빈 자식 평균0 설명 오승인 기록은 보존된다. 이번에는 결과 검사 선택 계약과 compiler null-branch 규칙, 계산 사실/AI 설명 분리로 해당 실행 경로를 변경했다. 이 효과를 새 SFT 가중치 개선이라고 표현하지 않는다.

고정 Rule의 역사적 학습은 silver527건·2epochs·264steps이다. 교사 오류2건의 교정 복사본은 존재하지만 현재 가중치에는 과거 노출이 남아 있다. 이번에는 Rule을 재학습하지 않았다. 실제 현재 조건·근거·설명 검증은 노출 이력이 없어졌다는 증명이 아니다.

각 LoRA는608개 언어 tensors만 포함한다. full-attention q/k/v/o와 hybrid in_proj_qkv/in_proj_a/in_proj_b/in_proj_z/out_proj를 포함하며 Vision/Base 학습 tensors는 없다. weight SHA와 공개 config SHA는 manifest에 고정한다. Base는 배포하지 않는다.

지원: 승인된 명시적 메타데이터 캡처, 선언된 관계, 같은 행 연산, result_use 가용성, 자식 평균/합계/개수, 집합 제외, 확인 HR, 관측 SR, 별도 TRAIN 참조 SR, 정확한 근거 요청/보류. 미지원: 부모 속성 조회, 임의 스키마/업무 의미 추론, 온라인 SLA/갱신 승인, IF·실제 장애 탐지. 원시 IN/VOID/UNKNOWN/MAP은 범위가 확인된 문서 없이 업무 의미로 확장하지 않는다.

AI 문장과 compiler 공식 사실은 별개이다. 자동 설명 검사는 한정된 모순·완결성을 확인하며, 모든 자연어·업무 타당성을 증명하지 않는다. 최종 기능 실행과 사후 의미 검토는 `EVALUATION.md`에 별도로 기록한다. 운영 반영 전 사용자의 원천 승인·업무 근거·온라인 가용성 검토가 필요하다.
