# Server infrastructure

서버 컨테이너 실행은 [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)와 `compose.yaml`을 사용한다. GPU 서비스·CPU 워커·API·화면을 분리하고 기존 PostgreSQL 외부 네트워크에 연결한다. `.env.example`을 로컬 `.env`로 복사해 값과 경로를 지정한다.

Infrastructure entry points and deployment configuration belong here. `scripts/bootstrap.sh` is a local frontend dependency helper; it does not provision services or contact production systems.

`migrations/001_postgres_queues.sql`은 탐지·학습 큐를 만든다. 변수 큐는 `004_feature_pipeline.sql`에서 추가된다. 큐 부하 도구는 `server/worker/README.md`에 설명한다.

`migrations/002_core_erd.sql` defines the supplied 24-table PostgreSQL ERD in one transaction. It creates enums, tables, indexes and selected checks/FKs; nullable circular references are added after their target tables. Apply it only to a fresh development database after inspecting the SQL and taking a backup. The migration deliberately does not move the existing JSON/JWT account store. The user has reported that the server database already has these 24 tables; verify the actual schema before applying later migrations.

`migrations/003_rule_versions_immutable.sql`은 Rule 초안을 JSONB 불변 버전으로 관리한다. `migrations/004_feature_pipeline.sql`은 변수 큐·정제 결과·수동 라벨·단계별 지연·결측률 변화 측정과 불변 트리거를 추가한다. 기존 DB에는 001→002→003→004 순서로 백업과 적용 이력 확인 후 실행한다. 서버 DB에는 이 턴의 migration을 적용하지 않았다.

실제 T/M as-of 부모 관계와 원천 시간대·revision 계약은 아직 확인해야 한다. migration만으로 실제 추천/IF 모델이 연결되지는 않는다. `server/worker/model_adapters.py`의 각 함수를 검증된 산출물에 연결해야 한다.

`005_research_runtime.sql`은 v12 감시 설정·시간창 판정 테이블을 추가한다. 최신 ERD의 003/004와 Rule/Feature 003/004 파일을 모두 적용해야 한다. 파일명 정렬 대신 `../worker/DEPLOYMENT.md`의 명시된 순서를 따른다.

## 2026-10-15 서비스 전환

기본은 web/api/collector/detector/trainer/llm + 기존 PostgreSQL이다. host filesystem을 bind mount하고 feature 계산은 공통 코드, monitor/window는 선택 프로필로 실행한다. `006_service_transition.sql`은 LLM 물리 큐·catalog/checkpoint·고정 job snapshot·판정 event를 추가한다. `007_api_collection_metrics.sql`은 지속 API 응답 snapshot·실시간성 측정 sample을 추가한다. 상세 실행 순서는 [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)를 따른다. 서버 DB에는 적용하지 않았다.

### 기존 CPU / PostgreSQL 간소 데모

원본을 보존하며 별도 데모 source에 초당 N개 Process를 보내 실제 CPU Isolation Forest 판정을 저장한다. 서버 입력은 `EC_TRANSACTION_MONITORING.csv`, `EC_PROCESS_MONITORING.csv`, `EC_MESSAGE_BODY.csv`다. 권한 문제·사전 검사·마이그레이션·재개·결과 확인은 [데모 실행 안내](DEMO_REPLAY.md)를 따른다.

## API / CSV 공통 전체 프로세스

현재 권장 입력은 [입력 전환 안내](SWITCHABLE_INPUTS.md)다. `009_switchable_inputs.sql` 적용 후 `/detection/pipeline`에서 API·네 CSV·일시정지를 선택한다. 같은 sourceId·피처·Rule·모델을 유지하며 Rule 추천/사용자 채택 과정도 포함한다. Message 모니터링 82,351행과 BODY 참조 579행을 구분한다. 실제 BODY 원문은 수집하지 않는다.
