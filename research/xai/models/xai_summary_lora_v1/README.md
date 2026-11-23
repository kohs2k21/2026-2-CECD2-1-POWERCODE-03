# XAI Summary LoRA v1

- Base: `Qwen/Qwen3.6-27B`, revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`.
- 학습: NF4 double quantization·BF16 Base, LoRA rank8/alpha16/dropout0.05, AdamW1e-4.
- 학습 단위: 거래별 근거 요약64 TRAIN·12 validation·16 TEST 사례. raw CSV 전체를 토큰으로 학습하지 않음.
- 교사: AI 작성·코드 검증 silver. 운영 전문가의 실제 장애·원인 라벨이 아님.
- 2epochs·32optimizer steps, assistant answer+EOS에만 loss, seed42.
- 선택: validation answer-token-weighted NLL로 epoch2 선택, TEST로 checkpoint 선택하지 않음.
- 원래 학습 출력: `xai-summary-plan/1.0.0` 근거 ID와 확인 코드.
- 현재 권장 출력: `xai-evidence-selection/1.0.0` 근거 ID1~3개만, 확인 안내는 전체 근거에서 코드 생성.
- 원래16TEST에서 Base12/16·어댑터16/16. 후속5거래41분할 새 계약에서 각각41/41.
- 가중치는 후속 계약 보완 후에도 동일하며 이 업로드에서 추가 SFT 없음.
- 구조 유효성은 실제 원인 진단·독립 일반화·LLM의 실무 우월성을 입증하지 않음.
- 학습 당시 양성 논리 SR 교사 없음. 후속 서비스 HR·관계·프로토콜 입력의 실제 품질은 별도 평가 필요.
- 서버 절대 경로를 제거한 config를 제공하며 `model_manifest.json`에 원본/공개 config SHA를 각각 기록.
- Base 라이선스는 상위 `BASE_LICENSE.txt` 참고. Base 전체 가중치와 고객 원천 자료는 포함하지 않음.
