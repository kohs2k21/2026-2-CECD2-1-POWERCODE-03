# Research workspace

`research/` contains exploratory notebooks, scenarios, model experiments, and evaluation notes. It is not a runtime dependency of `server/`.

- [이상 탐지 연구 (#38)](anomaly_detection/README.md): 분석 노트북·규칙/IF·K-means·시간창 선택·평가 결과 및 학습 모델 포함, 데이터셋 제외

## 파생 변수·Rule 생성 모델 v0.1 (#40)

- [모델 코드·실제 SFT v2 LoRA 어댑터·모델 카드](models/rule_generator_v0_1/README.md)
- [집계 입력·HR/SR 결과·브라우저 데모](evaluation/rule_generator_v0_1/README.md)
- [개발 사례 구성](scenarios/rule_generator_v0_1/README.md)

피처64개·모델 생성 HR5/SR15와 보류·근거 요청·실패를 공유하는 개발 데모다. 고객 원본 로그·Base 가중치·환경은 포함하지 않는다. 임의 새 스키마 자동 설계, 실제 이상탐지와 운영 배포는 별도 작업이다.
