-- Prepared CSVs remain isolated from operational observations and detection queues.
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'import_csv';
BEGIN;
ALTER TABLE run_jobs DROP CONSTRAINT run_jobs_v03_contract;
ALTER TABLE run_jobs ADD CONSTRAINT run_jobs_v03_contract CHECK ((
 (contract_version IS NULL AND kind NOT IN ('detect','recommend_feature','explain') AND kind::text <> 'import_csv'
  AND requested_by_actor_id IS NOT NULL AND requester_role IS NOT NULL
  AND queue IS NULL AND producer_kind IS NULL AND producer_key IS NULL
  AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
 OR (contract_version IS NOT NULL AND contract_version = 'v03' AND queue IS NOT NULL
  AND producer_kind IS NOT NULL AND producer_key IS NOT NULL AND idempotency_key IS NOT NULL
  AND ((kind = 'detect' AND queue = 'detection' AND producer_kind = 'system'
    AND requested_by_actor_id IS NULL AND requester_role IS NULL
    AND input_manifest_id IS NOT NULL AND input_config_version_id IS NOT NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR ((kind IN ('preview','train','evaluate') OR kind::text = 'import_csv') AND queue = 'training' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR (kind IN ('recommend_rule','recommend_feature') AND queue = 'llm_recommendation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NOT NULL)
   OR (kind = 'explain' AND queue = 'llm_explanation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role IN ('user','admin')
    AND input_decision_id IS NOT NULL AND input_config_version_id IS NOT NULL AND input_manifest_id IS NULL AND llm_spec IS NOT NULL)))
) IS TRUE);

CREATE TABLE data_prepared_datasets (
 id uuid PRIMARY KEY,
 source_id uuid NOT NULL REFERENCES ops_sources(id),
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','ready','failed')),
 manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
 manifest_hash text NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
 original_relative_path text NOT NULL UNIQUE,
 original_hash text NOT NULL CHECK (original_hash ~ '^[0-9a-f]{64}$'),
 original_byte_size bigint NOT NULL CHECK (original_byte_size BETWEEN 1 AND 5242880),
 normalized_relative_path text UNIQUE,
 normalized_hash text CHECK (normalized_hash ~ '^[0-9a-f]{64}$'),
 row_count integer CHECK (row_count BETWEEN 1 AND 10000),
 row_metadata jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(row_metadata) = 'array' AND jsonb_array_length(row_metadata) <= 10000),
 validation_result jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(validation_result) = 'object'),
 snapshot_id uuid UNIQUE,
 created_job_id uuid NOT NULL UNIQUE REFERENCES run_jobs(id),
 requested_by_actor_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ready_at timestamptz,
 FOREIGN KEY(snapshot_id,source_id) REFERENCES data_snapshots(id,source_id),
 CHECK (original_relative_path = 'prepared/' || id::text || '/original.csv'),
 CHECK (normalized_relative_path IS NULL OR normalized_relative_path = 'prepared/' || id::text || '/' || normalized_hash || '.jsonl'),
 CHECK ((lower(manifest->>'sourceId') = source_id::text) IS TRUE),
 CHECK (status <> 'ready' OR (normalized_relative_path IS NOT NULL AND normalized_hash IS NOT NULL
   AND row_count IS NOT NULL AND jsonb_array_length(row_metadata) = row_count AND snapshot_id IS NOT NULL AND ready_at IS NOT NULL))
);
CREATE INDEX data_prepared_datasets_source_idx ON data_prepared_datasets(source_id,created_at DESC);

CREATE FUNCTION protect_prepared_dataset() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'INSERT' THEN
   IF NEW.status <> 'queued' OR NEW.snapshot_id IS NOT NULL OR NEW.normalized_relative_path IS NOT NULL
      OR NEW.normalized_hash IS NOT NULL OR NEW.row_count IS NOT NULL OR NEW.ready_at IS NOT NULL
      OR NEW.row_metadata <> '[]'::jsonb THEN
     RAISE EXCEPTION 'prepared dataset must be queued before validation';
   END IF;
   RETURN NEW;
 END IF;
 IF TG_OP = 'DELETE' OR OLD.status IN ('ready','failed') THEN
   RAISE EXCEPTION 'prepared dataset is immutable';
 END IF;
 IF (NEW.id,NEW.source_id,NEW.manifest,NEW.manifest_hash,NEW.original_relative_path,NEW.original_hash,
     NEW.original_byte_size,NEW.created_job_id,NEW.requested_by_actor_id,NEW.created_at)
    IS DISTINCT FROM
    (OLD.id,OLD.source_id,OLD.manifest,OLD.manifest_hash,OLD.original_relative_path,OLD.original_hash,
     OLD.original_byte_size,OLD.created_job_id,OLD.requested_by_actor_id,OLD.created_at) THEN
   RAISE EXCEPTION 'prepared dataset input is immutable';
 END IF;
 IF NEW.status = 'ready' AND NOT EXISTS (SELECT 1 FROM data_snapshots
    WHERE id=NEW.snapshot_id AND source_id=NEW.source_id AND status='ready' AND schema_version='prepared-features-v1') THEN
   RAISE EXCEPTION 'prepared snapshot is not ready';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER prepared_dataset_immutable BEFORE INSERT OR UPDATE OR DELETE ON data_prepared_datasets
 FOR EACH ROW EXECUTE FUNCTION protect_prepared_dataset();

-- Reclaimed/dead/max-attempt jobs can finish without executing the import handler.
CREATE FUNCTION fail_prepared_dataset_with_job() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.kind::text = 'import_csv' AND NEW.status = 'failed' THEN
   UPDATE data_prepared_datasets SET status='failed',
     validation_result=validation_result || jsonb_build_object('errorCode',COALESCE(NEW.error_code,'import_failed'))
     WHERE created_job_id=NEW.id AND status='queued';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER prepared_dataset_job_failure AFTER UPDATE OF status ON run_jobs
 FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION fail_prepared_dataset_with_job();
COMMIT;
