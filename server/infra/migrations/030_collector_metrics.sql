-- 030: Collector 운영 계측(contracts/collector-metrics.md). 원천 발생 시각 인덱스·입력 구간(ingest_phase)·API 수신 시각 NULL 허용·시스템 자원 표.
-- 바꾸는 것: 002 data_observations(NOT NULL 1개 해제 + 부분 인덱스 1개), 009 data_input_origins(열 1개 + 이름 있는 CHECK 1개), 새 표 1개.
--   001~029 함수·트리거·enum·다른 표 정의는 바꾸지 않는다. 새 함수·트리거·시퀀스·enum 없음.
-- 전제: 029까지 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 002·009 표 소유자로 실행. 이어서 DBA가 scripts/database-roles.sql 재적용(030 블록 GRANT 2줄: collector의
--   새 표·ops_storage_measurements SELECT·INSERT·DELETE 1줄, api의 새 표 SELECT 1줄). 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음
--   (되돌리기 = 새 표 DROP + 인덱스 DROP + ingest_phase DROP COLUMN. api_received_at NOT NULL 복원은 NULL 행이 생긴 뒤에는 불가 — 이전 Collector는 늘 값을 넣었다).
--   다시 실행하면 이미 있는 열·인덱스·표에서 실패해 전체 ROLLBACK(반쪽 적용 없음). IF NOT EXISTS를 쓰지 않는다(018과 같은 근거).
-- 잠금·순서(계약 1.2, WebUI가 적용): Collector 입력을 일시정지(`select paused`)하고 백업한 뒤 적용한다.
--   - 인덱스: 일반 CREATE INDEX(018과 같은 근거 — CONCURRENTLY는 실패 때 INVALID 인덱스가 남는다). 생성이 끝날 때까지 data_observations에 SHARE:
--     읽기는 막지 않고 관측 INSERT는 기다린다. 기존 행은 모두 source_log_occurred_at NULL이라 인덱스 항목은 0개지만 표 전체를 한 번 읽는다(시간 ∝ 표 크기).
--   - ALTER COLUMN DROP NOT NULL·ADD COLUMN·ADD CONSTRAINT NOT VALID: 각 표에 ACCESS EXCLUSIVE(행 재작성·전체 검사 없음).
--   - 이 파일 전체가 한 트랜잭션이라 잠금은 COMMIT까지 유지된다. data_observations는 인덱스 동안 SHARE, 첫 ALTER(DROP NOT NULL)부터
--     COMMIT까지 ACCESS EXCLUSIVE다. 인덱스(표 전체 읽기)를 먼저 두어 긴 구간에는 읽기(Detector·API)가 막히지 않는다. ACCESS EXCLUSIVE를
--     기다리는 동안 새로 오는 읽기도 그 뒤에 줄을 서므로, 오래 열린 읽기 트랜잭션이 있으면 그만큼 읽기가 멈춘다.
--   - SHARE → ACCESS EXCLUSIVE 승격 중 다른 트랜잭션과 교착이 생길 수 있다. 생기면 PostgreSQL이 한쪽을 끊고 이 파일은 전체 ROLLBACK(반쪽 적용 없음)
--     → 다시 실행하면 된다. 적용 세션에 `SET lock_timeout = '5s'`처럼 잠금 대기 상한을 두기를 권한다(초과하면 전체 ROLLBACK, 인덱스 생성 시간은 제한하지 않음).
-- 배포 순서: migration 030 → 역할 스크립트 → Collector(새 코드는 ingest_phase·NULL api_received_at을 쓴다. 030 전에는 INSERT가 실패한다) → resources 서비스
--   (`--profile resources`는 WebUI storage API의 `database_data`·`disks[]` 반영 뒤 켠다 — 지금 readStorage는 키를 가리지 않고 가장 최근
--   measured 행을 골라 artifacts 값이 `disk`로 보일 수 있다).
--   이전 Collector가 030 뒤에 돌아도 안전하다(열 목록을 명시하고 api_received_at에 값을 넣으며 ingest_phase는 NULL).
-- 기존 행 보정(backfill) 없음: 기존 관측은 clock_status 'unknown'·원천 시각 NULL, 기존 출처 행은 ingest_phase NULL로 남는다(계약 1.1).
BEGIN;

