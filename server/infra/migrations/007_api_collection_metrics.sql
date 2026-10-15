BEGIN;
-- Source API snapshots are distinct from verified execution/observation records.
CREATE TABLE data_api_snapshots (
 id uuid PRIMARY KEY,source_id uuid NOT NULL REFERENCES ops_sources(id),endpoint text NOT NULL,
 content_hash text NOT NULL,values_json jsonb NOT NULL,
 mapping_status text NOT NULL CHECK(mapping_status IN ('catalog','monitoring_contract_required')),
 item_count integer NOT NULL CHECK(item_count>=0),received_at timestamptz NOT NULL,
 stored_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(source_id,endpoint,content_hash),CHECK(jsonb_typeof(values_json)='array')
);
CREATE TRIGGER api_snapshot_immutable BEFORE UPDATE OR DELETE ON data_api_snapshots
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE TABLE ops_api_poll_samples (
 id uuid PRIMARY KEY,source_id uuid NOT NULL REFERENCES ops_sources(id),endpoint text NOT NULL,
 started_at timestamptz NOT NULL,finished_at timestamptz NOT NULL,
 duration_ms double precision NOT NULL CHECK(duration_ms>=0),
 db_commit_ms double precision CHECK(db_commit_ms>=0),
 http_status integer,response_code text,error_code text,
 response_bytes integer NOT NULL CHECK(response_bytes>=0),item_count integer CHECK(item_count>=0),
 snapshot_id uuid REFERENCES data_api_snapshots(id),snapshot_created boolean,
 poll_interval_seconds double precision NOT NULL CHECK(poll_interval_seconds>0),
 -- Source time/arrival coverage has not been verified by the supplied transcript.
 source_latency_ms double precision,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(finished_at>=started_at),CHECK(source_latency_ms IS NULL)
);
CREATE INDEX api_poll_samples_time_idx ON ops_api_poll_samples(source_id,endpoint,started_at DESC);
CREATE TRIGGER api_poll_sample_immutable BEFORE UPDATE OR DELETE ON ops_api_poll_samples
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
COMMIT;
