Created: 2026-10-17T14:29:35.9678950+09:00
Updated: 2026-10-17T14:35:55.9994574+09:00
Author: research_model_diagnostics
Status: draft

# 유형별 SR·raw flag OR 독립 비교

- 범위: 연구 전용 통제 합성 집계. 기존 장기추세 소스·모델·실험 결과·운영 변경 없음
- 고정 비교: 공통 W1, primary C5, 추가 C1 진단. seed101/137/173 정상 train7/cal7일 → 정상자료로 모델·cut·적격 잠금 → 새 blind14일·관계 개입 제거 CF
- 10군: old_sr, family_sr, IF/EIF full43 및 relation2, family_sr와 네 forest의 결합
- forest main12fit: 2알고리즘×2피처패키지×3seed, 각128tree/sample256/CPU1. fixture9102의4fit은 별도 기능검사
- old_sr: 기존40active의 최대 정상 편차, clock3 포함. family_sr: 같은 train 통계에서 count18·latency17·relation2의 최대 편차, clock3/neutral3 제외
- full43은 기존 전체 정상 공통 C5 train 행, relation2는 그중 두 관계 피처 모두 valid인 train 행 및 전용 encoder. 두 패키지 차이는 차원과 train 코호트 모두 포함
- relation2/관계 family: 공통 원천 ready 및 관계2열 모두 valid일 때만 점수. count/latency family는 유효 물리 피처 하나 이상 필요
- OR 삼값: 유효 양성 분기 하나면 양성, 모든 분기가 유효 음성이면 음성, 나머지는 미판정. 미지원 관계를 정상 점수0으로 대체하지 않음
- 단독 family 각1/3 예산, 결합 SR family 각1/6+ML1/2 예산. 모든 cut는 정상 cal에서 strict `raw > cut`, 기존 사전 quantile 후보+할당 예산별 group/cadence floor로 고정
- C1/C5/C15×모든 필수 그룹의 분기 예산 및 실제 OR 전체 예산 검사. 정상 오경보 ≤.05/그룹시간·양성시간 ≤1%; 조건부 monitored 분모와 공통 원천 eligible 분모 함께 기록
- cut<1 및 각 fitted forest의 tree별 최단 leaf 기반 보수 score 상한 검사. 상한 아래 cut가 실제 동시 도달 가능한 입력을 보장한다는 의미 아님
- 검사 실패 군은 이유와 함께 부적격·미판정. 예산 완화·holdout 기반 재선정 없음
- CF는 동일 RNG에서 관계 duration 재대입만 제거. 실제60사건·CF48서비스사건·paired12관계창 분모 분리, CF 관계창 시간은 정상 오경보/양성시간 포함
- 새 episode 시작·10분 gap 병합·전체60분모·360분 미탐손실·회복 뒤 정상 경보 부담은 기존 평가 함수를 읽기로 재사용

```powershell
# 독립 코드 검토·fixture 확인 뒤 실제 출력 경로 지정
.\.venv\Scripts\python.exe -B research/evaluation/trend_family_comparison/run_experiment.py seal --output <outside-repo-source-seal>
.\.venv\Scripts\python.exe -B research/evaluation/trend_family_comparison/run_experiment.py screen --seal <seal.json> --output <outside-repo-screen> --fixture
.\.venv\Scripts\python.exe -B research/evaluation/trend_family_comparison/run_experiment.py evaluate-blind --screen <fixture-screen> --output <fixture-blind>
# main normal phase: --fixture 제외. main blind는 별도 root GO 이후 evaluate-blind
```

- 저장: 소스 snapshot/SHA·환경·모델·참조·정상 cut lock·train 행수/시각/hash/cap 정책·정상 cal 원시 점수·실제/CF 분기 raw/cut/flag·episode·사건·pair·coverage 원장
- source seal 또는 정상 lock 이후 코드·protocol·참조·모델 SHA 변경 시 실행 차단
- 출력은 저장소 밖 새 폴더만 허용. holdout 추가fit0/재보정0. 연구 성능과 실제 고객 장애 정답·운영 가용성·SLA 구분
