# Rule v3 기준선(수치 SR 임계값) 1회성 GPU 시험

`research/models/feature_rule_spec`(Rule v3 묶음)의 고정 Rule LoRA가 수치 SR 기준선(`requests=["baseline"]`)을
코드 p95와 같은 조건으로 내는지, 보류·근거 요청 상태를 맞게 고르는지 11/14(토) 한 번 실제 GPU로 확인한다.
번들 파일은 고치지 않는다(`verify_package.py`가 파일 SHA를 검사). 운영 서비스·운영 DB 쓰기·운영 컨테이너는 건드리지 않는다.

- 숫자(임계값)는 코드가 정한다. 모델은 상태·근거 ID·설명 문장만 낸다. 판정은 `condition.value == 코드 p95`를 같음으로만 본다.
- 이상탐지 정확도·운영 승인·온라인 갱신은 이 시험의 범위가 아니다.

## 1. 사례 묶음 두 가지

| 묶음 | 사례 ID | 입력 | 목적 | 실행 순서 |
|---|---|---|---|---|
| **실데이터 사례** | `real-001`… | 서버 DB 스냅샷 train 멤버 PROCESS 행(회사 제공 CSV 적재분)을 원천 시각으로 다시 정렬해 80/20 분할. `export_server_captures.py`가 서버에서만 만든다(저장소 밖) | 실제 분포에서 기준선 상태·임계값 일치 | export → validate-feature(CPU) → rule(GPU) |
| **합성 경계 사례** | `syn-001`…`syn-009` | `cases/`(커밋, 고정 시드 합성) | 실데이터에 없을 수 있는 경계와 경로 smoke | 별도 실행: validate-feature(CPU) → rule(GPU) |

실데이터 전역 사례는 필드당 1개(서비스 `SOFT_RULE_FIELDS` 6종: `total_count`·`success_count`·`error_count`·`retry_count`·
`elapsed_ms`(datetime_diff END−START ms)·`error_ratio`(ERROR_COUNT/TOTAL_COUNT))다. `--per-interface-top N`을 주면
행 수 상위 N개 인터페이스 × 6필드가 더해진다(`real-007`부터). 인터페이스 ID는 `private/interface_map.json`에만 있다.

합성 경계 사례(기대 상태는 번들 코드 순서로 정함, 모델 답이 아님):

| 사례 | 필드 | 경계 | 기대 상태(needed) |
|---|---|---|---|
| syn-001 | elapsed_ms | 정상 경로 smoke, 원천 시각 동률 포함 | proposed, gt p95 |
| syn-002 | error_ratio | 정상 경로(count/count → ratio) | proposed |
| syn-003 | error_count | 정상 경로(count 단위 근거) | proposed |
| syn-004 | elapsed_ms | 참조 유효값 16 < 20 | request_evidence (need_reference_minimum_n) |
| syn-005 | error_ratio | 단위 미검증 | request_evidence (need_verified_unit) |
| syn-006 | retry_count | 단위 미검증 | request_evidence (need_verified_unit) |
| syn-007 | elapsed_ms | 현재 END_TIME 전부 없음 → 현재 유효 0 | deferred (need_valid_observations) |
| syn-008 | error_count | 참조 상수(전부 0) | proposed(gt 0) 또는 no_applicable |
| syn-009 | tx_failed_ratio(관계 비율) | 증명된 상한 1 ≤ p95 1 | no_applicable |

상수(syn-008): 번들 검증기는 기준선이 verified이고 현재 유효값이 있으면 `proposed`(gt p95)와 `no_applicable`을 모두
통과시킨다(CPU 대조 결과, 4절). 서비스 `threshold_recommendation`은 상수면 LLM을 부르지 않고 `no_applicable`이다.
그래서 두 상태 모두 PASS로 보고, 판정에 "service code returns no_applicable…" 메모를 남긴다.
열 기반 필드(시각 차·열 나눗셈·열 값)는 번들 `range_proof`가 상한을 증명하지 않으므로 "관측 최대 ≤ p95"만으로는
unreachable이 아니다. 그래서 증명 상한 사례는 관계 비율(조건부 자식 수/자식 수 ∈ [0,1])로 만든다.

## 2. 파일

