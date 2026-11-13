# 프로토콜 경로 설명·검토 의견 1회성 시험 키트

프로토콜 규칙(인터페이스별 허용 경로, `server/contracts/protocol-rules.md`)의 **설명 문장**을 기반 모델이 사실만으로
쓸 수 있는지 확인한다. 계약 1절 "LLM은 설명만. 경로 집계는 코드가 한다"를 따른다.

- 경로·건수·비율·허용 여부·판정은 모두 코드 값이다. 모델은 `{"explanation", "reviewPoints"}` 문장만 낸다(판정·숫자 필드 없음).
- 모델: Qwen3.6-27B NF4 기반 모델만(어댑터 없음, 학습 없음). 한국어. 결과는 학습 필요성 근거로 기록만 한다.
- 운영 서비스·운영 DB 쓰기·운영 컨테이너는 건드리지 않는다. 실데이터 실행 결과는 서버에만 둔다.

## 1. 파일

| 파일 | 역할 |
|---|---|
| `server/worker/protocol_explanation.py` | 순수 함수(운영 연결 때 재사용): 코드 산출물 → 사실 JSON(별칭), 프롬프트, 출력 파싱, 자동 점검 |
| `kit.py` | 사례 폴더 형식 `protocol-explain-export-v1`, 사실 다시 만들기 |
| `make_cases.py` | 합성 사례 `cases/` 생성(합성 행 → Trainer `ProtocolPathDrafts` → 사실) |
| `export_cases.py` | 실데이터 사례 export(서버 전용, READ ONLY, 저장소 밖만) |
| `run_cases.py` | 실행기: `--mode cpu`(가짜 생성기) / `--mode gpu`(기반 모델), 점검·요약 |
| `tests/test_kit.py` | 합성 재생성·CPU 실행·실데이터 형식 export 값 노출·GPU 배선(가짜 모델) |
| `server/worker/tests/test_protocol_explanation.py` | 사실 만들기·점검 함수 단위 시험 |

## 2. 사실(모델 입력) — `protocol-explain-facts-v1`

원천은 코드 산출물 그대로이며 키트는 다시 집계하지 않는다.

| kind | 원천 | 화면 위치 |
|---|---|---|
| `observed_paths` | Trainer `protocolPathDrafts.interfaces[]` 항목(학습 기간 관측 경로) + 룰 세트 `protocolRules[]` 항목(없으면 미등록) | 프로토콜 설정 상세 "관측 경로 초안"과 허용 경로 비교 |
| `alert` | Detector 판정 `evidence.protocolReview`(5.3) + 그 판정 룰 세트 리비전의 항목 | 알림 "정상 프로토콜 확인" |

- 별칭: 인터페이스 `IF-01`, 채널 `CH-01`…(경로에 처음 나온 순서), 경로 id `P1`…(같은 채널 배열 = 같은 id). 실제 ID는 사실에 없다.
  별칭 표는 실데이터 export의 `private/alias_map.json`(서버 전용)에만 있다. `--keep-real-ids`로 끌 수 있다.
- 허용 여부(`inAllowed`)는 판정 규칙(5.2)과 같이 채널 배열이 길이·순서·원래 문자열까지 같아야 참. 연속 같은 채널은 합치지 않는다.
- 비율(`percent`)은 화면 `ratioText`와 같은 표기(한 자리 내림, 0·100만 정수, 그 사이 0.1~99.9).
- 사유 이름은 화면 문구(`protocolLabels.ts` `pathReasonLabels`)와 같다.
- 관측 경로는 사실에 최대 20개(허용 경로에 없는 경로를 먼저)만 싣고 나머지는 `omitted{paths, transactions}`로 요약한다.

### 프롬프트(`protocol-explain-prompt-v3`)

- system: 사실만 쓰기·원인 단정 금지, 숫자·기간은 사실 값 그대로, 표기는 사실 id만, **키 이름·코드값(draft·
  insufficient_evidence·사유 코드·true/false 등)을 문장에 쓰지 않고 이름표의 한국어 이름으로만 쓰기**, 판정 변경 금지,
  확인할 점은 "~을 확인합니다" 평서문(명령형 "하세요"·"하십시오"·"해야 합니다" 금지), JSON 하나만 출력
- user: 이름표 + 사실 JSON. 이름표 = 키 → 한국어 이름(종류별 고정), 참/거짓 값 이름, **사실에 실제로 나온** 코드값 이름
  (근거 부족·처음 보는 경로·등록되지 않은 인터페이스·사유 이름은 화면 문구 그대로, "초안"만 지은 이름). 사실 형식은 그대로다
