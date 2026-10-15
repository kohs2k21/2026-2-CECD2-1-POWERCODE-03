BEGIN;
CREATE TABLE ops_demo_runs (
 id uuid PRIMARY KEY,run_key text NOT NULL UNIQUE,source_id uuid NOT NULL UNIQUE REFERENCES ops_sources(id),
 input_kind text NOT NULL CHECK(input_kind IN ('postgres','csv')),origin_source_id uuid,
 status text NOT NULL CHECK(status IN ('loading','preparing','ready','failed')),
 record_count integer NOT NULL DEFAULT 0,baseline_count integer NOT NULL DEFAULT 0,
 input_hash text,feature_set_id uuid REFERENCES ml_feature_sets(id),
 train_job_id uuid REFERENCES run_jobs(id),config_version_id uuid REFERENCES ml_config_versions(id),
 preparation_spec jsonb NOT NULL,error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),ready_at timestamptz,
 CHECK(record_count>=0 AND baseline_count>=0 AND baseline_count<=record_count),
 CHECK(status<>'ready' OR (input_hash IS NOT NULL AND config_version_id IS NOT NULL))
);
CREATE TABLE data_demo_inputs (
 run_id uuid NOT NULL REFERENCES ops_demo_runs(id),sequence integer NOT NULL CHECK(sequence>=0),
 execution_group text NOT NULL,origin_observation_id uuid,values_json jsonb NOT NULL,
 label text CHECK(label IN ('normal','anomaly')),content_hash text NOT NULL,
 PRIMARY KEY(run_id,sequence),CHECK(jsonb_typeof(values_json)='object')
);
CREATE TRIGGER demo_input_immutable BEFORE UPDATE OR DELETE ON data_demo_inputs
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE TABLE ops_demo_passes (
 id uuid PRIMARY KEY,run_id uuid NOT NULL REFERENCES ops_demo_runs(id),pass_key text NOT NULL,
 rate double precision NOT NULL CHECK(rate>0),repeat_count integer NOT NULL CHECK(repeat_count>0),
 total_count bigint NOT NULL CHECK(total_count>0),next_sequence bigint NOT NULL DEFAULT 0,
 status text NOT NULL CHECK(status IN ('running','interrupted','completed','failed')),
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),finished_at timestamptz,error_code text,
 UNIQUE(run_id,pass_key),CHECK(next_sequence>=0 AND next_sequence<=total_count)
);
CREATE TABLE data_demo_deliveries (
 pass_id uuid NOT NULL REFERENCES ops_demo_passes(id),sequence bigint NOT NULL CHECK(sequence>=0),
 input_sequence integer NOT NULL,observation_id uuid NOT NULL UNIQUE REFERENCES data_observations(id),
 job_id uuid NOT NULL UNIQUE REFERENCES run_jobs(id),sent_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(pass_id,sequence)
);
CREATE TRIGGER demo_delivery_immutable BEFORE UPDATE OR DELETE ON data_demo_deliveries
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
-- A ready input/config and a pass's interpretation may not be rewritten on resume.
CREATE FUNCTION guard_demo_state() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'demo history cannot be deleted by runtime'; END IF;
 IF TG_TABLE_NAME='ops_demo_runs' AND OLD.status='ready' AND NEW IS DISTINCT FROM OLD
 THEN RAISE EXCEPTION 'ready demo input/config is fixed; use a new run key'; END IF;
 IF TG_TABLE_NAME='ops_demo_passes' THEN
  IF ROW(NEW.id,NEW.run_id,NEW.pass_key,NEW.rate,NEW.repeat_count,NEW.total_count)
   IS DISTINCT FROM ROW(OLD.id,OLD.run_id,OLD.pass_key,OLD.rate,OLD.repeat_count,OLD.total_count)
  THEN RAISE EXCEPTION 'pass settings are fixed; use a new pass key'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER demo_run_guard BEFORE UPDATE OR DELETE ON ops_demo_runs FOR EACH ROW EXECUTE FUNCTION guard_demo_state();
CREATE TRIGGER demo_pass_guard BEFORE UPDATE OR DELETE ON ops_demo_passes FOR EACH ROW EXECUTE FUNCTION guard_demo_state();
COMMIT;