| 파일 | 역할 |
|---|---|
| `capture_layout.py` | 사례 폴더 형식(approved-metadata-capture1.0), 표본 필터·원천 시각 분할 경로, 독립 p95·기대 상태 |
| `make_cases.py` | 합성 경계 사례 `cases/` 생성(폴더가 있으면 실패) |
| `export_server_captures.py` | 실데이터 사례 export(서버 전용, READ ONLY, 저장소 밖만) |
| `run_cases.py` | 실행기: verify_package → 번들 CPU 시험 → 사례별 validate-feature → [rule] → replay → judge |
| `judge.py` | 사례별 PASS/FAIL·사유, `summary.json`·`summary.md` |
| `requirements-oneoff.txt` | 환경 B 고정 버전(번들 `requirements-verified.txt`와 같음) |
| `tests/` | `test_pipeline.py`(export 본체·judge), `test_run_gpu_wiring.py`(가짜 모델로 GPU 경로 배선), `integration_export_pg.py`(임시 PostgreSQL) |
| `server/worker/trainer/source_time.py` | 원천 시각 해석·거래 묶음·블록 분할(순수 함수). 시험 `server/worker/tests/test_source_time.py` |

원천 시각 규칙(`source_time.py`): 행 시각은 사건 묶음 `occurred_time`과 같은 해석이다. **END_TIME 먼저, END가 없거나 해석 실패면 START_TIME**(
`fromisoformat`, 오프셋 있으면 그대로, 없으면 검증된 원본 시간대 또는 가정 Asia/Seoul). 못 읽으면 제외하고 센다
(`persisted_at`로 대신하지 않음). 거래(`transaction_id`) 묶음 시각 = 행 시각 최솟값. (묶음 시각, 거래 ID) 순 정렬,
같은 묶음 시각은 한 블록이라 분할이 같은 시각·한 거래를 가르지 않는다. 기간 문자열은 고정 길이 UTC
`YYYY-MM-DDTHH:MM:SS.ffffff+00:00`이며 참조 `end_exclusive` = 현재 `start_inclusive`(번들은 문자열로 비교).
표본은 `threshold_recommendation`과 같다: status=S, 명시적 anomaly 라벨 제외, 라벨 없음 유지(필터 후 묶음).
선택 원천 시각 창(`--source-time-from`/`--source-time-until`, 보류 창 회피)은 묶기 전에 행 단위로 적용하며 창 밖 행은
`outside_source_window`로 센다(경계에 걸친 거래는 창 안 행만 남는다).

### 입력 가정(실데이터)

**Trainer 가정: TOTAL_COUNT·SUCCESS_COUNT·ERROR_COUNT·RETRY_COUNT는 서비스 소프트룰 필드 카탈로그
(`server/worker/methods/registry.py` `SOFT_RULE_FIELDS`, unit=count)대로 정수 건수다.** 캡처에는 이 근거를
count 단위 evidence(`confirmation=document_confirmed`, `authority.json#/unit_count`)로 넣는다. 원천 문서가 단위를
따로 확인한 것은 아니다. 이 문장은 `index.json`·`run.json`·`summary.json`의 `input_assumptions`와 `summary.md` 머리에 그대로 나온다.

## 3. 환경 선택 — 권장: B

| | A. `powercode-llm:latest` + xgrammar만 추가 | **B. `python:3.10-slim` + 별도 venv (권장)** |
|---|---|---|
| Python | 3.12 | 3.10(번들 검증 환경과 같음) |
| torch | 2.11.0(PyPI 기본 CUDA 빌드) | 2.11.0+cu129(번들 검증 빌드와 같음) |
| 나머지 | transformers 5.19.0·peft 0.21.2·bitsandbytes 0.50.2·accelerate 1.15.0 같음(`requirements-generation.txt`), jsonschema 4.26.0은 `requirements.txt`에 있음(이미지 빌드 시점 확인 필요), xgrammar와 그 의존(apache-tvm-ffi·pydantic) 없음 | 전부 고정 설치 |
| 위험 | CUDA 빌드·Python 차이, `--target` 설치가 의존을 잘못 끌어오면 torch가 겹침 | 설치 시간(torch+CUDA 휠 수 GB) |

B를 권장하는 이유: 번들의 GPU 검증은 Python 3.10 + torch cu129에서만 했다. 오늘 CPU 근거(4절)로 Python 3.12와 3.10의
측정 SHA가 사례 9개 모두 같음을 확인했지만, greedy 생성은 CUDA 빌드 차이에 민감할 수 있어 GPU 경로는 검증 환경을
그대로 재현하는 쪽이 판정 해석이 깨끗하다. A는 B 설치가 막힐 때의 대안이다.

