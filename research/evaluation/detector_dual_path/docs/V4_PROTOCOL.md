Created: 2026-10-14T11:56:57.9068268+09:00
Updated: 2026-10-14T12:01:07.7505357+09:00
Author: research_experiment_builder
Status: draft

# v4 재현·해석 계약

- 범위: 새 합성 공정 비교·동일 자원 부하; 기존 v3 코드/config/model/runs 보존
- 작성자: 코드 작성만; 시험/재학습/실험 실행은 별도 검증 담당자
- 고정 최신 GitHub source: cb006179ec61531e547af3d33269cd596084fb5f, PR47 미병합 snapshot
- v12 입력: 완료 PROCESS와 연속 1분 grid/prefix; 단일 PROCESS 추론 모델 직접 대체 불가
- v12 원본 재fit: development 입력·benchmark/history·중간 frozen 산출물 미업로드로 불가; portable hash 검증 smoke는 별도 결과
- v4 주시험: 기존 v3 PROCESS global IF 재fit + 최신 snapshot v5 RawModel IF128/v8 ClusterDistance radius 재fit. v12 복합 분기 전체 재현 아님
- v4 추가 입력: observed elapsed·7요일/24시간대·동일 guard/support/missing·300초 창. 최신 모델과 이름/스키마/재적합 경계 분리

## 품질 실행

```powershell
python -m unittest research.evaluation.detector_dual_path.tests.test_v4
python -m research.evaluation.detector_dual_path.v4 --output research/evaluation/detector_dual_path/runs/v4_balanced_20261014
```

- 새 출력 폴더 필수; 기존 sealed test 덮어쓰기 금지
- seed451~455, 역할별 시간 순서/블록 ID 독립; 월~일·각24시간대 2날짜 이상
- 정상 역할마다 336블록, 블록 준비900초+적격240초=22.4h/seed; 최종 독립 정상112h. 비연속 표본 공백 미집계
- validation/test: 시나리오당7요일×3시간대21주입+짝정상, 별도 관측 대조. 순수 유입 폭주와 개별 count 증가 분리
- fit: train encoder/history/model, calibration normalizer/cluster radius, threshold 정상 한도; validation만 후보 선택
- PROCESS A: nextafter(threshold 정상 최대 정규화 점수,+inf) 고정; 모든 군/budget/algorithm/cadence에 동일
- 단순 Rule: cohort99분위 관측 신규 간격·300초 유입량·duration·count 상대비 최대값, 배율1/2/4/off 사전 후보
- ML: threshold 정상 최대 정규화 점수+0/1/3/off 후보; IF/KMeans 각각 독립 비교
- A/B/C/D: 공통 PROCESS/논리 Rule + 선택 추세 경로; 최종 합집합60초 억제 경보/h 예산0.1/0.5/1
- A 예산 초과 시 해당 전체 군 부적격; D 공동 임계값. test 예산 초과 실패 공개, 후보 재선정 금지
- 선택 순서: 예산→동일 시나리오 사건 recall→미탐지300초 패널티 포함 지연→고정 계산량 proxy→후보 ID. proxy는 실제 CPU 비용 아님
- test는 seed별 selection_seal 저장 후 처음 생성; 같은 score 재사용으로 동결된 모든 예산 평가
- q=길이([t−300,t)∩[s,e))/300; onset 선행/active/recovery/clean 분리. s<t<e hit, 복구 잔류는 hit/clean FA 미합산
- 복구: scenario끝=e+300+61초, 첫 eligible q0에서 raw추세경보 없음·직전stride 실시간경보 없음의 지연과 right-censor 기록. q0는 개입도착 겹침 종료, 지연 완료행의 clean 보장 아님
- 학습 source 내부 BLAS2/n_jobs2는 원본 fit 코드 보존; 부하 추론은 BLAS1/n_jobs1 고정. 학습비용/동일자원 부하 해석 구분
- 논리 count 계약: 관측 success+error≠total로 직접 판정; ML 능력 사건 recall 분모 제외
- 출력: declaration, 역할별 피처/모델 hash·manifest, 달력 support CSV, validation, seal, raw test window CSV, event/alert JSON, 비교 CSV, seed 변동 summary
- 예상 실행: 가상 시간 재생, 학습/데이터 처리 CPU 비용만 소요; 10~30분 추정·미측정