- 이력: v1 첫 안 → v2 기간 그대로 쓰기 → v3 GPU 1차(합성 4/8·실데이터 5/8)에서 키·코드값 노출(`identifiers_subset`)과
  명령형(`no_forbidden` directive)이 실패 원인이라 이름표·평서문 지시를 더함

## 3. 자동 점검(코드, 참/거짓) — PASS = 아래 전부

| 점검 | 기준 |
|---|---|
| `json_parsed` | 출력에 JSON 객체가 있음 |
| `json_strict` | 출력 전체(앞뒤 공백 제외)가 JSON 하나. 운영 `generation_service.py`가 `json.loads`로 엄격하게 읽으므로 같은 기준. 코드 블록·앞뒤 글이 있으면 FAIL `json_not_strict` — 이때 나머지 점검은 꺼낸 객체로 계산한 정보(`details.checks_on_extracted_object`)이며 JSON 밖 글은 점검하지 않는다 |
| `schema_ok` | 키가 정확히 `explanation`(문자열)·`reviewPoints`(문자열 목록) |
| `length_ok` | explanation 1~600자, reviewPoints 0~3개·각 1~200자 |
| `numbers_subset` | 출력 숫자(아라비아 숫자·쉼표 천 단위·소수, 고유어 수+단위 "다섯 개"·"세 가지" 등) ⊆ 사실 숫자(기간 제외). 표기(IF-01 등) 속 숫자는 빼고 센다. `3.0`=`3`. 날짜 표현(`YYYY-MM-DD`·ISO 시각·`YYYY년 M월 D일`·`M월 D일`)은 따로 기간(`period.from`·`until`) 값과 적힌 부분까지 같은지 보고(시간대 변환 없음) 지운 뒤 남은 숫자만 비교한다. 기간의 연·월·일은 건수로 쓸 수 없다 |
| `identifiers_subset` | 출력의 영문 표기 ⊆ 사실 표기(인터페이스·채널·경로 id) ∪ 허용 용어(ESB·PROCESS·TRANSACTION·…) |
| `paths_in_facts` | `→`·`->` 등으로 이은 채널 목록이 사실 경로의 연속 구간 |
| `korean_ok` | 표기를 뺀 글자 중 한글 비율 ≥ 0.7 |
| `no_forbidden` | 원인 단정(원인 — 주원인·근본원인 같은 붙여 쓴 말도 포함, "직원인지"와 확인을 권하는 꼴 "원인(을/를/이/은) 확인·파악·점검·검토"는 제외·때문·로 인해·탓·유발·에 의해)·추정(추정·보입니다·처럼 보입니다·일 수 있습니다·가능성·아마·듯합니다)·확신(확실히·분명히)·지시(해야 합니다·반드시·하세요·하십시오) 없음 |
| 실행 | 사실 재생성 일치(`facts_match`), 입력 SHA 일치, `length_limit_hit=false`, 시도당 ≤ 120초(`max_time`). 운영 설명 상한 60초 이하 사례 수는 요약에 기록만 한다 |

정보용(통과 기준 아님): 허용 경로에 없는 경로·드문 경로·근거 부족·미등록·경로를 만들지 못한 사유를 언급했는지(`coverage`).

한계(점검이 잡지 않는 것):

- 수: 한자어 수("이십")·"절반", 단독 고유어 수("하나·둘·셋")는 세지 않는다. "한 번"·"한번"은 관용 표현이라 세지 않고(다른 수 + 번, 예 "세 번"은 센다), 서수 "번째"도 세지 않는다.
- 날짜: 연·월만 쓴 표현("2026년 1월")이나 다른 형식(`2026.01.05`)은 날짜로 보지 않고 숫자로 비교한다(기간 숫자는 허용 숫자가 아니므로 FAIL 쪽). 끝 날짜(until, 미포함)를 하루 당겨 쓰면 FAIL이다. 시간대 오프셋(`Z`·`+09:00`)은 대조하지 않는다 — 적힌 연·월·일·시·분·초만 기간 문자열과 비교하므로 오프셋만 다르게 쓴 시각도 통과한다.
- 경로: 경로 id와 채널의 대응은 보지 않는다(`P2(CH-01 → CH-02)`처럼 다른 경로의 채널 구간을 붙여도 그 구간이 어느 사실 경로에든 있으면 통과). 화살표는 `→ -> => ⇒ ＞ >`만 경로로 읽고 그 밖의 기호(`⟶`·`-`·`~` 등)는 경로로 보지 않는다. "CH-01에서 CH-03으로"처럼 문장으로 쓴 순서는 점검하지 않는다.
- 금지 표현: "원인"은 "직원인"만 예외라 "회원인지" 같은 다른 낱말은 오탐할 수 있다(FAIL 쪽).
- 표기: 별칭을 끄면 비영문 실제 ID는 지어낸 표기 탐지가 안 된다(사실 표기 제거만 됨).
- 의미: 문장이 사실과 반대로 말하는 경우(예: 허용 경로를 "허용 경로에 없다"고 씀)는 잡지 않는다 — 합성 사례 문장은 `summary.md`에 그대로 남으므로 사람이 읽어 확인한다.