### 서버 값(운영 세션이 채움)

이 문서의 명령은 아래 변수만 쓴다. 실제 경로·스냅샷·장치 번호는 서버 운영 세션이 알고 있으며 저장소에 적지 않는다.

```bash
RUNTIME_ROOT=<서버 런타임 루트>          # Qwen Base: $RUNTIME_ROOT/models/Qwen3.6-27B, 운영 번들: $RUNTIME_ROOT/bundles/research-runtime
OUTPUT_ROOT=<저장소 밖 비공개 작업 루트>   # clone·venv·runs·exports가 여기 생김(예: /data 아래)
SNAPSHOT_ID=<ready 상태 스냅샷 UUID>
GPU=<이번 실행에 할당된 GPU 번호>          # 실행 1개 = GPU 1장
SERVICE_GPU=<서비스가 쓰는 GPU 번호>       # 중단 기준 감시용
DB_NETWORK=<compose PWC_DB_NETWORK 값>     # 기본 anomaly-net
BASE=$RUNTIME_ROOT/models/Qwen3.6-27B
W=$OUTPUT_ROOT
```

### 서버 경로(공통)

```bash
# 운영 체크아웃(서버의 운영 저장소)은 건드리지 않는다(브랜치 전환·pull 금지). 별도 clone만 쓴다.
git clone --branch feat/rule-v3-baseline-weekend <origin-url> $W/repo-tmp
SHA=$(git -C $W/repo-tmp rev-parse --short HEAD) && mv $W/repo-tmp $W/repo-$SHA
REPO=$W/repo-$SHA; git -C $REPO rev-parse HEAD        # run.json에 기록되는 전체 SHA
python3 -B $REPO/research/models/feature_rule_spec/verify_package.py   # 첫 단계: passed true, mismatches [] 이어야 함
mkdir -p $W/runs $W/exports
```

서버의 `$RUNTIME_ROOT/bundles/research-runtime`은 운영 export(feature_rule_design_v0_2 / rule_v2)이며
Rule v3가 아니다. Rule v3 묶음은 위 clone의 `research/models/feature_rule_spec`(어댑터 바이트가 git에 있음)을 읽기 전용으로 쓴다.
Qwen Base: `$BASE`(운영 세션이 27개 파일 `base_files.json`과 일치를 확인함). 번들 Base 검사는
목록의 파일만 SHA 비교하므로 목록 밖 `README.md`가 있어도 통과한다(`specmodel/local_models.py` `_cpu`).

### 환경 B 설치(1회, 네트워크 필요, GPU 불필요)

```bash
docker run --rm --user 0:0 -v $W:/w -v $REPO:/repo:ro python:3.10-slim sh -c \
  'python -m venv /w/venv-rulev3 && /w/venv-rulev3/bin/pip install --no-cache-dir -r /repo/research/experiments/rule-v3-baseline/requirements-oneoff.txt \
   && /w/venv-rulev3/bin/python -c "import torch,transformers,peft,bitsandbytes,xgrammar,jsonschema;from importlib.metadata import version as v;print({n: v(n) for n in (\"torch\",\"transformers\",\"peft\",\"bitsandbytes\",\"accelerate\",\"xgrammar\",\"jsonschema\")})"'
# 버전은 importlib.metadata로 읽는다(xgrammar에는 __version__ 속성이 없다)
```

### 환경 A 설치(대안)

```bash
docker run --rm --user 0:0 -v $W:/w powercode-llm:latest sh -c \
  'pip show jsonschema torch transformers peft bitsandbytes | grep -E "^(Name|Version)"; \
   pip install --no-cache-dir --no-deps --target /w/pylib xgrammar==0.2.3 apache-tvm-ffi && \
   PYTHONPATH=/w/pylib python -c "import xgrammar, jsonschema, pydantic, numpy, triton; print(\"ok\")"'
# 실패(pydantic 없음 등)하거나 jsonschema가 4.26.0이 아니면 A를 버리고 B를 쓴다. torch를 /w/pylib에 넣지 않는다(--no-deps).
# 선택 확인: docker run --rm -v $W/pylib:/pylib -e PYTHONPATH=/pylib powercode-llm:latest pip show jsonschema xgrammar
#   (jsonschema 4.26.0, xgrammar 0.2.3이 보여야 함. server/worker/requirements.txt가 jsonschema==4.26.0을 고정하지만 이미지 빌드 시점에 따라 다를 수 있다)
```

