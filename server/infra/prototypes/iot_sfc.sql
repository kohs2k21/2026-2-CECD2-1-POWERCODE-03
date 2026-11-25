-- IoT-SFC 수동 시험용 저장소. ESB 관측·활성 구성·기존 탐지 큐와 분리한다.
-- 명시적으로 실행할 때만 적용한다. 운영 마이그레이션 디렉터리 밖에 둔다.
BEGIN;
CREATE SCHEMA IF NOT EXISTS powercode_iot;

CREATE TABLE powercode_iot.runs (
    id uuid PRIMARY KEY,
    model_hash text NOT NULL,
    manifest_hash text NOT NULL,
    metadata jsonb NOT NULL CHECK (jsonb_typeof(metadata) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE powercode_iot.observations (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES powercode_iot.runs(id),
    row_number bigint NOT NULL CHECK (row_number > 0),
    source_time timestamptz NOT NULL,
    split text NOT NULL CHECK (split IN ('train', 'calibration', 'test', 'excluded')),
    content_hash text NOT NULL,
    allowed_values jsonb NOT NULL CHECK (jsonb_typeof(allowed_values) = 'object'),
    quality_flags jsonb NOT NULL,
    persisted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (run_id, row_number),
    UNIQUE (id, run_id)
);
-- 평가 정답을 수치 피처와 탐지 입력에서 분리한다.
CREATE TABLE powercode_iot.ground_truth (
    observation_id uuid PRIMARY KEY REFERENCES powercode_iot.observations(id),
    attack boolean,
    attack_type text
);
CREATE TABLE powercode_iot.detection_queue (
    id uuid PRIMARY KEY,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'leased', 'done', 'dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_until timestamptz,
    lease_token uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    last_error text
);
CREATE INDEX iot_queue_ready_idx ON powercode_iot.detection_queue
    ((payload->>'benchmarkRunId'), available_at, created_at) WHERE status IN ('pending', 'leased');
CREATE TABLE powercode_iot.decisions (
    observation_id uuid PRIMARY KEY,
    run_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('complete', 'unavailable')),
    anomaly_score double precision,
    rule_anomaly boolean,
    ml_anomaly boolean,
    anomaly boolean,
    evidence jsonb NOT NULL,
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (observation_id, run_id) REFERENCES powercode_iot.observations(id, run_id),
    CHECK ((status = 'complete' AND anomaly IS NOT NULL AND anomaly_score IS NOT NULL)
        OR (status = 'unavailable' AND anomaly IS NULL AND anomaly_score IS NULL))
);
-- 관측·정답·고정 모델 참조·판정은 추가만 허용한다. 임대 상태가 변하는 큐는 제외한다.
CREATE FUNCTION powercode_iot.reject_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'iot_sfc_record_is_immutable' USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER immutable_run BEFORE UPDATE OR DELETE ON powercode_iot.runs
    FOR EACH ROW EXECUTE FUNCTION powercode_iot.reject_mutation();
CREATE TRIGGER immutable_observation BEFORE UPDATE OR DELETE ON powercode_iot.observations
    FOR EACH ROW EXECUTE FUNCTION powercode_iot.reject_mutation();
CREATE TRIGGER immutable_truth BEFORE UPDATE OR DELETE ON powercode_iot.ground_truth
    FOR EACH ROW EXECUTE FUNCTION powercode_iot.reject_mutation();
CREATE TRIGGER immutable_decision BEFORE UPDATE OR DELETE ON powercode_iot.decisions
    FOR EACH ROW EXECUTE FUNCTION powercode_iot.reject_mutation();
COMMIT;