## 4. 합성 사례 `cases/`

| 사례 | 시나리오 | 내용 |
|---|---|---|
| syn-001 | single_path_normal | 관측 경로 1개 = 허용 경로 |
| syn-002 | multiple_allowed | 허용 경로 4개, 갈라진 거래 포함, 모두 허용 |
| syn-003 | unseen_path | 허용 경로에 없는 경로 1개 + 관측 목록에 없는 허용 경로 1개 |
| syn-004 | unregistered_interface | 정상 프로토콜 미등록 인터페이스 |
| syn-005 | rare_path | 1% 미만 경로(연속 같은 채널)가 허용 경로에 없음 + 제외 사유 2종 |
| syn-006 | minimal_facts | 경로를 만든 거래 3건(근거 부족), 대부분 제외 |
| syn-007 | alert_path_unmatched | 알림: 갈라진 경로 2개 중 1개 불일치 |
| syn-008 | alert_unregistered_no_routes | 알림: 미등록 인터페이스, 경로 없음(선행 사슬 끊김) |

다시 만들기: `python -X utf8 make_cases.py --output <새 폴더>` → `cases/`와 같은 SHA여야 한다(시험이 확인).

## 5. 서버 실행 순서

### 서버 값(운영 세션이 채움, 저장소에 적지 않음)

```bash
RUNTIME_ROOT=<서버 런타임 루트>; BASE=$RUNTIME_ROOT/models/Qwen3.6-27B
W=<저장소 밖 비공개 작업 루트>        # rule-v3-baseline과 같은 루트면 venv-rulev3 재사용
GPU=<할당 GPU 번호>; DB_NETWORK=<compose PWC_DB_NETWORK 값>
BRANCH=<이 키트 브랜치>
```

### 0. clone(운영 체크아웃은 건드리지 않음)

```bash
git clone --branch $BRANCH <origin-url> $W/pe-tmp && SHA=$(git -C $W/pe-tmp rev-parse --short HEAD) \
  && mv $W/pe-tmp $W/pe-repo-$SHA && REPO=$W/pe-repo-$SHA
mkdir -p $W/runs $W/exports
```

환경: rule-v3-baseline 환경 B venv(`$W/venv-rulev3`: torch 2.11.0+cu129·transformers 5.19.0·bitsandbytes 0.50.2·
accelerate 1.15.0)를 읽기 전용으로 재사용한다. 이 키트는 peft·xgrammar를 쓰지 않는다. 새 설치 없음.

### 1. 실데이터 export(CPU, DB 읽기 전용)

```bash
EXPORT=protocol-explain-$(date +%Y%m%d-%H%M)
docker run --rm --user 0:0 --network $DB_NETWORK -e DATABASE_URL -e PYTHONDONTWRITEBYTECODE=1 \
  -v $REPO:/repo:ro -v $W/exports:/exports powercode-llm:latest \
  python -X utf8 /repo/research/experiments/protocol-explain/export_cases.py --output-dir /exports/$EXPORT
# 작업 결과에 protocolPathDrafts가 없으면(STOP 메시지): --source snapshot --snapshot-id <ready 스냅샷 UUID>
```

화면에는 개수만 나온다(`cases= observed_paths= alert= skipped= …`). 기본 선택: 경로 거래 수 상위 6개 + 적은 2개(근거 부족 먼저)
+ 등록됐지만 관측 없는 1개 + 최근 알림 묶음 4개(`--top`·`--small`·`--unobserved`·`--alerts`).

### 2. CPU 점검(합성·실데이터, GPU 없음)

```bash
run_pe() {  # 네트워크 없음, DB 없음
  docker run --rm --user 0:0 --network none $GPU_ARGS -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
    -e PYTHONDONTWRITEBYTECODE=1 -v $REPO:/repo:ro -v $BASE:/models/qwen-base:ro -v $W/venv-rulev3:/venv:ro \
    -v $W/runs:/out -v $W/exports:/exports:ro python:3.10-slim /venv/bin/python -X utf8 \
    /repo/research/experiments/protocol-explain/run_cases.py --output-root /out --repo-commit $(git -C $REPO rev-parse HEAD) "$@"
}
GPU_ARGS= run_pe --cases-dir /repo/research/experiments/protocol-explain/cases --run-name syn-cpu --mode cpu
GPU_ARGS= run_pe --cases-dir /exports/$EXPORT --run-name real-cpu --mode cpu
```