### 실행 컨테이너(실행 1개 = GPU 1장)

```bash
# GPU는 "서버 값"에서 정한 번호(사용자 승인 범위 안). 컨테이너 안에서는 이 장치 하나만 보이며 CUDA_VISIBLE_DEVICES=0
run_container() {  # 환경 B. 네트워크 없음, DB 없음, 운영 컨테이너와 별개
  docker run --rm --user 0:0 --name rulev3-gpu$GPU --gpus device=$GPU --network none \
    -e CUDA_VISIBLE_DEVICES=0 -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e PYTHONDONTWRITEBYTECODE=1 \
    -v $REPO:/repo:ro -v $BASE:/models/qwen-base:ro \
    -v $W/venv-rulev3:/venv:ro -v $W/runs:/out -v $W/exports:/exports:ro \
    python:3.10-slim /venv/bin/python -X utf8 /repo/research/experiments/rule-v3-baseline/run_cases.py \
    --base-path /models/qwen-base --output-root /out --repo-commit $(git -C $REPO rev-parse HEAD) "$@"
}
# 환경 A면: 이미지 powercode-llm:latest, -v $W/pylib:/pylib:ro -e PYTHONPATH=/pylib, 실행 파일 python
```

## 4. 오늘(11/13) CPU 근거 — GPU 없이 확인한 것

| 확인 | 명령 | 결과 |
|---|---|---|
| 번들 패키지 SHA | `run_cases.py --cpu-only` 사전 검사 | verify_package passed, 312 files, mismatch 0 |
| 번들 CPU 시험 | 같은 사전 검사(`unittest discover -s tests`) | 32개 OK(Python 3.12.13·3.10.8 모두) |
| 합성 9사례 validate-feature·replay·기준선 사전 검사·검증기 대조 | `run_cases.py --cases-dir cases --cpu-only` | 9/9 PASS(3.12·3.10), 측정 SHA 3.12=3.10 |
| 실데이터 형식 export(임시 PG, 합성 행) → CPU 실행 | `tests/integration_export_pg.py` | export 18사례(전역 6 + 인터페이스 2×6), CPU 18/18 PASS |
| export 본체·보류 창 회피·judge·GPU 배선(가짜 모델) | `python -X utf8 -m unittest discover -s tests` | 22개 OK(3.12·3.10) |
| source_time(원천 시각 창 포함) | `server/worker`: `python -m unittest tests.test_source_time` | 16개 OK(3.12·3.10), 사건 묶음 occurred_time과 표본 일치 |

Windows CPU 확인에서만 `fcntl`이 없어 `run_cases.py`가 빈 `fcntl` 대체를 끼운다(모델 호출 예산 잠금에만 쓰이며 CPU 경로는 안 씀).
GPU 실행은 Linux에서만 허용한다. 검증기 대조(`cpu_checks.oracle_validator_errors`)로 확인한 번들 검증기 성질:
기준선 verified면 `no_applicable`·`deferred`도 통과한다(검증기가 proposed를 강제하지 않음) → judge의 기대 상태가 더 엄격하다.
`proposed`는 현재 유효 0이면 `no_current_numeric_observations`, 증명 상한이면 `structurally_unreachable`로 거절된다.

## 5. 토요일 실행 순서(정확한 명령)

0. **모니터 시작(호스트, 별도 터미널)** — 다른 GPU 메모리 변화는 컨테이너 안에서 보이지 않으므로 호스트에서 기록한다.
   `nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv -l 30 | tee $W/gpu-monitor-$(date +%H%M).csv`
   `df -h /data` 확인.
