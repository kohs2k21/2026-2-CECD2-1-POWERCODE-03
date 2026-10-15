> 아래 비교는 2026-10-14 전환 전 코드 기준 기록이다. 2026-10-15에 기본 7개 서비스, Detector 3모드·병렬·deadline·임대 확정, Trainer/LLM 큐 분리, 요청 설명·저장 판정 SSE, 공통 snapshot 재사용, host storage·서비스별 DB 설정을 구현했다. 제공된 고객 API는 계속 조회해 catalog/투영 snapshot을 DB에 축적하고 동일 요청의 실시간성을 측정한다. T/P/M 원천 계약·GPU 실적재·GHCR/CD release 통합은 추가 확인이 필요하다. 현재 실행 기준은 [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)다.

# 기획서와 Docker 구현 비교

2026-10-14 검토. Notion 앱에서 상위 기획서와 컨테이너 목록을 읽고, 하위 페이지는 Notion 웹 앱에서 확인했다. 비교 대상은 dev `acedb8c`와 아직 커밋하지 않은 Docker 추가 변경이다. 별도 CD 브랜치는 로컬 `origin/ci/21-ci-cd` 참조의 `3bf822e`를 확인했다. 서버 접속·설정 변경·Notion 본문 수정은 수행하지 않았다. 아래 목표 구조는 사용자의 기획서 우선 선호를 반영한 설계 기준이며 실제 코드 전환은 아직 완료하지 않았다.

## 채택한 설계 기준

사용자 요청: "웬만해서는 기획서를 따라가고 싶어". 후속 구현은 아래 기준을 따른다.

1. 기본 운영 서비스는 web·api·collector·detector·trainer·llm·기존 PostgreSQL의 7개다. CPU/GPU 이미지 환경은 분리하며 여러 CPU 서비스가 이미지를 공유할 수 있다.
2. 변수 계산은 Detector와 Trainer의 공통 모듈로 사용한다. feature를 별도 상시 서비스로 추가하는 것은 성능 근거가 필요한 확장이다.
3. Detector는 Rule only / ML only / 기본 Rule+ML 병렬을 구현한다. 입력·모드·기한·결합·fallback을 승인 구성에 고정한다. 모델 장애 시 가능한 Rule 실행을 유지하며 결과 확정은 한 번만 한다.
4. Trainer는 학습·평가·산출물 생성, LLM은 추천·명시적 설명 요청·출력 검증·재사용을 담당한다. LLM 준비 상태가 기존 모델의 학습/평가·탐지를 차단하지 않도록 한다.
5. Collector는 저장 커밋 후 직접 전달하고 실패 시 같은 영속 작업을 복구한다. 실제 원천 API·revision·관계·BODY 보관 계약이 없는 부분은 완료된 것으로 간주하지 않는다.
6. 파일저장소는 합의된 영속 경로로 유지하고 별도 컨테이너를 만들지 않는다. 기존 DB·인증 데이터·산출물을 보존한다.
7. monitor/window는 선택 실행으로 둔다. 1분 v12 연구 탐지는 기본 PROCESS 실시간 판정과 구분한다.
8. 배포는 기존 CD의 main CI·고정 digest·서버 pull·실패 복원 경로에 통합한다.
9. 기획서의 OR 결합·심각도 기준·처리 기한 수치 등 검토안은 별도 결정 없이 확정 정책으로 바꾸지 않는다. 기획서에서 벗어나는 변경은 근거와 영향, 필요한 문서 변경을 먼저 제시한다.

이 기준을 기록한 것과 실제 코드가 모두 충족한 것은 구분한다. 현재 구현과 남은 차이는 아래 표를 따른다.

## 결론

기획서의 7개 기본 서비스(WebUI, API, Collector, Detector, Trainer, LLM, 기존 PostgreSQL)를 운영 기준으로 채택하는 것이 적절하다. 현재 Docker의 CPU/GPU 환경 분리, 이미지 밖 모델 가중치, 읽기 전용 모델 mount, nginx 프록시, 영속 저장은 유지한다. Detector와 Trainer에 들어간 책임을 조정하고 Collector와 LLM의 영속 작업 실행을 보완한다.