-- 1. 원천 발생 시각 조회(거래량 집계·수식 추천 esb_occurred_at, 원천 → 저장 지연). 시각 있는 새 관측만 담는 부분 인덱스.
--    source_log_occurred_at >= $1 같은 조건은 IS NOT NULL을 함의하므로 이 인덱스를 쓴다.
CREATE INDEX data_observations_source_occurred_idx ON data_observations(source_id,source_log_occurred_at)
 WHERE source_log_occurred_at IS NOT NULL;

-- 2. API 수신 시각: API 조회 응답 수신 시각(api_client receipt finished_at)만 담는다. CSV 재생·이전 재생 경로는 NULL(계약 1.1).
--    런타임 읽는 곳 없음(backend·worker 코드 확인). 002 정의에서 NOT NULL만 푼다.
ALTER TABLE data_observations ALTER COLUMN api_received_at DROP NOT NULL;

-- 3. 입력 구간: replay(CSV) / initial(API 실행의 시작·재개 뒤 첫 성공 조회 — 이미 진행 중이던 거래) / live(그 뒤 조회).
--    지연 표본은 live만 쓴다(계약 3.1). 이전 행은 NULL(구간 모름). 출처 행은 009 trigger로 불변이라 INSERT 때 한 번 정해진다.
--    NOT VALID: 기존 행 검사를 건너뛴다(기존 행은 NULL이라 어차피 통과). 새 INSERT는 검사된다. 나중에 VALIDATE CONSTRAINT 해도 된다.
ALTER TABLE data_input_origins ADD COLUMN ingest_phase text;
ALTER TABLE data_input_origins ADD CONSTRAINT data_input_origins_ingest_phase_check
 CHECK (ingest_phase IS NULL OR (ingest_phase IN ('live','initial','replay') AND (ingest_phase='replay')=(mode='csv'))) NOT VALID;

-- 4. 시스템 자원 표본(Collector resources 서비스가 60초마다 1행, 24시간 보존 정리). API는 읽기만 한다.
--    CPU·메모리는 지표별 상태: 첫 표본은 CPU만 측정 불가일 수 있다. 측정 불가 = 값 NULL + 사유 코드(0·이전 값으로 채우지 않음).
--    scope: host_proc_unverified = 컨테이너 안 /proc 값(일반 Docker에서는 호스트 전체, lxcfs 등이면 컨테이너 값 — 대상 서버 미확인), host = 운영자가 확인.
CREATE TABLE ops_resource_measurements (
 id uuid PRIMARY KEY,
 scope text NOT NULL CHECK (scope IN ('host_proc_unverified','host')),
 cpu_status measurement_status NOT NULL,
 cpu_percent double precision CHECK (cpu_percent IS NULL OR (cpu_percent >= 0 AND cpu_percent <= 100)),
 cpu_reason_code text,
 sample_interval_ms double precision CHECK (sample_interval_ms IS NULL OR sample_interval_ms > 0),
 memory_status measurement_status NOT NULL,
 memory_used_bytes bigint CHECK (memory_used_bytes IS NULL OR memory_used_bytes >= 0),
 memory_total_bytes bigint CHECK (memory_total_bytes IS NULL OR memory_total_bytes > 0),
 memory_reason_code text,
 measured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK ((cpu_status = 'measured') = (cpu_percent IS NOT NULL AND sample_interval_ms IS NOT NULL)),
 CHECK ((cpu_status = 'measured') = (cpu_reason_code IS NULL)),
 CHECK ((memory_status = 'measured') = (memory_used_bytes IS NOT NULL AND memory_total_bytes IS NOT NULL)),
 CHECK ((memory_status = 'measured') = (memory_reason_code IS NULL)),
 CHECK (memory_used_bytes IS NULL OR memory_total_bytes IS NULL OR memory_used_bytes <= memory_total_bytes)
);
CREATE INDEX ops_resource_measurements_time_idx ON ops_resource_measurements(measured_at);

COMMIT;