1. **clone·verify_package** — 3절 "서버 경로". `verify_package.py`가 실패하면 중단.
2. **환경 설치** — 3절 환경 B(또는 A).
3. **실데이터 export(CPU, DB 읽기 전용)** — 다른 세션이 실행. DB URL은 화면·파일에 출력하지 않는다.
   ```bash
   EXPORT=$W/exports/export-$(date +%Y%m%d-%H%M)
   docker run --rm --user 0:0 --network $DB_NETWORK -e DATABASE_URL -e PYTHONDONTWRITEBYTECODE=1 \
     -v $REPO:/repo:ro -v $W/exports:/exports powercode-llm:latest \
     python -X utf8 /repo/research/experiments/rule-v3-baseline/export_server_captures.py \
     --snapshot-id $SNAPSHOT_ID --output-dir /exports/$(basename $EXPORT) [--per-interface-top 3]
   ```
   (`powercode-llm` 이미지는 psycopg·tzdata 포함.)
   **보류 창 확인**: export를 먼저 한 번 돌리고 마지막 줄의 `held-window overlaps`와 `index.json`의
   `held_window.overlap_before_rule`·`counts.global`을 본다. 겹침이 보고되면(그 사례들은 실행기가 돌리지 않고 FAIL로 남김)
   **새 `--output-dir`로 `--avoid-held-window`를 붙여 다시 export**한다. 겹침 판정은 보수적이다: 보류 계보는 시간대 없는
   KST 날짜라 UTC 날짜와 KST 날짜 해석을 모두 막는다(기간이 UTC 2026-09-29T15:00(=KST 9/30 00:00) 뒤에 끝나고
   2026-10-03T00:00Z 전에 시작하면 겹침. 번들 자체 검사보다 넓음). 규칙(고정 순서): 보류 창 뒤쪽(원천 시각 ≥ 2026-10-03T00:00:00Z)만
   남기고, 뒤쪽으로 80/20 분할이 안 되면 앞쪽(< 2026-09-29T15:00:00Z = KST 9/30 00:00)만 남긴다.
   겹침 판정은 묶음 시각 기간뿐 아니라 각 쪽에 들어간 모든 행의 START·END 시각 범위도 본다(늦은 행이 창 안인 거래 포함).
   회피 시에는 먼저 해석된 START·END 시각의 [최소, 최대]가 보류 창과 겹치는 **거래를 통째로** 빼고(`dropped_held_transactions`,
   `dropped_held_rows`), 그다음 앞쪽/뒤쪽 창을 적용한다(`dropped_outside_side_rows`). 회피 뒤 기간·전체 시각 검사가 깨끗한
   쪽만 채택하며, 사례별 `held_window_overlap`도 같은 검사라 남은 겹침이 있으면 그 사례는 돌지 않고 FAIL이다.
   남긴 쪽은 `held_window.side`, 버린 행 수 합계는 `held_window.dropped_rows`(개수만). 둘 다 안 되면 `held_window.avoid_failed=true`
   → 중단하고 보고. 특정 기간만 쓰려면 `--source-time-from 2026-10-03T00:00:00+09:00`처럼 오프셋 있는 ISO로 준다.
4. **실데이터 CPU 확인** — `run_container --cases-dir /exports/<export 폴더> --run-name real-cpu --cpu-only`
   → `summary.md` 전부 PASS여야 다음 단계.
5. **실데이터 GPU** — `run_container --cases-dir /exports/<export 폴더> --run-name real-gpu$GPU --cases real-001,real-002,real-003,real-004,real-005,real-006`
   (인터페이스 사례는 같은 방식으로 다른 GPU·다른 run-name에 나눠도 된다.)
6. **합성 경계 GPU(별도 실행)** — `run_container --cases-dir /repo/research/experiments/rule-v3-baseline/cases --run-name syn-gpu$GPU`
7. **선택: 한국어 reason 변형** — 위 명령에 `--reason-language ko`, run-name을 따로(예 `syn-ko-gpu$GPU`). 번들 외 변형으로 표시된다.
8. **판정 다시 보기** — 실행 끝에 자동으로 judge가 돈다. 다시: `/venv/bin/python /repo/.../judge.py --run /out/<실행 폴더>`
   (컨테이너 밖에서 경로가 다르면 `--index <cases 또는 export>/index.json`).

예상 시간: 실행당 Base SHA 검사 + NF4 적재 1회(최대 약 20분). 사례당 Rule 호출 최대 2회(첫 시도 + 수리 1회) × 시도당 240초
상한 → 사례당 최대 8분. 실데이터 전역 6사례 ≈ 최대 48분 + 적재, 합성 9사례 ≈ 최대 72분 + 적재. GPU 여러 장에 나누면
실행마다 적재가 따로 든다(호스트 RAM을 보고 동시 적재는 2개 이하 권장).

## 6. 중단 기준

