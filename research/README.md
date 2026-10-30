# Research workspace

`research/` contains exploratory notebooks, scenarios, model experiments, and evaluation notes. It is not a runtime dependency of `server/`.

- [이상 탐지 최신 연구 (#80)](anomaly_detection/README.md): 단일 IF·K-means·LOF, 선택 구간 장기 모델, 새 데이터 학습·예측·평가 진입점과 결과 노트북 (박준서)
- [이전 이상 탐지 연구 (#38)](anomaly_detection/LEGACY_ISSUE38.md): 기존 v12 모델·분석·참조 아카이브·캐시 보존, 기간별 원본 및 벤치마크 CSV·정답 추가 없음 (박준서·고민성)
- [PROCESS 행 단위 Soft Rule 프로토타입 (#75)](evaluation/process_sr_prototype/README.md): 행 1개+어댑터 train 기준선으로 판정하는 SR 후보 6종(robust_z·iqr_fence·quantile_tail·centered_tail·ratio_rule·count_conservation) 조사·fixture 실행·카탈로그 메타, 데이터셋 제외(지어낸 lab fixture만, 고객 데이터 없음)

## 파생 변수·Rule 생성 모델 v0.1 (#40)

- [모델 코드·실제 SFT v2 LoRA 어댑터·모델 카드](models/rule_generator_v0_1/README.md)
- [집계 입력·HR/SR 결과·브라우저 데모](evaluation/rule_generator_v0_1/README.md)
- [개발 사례 구성](scenarios/rule_generator_v0_1/README.md)

피처64개·모델 생성 HR5/SR15와 보류·근거 요청·실패를 공유하는 개발 데모다. 고객 원본 로그·Base 가중치·환경은 포함하지 않는다. 임의 새 스키마 자동 설계, 실제 이상탐지와 운영 배포는 별도 작업이다.

## 피처·규칙 자동 명세 연구 v0.2 (#40)

- [피처 v5·규칙 v2 코드와 실제 어댑터](models/feature_rule_design_v0_2/README.md)
- [20개 평가와12개 규칙 판단 기록](evaluation/feature_rule_design_v0_2/README.md)
- [지정 분석·자유 발굴·지원 한계 시나리오](scenarios/feature_rule_design_v0_2/README.md)
- [기존 IF 연구와 입력·역할 연결 관계](models/feature_rule_design_v0_2/CONNECTIONS.md)

최신 완료 연구 v5를 보관한다. 동일 입력에서 피처 v4 3/20→v5 14/20, 자유 발굴0/8→6/8을 기록하며 기존 v0.1과 이상탐지 연구를 보존한다. 신규 IF 연결·서버 변경·실시간 탐지·운영 승인은 포함하지 않는다.
