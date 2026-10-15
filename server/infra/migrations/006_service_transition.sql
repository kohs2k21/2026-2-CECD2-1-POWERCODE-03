-- Stop earlier workers before applying; preserve old queue history for the recovery bridge.
BEGIN;
CREATE TABLE powercode_queue.llm_recommendation_jobs
 (LIKE powercode_queue.training_jobs INCLUDING ALL);
CREATE TABLE powercode_queue.llm_explanation_jobs
 (LIKE powercode_queue.training_jobs INCLUDING ALL);
CREATE TABLE ops_api_catalog (
 source_id uuid NOT NULL REFERENCES ops_sources(id), interface_id text NOT NULL,
 revision bigint NOT NULL CHECK(revision > 0), content_hash text NOT NULL,
 -- Restricted catalog: no adapter connection params, SQL, payload or credentials.
 configuration jsonb NOT NULL CHECK(jsonb_typeof(configuration) = 'object'),
 collected_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(source_id,interface_id,revision), UNIQUE(source_id,interface_id,content_hash)
);
CREATE TRIGGER api_catalog_immutable BEFORE UPDATE OR DELETE ON ops_api_catalog
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE TABLE ops_api_poll_state (
 source_id uuid NOT NULL REFERENCES ops_sources(id), endpoint text NOT NULL,
 checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
 last_success_at timestamptz, last_attempt_at timestamptz, error_code text,
 PRIMARY KEY(source_id,endpoint)
);
CREATE TABLE run_decision_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 decision_id uuid NOT NULL UNIQUE REFERENCES run_process_decisions(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER decision_event_immutable BEFORE UPDATE OR DELETE ON run_decision_events
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
-- Durable SSE delivery is based exclusively on a committed final decision.
CREATE FUNCTION publish_decision_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(81736,0);
 INSERT INTO run_decision_events(decision_id) VALUES(NEW.id);
 RETURN NEW;
END $$;
CREATE TRIGGER decision_event_insert AFTER INSERT ON run_process_decisions
 FOR EACH ROW EXECUTE FUNCTION publish_decision_event();
CREATE TABLE run_job_snapshots (
 job_id uuid PRIMARY KEY REFERENCES run_jobs(id), snapshot_id uuid NOT NULL REFERENCES data_snapshots(id)
);
CREATE TRIGGER job_snapshot_immutable BEFORE UPDATE OR DELETE ON run_job_snapshots
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
COMMIT;