- 서비스 GPU(`$SERVICE_GPU`) 사용률·메모리에 이 시험 시작 뒤 변화가 보이면 즉시 중단(`docker stop rulev3-gpu$GPU`).
- 생성이 멈춘 것처럼 보이면(한 사례가 수리 포함 8분을 크게 넘김) 중단: `docker stop rulev3-gpu$GPU`. 240초는 실행 중
  타임아웃이 아니라 판정(judge)의 사후 기준이다. 실행기·번들은 생성을 시간으로 끊지 않는다. 멈춘 뒤 `judge.py --run`으로
  끝난 사례만 판정하고, 남은 사례는 `--cases`로 새 run-name에 다시 돌린다.
- 할당하지 않은 다른 GPU 메모리가 바뀌면 중단 후 확인(호스트 모니터 기준. 컨테이너 안 `run.json` `warnings`는 보이는 GPU만 비교).
- 디스크: `/data` 여유가 부족하면 중단(실행기는 출력 위치 여유가 `--min-free-gb`(기본 5GB) 미만이면 다음 사례 전에 멈춤).
- 고객 데이터가 저장소 경로에 쓰이면 중단·삭제 보고(실행기·export는 저장소 안 출력 경로를 거부한다).
- `verify_package` 또는 번들 CPU 시험 실패 → 실행기가 사례 전에 멈춤(`status: stopped_precheck_failed`).
- export의 `held_window_overlap`이 참: 보류 창(KST 2026-09-30~10-02, UTC·KST 해석 모두 막는 보수적 판정, 5절 3단계)과
  기간이 겹친다. 번들 입장 검사도 UTC 문자열 기준으로 일부를 거부한다. 실행기는 그 사례를 돌리지 않고 `held_window_overlap_not_run`으로 FAIL 처리한다. 5절 3단계대로
  `--avoid-held-window`로 다시 export하고, `avoid_failed`면 중단·보고(기간 선택은 사용자 결정).

## 7. 판정 기준(PASS = 아래 전부)

번들 `rule_verified`(rule exit 0)는 번들 검증기가 출력을 받아들였다는 뜻일 뿐이며 judge PASS와 다르다. 검증기는 기준선이
verified일 때 `deferred`·`no_applicable`도 통과시키므로(4절), PASS는 기대 상태·needed·코드 p95와 아래 열을 모두 요구한다.

| 열 | 기준 |
|---|---|
| 기대 상태 / 결과 상태 | 결과 `state` ∈ 기대 상태, `needed` 집합 = 기대 needed, Rule 입력 baseline.state = 기대 baseline 상태 |
| p95 재계산 일치 | proposed·deferred: `condition.op=gt`, `condition.value` = baseline.value = CSV에서 따로 계산한 p95(linear (n-1)·0.95, Decimal 50자리). 비교는 번들 JSON 수 표기(정수면 int, 아니면 float repr)로 같음만 본다 |
| 수리 전/후 | 첫 시도 통과 / 첫 실패→수리(최대 1회) 통과·실패를 따로 기록. 최종 `rule_verified`(rule exit 0)여야 PASS |
| 초 | 시도마다 `seconds ≤ 240`(모델 적재 시간은 실행 단위로 따로 `run.json`) |
| length_limit | 모든 시도 `length_limit_hit=false` |
| reason 형식 | 1~320자, 최종 번들 검증기 오류에 문장 잘림·인용 수 범위·연산자 모순 코드 없음(번들 검증 결과를 그대로 사용). 끝 문장부호 없음은 경고만 |
| 서비스 조건 변환 | 시험 변환 `{status=state, operator=condition.op, threshold=코드 p95, rationale=reason}`. proposed 외에는 operator·threshold null, proposed는 condition.value = 코드 p95, rationale 1~500자 |
| 그 밖 | 입력 SHA = index, validate-feature exit 0, replay exit 0, CPU 사전 검사(기준선 상태·p95·검증기 대조) 일치 |

## 8. 결과 읽는 법

`<output-root>/<YYYYMMDD-HHMMSS>-<run-name>/`(이미 있으면 즉시 실패, 덮어쓰기 없음):

- `summary.md` 맨 위 **통과 n/N**, 표 열: 사례 | 기대 상태 | 결과 상태 | p95 재계산 일치 | 수리 전/후 | 초 | length_limit | reason 형식 | 서비스 조건 변환 | PASS/FAIL·사유
- `summary.json`: 같은 표 + 사유 코드. 실데이터는 상태·개수·참/거짓만(p95·임계값·인터페이스 없음).
- `run.json`: 패키지·어댑터·프롬프트 SHA, 라이브러리 버전, GPU 이름, CUDA_VISIBLE_DEVICES, Base 검사·모델 적재 초,
  `max_memory_allocated_bytes`, 시작/종료 시각, clone 커밋 SHA, 변형 표시, 사전 검사 결과, 경고.
