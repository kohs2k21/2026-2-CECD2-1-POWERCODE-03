-- 011: evaluation sets, configuration comparison and LLM method recommendation (#78 A; contracts #74).
-- Run once with psql -v ON_ERROR_STOP=1 -f. The enum values below are committed before the
-- transaction (010 import_csv pattern); new labels are only compared as kind::text in this file.
-- A failure inside BEGIN..COMMIT rolls back every structural change except these enum values.
-- Every function defined here pins search_path to public, pg_temp (SECURITY INVOKER kept) so a
-- session temp table cannot shadow the tables these validators read or write.
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'evaluation_set';
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'compare';
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'recommend_methods';
BEGIN;
-- evaluation_set/compare: Trainer queue, admin users. recommend_methods: LLM recommendation queue.
-- The new kinds never use the legacy NULL contract branch.
ALTER TABLE run_jobs DROP CONSTRAINT run_jobs_v03_contract;
ALTER TABLE run_jobs ADD CONSTRAINT run_jobs_v03_contract CHECK ((
 (contract_version IS NULL AND kind NOT IN ('detect','recommend_feature','explain')
  AND kind::text NOT IN ('import_csv','evaluation_set','compare','recommend_methods')
  AND requested_by_actor_id IS NOT NULL AND requester_role IS NOT NULL
  AND queue IS NULL AND producer_kind IS NULL AND producer_key IS NULL
  AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
 OR (contract_version IS NOT NULL AND contract_version = 'v03' AND queue IS NOT NULL
  AND producer_kind IS NOT NULL AND producer_key IS NOT NULL AND idempotency_key IS NOT NULL
  AND ((kind = 'detect' AND queue = 'detection' AND producer_kind = 'system'
    AND requested_by_actor_id IS NULL AND requester_role IS NULL
    AND input_manifest_id IS NOT NULL AND input_config_version_id IS NOT NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR ((kind IN ('preview','train','evaluate') OR kind::text IN ('import_csv','evaluation_set','compare')) AND queue = 'training' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR ((kind IN ('recommend_rule','recommend_feature') OR kind::text = 'recommend_methods') AND queue = 'llm_recommendation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NOT NULL)
   OR (kind = 'explain' AND queue = 'llm_explanation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role IN ('user','admin')
    AND input_decision_id IS NOT NULL AND input_config_version_id IS NOT NULL AND input_manifest_id IS NULL AND llm_spec IS NOT NULL)))
) IS TRUE);

-- recommend_methods stores a validated result without a decision (004:112, 004:120-121).
ALTER TABLE run_llm_results DROP CONSTRAINT run_llm_results_kind_check;
ALTER TABLE run_llm_results ADD CONSTRAINT run_llm_results_kind_check
 CHECK(kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods'));
ALTER TABLE run_llm_results DROP CONSTRAINT run_llm_results_check;
ALTER TABLE run_llm_results ADD CONSTRAINT run_llm_results_check
 CHECK((kind::text = 'explain' AND decision_id IS NOT NULL AND config_version_id IS NOT NULL)
  OR (kind::text IN ('recommend_rule','recommend_feature','recommend_methods') AND decision_id IS NULL));

-- Same behavior as 004 except that recommend_methods joins the llm_spec and LLM completion kind lists.
-- No evaluate-only completion rule exists, so compare needs no extra completion branch.
CREATE OR REPLACE FUNCTION erd_v03_validate_job() RETURNS trigger LANGUAGE plpgsql
 SET search_path = public, pg_temp AS $$
DECLARE d run_process_decisions; s text; a run_job_attempts;
BEGIN
 IF TG_OP = 'UPDATE' AND OLD.contract_version IS DISTINCT FROM NEW.contract_version THEN RAISE EXCEPTION 'job contract version is fixed; create a new job'; END IF;
 IF TG_OP = 'UPDATE' AND OLD.contract_version = 'v03' AND
  ROW(NEW.kind,NEW.contract_version,NEW.queue,NEW.producer_kind,NEW.producer_key,NEW.requested_by_actor_id,NEW.requester_role,NEW.idempotency_key,NEW.request_hash,NEW.input_config_version_id,NEW.input_snapshot_id,NEW.input_manifest_id,NEW.input_decision_id,NEW.input_spec,NEW.llm_spec)
  IS DISTINCT FROM ROW(OLD.kind,OLD.contract_version,OLD.queue,OLD.producer_kind,OLD.producer_key,OLD.requested_by_actor_id,OLD.requester_role,OLD.idempotency_key,OLD.request_hash,OLD.input_config_version_id,OLD.input_snapshot_id,OLD.input_manifest_id,OLD.input_decision_id,OLD.input_spec,OLD.llm_spec)
 THEN RAISE EXCEPTION 'v03 job input is immutable'; END IF;
 IF NEW.contract_version IS DISTINCT FROM 'v03' THEN RETURN NEW; END IF;
 IF NEW.producer_kind = 'user' AND NEW.producer_key IS DISTINCT FROM NEW.requested_by_actor_id THEN RAISE EXCEPTION 'user producer mismatch'; END IF;
 IF NEW.status = 'blocked' AND NEW.blocked_reason_code IS NULL THEN RAISE EXCEPTION 'blocked reason required'; END IF;
 IF NEW.kind = 'detect' AND
  (SELECT source_id FROM run_input_manifests WHERE id = NEW.input_manifest_id) IS DISTINCT FROM
  (SELECT source_id FROM ml_config_versions WHERE id = NEW.input_config_version_id) THEN RAISE EXCEPTION 'detect config/input source mismatch'; END IF;
 IF NEW.kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods') THEN
  IF jsonb_typeof(NEW.llm_spec) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'LLM spec object required'; END IF;
  FOREACH s IN ARRAY ARRAY['evidence_version','input_evidence_hash','model_version','prompt_version','output_schema_version','reuse_key'] LOOP
   IF jsonb_typeof(NEW.llm_spec->s) IS DISTINCT FROM 'string' OR COALESCE(NEW.llm_spec->>s,'') = '' THEN RAISE EXCEPTION 'LLM spec string required: %',s; END IF;
  END LOOP;
 END IF;
 IF NEW.kind = 'explain' THEN
  SELECT * INTO STRICT d FROM run_process_decisions WHERE id = NEW.input_decision_id;
  IF d.config_version_id IS DISTINCT FROM NEW.input_config_version_id THEN RAISE EXCEPTION 'explanation decision/config mismatch'; END IF;
 END IF;
 IF NEW.status = 'succeeded' AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'succeeded') THEN
  SELECT * INTO STRICT a FROM run_job_attempts WHERE id = NEW.current_attempt_id AND job_id = NEW.id;
  IF a.status NOT IN ('running','succeeded') OR a.lease_until <= clock_timestamp() THEN RAISE EXCEPTION 'completion requires current unexpired attempt'; END IF;
  IF NEW.kind = 'detect' AND NOT EXISTS(SELECT 1 FROM run_process_decisions WHERE job_id = NEW.id AND result_attempt_id = a.id) THEN RAISE EXCEPTION 'detect completion requires decision'; END IF;
  -- Nested IF: kind::text is not constant-folded, so the run_llm_results check (and its SELECT
  -- privilege) must stay out of non-LLM completions such as the demo role's train jobs.
  IF NEW.kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods') THEN
   IF NOT EXISTS(SELECT 1 FROM run_llm_results WHERE job_id = NEW.id AND created_attempt_id = a.id) THEN RAISE EXCEPTION 'LLM completion requires result'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

-- evaluation-set-v1: one fixed population per request; queued -> ready | failed, then immutable.
CREATE TABLE data_evaluation_sets (
 id uuid PRIMARY KEY,
 job_id uuid NOT NULL UNIQUE REFERENCES run_jobs(id),
 source_id uuid NOT NULL REFERENCES ops_sources(id),
 snapshot_id uuid NOT NULL,
 purpose split_role NOT NULL CHECK (purpose IN ('validation','test')),
 display_name text NOT NULL,
 synthetic_request jsonb CHECK (synthetic_request IS NULL OR jsonb_typeof(synthetic_request) = 'object'),
 synthetic_profile_binding jsonb CHECK (synthetic_profile_binding IS NULL OR jsonb_typeof(synthetic_profile_binding) = 'object'),
 generation jsonb CHECK (generation IS NULL OR jsonb_typeof(generation) = 'object'),
 content_hash text CHECK (content_hash ~ '^[0-9a-f]{64}$'),
 artifact_relative_path text UNIQUE,
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','ready','failed')),
 error_code text,
 created_by_actor_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ready_at timestamptz,
 FOREIGN KEY(snapshot_id,source_id) REFERENCES data_snapshots(id,source_id),
 CHECK (artifact_relative_path IS NULL OR (content_hash IS NOT NULL
   AND artifact_relative_path = 'evaluation-sets/' || id::text || '/' || content_hash || '.json')),
 CHECK (status <> 'ready' OR (generation IS NOT NULL AND content_hash IS NOT NULL AND ready_at IS NOT NULL)),
 CHECK ((status = 'ready') = (ready_at IS NOT NULL)),
 CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
CREATE INDEX data_evaluation_sets_source_idx ON data_evaluation_sets(source_id,created_at DESC);

CREATE FUNCTION protect_evaluation_set() RETURNS trigger LANGUAGE plpgsql
 SET search_path = public, pg_temp AS $$
BEGIN
 IF TG_OP = 'INSERT' THEN
   IF NEW.status <> 'queued' OR NEW.generation IS NOT NULL OR NEW.content_hash IS NOT NULL
      OR NEW.artifact_relative_path IS NOT NULL OR NEW.error_code IS NOT NULL OR NEW.ready_at IS NOT NULL THEN
     RAISE EXCEPTION 'evaluation set must be queued before generation';
   END IF;
   IF (SELECT kind::text FROM run_jobs WHERE id = NEW.job_id) IS DISTINCT FROM 'evaluation_set' THEN
     RAISE EXCEPTION 'evaluation set requires an evaluation_set job';
   END IF;
   IF NOT EXISTS (SELECT 1 FROM data_snapshots WHERE id = NEW.snapshot_id AND source_id = NEW.source_id
      AND status = 'ready' AND schema_version <> 'prepared-features-v1') THEN
     RAISE EXCEPTION 'evaluation set requires a ready non-prepared snapshot';
   END IF;
   RETURN NEW;
 END IF;
 IF TG_OP = 'DELETE' OR OLD.status IN ('ready','failed') THEN
   RAISE EXCEPTION 'evaluation set is immutable';
 END IF;
 IF NEW.status NOT IN ('ready','failed') THEN
   RAISE EXCEPTION 'queued evaluation set may only become ready or failed';
 END IF;
 IF (NEW.id,NEW.job_id,NEW.source_id,NEW.snapshot_id,NEW.purpose,NEW.display_name,NEW.synthetic_request,
     NEW.synthetic_profile_binding,NEW.created_by_actor_id,NEW.created_at)
    IS DISTINCT FROM
    (OLD.id,OLD.job_id,OLD.source_id,OLD.snapshot_id,OLD.purpose,OLD.display_name,OLD.synthetic_request,
     OLD.synthetic_profile_binding,OLD.created_by_actor_id,OLD.created_at) THEN
   RAISE EXCEPTION 'evaluation set input is immutable';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER evaluation_set_immutable BEFORE INSERT OR UPDATE OR DELETE ON data_evaluation_sets
 FOR EACH ROW EXECUTE FUNCTION protect_evaluation_set();

-- Reclaimed/dead/max-attempt jobs can fail without running the set handler (010 import_csv pattern).
CREATE FUNCTION fail_evaluation_set_with_job() RETURNS trigger LANGUAGE plpgsql
 SET search_path = public, pg_temp AS $$
BEGIN
 IF NEW.kind::text = 'evaluation_set' AND NEW.status = 'failed' THEN
   UPDATE data_evaluation_sets SET status='failed', error_code=COALESCE(NEW.error_code,'evaluation_set_failed')
     WHERE job_id=NEW.id AND status='queued';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER evaluation_set_job_failure AFTER UPDATE OF status ON run_jobs
 FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION fail_evaluation_set_with_job();

-- config-comparison-v1: written once with its item evaluations and the job success; never rewritten.
-- Insert order in that transaction: comparison -> run_evaluations -> comparison items.
CREATE TABLE run_config_comparisons (
 id uuid PRIMARY KEY,
 job_id uuid NOT NULL UNIQUE REFERENCES run_jobs(id),
 evaluation_set_id uuid NOT NULL REFERENCES data_evaluation_sets(id),
 source_id uuid NOT NULL REFERENCES ops_sources(id),
 baseline_config_version_id uuid REFERENCES ml_config_versions(id),
 active_revision bigint NOT NULL CHECK (active_revision >= 0),
 status text NOT NULL CHECK (status IN ('succeeded','failed')),
 summary jsonb NOT NULL CHECK (jsonb_typeof(summary) = 'object'),
 completed_at timestamptz NOT NULL
);
CREATE INDEX run_config_comparisons_set_idx ON run_config_comparisons(evaluation_set_id,completed_at DESC);
CREATE INDEX run_config_comparisons_source_idx ON run_config_comparisons(source_id,completed_at DESC);

CREATE TABLE run_config_comparison_items (
 comparison_id uuid NOT NULL REFERENCES run_config_comparisons(id),
 config_version_id uuid NOT NULL REFERENCES ml_config_versions(id),
 role text NOT NULL CHECK (role IN ('active','candidate')),
 status text NOT NULL CHECK (status IN ('succeeded','failed')),
 error_code text,
 evaluation_id uuid,
 PRIMARY KEY(comparison_id,config_version_id),
 FOREIGN KEY(evaluation_id,config_version_id) REFERENCES run_evaluations(id,config_version_id),
 CHECK ((status = 'succeeded' AND evaluation_id IS NOT NULL AND error_code IS NULL)
     OR (status = 'failed' AND evaluation_id IS NULL AND error_code IS NOT NULL))
);
CREATE UNIQUE INDEX run_config_comparison_items_active_idx ON run_config_comparison_items(comparison_id) WHERE role = 'active';
CREATE UNIQUE INDEX run_config_comparison_items_evaluation_idx ON run_config_comparison_items(evaluation_id) WHERE evaluation_id IS NOT NULL;

-- One compare job may own one evaluation row per configuration (N6). Existing rows keep NULL lineage.
ALTER TABLE run_evaluations ADD COLUMN comparison_id uuid REFERENCES run_config_comparisons(id);
ALTER TABLE run_evaluations ADD COLUMN evaluation_set_id uuid REFERENCES data_evaluation_sets(id);
ALTER TABLE run_evaluations DROP CONSTRAINT run_evaluations_job_id_key;
ALTER TABLE run_evaluations ADD CONSTRAINT run_evaluations_job_config_key UNIQUE(job_id,config_version_id);
-- Jobs outside a comparison (evaluate, prepared evaluate) keep exactly one evaluation row.
CREATE UNIQUE INDEX run_evaluations_single_job_idx ON run_evaluations(job_id) WHERE comparison_id IS NULL;
ALTER TABLE run_evaluations ADD CONSTRAINT run_evaluations_comparison_set_check CHECK (comparison_id IS NULL OR evaluation_set_id IS NOT NULL);
CREATE INDEX run_evaluations_comparison_ref_idx ON run_evaluations(comparison_id) WHERE comparison_id IS NOT NULL;

CREATE FUNCTION validate_comparison_lineage() RETURNS trigger LANGUAGE plpgsql
 SET search_path = public, pg_temp AS $$
DECLARE s data_evaluation_sets; cmp run_config_comparisons; e run_evaluations;
BEGIN
 IF TG_TABLE_NAME = 'run_config_comparisons' THEN
  IF (SELECT kind::text FROM run_jobs WHERE id = NEW.job_id) IS DISTINCT FROM 'compare' THEN
   RAISE EXCEPTION 'comparison requires a compare job'; END IF;
  SELECT * INTO STRICT s FROM data_evaluation_sets WHERE id = NEW.evaluation_set_id;
  IF s.status <> 'ready' OR s.source_id IS DISTINCT FROM NEW.source_id THEN
   RAISE EXCEPTION 'comparison requires a ready evaluation set of the same source'; END IF;
  IF NEW.baseline_config_version_id IS NOT NULL AND
   (SELECT source_id FROM ml_config_versions WHERE id = NEW.baseline_config_version_id) IS DISTINCT FROM NEW.source_id THEN
   RAISE EXCEPTION 'comparison baseline source mismatch'; END IF;
 ELSIF TG_TABLE_NAME = 'run_evaluations' THEN
  IF NEW.evaluation_set_id IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO STRICT s FROM data_evaluation_sets WHERE id = NEW.evaluation_set_id;
  IF s.status <> 'ready' OR s.snapshot_id IS DISTINCT FROM NEW.snapshot_id OR s.purpose IS DISTINCT FROM NEW.purpose
   OR (SELECT source_id FROM ml_config_versions WHERE id = NEW.config_version_id) IS DISTINCT FROM s.source_id THEN
   RAISE EXCEPTION 'evaluation set lineage mismatch'; END IF;
  IF NEW.comparison_id IS NOT NULL THEN
   SELECT * INTO STRICT cmp FROM run_config_comparisons WHERE id = NEW.comparison_id;
   IF cmp.job_id IS DISTINCT FROM NEW.job_id OR cmp.evaluation_set_id IS DISTINCT FROM NEW.evaluation_set_id THEN
    RAISE EXCEPTION 'comparison evaluation job/set mismatch'; END IF;
  END IF;
 ELSE
  SELECT * INTO STRICT cmp FROM run_config_comparisons WHERE id = NEW.comparison_id;
  IF (NEW.role = 'active') <> (cmp.baseline_config_version_id IS NOT DISTINCT FROM NEW.config_version_id) THEN
   RAISE EXCEPTION 'comparison active item must be the baseline configuration'; END IF;
  IF NEW.evaluation_id IS NOT NULL THEN
   SELECT * INTO STRICT e FROM run_evaluations WHERE id = NEW.evaluation_id;
   IF e.comparison_id IS DISTINCT FROM NEW.comparison_id THEN RAISE EXCEPTION 'comparison item evaluation mismatch'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER config_comparison_validate BEFORE INSERT ON run_config_comparisons
 FOR EACH ROW EXECUTE FUNCTION validate_comparison_lineage();
CREATE TRIGGER evaluation_comparison_validate BEFORE INSERT ON run_evaluations
 FOR EACH ROW EXECUTE FUNCTION validate_comparison_lineage();
CREATE TRIGGER config_comparison_item_validate BEFORE INSERT ON run_config_comparison_items
 FOR EACH ROW EXECUTE FUNCTION validate_comparison_lineage();
CREATE TRIGGER config_comparison_immutable BEFORE UPDATE OR DELETE ON run_config_comparisons
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE TRIGGER config_comparison_item_immutable BEFORE UPDATE OR DELETE ON run_config_comparison_items
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
COMMIT;
