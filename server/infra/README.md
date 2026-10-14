# Server infrastructure

- 현재 실행 안내: [다중 컨테이너 베이스라인](baseline.md)
- E2E 전 운영 자동 배포 보류·DB 자동 변경 제외; 기존 CD 코드는 후속 재개용

Infrastructure entry points and deployment configuration belong here. `scripts/bootstrap.sh` is a local frontend dependency helper; it does not provision services or contact production systems.

`migrations/001_postgres_queues.sql`은 탐지·학습 큐를 만든다. 변수 큐는 `004_feature_pipeline.sql`에서 추가된다. 큐 부하 도구는 `server/worker/README.md`에 설명한다.

`migrations/002_core_erd.sql` defines the supplied 24-table PostgreSQL ERD in one transaction. It creates enums, tables, indexes and selected checks/FKs; nullable circular references are added after their target tables. Apply it only to a fresh development database after inspecting the SQL and taking a backup. The migration deliberately does not move the existing JSON/JWT account store. The user has reported that the server database already has these 24 tables; verify the actual schema before applying later migrations.

`migrations/003_rule_versions_immutable.sql`은 Rule 초안을 JSONB 불변 버전으로 관리한다. `migrations/004_feature_pipeline.sql`은 변수 큐·정제 결과·수동 라벨·단계별 지연·결측률 변화 측정과 불변 트리거를 추가한다. 기존 DB에는 001→002→003→004 순서로 백업과 적용 이력 확인 후 실행한다. 서버 DB에는 이 턴의 migration을 적용하지 않았다.

실제 T/M as-of 부모 관계와 원천 시간대·revision 계약은 아직 확인해야 한다. migration만으로 실제 추천/IF 모델이 연결되지는 않는다. `server/worker/model_adapters.py`의 각 함수를 검증된 산출물에 연결해야 한다.

`005_research_runtime.sql`은 v12 감시 설정·시간창 판정 테이블을 추가한다. 최신 ERD의 003/004와 Rule/Feature 003/004 파일을 모두 적용해야 한다. 파일명 정렬 대신 `../worker/DEPLOYMENT.md`의 명시된 순서를 따른다.