- `<case>/case_result.json`: 단계별 exit·초, 입력 SHA256(입력 내용은 복사하지 않음), 시도별 초·토큰·length_limit, GPU 전후.
- `<case>/raw_attempt_<n>.txt`·`parsed_attempt_<n>.json`: 모델 원문·파싱 결과. `<case>/judgment.json`: 사례 판정.
- `<case>/feature/`(validate-feature), `<case>/rule/`(번들 rule 출력: `rule_input.json`=Rule 요청, `rule_bundle.json`,
  `model/rule_<n>/{input,output}.json`), `<case>/replay/`, `<case>/cpu_checks`는 `case_result.json` 안.
- **실데이터 실행 폴더는 통계(p95·최소·최대·건수 요약)를 담고 있어 서버 밖으로 옮기지 않는다.** 번들 출력
  (`rule_input.json`·`model/*/input.json`·`feature/`)과 `parsed_attempt_*`·`raw_attempt_*`(모델이 임계값을 그대로 인용)가 해당한다.
  서버 밖으로 복사해도 되는 것은 값이 없는 `summary.json`·`summary.md`뿐이다. export 폴더(고객 CSV)도 서버 전용이다.

## 9. export 출력 형식 `rule-v3-baseline-export-v1`(고정)

일요일 학습 자료 생성기가 직접 읽는 형식이다. 바꾸려면 `format` 문자열을 올린다.

```
<output-dir>/
  index.json
  private/interface_map.json          # --per-interface-top > 0일 때만. 서버 전용(인터페이스 ID)
  real-001/ … real-NNN/
    reference/  capture.json  PROCESS.csv  authority.json    # 참조 = 앞 80%(split "train")
    application/ capture.json PROCESS.csv  authority.json    # 현재 = 뒤 20%(split "application")
    proposal.json      # validate-feature --proposal (feature5.2 후보 1개, result_use=value)
    request.json       # validate-feature --request (공개 분석 요청)
    rule_plan.json     # rule --rule-plan {"requests":["baseline"],"question":…,"reference_policy":{"minimum_n":20,"operator":"gt"}}
```

- `capture.json`: 번들 `approved-metadata-capture1.0`. `data_class` approved_real, `period` {start_inclusive, end_exclusive}(UTC 고정 형식),
  `tables[0]` PROCESS(`ROW_ID` 기본키 + 필드 열), count 열은 `unit {name:"count", status:"verified", evidence_ids}` +
  `evidence[]`(kind unit, confirmation document_confirmed, `authority.json#/unit_count`).
- `PROCESS.csv`(UTF-8, 헤더 1줄, `\n`): `ROW_ID`(r000001부터, 파일마다) + 필드 열. 빈 칸 = 결측.
  `elapsed_ms`: START_TIME, END_TIME(원문을 원천 시각 규칙으로 해석한 UTC 고정 형식, 해석 실패는 빈 칸) ·
  `error_ratio`: ERROR_COUNT, TOTAL_COUNT · 건수 필드: 해당 열(TOTAL_COUNT·SUCCESS_COUNT·ERROR_COUNT·RETRY_COUNT, `derived_expression.number`로 읽은 Decimal 문자열, 숫자 아님은 빈 칸).
  행 순서 = (거래 묶음 시각, 거래 ID) 순, 묶음 안은 스냅샷 멤버(observation_id) 순.
- Rule 요청 JSON은 export·validate-feature가 만들지 않는다. GPU `rule` 단계가 `<run>/<case>/rule/rule_input.json`(Rule 입력·메시지)으로 쓴다.
  validate-feature 출력은 `<run>/<case>/feature/`(`feature_bundle.json`·`feature_values.json`·`run_manifest.json` 등).
- `index.json` 필드:

