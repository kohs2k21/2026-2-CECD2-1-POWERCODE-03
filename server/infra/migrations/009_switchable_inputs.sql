-- Manual API/CSV input switching; both modes retain the same logical source/config.
BEGIN;
ALTER TABLE data_api_snapshots DROP CONSTRAINT data_api_snapshots_mapping_status_check;
ALTER TABLE data_api_snapshots ADD CHECK(mapping_status IN ('catalog','monitoring_contract_required','mapped-arrival-snapshot-v1'));
CREATE TABLE data_input_replays (
 id uuid PRIMARY KEY,source_id uuid NOT NULL REFERENCES ops_sources(id),replay_key text NOT NULL,
 status text NOT NULL CHECK(status IN ('loading','ready','failed')),record_count bigint NOT NULL DEFAULT 0,
 layer_counts jsonb NOT NULL DEFAULT '{}',manifest_hash text,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(source_id,replay_key),CHECK(record_count>=0),CHECK(status<>'ready' OR manifest_hash IS NOT NULL)
);
CREATE TABLE data_input_replay_rows (
 replay_id uuid NOT NULL REFERENCES data_input_replays(id),position bigint NOT NULL,
 layer source_layer NOT NULL,values_json jsonb NOT NULL,content_hash text NOT NULL,
 PRIMARY KEY(replay_id,position),CHECK(position>=0),CHECK(jsonb_typeof(values_json)='object')
);
CREATE TRIGGER input_replay_row_immutable BEFORE UPDATE OR DELETE ON data_input_replay_rows
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE TABLE ops_input_runs (
 id uuid PRIMARY KEY,source_id uuid NOT NULL REFERENCES ops_sources(id),mode text NOT NULL CHECK(mode IN ('api','csv')),
 replay_id uuid REFERENCES data_input_replays(id),settings jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('running','paused','completed','failed')),
 next_position bigint NOT NULL DEFAULT 0,accepted_count bigint NOT NULL DEFAULT 0,duplicate_count bigint NOT NULL DEFAULT 0,
 unidentified_count bigint NOT NULL DEFAULT 0,last_error text,last_attempt_at timestamptz,last_success_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),CHECK(next_position>=0),
 CHECK((mode='csv')=(replay_id IS NOT NULL))
);
CREATE TABLE ops_input_controls (
 source_id uuid PRIMARY KEY REFERENCES ops_sources(id),mode text NOT NULL DEFAULT 'paused' CHECK(mode IN ('paused','api','csv')),
 revision bigint NOT NULL DEFAULT 0,run_id uuid REFERENCES ops_input_runs(id),
 config_version_id uuid REFERENCES ml_config_versions(id),updated_by_actor_id text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),worker_heartbeat_at timestamptz,
 CHECK(revision>=0),CHECK(mode='paused' OR run_id IS NOT NULL)
);
CREATE TABLE data_input_origins (
 observation_id uuid PRIMARY KEY REFERENCES data_observations(id),run_id uuid NOT NULL REFERENCES ops_input_runs(id),
 mode text NOT NULL CHECK(mode IN ('api','csv')),position bigint,namespace text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX input_runs_source_idx ON ops_input_runs(source_id,created_at DESC);
CREATE INDEX input_origins_run_idx ON data_input_origins(run_id);
CREATE TRIGGER input_origin_immutable BEFORE UPDATE OR DELETE ON data_input_origins
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE FUNCTION guard_input_replay() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status='ready' AND NEW IS DISTINCT FROM OLD THEN
  RAISE EXCEPTION 'ready replay is fixed; register another replay key';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER input_replay_guard BEFORE UPDATE OR DELETE ON data_input_replays
 FOR EACH ROW EXECUTE FUNCTION guard_input_replay();
CREATE FUNCTION guard_input_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'input history is retained'; END IF;
 IF ROW(OLD.id,OLD.source_id,OLD.mode,OLD.replay_id,OLD.settings)
 IS DISTINCT FROM ROW(NEW.id,NEW.source_id,NEW.mode,NEW.replay_id,NEW.settings)
 THEN RAISE EXCEPTION 'input run interpretation is fixed'; END IF;
 IF NEW.next_position<OLD.next_position THEN RAISE EXCEPTION 'input checkpoint cannot go backwards'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER input_run_guard BEFORE UPDATE OR DELETE ON ops_input_runs FOR EACH ROW EXECUTE FUNCTION guard_input_run();
COMMIT;