둘 다 `passed N/N`이어야 다음 단계(가짜 생성기 문장이 점검을 통과 = 사실·점검 경로 정상).

### 3. GPU(기반 모델, 실행 1개 = GPU 1장)

```bash
GPU_ARGS="--gpus device=$GPU -e CUDA_VISIBLE_DEVICES=0 --name pe-gpu$GPU"
run_pe --cases-dir /repo/research/experiments/protocol-explain/cases --run-name syn-gpu$GPU --mode gpu --base-path /models/qwen-base
run_pe --cases-dir /exports/$EXPORT --run-name real-gpu$GPU --mode gpu --base-path /models/qwen-base
```

- 적재: 운영 `generation_service.py` `DesignModel`과 같은 설정(NF4·double quant·bf16 compute, `Qwen3_5ForConditionalGeneration`,
  `device_map={"":0}`, sdpa, norm float32)에서 어댑터만 뺐다. 생성: chat template `enable_thinking=False`, greedy, seed 42,
  `max_new_tokens` 512, `max_time` 120초(`--time-limit`), 사례당 1회.
- Base 검사: 기본 `--base-check metadata`(번들 `research/models/feature_rule_spec/models/base_files.json`의 safetensors 밖 파일 SHA +
  `config.json` architectures). 전부 검사는 `--base-check full`(가중치 SHA, 오래 걸림).
- 예상 시간: 적재 1회(실행당) + 사례당 ≤ 120초 → 합성 8사례 ≤ 16분, 실데이터 최대 13사례 ≤ 26분(적재 별도).
- 같은 export를 두 GPU에 나누려면 `--cases real-001,real-002,…`와 run-name을 다르게 준다(실행마다 적재가 따로 든다).

### 중단 기준

- 서비스 GPU 사용률·메모리 변화 → `docker stop pe-gpu$GPU`. 한 사례가 120초를 크게 넘기면(멈춤) 같은 방법으로 중단.
- 출력 위치 여유 < `--min-free-gb`(기본 2GB) → 실행기가 다음 사례 전에 멈춤(`stopped_disk`).
- 고객 데이터가 저장소 경로에 쓰이면 중단·삭제 보고(실행기·export는 저장소 안 출력 경로를 거부한다).

## 6. 결과 읽는 법과 반출 규칙

`<output-root>/<YYYYMMDD-HHMMSS>-<run-name>/`(이미 있으면 실패):

- `summary.md`·`summary.json`: 맨 위 통과 n/N, 운영 설명 상한 60초 이하 사례 수, 사례별 점검 참/거짓·실패 사유 코드·개수·초·토큰·length_limit, 정보용 언급 여부.
  - **실데이터**: 상태·개수·참/거짓만(ID·건수·생성 문장·점검 실패 값 없음) → 서버 밖으로 복사해도 되는 것은 이 두 파일뿐.
    index `kind`가 정확히 `synthetic`이 아니면(누락·오타 포함) 모두 실데이터로 본다
  - 합성: 생성 문장과 점검 실패 값까지 그대로
- `run.json`: 모드, 프롬프트 버전·system 프롬프트 SHA, 모듈 SHA, 라이브러리 버전, GPU, Base 검사, 적재 초, `max_memory_allocated_bytes`
- `<case>/raw_output.txt`(모델 원문), `checks.json`(점검 + 원천 값), `case_result.json` — **실데이터는 서버 전용**
- export 폴더(`source.json` 실제 ID, `facts.json` 건수·기간, `private/alias_map.json`)도 서버 전용

결과 해석(제안): 합성 8/8과 실데이터 전부 PASS면 "기반 모델로 설명 가능" 근거, 실패 사유 분포(지어낸 숫자·표기·추정 표현·형식)는
학습 필요성 근거로 기록한다(이번 주말 학습 없음).

## 7. 다시 확인하는 명령(로컬 CPU)

```bash
cd research/experiments/protocol-explain
python -X utf8 run_cases.py --cases-dir cases --output-root <저장소 밖> --run-name cpu-check --mode cpu
python -X utf8 -m unittest discover -s tests -v
cd ../../../server/worker && python -X utf8 -m unittest tests.test_protocol_explanation -v
```