| 필드 | 뜻 |
|---|---|
| `format` | `rule-v3-baseline-export-v1` |
| `kind` | `real`(export) / `synthetic`(make_cases, 같은 형식) |
| `generator`, `created_at`, `snapshot_id`, `split_source` | 생성기, UTC 생성 시각, 스냅샷 UUID, 읽은 분할(train) |
| `source_timezone_basis` | `assumed_Asia/Seoul` / `verified` / `verified_without_timezone`(ops_sources.clock_basis 규칙) |
| `period_format`, `reference_ratio`, `exclusion_rule`, `minimum_n`, `fields` | 기간 형식, 0.8, 표본 규칙 문구, 20, 전역 필드 순서 |
| `counts.global` | observations, excluded_status_not_S, excluded_anomaly_label, held_transactions_dropped, held_rows_dropped, grouping{rows_in, rows_grouped, excluded_unparsed_time, excluded_missing_transaction, outside_source_window, basis{offset,verified_tz,assumed_tz}, field{end,start}, groups}, cut{reference/current_groups·rows·blocks}, value_parse_failures{unparsed_<열>} |
| `counts.interface_lookup` | (선택) transactions_with_T_row, mapped, ambiguous, no_interface |
| `input_assumptions` | 입력 가정 문장 목록(실데이터: count 단위 Trainer 가정) |
| `source_window` | 적용한 원천 시각 창 {from, until}(UTC ISO 또는 null)과 인수로 준 값 {argument_from, argument_until} |
| `held_window` | {avoid_requested, overlap_before_rule, side(after/before/null), avoid_failed, dropped_rows(= dropped_held_rows + dropped_outside_side_rows), dropped_held_transactions, dropped_held_rows, dropped_outside_side_rows} |
| `cases[]` | case_id, field, scope(global/interface), interface_rank(정수/null), kind, path, files{상대경로: sha256}, held_window_overlap, counts{cut 개수 + reference_valid, current_valid, reference_constant}, expected(실데이터 null: judge가 CSV 사실로 정함) |
| `skipped[]` | 분할 불가 인터페이스 {scope, interface_rank, reason} |

합성 `cases/index.json`은 같은 형식에 `purpose`, `periods`, `expected{baseline_state, states, needed, condition{op, value_decimal, value_canonical}, reference_p95_decimal}`가 더 있다.

## 10. 다루지 않는 것

- 보류 계보 검사는 행 시각(END 먼저)만이 아니라 해석 가능한 START_TIME·END_TIME을 모두 본다. 다만 시각을 못 읽은 열은
  검사할 수 없다(원문이 보류 날짜여도 해석 실패면 그 열은 빈 칸이며 export 개수의 `unparsed_*`·`excluded_unparsed_time`으로만 보인다).

- 실제 GPU 생성 품질·시간·VRAM(토요일에만 확인). 가짜 모델 배선 시험은 출력 처리 경로만 본다.
- Feature 생성(모델) 단계: 정의는 서비스 필드 AST를 그대로 쓰는 `validate-feature`(CPU)이며 Feature LoRA는 부르지 않는다.
- 혼합 시간대 원문(한쪽만 오프셋): 서비스는 elapsed를 계산 불가로 보지만 캡처는 각 열을 따로 UTC로 바꿔 값이 생긴다.
- 일광절약시간이 있는 원본 시간대: 캡처의 elapsed는 UTC 차이라 전환 시각을 낀 행은 서비스의 시간대 없는 차이와 다를 수 있다
  (Asia/Seoul 가정·검증 시간대가 일광절약시간이 없으면 해당 없음).
- 서비스 쪽 변환·임계값 저장·snapshot.py 원천 시각 분할 연결(별도 작업). `split_blocks`는 준비만 했다.
- 이상탐지 정확도, 운영 승인, 온라인 갱신, 독립 기업 일반화.

## 11. 다시 확인하는 명령(로컬 CPU)

```bash
cd research/experiments/rule-v3-baseline
python -X utf8 make_cases.py --output <새 임시 폴더>        # cases/와 SHA가 같아야 함(고정 시드)
python -X utf8 run_cases.py --cases-dir cases --output-root <저장소 밖> --run-name cpu-check --cpu-only
python -X utf8 -m unittest discover -s tests -v
cd ../../../server/worker && python -m unittest tests.test_source_time -v
# 임시 PostgreSQL(D 드라이브, migration 001~022를 prepare-runtime-db.py 순서로 적용한 빈 DB qa_rule_v3_export):
DATABASE_URL=postgresql://postgres@127.0.0.1:<port>/qa_rule_v3_export RV3_WORK=<임시 폴더> python -X utf8 tests/integration_export_pg.py
```
