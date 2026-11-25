# 단일 시나리오 합성 데이터 실험

- [측정 결과](RESULTS.md) / [규약·시행착오](PROTOCOL.md)
- GutenTAG 단일 채널 평균 이동, TSB-UAD의 실제 PROCESS 처리시간 구간 주입.
- 연구 실험만 추가하며 `server/`, ERD, Compose, CI, 기존 연구 모델은 변경하지 않는다.
- 기존 미커밋 서비스 변경은 사용자 요청으로 모두 원복했다. 커밋·푸시·서버 접속 없음.

## 검증 범위

85개 seed·주입 조합(합성 40 / 실제 기반 add 40 / flip 5), IF·median/MAD, 추가 95분위 민감도를 합쳐 평가 260행. 실제 CSV는 원본 장애 라벨이 없으므로 주입 마스크를 고객 장애 정답으로 해석하지 않는다.

부하는 로컬 IF 추론+상한 512 메모리 큐, 단일 작업 스레드, 10/100/1000/5000행/초, 배치 상한 1/32, 각 8초×2회×2데이터의 32조건이다. PostgreSQL·실제 Collector·Docker·SSE·GPU·LLM·긴 시간 안정성은 검증하지 않았다. 운영 SLA의 근거로 사용하지 않는다.

## 준비

Python 3.12. 설치는 별도 가상 환경, upstream은 저장소 밖 디렉터리를 사용한다. 원본 CSV나 모델을 Git에 추가하지 않는다.

```bash
python3.12 -m venv /tmp/pwc-synthetic-venv
/tmp/pwc-synthetic-venv/bin/python -m pip install -r research/evaluation/synthetic_scenarios/requirements.txt

git clone https://github.com/TimeEval/GutenTAG.git /tmp/pwc-synthetic-gutentag
git -C /tmp/pwc-synthetic-gutentag checkout 0bf06e601d57f1f1e4e2d52cfbfa27871dd6a021
git clone https://github.com/TheDatumOrg/TSB-UAD.git /tmp/pwc-synthetic-tsb-uad
git -C /tmp/pwc-synthetic-tsb-uad checkout 313f0fdeba14292b9db4e1aa94c74a983a25de31
```

GutenTAG는 위 revision에서 설치한 패키지를 사용하고 TSB-UAD는 지정한 소스의 transformer를 직접 import한다. TSB 전체 모델이나 외부 pickle은 로딩하지 않는다. upstream 소스 변경과 revision 불일치는 거부한다. NumPy 1.26으로 낮추면 이 GutenTAG revision의 내부 API 호환 오류가 나므로 2.2.6을 사용한다. TSB-UAD의 scalar 변환 deprecation은 실제 남은 upstream 경고로 문서화했다.

## 실행

원본 P 경로를 지정한다. 서버에서 실행할 때도 아래 명령은 사용자 본인이 실행한다. 이번 결과는 Mac의 로컬 실행이다.

```bash
export MPLBACKEND=Agg
export MPLCONFIGDIR=/tmp/pwc-mpl
export XDG_CACHE_HOME=/tmp/pwc-cache
export PYTHONDONTWRITEBYTECODE=1

/tmp/pwc-synthetic-venv/bin/python -m research.evaluation.synthetic_scenarios.run \
  --input /data/gst-dongguk/powercode-esb/tmp/EC_PROCESS_MONITORING.csv \
  --output research/evaluation/synthetic_scenarios/runs/my-run-01 \
  --gutentag-source /tmp/pwc-synthetic-gutentag \
  --tsb-source /tmp/pwc-synthetic-tsb-uad

/tmp/pwc-synthetic-venv/bin/python -m research.evaluation.synthetic_scenarios.load \
  --output research/evaluation/synthetic_scenarios/runs/my-run-01

/tmp/pwc-synthetic-venv/bin/python -m research.evaluation.synthetic_scenarios.report \
  --output research/evaluation/synthetic_scenarios/runs/my-run-01
```

- 신규 output 디렉터리만 허용한다. 기존 실행을 덮어쓰지 않는다.
- `run.py`가 설정·소스 SHA·원본 CSV SHA·분할·환경·모델 SHA를 manifest에 고정한다. `load.py`는 고정한 수치 코드/모델이 달라지면 거부한다.
- 설정을 바꾸려면 새 config와 새 output으로 다시 실행한다. 이번 측정 결과를 재현할 때 config.json을 유지한다.
- 실제 CSV는 동일 프로세스 중 유효 후보가 가장 많은 그룹을 선택한다. 시간 순서/거래 경계/원본 값을 보존하고 미리 정한 한 구간만 바꾼다. train/calibration/test 어디에도 통계 꼬리 제거·평활화·리샘플링을 적용하지 않는다.
- test 원본 쌍에서 기존에 울리던 경보와 주입으로 새로 울린 경보를 별도로 집계한다.
- 결과 문서는 현재 실행 결과로 갱신된다. 과거 보고서는 각 runs 하위에 남는다.

## 산출물

`runs/final-20261018-03`은 이번 최종 실행이다. runs는 Git 제외다.

| 산출물 | 내용 |
|---|---|
| manifest.json / config.json | 환경·소스·입력·설정·모델 계보 |
| quality.csv | seed·이상 크기·길이·모델·임계값별 원시 평가 |
| microbenchmark.csv | 배치별 순수 추론 시간 |
| load.csv | offered·거절·완료·미완료·지연·큐·자원 |
| representative.npz / iforest.joblib | 식별자를 제외한 수치 예시·자체 학습 모델 |
| scenarios.png / quality.png / load.png | 시나리오·검출·부하 그림 |
| RESULTS.md / summary.json | 결과 및 한계 |

## 회귀 검증

```bash
/tmp/pwc-synthetic-venv/bin/python -m unittest discover -s research/evaluation/synthetic_scenarios/tests -v
```

7개 검사: GutenTAG의 단일 라벨 구간, TSB 주입 밖 불변·seed 재현, 순서 뒤집기의 점별 판정 불변성, train/보정 분리, point adjustment 금지, 부하 분모·거절/미완료 정산, 원본 불변·완료 시각 순서. 테스트의 TSB 소스는 기본 `/tmp/pwc-synthetic-tsb-uad`이며 다른 경로는 `TSB_UAD_SOURCE`로 지정한다.