컨테이너 개수보다 작업 소유권·장애 분리·판정 완전성이 중요하다. 추가한 feature는 우선 공통 계산 라이브러리로 Detector/Trainer 안에서 사용하고, monitor/window는 선택 profile의 보조·연구 실행으로 둔다. 측정된 부하나 독립적인 운영 요구가 생기면 분리한다.

## 확인한 기획서

- [상위 기획서](https://app.notion.com/p/3f6e47a1056080ffb8e0dfc6ada06460)
- [WebUI](https://app.notion.com/p/WebUI-3f6e47a105608080b59dd76b770611eb)
- [API](https://app.notion.com/p/API-Express-3f6e47a1056081ccb472d6821a77334c)
- [Collector](https://app.notion.com/p/Collector-3f6e47a1056081ee8e39eb38ddd389c1)
- [Detector](https://app.notion.com/p/Detector-3f6e47a10560814e9eebc7eaf6320bdb)
- [Trainer](https://app.notion.com/p/Trainer-3f6e47a1056081e59009fd635dee46fa)
- [LLM](https://app.notion.com/p/LLM-3f6e47a1056081e0a75bf8f545d71195)
- [PostgreSQL](https://app.notion.com/p/PostgreSQL-3f6e47a10560816184b3f98cd7a79a5f)
- [파일 저장소](https://app.notion.com/p/File-Storage-3f6e47a1056081abbc12c342bad2bbd6)
- [CI/CD](https://app.notion.com/p/CI-CD-3f6e47a1056081afb9a8daa53b352e05)
- [공통 규약](https://app.notion.com/p/3f6e47a1056081a7a7bac7151f52caf8)

상세 문서는 팀 검토안 v1.0이며, 문서의 현재 구현 평가는 main ac996cb / dev 656e783 등 이전 커밋 기준이다. 미구현 목록을 그대로 최신 코드의 상태로 단정하지 않았다. Detector의 3모드·기본 병렬·완료/기한 후 1회 확정은 문서에서 확정으로 표기한다. OR 결합과 심각도 기준은 검토안이므로 운영 기본값으로 확정했다고 판단하지 않는다. 상위 문서 말미에는 이번 9개 역할 표가 추가되어 있지만 기존 7개 목표를 공식 대체했다는 결정은 확인되지 않는다.

## 주요 차이

| 항목 | 기획 목표 | 현재 구현 | 권고 |
|---|---|---|---|
| 기본 서비스 | 7개, 파일저장소 제외 | 기본 7개 앱 서비스 + 기존 DB = 8개. window 활성화 시 9개 | 기본 7개 + 보조/연구 profile |
| Collector | 고객 API 지속 수집, checkpoint, BODY 별도 보존·재시도 | 저장 함수와 CSV 재생. 상시 Collector 서비스·고객 API·BODY 저장 없음 | 독립 Collector 구현, 원천 API 계약 확인 |
| 직접 전달/복구 | 관측·불변 입력·작업·checkpoint 커밋 후 같은 입력을 Detector에 직접 전달. 실패 시 동일 작업 복구 | 관측 ID를 feature 큐에 넣고 DB 조회·계산 후 detection 큐에서 다시 처리 | 동일 detect job의 직접 전달 + PG 복구, 영속 입력/hash 검증 |
| Detector 모드 | Rule only, ML only, 기본 Rule+ML 병렬 | Rule 실행 후 ML 순차 호출, 모드 선택·deadline 없음 | Detector 내부 독립 실행, 모드별 준비 조건·기한·결합 계약 |
| 모델 장애 | 병렬에서 가능한 Rule 유지 | 일부 미준비는 partial 처리하지만, 활성 모델 로드 실패로 프로세스 종료 가능. 적용 API는 IF 산출물 필수 | Rule 입력/준비를 ML과 분리, Rule only 및 병렬 fallback 허용 |
| 변수 계산 | 같은 정의를 학습과 판정에 적용 | 별도 feature 서비스/큐. Detector도 materialize 호출 | 현재 P 행 계산은 공통 라이브러리로 사용. 별도 계산 서비스는 부하 측정 후 |
| LLM | 추천·설명 작업 종류/예산 분리, 결과 저장·재사용 | generation은 HTTP 추론 엔진. 추천도 물리 training_jobs에서 Trainer가 소비. 설명 실행 없음 | LLM이 추천/설명 영속 작업을 담당. Trainer는 학습/평가만 |
| 시작 의존성 | 학습·판정·LLM 지연 분리 | Trainer 시작이 generation healthy에 의존 | Trainer는 DB/파일저장소만 필요. LLM 실패는 LLM 작업에 기록 |
| 작업 계약 | 입력/구성/attempt/token을 고정하고 결과·완료를 원자 확정 | 추천은 v03 감사 계약, train/evaluate와 탐지는 기존 호환 경로. 물리 큐도 혼용 | backlog 보존하며 단일 작업 계약으로 단계 전환 |
| 화면·알림 | 저장한 최종 판정 전달, 모드·부분 상태·근거 표시 | 운영 페이지 PG 조회는 연결. 기존 SSE는 관리자 로그 입력의 메모리 방송. 최종 PG 판정과 미연결 | 저장 결과 기반 SSE/알림, 재연결·중복 방지. API에서 판정 재계산 금지 |
| 파일저장소 | 호스트 영속 영역, DB 메타/hash, 완성 후 공개, 읽기/쓰기 역할 분리 | artifacts/users named volume, Base/번들 bind mount, 모델/fit hash·버전 저장 | 저장 방식 자체는 유효. 기존 서버 경로·계정·rootless 권한·DB/파일 대응 백업과 통합 |
| 배포 | main CI → GHCR digest → 완성 Release → 서버 pull·복원 | 현재 추가 Compose는 서버 local build와 수동 시작 | CD 브랜치의 발행/배포 계약을 확장하고 build용/운영용 Compose 분리 |

### Detector에서 먼저 해결할 차이

`worker/detector/runner.py`는 Rule 실행 뒤 `_score`를 호출한다. 조건부로 ML을 생략하는 직렬 필터를 구현한 것은 아니지만, 기획서의 동시 실행도 아니다. Rule 0개를 skipped로 처리하며 모델/fit 미준비와 의도적인 모드 생략을 구성 정책으로 구분하지 않는다.

`feature_worker.materialize`는 `ml_config_models`에서 변수셋을 찾고, `configRoutes.ts`의 적용 gate도 하나의 IF 산출물을 요구한다. 따라서 Docker의 service 이름만 바꿔서는 Rule only나 모델 장애 시 독립 Rule 실행이 가능해지지 않는다. 구성 모델과 실행 코드 모두 변경해야 한다.

현재 탐지 계산은 DB transaction 안에서 수행된다. 기획서대로 짧은 claim/입력 고정 → transaction 밖 계산 → 현재 token/lease 검사 및 판정·완료 저장으로 나누고, 처리 기한 후 도착한 결과는 기존 판정을 수정하지 않아야 한다.

### LLM에서 먼저 해결할 차이

`jobRoutes.ts`는 추천에 논리 queue=llm_recommendation을 저장하지만 실제 메시지는 training_jobs에 넣는다. `trainer.runner`가 추천 요청까지 처리하며 HTTP 응답을 기다린다. 논리 분류만으로 실행 자원이 분리되지는 않는다.

LLM 컨테이너가 추천과 설명의 별도 작업을 소비하고, GPU 추론을 담당하는 구조를 권고한다. 한 컨테이너 안에 작업 처리와 추론 엔진을 모듈로 분리할 수 있다. 기존 generation 엔진은 재사용한다. 추론 동시성은 우선 1개로 제한하고 작업별 timeout/retry/budget을 관리한다. 추론 엔진과 큐 소비자를 별도 컨테이너로 나누는 선택은 확장 시 가능하지만 기본 7개에서 벗어나는 추가 설계다.

현재 feature_v5/rule_v2 어댑터는 이상 설명 모델이 아니다. 설명은 별도 출력 계약·프롬프트/모델 적합성 검증이 필요하며, 저장된 판정 근거만 문장화한다. 실제 기여도 계산 없이 SHAP 등의 값을 생성하면 안 된다. LLM 중단이 Detector 판정·알림이나 기존 모델 평가를 막지 않도록 한다.

## 권고하는 기본 7개 서비스

| 이름 | 역할 | 실행 자원 |
|---|---|---|
| web | nginx + React, API/SSE 프록시 | CPU |
| api | 인증·조회·작업 접수·승인·적용 요청·저장된 이벤트 전달 | CPU |
| collector | 원천 API 수집·관측/BODY·checkpoint·불변 입력/job 저장·직접 전달 | CPU, 고객 API 네트워크 |
| detector | 고정 입력/구성 검증·변수 계산·Rule/ML 3모드·기한·판정 1회 확정·실제 적용 | CPU, 모델 산출물 읽기 |
| trainer | 고정 snapshot·train-only fit·IF 학습·공통 평가·산출물 게시 | CPU, 산출물 쓰기 |
| llm | 관리자 추천·사용자 설명 영속 작업·출력 검증·결과 저장/재사용 | GPU 0, 승인 Base/어댑터 읽기 |
| postgres | 기존 DB. 관측·작업·시도·버전·판정·감사 이력 | 기존 컨테이너 유지 |

CPU 서비스들은 공통 worker 이미지에서 다른 진입점으로 실행할 수 있다. 공유 이미지가 프로세스 공유를 뜻하지 않는다. 서비스명은 기존 CD의 web/api를 유지하고 나머지 allowlist를 collector/detector/trainer/llm으로 확장하는 것이 통합 부담을 줄인다.

모델·fit·manifest·Base·번들은 이미지 밖에 둔다. 파일저장소는 컨테이너 수에 포함하지 않는다. 단일 서버에서는 운영자가 합의한 호스트 bind 경로가 확인·백업에 편리하다. named volume을 유지해도 된다. 기존 DB·인증 파일·모델을 새 빈 경로로 조용히 대체하지 않는다.

### 추가한 서비스의 처리

- feature: `feature_runtime`·`derived_expression`·버전/결측 검증은 유지한다. 운영 P 행 계산은 Detector 내부 단계, 학습/추천 미리보기는 같은 라이브러리로 수행한다. 독립 서비스가 필요할 때는 raw Rule까지 지연시키지 않도록 입력/기한을 분리한다.
- monitor: 처음에는 가벼운 주기 작업 또는 별도 선택 profile. 실패가 탐지를 중단시키지 않도록 한다.
- window: 현재는 연구/검증 profile 유지. v12는 1분 연속 grid와 과거 prefix를 사용하고 행별 IF와 다른 점수 계약을 가진다. 행별 2~3초 판정의 대체물이 아니다. 운영 채택 시 동일 PROCESS 관측과 시간창 근거를 어떻게 연결할지, coverage·late arrival·기한·CPU 예산을 별도로 평가한다.
- bundle/migrate: 운영 상시 컨테이너가 아닌 일회성 배포 도구.

### 작업과 통신

탐지: Collector가 관측·불변 입력·detect job·checkpoint를 원자 커밋 → Detector 내부 HTTP에 같은 job/input 전달 → 검증/claim → Rule·ML 독립 실행 → 완료 또는 기한 → 현재 소유권 확인 → 판정/완료/알림 근거 원자 확정. 직접 전달 실패는 같은 PG job을 재확보한다.

학습: API가 training job 접수 → Trainer가 기존 snapshot을 선택/고정 → 피처 계산·train-only fit·모델 학습 → 평가 → 관리자 승인 → 별도 Detector 적용 작업.

LLM: API가 llm_recommendation 또는 llm_explanation 작업 접수 → LLM이 입력 근거·예산·현재 소유권을 검사 → 생성/검증 → 버전과 결과 저장 → API 조회. 설명은 초기에는 사용자가 요청할 때만 생성하고 같은 근거·버전은 재사용한다.

PostgreSQL의 네 종류 논리 작업을 기준으로 분리하며 Redis/Kafka는 지금 필수 서비스로 추가하지 않는다. 전환 중 기존 물리 큐를 임의 삭제하지 않고 같은 작업/결과 ID로 재전달·복구·멱등성을 검증한다.

### 네트워크와 자원

- 외부 진입점은 web. API는 내부 서비스이며 직접 공개 여부는 기존 프록시 정책과 맞춘다.
- 앱/작업 네트워크와 DB 네트워크를 구분하고 각 서비스의 DB 역할·권한을 분리한다. 현재 Compose는 DB를 쓰는 서비스에 동일 DATABASE_URL을 주입하므로 권한 분리는 후속 작업이다.
- Collector만 필요한 고객 API 연결 정보를 갖는다. LLM의 모델 mount는 읽기 전용, Trainer 산출물 영역은 쓰기, Detector는 읽기 전용으로 유지한다.
- IF/K-means는 CPU. GPU 0을 LLM에 지정하고 GPU 2/3의 기존 임베딩 작업은 변경하지 않는다. GPU 1은 필요성이 측정될 때 사용한다.
- 기존 CD의 compose.gpu.yaml은 detector/trainer/llm 모두 count=1로 예약한다. 지금의 sklearn 모델에 그대로 적용하지 않고 LLM만 device_ids로 예약하도록 조정한다.
- 학습·LLM과 탐지의 CPU/메모리/동시 작업 수를 제한하고 ESB 발생→판정→API 공개를 측정한다. 현재 두 단계 1초 polling과 운영 화면 3초 polling은 목표 지연·알림 전달에 대한 추가 검증이 필요하다.

## CD와 평가에서 추가로 맞출 항목

기획서의 CD는 main CI와 고정 digest 기반 서버 pull 배포다. 별도 CD 브랜치의 deploy_lib.py/publish_release.py는 아직 web/api만 허용한다. Compose 확장만으로 새 worker가 배포되지는 않는다. 런타임 코드를 CD에 통합하고 필요한 이미지 발행·완성 Release·allowlist·health/복원 검증을 함께 확장한다. 기존 PostgreSQL은 앱 업데이트와 함께 재생성·초기화하지 않는다.

GPU 모델 준비는 현재 최대 20분 start_period인데 기존 CD web/api 준비 제한은 120초다. 새 LLM에 동일 제한을 그대로 적용하지 않고 모델 적재와 서비스별 readiness 예산을 구분한다. 코드 이미지 digest와 모델 bundle/Base 버전은 별도 manifest로 고정한다. 앱 이미지 복원과 활성 모델 구성 복원도 별도 상태로 기록한다.

같은 조건 비교에는 기존 snapshot/시험 세트를 재사용해야 한다. 현재 추천/학습의 `_execute`는 작업마다 새 snapshot을 만든다. 원본 범위·revision이 바뀌지 않은 비교 작업은 명시적 snapshotId를 사용하도록 보완한다. 현재 IF의 P99는 train 내부 보정 집합으로 계산하지만 Trainer 기획은 validation의 임계값/후보 선택을 제시한다. 최종 평가 프로토콜을 하나로 고정하고 공통 평가에서 비교해야 한다. test를 반복 임계값 조정에 사용하지 않는다.

기획서의 전체 피처 카탈로그·train-only 통계 fit보다 현재 실행 가능한 피처는 P 행 AST로 제한된다. T/M 관계·as-of가 검증되지 않은 식을 보류하는 구현은 유지하되 지원 범위를 운영 화면에서 명시한다. 이 검토가 관계/원천 계약의 실제 검증을 대신하지 않는다.

## 우선순위

1. P0: Detector 3모드·독립 입력·모델 장애 Rule 유지·deadline·소유권/최종 1회 확정.
2. P0: LLM 작업을 Trainer에서 분리하고 Trainer의 GPU readiness 의존 제거. 설명 작업과 결과 재사용 구현.
3. P0: Collector 실제 원천 계약·상시 실행·checkpoint·불변 입력/job·직접 전달/동일 작업 복구.
4. P1: 저장 판정 기반 API/SSE/알림, 기존 UI의 모드·실제 적용·부분 상태 연결.
5. P1: 공통 snapshot/평가·피처 카탈로그·산출물 manifest/백업·DB 역할별 권한.
6. P1: 기존 CD와 7개 서비스 발행·시작·실패 복구 통합. DB migration은 별도 승인/적용 절차.
7. P2: monitor/window 독립 운영 여부·추가 큐/추론 엔진·다중 GPU는 실측 후 결정.

현재 Docker의 로컬 빌드·22개 단위 시험·CPU 연구 연결 통합 통과는 구현한 경로의 검증 결과다. 기획서의 3모드 병렬·고객 API 수집·XAI·서버 GPU·2~3초 운영 지연까지 검증했다는 의미는 아니다.
