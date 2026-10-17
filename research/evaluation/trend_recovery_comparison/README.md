Created: 2026-10-17T15:16:31+09:00
Updated: 2026-10-17T15:16:31+09:00
Author: research_model_diagnostics
Status: implementation-review

# 고정 모델의 회복 반영·경보 해제 비교

- 범위: 연구용 알림 후처리. 기존 W1 점수·판정선·모델·원평가 불변, 새 모델 fit·원판정선 보정 0.
- 고정 3군: 결합용 유형별 SR 각 1/6 예산의 SR-only, 동일 SR+IF 관계2, 동일 SR+EIF 관계2. 기존 단독 SR 1/3의 seed137 부적격을 구제하는 비교 아님.
- 정책: 무해제 / 빠른 5·5·30분 / 보수 15·15·45분(발생량·지연·관계). 후보 및 성공 기준은 protocol.json 고정.
- 입력: 사건 정답·종료시각·대조 라벨을 제외한 당시 관측 정보. 원 모델 예측과 후처리 알림 별도 원장.

## 정책 계약

- 첫 원양성: 즉시 발행. 첫 시점이 quiet이면 시작시각만 기록, 연속 quiet의 실제 경과 10분에서 해당 분기 억제. 1분 시각 11개에 해당.
- 억제 후: 중간 영역에서 유지, 같은 방향의 강한 원시 증거 3개 연속 분에서 재개. 발생량·지연은 동결 정상학습 표의 Q01/Q99 밖, 관계는 짧은 관계편차가 기존 SR 관계 판정선 초과.
- quiet: 발생량·지연은 활동중·완전관측 최근 창 전체가 Q05~Q95 안. 관계는 최소30 pairs·전체 관측·분산 조건 및 동결 SR 통계 편차 최대값 ≤1.
- Q01/Q99는 기존 학습 표의 values에서 linear 분위수 조회한 회복정책 기준. 기존 원점수 판정선 재보정 아님. ML 관계2에도 SR 관계 proxy 사용, ML 정상성 증명이나 기여 설명 아님.
- 원음성: 상태 초기화. 원미판정: 원미판정 유지·상태 초기화. 회복 증거 부족만으로 원관측 가능 시간을 줄이지 않으며, 억제를 풀고 원양성으로 복귀.
- 삼값 OR: 어느 분기 양성이면 양성, 전부 준비된 음성이면 음성, 나머지 미판정. 억제로 OR만 미판정이 되면 원출력으로 fallback하고 관련 억제 해제·원인 기록.
- 모델값 수신은 native C5/C1 발행 시점만. 미발행 모델 점수는 정책에 영향 없음. 그 사이 기존 유효구간의 마지막 발행값 유지·1분 증거만 누적. 실제 원천 미판정은 기존 구간 규칙대로 차단.
- 중복 시각 거부·누락 분의 연속 증거 초기화. 같은 전체 입력의 반복 호출 결과 동일. 사건 라벨이 달라도 같은 관측 입력이면 정책 결과 동일.

## 실행·검수

- policy.apply_policy(branch_flags, metadata, signals, branch_families, policy, cadence): native 알림 배열, native 분기 원장, 1분 내부 상태전이 원장 반환. metadata 필수 row_id/group/decision_tick/eligible, 분기값 0/1/NaN.
- 정상 cal은 기존 저장 raw/metadata와 원 source hash 재생·정렬 검증 후 신호만 계산. C1/C5/C15 guard 유지, 재보정 없음. 과거 July 자료도 개발용 저장 raw 재사용.
- detector_eligible 상속과 policy_cal_eligible 별도 고정. 정상cal 실패도 진단 출력·미판정 mask를 보존하고 실패 seed/group/C 및 예산 이유 전수 기록, 정책 채택 불가. --policy-cal로 해당 잠금을 blind seal에 포함, 최종은 필수.
- fixture9102는 원모델·원 source/feature/raw/support bitwise 재현 검사. 새 fit 0. 새 출력 디렉터리만 사용.
- seal: 기존 계산 소스·정상 lock·pickle·원 raw/metadata와 새 계산 소스의 SHA 고정. replay 시작 전 전부 검증, pickle은 검증 후 로드. LF 소스 유지.
- 최종 source201/237/273은 모델101/137/173과 짝지어 2026-08-03부터 14일. 독립 개발 검수·봉인·별도 root GO 전 기본 차단. 최종 seal은 status=accepted와 현재 sources 해시가 일치하는 독립 검수 JSON 필요, 실행에는 --allow-final 별도 필요.
- 명령: `python -B -m research.evaluation.trend_recovery_comparison.run_experiment seal --phase fixture --screen <frozen-normal-directory> --prior-blind <frozen-blind-directory> --output <empty-seal-directory>`.
- 재생: `python -B -m research.evaluation.trend_recovery_comparison.run_experiment replay --seal <seal.json> --split calibration --output <empty-output-directory>`; blind는 `--split blind`. 최종 seal에는 `--phase final --approval <accepted-review.json>` 지정.
- OMP/BLAS/모델 CPU 1, 설치·환경·운영 변경 없음. 비용은 단계 총계이며 모델별 속도 우열 검증 아님.

## 평가·해석 경계

- 주 C5·보조 C1, actual 서비스60 / CF 서비스48 / 관계 짝12 별도. fixture는20/16/4의 기능검사이며 본 성능 주장 제외.
- 유지: 각 군 baseline-hit 사건 ID 상실0, 기존 hit 최초 알림 추가지연0, native 및 전체1분 finite/unknown 구간 mask exact 동일. 원분모·부분관측·미판정 유지.
- 정상 양성분 엄격 감소와 크기 보고. 25% 감소는 실용 보조 목표이며 필수 완료 기준 아님. seed×group의 0.05 경보episode/h·1% 양성 점유 목표 별도 검사.
- GT 진행중 실제 알림 조기해제0은 별도 엄격 안전 목표. recall 유지·평균 정상시간 개선만으로 채택 가능 판단 금지. 원양성을 억제한 진행중 분, 실제 조기해제 및 재개 횟수, 사건별 회복 첫음성·다음 사건/분할끝까지 안정음성 지연 병기.
- 엄격 안전은 진행중 억제분0 및 조기해제0 모두 요구. 사건 시작 전에 이미 억제되어 있던 경우도 진행중 억제분에 포함.
- 원미탐·알림미탐·전체미판정·종료전 이미 억제·후속음성 미관측은 회복 지연 0으로 대체하지 않으며 censor 이유와 null 지연 보존. 회복 지연은 관측된 첫 음성이며 이후 미관측 시간도 별도 보고.
- 내부 억제 전환시각과 native 발행 해제시각 구분. compare_alerts의 all_clear_count는 원양성이 계속되는 동안의 실제 양성→음성 전환이며, 원음성에 따른 자연 종료 제외.
- 기존 10분 episode 병합·사건별 1:1 onset 채점 유지. 새 hit는 알림 재개 포착이며 새 모델 신호 탐지로 해석 금지.
- 관계 CF는 시간대별 duration 값 목록을 보존하지만 시간 순서도 바꾸므로 모든 단변량 시간 패턴의 불변 대조 아님. CF 관계창도 정상 부담 평가에 포함·면제 없음.
- 재발 20/45/90분·연속 진행·unknown·동일관측 상이GT는 결정적 독립 state/evidence 시험만. 실제 모델을 포함한 근접 재발 성능과 운영 안전성 미검증.
