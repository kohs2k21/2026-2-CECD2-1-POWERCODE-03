-- 원천 저장과 탐지 사이의 버전 고정 변수 계산 단계.
BEGIN;

CREATE TABLE IF NOT EXISTS powercode_queue.feature_observations (
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
CREATE INDEX IF NOT EXISTS feature_observations_ready_idx
    ON powercode_queue.feature_observations (available_at, created_at)
    WHERE status IN ('pending', 'leased');

-- 하나의 원천 관측은 여러 승인 구성에서 재계산될 수 있다.
CREATE TABLE IF NOT EXISTS data_feature_materializations (
    observation_id uuid NOT NULL REFERENCES data_observations(id),
    feature_set_id uuid NOT NULL REFERENCES ml_feature_sets(id),
    input_manifest_hash text NOT NULL,
    values_json jsonb NOT NULL CHECK (jsonb_typeof(values_json) = 'object'),
    missing_names jsonb NOT NULL CHECK (jsonb_typeof(missing_names) = 'array'),
    calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (observation_id, feature_set_id, input_manifest_hash)
);
CREATE INDEX IF NOT EXISTS data_feature_materializations_set_idx
    ON data_feature_materializations (feature_set_id, calculated_at);

CREATE TABLE IF NOT EXISTS ops_pipeline_measurements (
    id uuid PRIMARY KEY,
    observation_id uuid NOT NULL REFERENCES data_observations(id),
    stage text NOT NULL CHECK (stage IN ('feature', 'detector')),
    config_version_id uuid NOT NULL REFERENCES ml_config_versions(id),
    duration_ms numeric NOT NULL CHECK (duration_ms >= 0),
    measured_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS ops_pipeline_measurements_stage_time_idx
    ON ops_pipeline_measurements (stage, measured_at);

-- 정답은 수집 상태에서 추정하지 않고 담당자가 근거 코드와 함께 별도로 입력한다.
CREATE TABLE IF NOT EXISTS data_label_evidence (
    id uuid PRIMARY KEY,
    observation_id uuid NOT NULL REFERENCES data_observations(id),
    revision_no integer NOT NULL CHECK (revision_no > 0),
    label text NOT NULL CHECK (label IN ('normal', 'anomaly')),
    reason_code text NOT NULL CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    created_by_actor_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (observation_id, revision_no)
);
CREATE INDEX IF NOT EXISTS data_label_evidence_latest_idx
    ON data_label_evidence (observation_id, revision_no DESC);

CREATE TABLE IF NOT EXISTS ops_feature_drift_measurements (
    id uuid PRIMARY KEY,
    config_version_id uuid NOT NULL REFERENCES ml_config_versions(id),
    feature_set_id uuid NOT NULL REFERENCES ml_feature_sets(id),
    feature_name text NOT NULL,
    baseline_count bigint NOT NULL CHECK (baseline_count >= 0),
    recent_count bigint NOT NULL CHECK (recent_count >= 0),
    baseline_missing_rate numeric,
    recent_missing_rate numeric,
    review_required boolean NOT NULL,
    reason_code text,
    measured_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS ops_feature_drift_latest_idx
    ON ops_feature_drift_measurements (config_version_id, measured_at DESC);

-- 추천 모델은 학습·구성 작업에만 사용한다.
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'recommend_feature';

-- 불변 카탈로그·산출물·평가와 저장된 변수값은 수정·삭제 대신 새 버전을 만든다.
CREATE OR REPLACE FUNCTION prevent_pipeline_immutable_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable pipeline row';
END;
$$;
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'ml_feature_versions', 'ml_feature_sets', 'ml_feature_set_members',
        'ml_config_versions', 'ml_config_models', 'ml_config_rules',
        'ml_artifacts', 'run_evaluations', 'data_feature_materializations',
        'data_label_evidence'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS pipeline_immutable ON %I', table_name);
        EXECUTE format('CREATE TRIGGER pipeline_immutable BEFORE UPDATE OR DELETE ON %I '
                       || 'FOR EACH ROW EXECUTE FUNCTION prevent_pipeline_immutable_mutation()', table_name);
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION protect_ready_snapshot_members()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    snapshot_state snapshot_status;
    target_snapshot_id uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_snapshot_id := OLD.snapshot_id;
    ELSE
        target_snapshot_id := NEW.snapshot_id;
    END IF;
    SELECT status INTO snapshot_state FROM data_snapshots
     WHERE id = target_snapshot_id;
    IF snapshot_state = 'ready' THEN
        RAISE EXCEPTION 'ready snapshot members are immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS ready_snapshot_members_immutable ON data_snapshot_members;
CREATE TRIGGER ready_snapshot_members_immutable
BEFORE INSERT OR UPDATE OR DELETE ON data_snapshot_members
FOR EACH ROW EXECUTE FUNCTION protect_ready_snapshot_members();

COMMIT;
