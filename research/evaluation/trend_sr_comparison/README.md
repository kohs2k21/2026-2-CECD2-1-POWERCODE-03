Created: 2026-10-17T17:53:18.813966+09:00
Updated: 2026-10-18T01:30:27.665212+09:00
Author: research_sr_methods_analyst
Status: 개발 전수·동결 최종·복합 스트레스 및 독립 검수 완료; 성능·안전 목표 미달

# SR 비교실험 실행기

[비교 결과와 검증 범위](RESULTS.md)에 개발 전수와 동결 최종 결과를 정리했다. 주요 후보는 정상 경보 예산 및 지속 장애 안전성을 충족하지 못했으며 운영 채택 성공을 뜻하지 않는다.

- 등록24방법, 실제30파라미터 변형×20W×7C core; B0/B2·A5/A15·무한CUSUM 별도감도
- 723설정/seed, 실행주기 포함4521셀/seed. 주요15형과 오류3형의 순위·정답분모 분리
- 모든 refit은 정상train, 동일cut은 정상cal에서 전체C·group 예산을 동시에 검사. quantile12개 전수검사
- 신규방법 달력은 Asia/Seoul. R00 A1/B1은 원43피처·family37·UTC·C5참조행·seed별4096표본을 그대로 재사용. 새정상참조/cut으로 재보정하므로 과거성능 재현 주장은 제외
- R00 A/B감도는 별도 adapted recipe. 신규군과 R00 차이는 수식 및 달력 해석 차이 포함
- R06 숫자는 W직사각창이 아닌 반감기H. 무한CUSUM의 유효W는 null. R21~23 W는 동결예측의 최근잔차창
- 극단 Poisson/Binomial/BetaBinomial 꼬리는 float64 logtiny에서 수치포화. 실제결측과 구분, 새범주·미공개·미관측을 정상값으로 치환하지 않음
- 상태형 방법은 당시 미공개bin을 소비하지 않음. 공통24h context와 source eligibility는 점수/양성 여부와 독립
- 예측방법: 시간당 정상train·주별 rolling-origin 잔차scale, fold마다 prefix만으로 대체값 추정. 최종trainfit 후 open-loop, 시험자료 업데이트/재fit 없음
- forecast subprocess별 CPU120초·RSS1GB 한도; fit실패/자원초과/미지원은 행으로 보존. 기존venv/전역 설치 변경 없음
- exact KS/Wasserstein/Spearman 공유cache, 평균화/분위수 sketch 근사 없음

## 실행

- Python3.12 환경과 numpy/pandas/scipy/psutil/statsmodels/patsy 필요. 선택한 외부 runtime경로를 PYTHONPATH로 명시
- 생성물은 Git 저장소 밖의 새폴더. 기존 출력폴더 재사용 거부. 예시는 운영승인이나 최종자료 개봉 권한을 생성하지 않음

```powershell
$env:PYTHONPATH='<격리 runtime_packages 경로>'
$env:OMP_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
python -m research.evaluation.trend_sr_comparison.runner pilot --output '<외부산출경로>/pilot' --pilot-train-days 42
python -m research.evaluation.trend_sr_comparison.runner development --output '<외부산출경로>/development' --gate '<검수완료 개발승인 JSON>'
python -m research.evaluation.trend_sr_comparison.runner final --output '<외부산출경로>/final' --development '<외부산출경로>/development' --gate '<선정봉인 SHA가 고정된 별도 최종승인 JSON>'
```

- --limit 통합시험은 integration_only_not_exhaustive 표시, 선정lock 생성 불가
- main의 최대3후보는 5seed 예산통과·가용성·macro·손실·정상점유·비용·단순성으로 순위. 같은 1분prediction+finite/support fingerprint는 대표1개; 서로 다른 실제 출력은 같은 방법이어도 유지
- 오류는 R13/R14별 상위설정1개씩 별도 error_selected. 단독branch와4분기OR 결과 분리
- 선정 후 R00+후보 전부 동일고정 legacy-fast proxy paired 비교. 기존5/5/30·quiet10·strong3, 신규error/역전hysteresis 근거부족은 raw fallback. 새SR의 정상확정이나 운영해제 규칙이라는 의미 아님
- 정상cal의 동일cut×전체7C fast 예산guard 별도 봉인. final main/error/normal/recurrence 모두 raw/fast·mask·hit상실·추가지연·진행중억제 저장
- source 잠금: 이 namespace의 모든 py/json(테스트 포함)와 실제 legacy 의존파일 SHA. 모델/참조/cut/guard 산출 SHA 및 runtime 버전/설치기록 해시 보존

## 결과 해석

- calpass 및 시험예산통과와 탐지 성능은 별도. q=1로 정상양성0이 되더라도 탐지0이면 실패 결과
- 방법별 미지원률/지원원인 보존. 임의95% 지원률 cutoff 없음
- batch CPU/wall 및 shared incremental 비용은 개별tick P95나 독립운영비용이 아님. 서버실시간 P95/SLA 미측정
- fixture 통합의 fast 진행중억제처럼 안전실패는 개선폭으로 상쇄하지 않음
- 기존 연구/운영/DB 수정 없음. 최종 전 사용자측 승인 게이트와 독립검수 필요

## 연구 완료 상태와 재현 경계

- 연구 실행 출처는 `4bd951672f9f05e4b8bd4b5c9f2ae919f8c9b6f4`다. 개발은 3615설정·22605실행주기 셀, 최종은 잠긴 6설정의 120 raw/fast 쌍, stress는 R00+main3의 4후보를 검증했다.
- 최종 학습·보정·재선정은 0이다. 코드·원천·참조·cut·지원/회복 정책·runtime 계보를 독립 검수했고 성능 미달과 실행 실패를 보존했다.
- 원 stress v1은 보조 평가 시간축 변수 재사용으로 중단됐다. 검증된 외부 scope 복구 실행은 1440개 복합 재생에 원 v1의 검증된 288개 재생을 재사용했다. 연구·보고 종료 뒤 공개 `stress.py`에는 시간축 scope 전달만 최소 수정했다. 당시 실험과 이 사후 수정본을 혼동하지 않는다.
- 연구 당시 잠금 파일의 해시를 현재 소스에 맞춰 바꾸거나 검사를 생략하면 안 된다. 현재 수정본으로 새 연구를 수행할 때는 해당 버전에서 검수 → 개발 → 선정 봉인 → 별도 최종 승인을 새로 수행한다. 당시 연구의 원 실행 재현에는 봉인된 소스와 외부 복구 계보가, 저장 결과 재집계에는 보존된 원장과 감사 receipt가 필요하다.
- 당시 실행 환경은 Python3.12.14, NumPy2.5.3, pandas3.0.6, SciPy1.18.1, statsmodels0.15.0, patsy1.0.3, psutil7.2.2였다. 수치 계산은 process별 BLAS/OMP 1스레드이며 추가 통계 패키지는 격리 경로에 두었다.
- 평균 점유 1% 미만만으로 예산 통과를 선언하지 않는다. 모든 seed/group에서 점유 ≤1%와 false episode/h ≤0.05를 함께 검사하며, source/common/all-branch-finite/판정 가능 분모를 분리한다.
- 운영 per-tick P95·운영 RSS·state/checkpoint 크기, 실고객 정상 라벨은 검증하지 않았다. 복합 stress에는 오류 추가 후보와 fast 정책을 포함하지 않았으며 모든 가능한 이상 조합의 전수 검증도 아니다.
