Created: 2026-10-14T18:04:09.1221691+09:00
Updated: 2026-10-14T18:11:01.6494366+09:00
Author: research_process_benchmark_builder
Status: draft

# PROCESS Rule·ML 순차/동시 실행 비교

- 목적: 같은 PROCESS의 Rule→ML 모두 직접 실행과 Rule/ML 독립 동시 실행 비용·예정 전체 결과 비교
- 대상: 기존 봉인 v4 seed451 PROCESS global IF; v12 1분 grid 모델 직접 사용·재학습 없음
- model SHA256: `fe790aa7e8bc9040837fe861c8aaac09ae400165d0ffd62364f27b6d65ce1b94`
- threshold: `2.306674688421936`; 전처리·normalizer·history·threshold 기존 봉인값 사용
- 작성자: 코드·시험 작성만. 기능 시험·부하 실행·결과 검수 별도 담당자

## 공정성·자원

- 순차: 한 PROCESS worker에서 Rule 함수 완료 후 ML 함수 직접 호출. Rule hit/error에도 ML 실행
- 동시: 동일 Rule·ML 함수 각 executor1에 제출 후 두 결과 수집
- 두 군 준비 시 같은 executor 생성; 순차 inner executor는 idle. 자연스러운 순차 비용과 동시 제출·대기 비용 포함
- PROCESS in-flight1, 대기1000; timer Rule executor1·대기1000 동일. 무제한 PROCESS/branch 병렬 없음
- 실제 interpreter child CPU4 하나, parent CPU0..3; BLAS/OMP/MKL/OpenBLAS/NumExpr1·IF n_jobs1 readback
- CPU affinity는 독점 quota 아님. Windows·SMT·host background 간섭 가능; multicore speedup 주장 없음
- PROCESS당 timed prepare+encoder1회, read-only features 공유. Rule은 prepared counts/completion·ML은 같은 frozen encoder/model/normalizer/threshold
- 원 v4 준비900초 source context와 causal `[t−300,t)` 시간창·5초 timer 동일; history는 load, fit 없음
- 시간창 Rule 배율1 고정, 추세 ML 제외. 준비900초 timer replay는 가상시간·측정 제외, 60초10Hz completed normal warmup·timer 완료 후 측정
- trace seeded source observations 고정, measurement count관계5% 주입; ML 품질 정답으로 해석 금지
- 측정 불완료5%·처리시간증가5%·count증가5% 유지. missing completion ML unavailable, Rule unknown 가능; 정상 False로 대체 금지

## 실행·출력

```powershell
.venv/Scripts/python.exe -m unittest research.evaluation.detector_dual_path.tests.test_process_pair
.venv/Scripts/python.exe -m research.evaluation.detector_dual_path.process_pair --run-dir research/evaluation/detector_dual_path/runs/v4_balanced_20261014 --output research/evaluation/detector_dual_path/runs/process_pair_60s_20261014
```

- 탐색: 60초·10/100Hz·4paired repeats, 순서 AB/BA/AB/BA 각2회·16fresh child trials; 같은 CPU4·seed451
- 결과 확인 후 총괄 선택 시600초 follow-up; CLI `--duration 600`. 60초 탐색으로 production/SLA 판단 금지
- 예정 release `perf_counter`→완료 latency, producer lateness·queue wait·prepare·branch start/end·compute 기록
- fixed inputend+60초 drain deadline, 큐초과·기한미완료·inputend미제출도 예정 전체 분모 유지
- complete: 두 branch complete. missing/error/timeout: partial; 알려진 Rule true이면 최종true 가능하되 complete 아님. 알려진 false만으로 전체 정상 금지
- 기한 snapshot 이후 running future는 저장 결과 수정 불가. 저장 후 dedicated child 종료, hanging thread별 kill 불요
- declaration: 교대순서·입력조건·code/protocol/model hash·thread env. child contract: trace/history/manifest hash·schema·threshold·pid/interpreter/affinity
- child warmup/result JSON: 모든 예정 PROCESS/timer record, child CPU/RSS·대기peak·coverage·실패포함3초 초과율
- comparison JSON: paired finalized complete/partial 교집합의 Rule/ML상태·prediction·ML margin·feature hash mismatch, complete교집합·군별 only-complete·전체 scheduled 실패율; 미완료 분모 숨김 금지
- 별도5초tester smoke: root 허용 후 `trial(args)` 직접호출 child, `trial_scope=smoke_unpaired_not_comparison`; paired60초 본시험 비교에 합산 금지
- forced fault 단위시험 별도; 성능 trace에 인위적 worker sleep/exception 없음
- 원 v4 code·protocol·model·runs 수정 없음. 고객/API/DB/network·서버·Notion·Git 업로드 범위 제외