## 실제 부하 실행

```powershell
python -m research.evaluation.detector_dual_path.v4_load --run-dir research/evaluation/detector_dual_path/runs/v4_balanced_20261014 --output research/evaluation/detector_dual_path/runs/v4_load/run_01_A --group A --algorithm if --seed 451 --rate 10 --stride 60 --cpu 4 --repeat 0 --warmup-epoch <UTC_UNIX_SECONDS> --measurement-epoch <UTC_UNIX_SECONDS>
```

- 외부 coordinator: A/B/C/D 4child 동시 시작·같은 UTC barrier/trace/ready hash. 모델 로딩 충분한 시간 후 warmup barrier
- 공통 warmup60초→큐 drain/ready.flag 확인→measurement barrier. drain 완료가 공통 barrier보다 늦으면 해당 군 실패 중단
- measurement barrier는 warmup 종료+최대60초 drain+준비 여유보다 뒤로 지정; 준비 대기는 실측600초 분모 밖
- 실행 설정: rate10/100×stride60/5×4회, 각 군1logical CPU, [4],[6],[8],[10] 서로 다른 물리 코어에 cyclic 배정; 실제 topology 검증은 실행 담당자
- 슬롯: RT1+trend1, 별도 executor/큐 각1000; A 추세 슬롯 idle·실시간 재배정 없음
- `--smoke`: 실측60초만으로 스케줄/입출력 확인; 본 부하 통계와 혼합 금지
- 타이머: producer에서 대기, executor 점유 금지; model/feature preparation 계산만 worker 제출
- ready: 과거900초 정상 prefix; warmup60초와 실측600초 별도. future/truth 미포함; 예정 trace에서 창 시각 이후 도착/완료 항상 제외
- raw_jobs.csv: 예정/제출/시작/끝·producer 지연·큐 대기·prepare+추론·전체 local 지연·status/drop/affinity
- input trace: 완료/미완료/지연/큰 count 혼합의 PROCESS snapshot 수신, rate는 입력 snapshot/s. 정상 ready prefix와 10분 예정 입력 동일
- CPU/RSS: measurement+최대60초 drain 포함, 처리량 분모600초 유지. 완료-only 3초 초과율과 drop/pending 포함 전체 예정 실패율 병기
- timeout: partial 결과 저장 후 전용 child 종료; 실행 담당자가 child exit/성공 상태 확인
- `result.json`/`contract.json`: affinity·threadpool1·model/selection/trace hash·실측 범위·군별 sample count. 추세 P99 유보
- validation 부적격 군은 사전 고정 보수 preset 비용 probe 사용·명시; 효과 선정 결과로 주장 금지
- 비용 산술: 16동시블록×(60warmup+600measurement)=10,560초≈2h56m, drain 최대16분 추가. 준비 barrier 여유·모델 로딩 별도
- Windows affinity는 독점 quota 아님; host/SMT sibling CPU/RSS 외부 계측·child affinity 검증·4반복 paired 차이는 coordinator 담당
- 단일 logical CPU의 time-sharing 효과; 멀티코어 가속/운영 서버/ESB SLA 근거 아님

## 해석 한계

- 합성 제어 효과와 실제 고객 일반화 분리; 운영 승인 경보 예산 미확정
- 5seed는 5고객/현장 대체 아님; 중첩 창을 독립 반복으로 취급 금지
- 희귀 경보의 Poisson 상한은 가정 기반 참고값; 시간 종속 합성/고객 신뢰구간 보증 아님
- 비용 proxy와 실제 wall-clock 결과 구분; 부하 raw job/failed 분모/CPU 조건 확인 전 성능 결론 금지
