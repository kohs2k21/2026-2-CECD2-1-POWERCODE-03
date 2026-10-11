-- v0.3 additive contracts. Apply AFTER 003 committed, once, with old writers compatible.
-- NULL contract_version is the legacy 001/002 path; never fabricate historical lineage.
BEGIN;
CREATE TYPE queue_kind AS ENUM ('detection','training','llm_recommendation','llm_explanation');
CREATE TYPE producer_kind AS ENUM ('user','system');
CREATE TYPE body_collection_status AS ENUM ('not_collected','not_found','collected','failed');
CREATE TYPE body_link_status AS ENUM ('not_checked','unmatched','verified','ambiguous','failed');
CREATE TYPE body_representation AS ENUM ('unknown','raw','masked');

CREATE TABLE data_message_bodies (
 observation_id uuid PRIMARY KEY REFERENCES data_observations(id),
 source_id uuid NOT NULL REFERENCES ops_sources(id), message_id text NOT NULL,
 collection_status body_collection_status NOT NULL,
 message_in text, message_out text, message_body text,
 field_states jsonb NOT NULL, representation body_representation NOT NULL,
 masking_policy_version text, access_policy_version text NOT NULL, retention_policy_version text NOT NULL,
 payload_hash text, payload_bytes bigint, error_code text,
 UNIQUE(observation_id,source_id),
 FOREIGN KEY(observation_id,source_id) REFERENCES data_observations(id,source_id),
 CHECK(jsonb_typeof(field_states) = 'object'),
 CHECK(payload_bytes IS NULL OR payload_bytes >= 0),
 CHECK(collection_status <> 'collected' OR (payload_hash IS NOT NULL AND payload_bytes IS NOT NULL)),
 CHECK(collection_status = 'collected' OR (message_in IS NULL AND message_out IS NULL AND message_body IS NULL AND payload_hash IS NULL AND payload_bytes IS NULL)),
 CHECK(representation <> 'masked' OR masking_policy_version IS NOT NULL)
);
CREATE INDEX data_message_bodies_lookup_idx ON data_message_bodies(source_id,message_id,observation_id);
CREATE TABLE data_message_body_links (
 id uuid PRIMARY KEY, source_id uuid NOT NULL REFERENCES ops_sources(id),
 body_observation_id uuid NOT NULL REFERENCES data_message_bodies(observation_id),
 message_observation_id uuid REFERENCES data_messages(observation_id),
 assessment_revision bigint NOT NULL, status body_link_status NOT NULL,
 candidate_execution_count integer, input_as_of timestamptz NOT NULL,
 relation_contract_version text NOT NULL, evidence jsonb NOT NULL, assessed_at timestamptz NOT NULL,
 UNIQUE(body_observation_id,assessment_revision),
 FOREIGN KEY(body_observation_id,source_id) REFERENCES data_message_bodies(observation_id,source_id),
 FOREIGN KEY(message_observation_id,source_id) REFERENCES data_observations(id,source_id),
 CHECK(assessment_revision > 0), CHECK(candidate_execution_count IS NULL OR candidate_execution_count >= 0),
 CHECK(((status = 'verified' AND message_observation_id IS NOT NULL AND candidate_execution_count = 1)
    OR (status <> 'verified' AND message_observation_id IS NULL)) IS TRUE)
);
CREATE INDEX data_message_body_links_message_idx ON data_message_body_links(message_observation_id,assessed_at);
CREATE INDEX data_message_body_links_status_idx ON data_message_body_links(source_id,status,assessed_at);

CREATE TABLE run_input_manifests (
 id uuid PRIMARY KEY, source_id uuid NOT NULL REFERENCES ops_sources(id),
 execution_id uuid NOT NULL REFERENCES data_executions(id),
 process_observation_id uuid NOT NULL REFERENCES data_processes(observation_id),
 transaction_observation_id uuid REFERENCES data_transactions(observation_id),
 message_observation_refs jsonb NOT NULL, readiness jsonb NOT NULL,
 input_as_of timestamptz NOT NULL, contract_version text NOT NULL CHECK(contract_version = 'v03'),
 content_hash text NOT NULL, created_at timestamptz NOT NULL,
 UNIQUE(source_id,content_hash), UNIQUE(id,process_observation_id,execution_id),
 UNIQUE(id,content_hash),
 FOREIGN KEY(process_observation_id,execution_id) REFERENCES data_observations(id,execution_id),
 FOREIGN KEY(process_observation_id,source_id) REFERENCES data_observations(id,source_id),
 FOREIGN KEY(transaction_observation_id,source_id) REFERENCES data_observations(id,source_id),
 CHECK(jsonb_typeof(message_observation_refs) = 'array'), CHECK(jsonb_typeof(readiness) = 'object')
);
CREATE INDEX run_input_manifests_process_idx ON run_input_manifests(process_observation_id,created_at);

ALTER TABLE run_jobs ALTER COLUMN requested_by_actor_id DROP NOT NULL;
ALTER TABLE run_jobs ALTER COLUMN requester_role DROP NOT NULL;
ALTER TABLE run_jobs ADD COLUMN contract_version text;
ALTER TABLE run_jobs ADD COLUMN queue queue_kind;
ALTER TABLE run_jobs ADD COLUMN producer_kind producer_kind;
ALTER TABLE run_jobs ADD COLUMN producer_key text;
ALTER TABLE run_jobs ADD COLUMN input_manifest_id uuid REFERENCES run_input_manifests(id);
ALTER TABLE run_jobs ADD COLUMN input_decision_id uuid REFERENCES run_process_decisions(id);
ALTER TABLE run_jobs ADD COLUMN blocked_reason_code text;
ALTER TABLE run_jobs ADD COLUMN llm_spec jsonb;
ALTER TABLE run_jobs ADD CONSTRAINT run_jobs_v03_contract CHECK ((
 (contract_version IS NULL AND kind NOT IN ('detect','recommend_feature','explain')
  AND requested_by_actor_id IS NOT NULL AND requester_role IS NOT NULL
  AND queue IS NULL AND producer_kind IS NULL AND producer_key IS NULL
  AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
 OR (contract_version IS NOT NULL AND contract_version = 'v03' AND queue IS NOT NULL
  AND producer_kind IS NOT NULL AND producer_key IS NOT NULL AND idempotency_key IS NOT NULL
  AND ((kind = 'detect' AND queue = 'detection' AND producer_kind = 'system'
    AND requested_by_actor_id IS NULL AND requester_role IS NULL
    AND input_manifest_id IS NOT NULL AND input_config_version_id IS NOT NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR (kind IN ('preview','train','evaluate') AND queue = 'training' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR (kind IN ('recommend_rule','recommend_feature') AND queue = 'llm_recommendation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NOT NULL)
   OR (kind = 'explain' AND queue = 'llm_explanation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role IN ('user','admin')
    AND input_decision_id IS NOT NULL AND input_config_version_id IS NOT NULL AND input_manifest_id IS NULL AND llm_spec IS NOT NULL)))
) IS TRUE);
-- Nullable historical keys keep original unique constraint; new keys have a producer namespace.
CREATE UNIQUE INDEX run_jobs_v03_producer_key_idx ON run_jobs(producer_kind,producer_key,kind,idempotency_key) WHERE contract_version = 'v03';
CREATE UNIQUE INDEX run_jobs_v03_detect_idx ON run_jobs(kind,input_manifest_id,input_config_version_id) WHERE contract_version = 'v03' AND kind = 'detect';
CREATE INDEX run_jobs_v03_queue_idx ON run_jobs(queue,status,next_run_at,created_at,id) WHERE contract_version = 'v03';

ALTER TABLE run_process_decisions ADD COLUMN job_id uuid UNIQUE REFERENCES run_jobs(id);
ALTER TABLE run_process_decisions ADD COLUMN input_manifest_id uuid;
ALTER TABLE run_process_decisions ADD COLUMN result_attempt_id uuid;
ALTER TABLE run_process_decisions ADD COLUMN result_lease_token uuid;
ALTER TABLE run_process_decisions ADD CONSTRAINT decisions_manifest_fk FOREIGN KEY(input_manifest_id,process_observation_id,execution_id)
 REFERENCES run_input_manifests(id,process_observation_id,execution_id);
ALTER TABLE run_process_decisions ADD CONSTRAINT decisions_manifest_hash_fk FOREIGN KEY(input_manifest_id,input_manifest_hash) REFERENCES run_input_manifests(id,content_hash);
ALTER TABLE run_process_decisions ADD CONSTRAINT decisions_attempt_fk FOREIGN KEY(result_attempt_id,job_id) REFERENCES run_job_attempts(id,job_id);
ALTER TABLE run_process_decisions ADD CONSTRAINT decisions_v03_refs CHECK (
 (job_id IS NULL AND input_manifest_id IS NULL AND result_attempt_id IS NULL AND result_lease_token IS NULL)
 OR (job_id IS NOT NULL AND input_manifest_id IS NOT NULL AND result_attempt_id IS NOT NULL AND result_lease_token IS NOT NULL));

ALTER TABLE ml_artifacts ADD COLUMN model_input_artifact_id uuid REFERENCES ml_artifacts(id);
ALTER TABLE ml_artifacts ADD CONSTRAINT artifacts_model_input_ref CHECK(model_input_artifact_id IS NULL OR (kind = 'model' AND model_input_artifact_id <> id));

-- Successful validated output only; failed attempts stay in run_jobs/run_job_attempts.
CREATE TABLE run_llm_results (
 id uuid PRIMARY KEY, job_id uuid NOT NULL UNIQUE REFERENCES run_jobs(id),
 kind job_kind NOT NULL CHECK(kind IN ('recommend_rule','recommend_feature','explain')),
 decision_id uuid REFERENCES run_process_decisions(id), config_version_id uuid REFERENCES ml_config_versions(id),
 evidence_version text NOT NULL, input_evidence_hash text NOT NULL,
 model_version text NOT NULL, prompt_version text NOT NULL, output_schema_version text NOT NULL,
 reuse_key text NOT NULL UNIQUE, output jsonb NOT NULL,
 created_attempt_id uuid NOT NULL, lease_token uuid NOT NULL, completed_at timestamptz NOT NULL,
 FOREIGN KEY(created_attempt_id,job_id) REFERENCES run_job_attempts(id,job_id),
 CHECK(jsonb_typeof(output) = 'object'),
 CHECK((kind = 'explain' AND decision_id IS NOT NULL AND config_version_id IS NOT NULL)
  OR (kind IN ('recommend_rule','recommend_feature') AND decision_id IS NULL))
);
CREATE INDEX run_llm_results_decision_idx ON run_llm_results(decision_id,completed_at);

CREATE FUNCTION erd_v03_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is immutable; append a new version', TG_TABLE_NAME; END $$;
CREATE TRIGGER body_immutable BEFORE UPDATE OR DELETE ON data_message_bodies FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE TRIGGER body_links_immutable BEFORE UPDATE OR DELETE ON data_message_body_links FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE TRIGGER input_manifest_immutable BEFORE UPDATE OR DELETE ON run_input_manifests FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE TRIGGER llm_result_immutable BEFORE UPDATE OR DELETE ON run_llm_results FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();
CREATE FUNCTION erd_v03_guard_decision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'v03 decision is immutable'; END IF;
 IF (to_jsonb(NEW) - 'published_at') IS DISTINCT FROM (to_jsonb(OLD) - 'published_at') THEN RAISE EXCEPTION 'v03 decision content is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER decision_v03_immutable BEFORE UPDATE OR DELETE ON run_process_decisions FOR EACH ROW WHEN (OLD.job_id IS NOT NULL) EXECUTE FUNCTION erd_v03_guard_decision();

CREATE FUNCTION erd_v03_validate_body() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o data_observations; m data_messages; state text; fld text; val text;
BEGIN
 IF TG_TABLE_NAME = 'data_message_bodies' THEN
  SELECT * INTO STRICT o FROM data_observations WHERE id = NEW.observation_id FOR SHARE;
  IF o.layer <> 'B' OR o.source_id <> NEW.source_id THEN RAISE EXCEPTION 'BODY observation layer/source mismatch'; END IF;
  FOREACH fld IN ARRAY ARRAY['message_in','message_out','message_body'] LOOP
   state := NEW.field_states->>fld; val := to_jsonb(NEW)->>fld;
   IF state IS NULL OR state NOT IN ('unknown','missing','source_null','empty','present')
      OR (state = 'empty' AND val IS DISTINCT FROM '') OR (state = 'present' AND (val IS NULL OR val = ''))
      OR (state IN ('unknown','missing','source_null') AND val IS NOT NULL) THEN RAISE EXCEPTION 'BODY field state mismatch: %',fld; END IF;
  END LOOP;
 ELSE
  SELECT * INTO STRICT o FROM data_observations WHERE id = NEW.body_observation_id FOR SHARE;
  IF o.persisted_at > NEW.input_as_of THEN RAISE EXCEPTION 'BODY link uses future observation'; END IF;
  IF NEW.status = 'verified' THEN
   SELECT * INTO STRICT o FROM data_observations WHERE id = NEW.message_observation_id FOR SHARE;
   SELECT * INTO STRICT m FROM data_messages WHERE observation_id = NEW.message_observation_id FOR SHARE;
   IF o.layer <> 'M' OR o.source_id <> NEW.source_id OR o.persisted_at > NEW.input_as_of
      OR m.message_id IS DISTINCT FROM (SELECT message_id FROM data_message_bodies WHERE observation_id = NEW.body_observation_id)
   THEN RAISE EXCEPTION 'BODY link source/key/as-of mismatch'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER body_validate BEFORE INSERT ON data_message_bodies FOR EACH ROW EXECUTE FUNCTION erd_v03_validate_body();
CREATE TRIGGER body_links_validate BEFORE INSERT ON data_message_body_links FOR EACH ROW EXECUTE FUNCTION erd_v03_validate_body();

CREATE FUNCTION erd_v03_validate_manifest() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o data_observations; ref jsonb; seen uuid[] := ARRAY[]::uuid[]; p data_processes; t data_transactions; m data_messages;
BEGIN
 SELECT * INTO STRICT o FROM data_observations WHERE id = NEW.process_observation_id FOR SHARE;
 SELECT * INTO STRICT p FROM data_processes WHERE observation_id = NEW.process_observation_id FOR SHARE;
 IF o.layer <> 'P' OR o.source_id <> NEW.source_id OR o.execution_id IS DISTINCT FROM NEW.execution_id OR o.persisted_at > NEW.input_as_of
 THEN RAISE EXCEPTION 'manifest PROCESS/source/as-of mismatch'; END IF;
 IF NEW.transaction_observation_id IS NOT NULL THEN
  SELECT * INTO STRICT o FROM data_observations WHERE id = NEW.transaction_observation_id FOR SHARE;
  SELECT * INTO STRICT t FROM data_transactions WHERE observation_id = NEW.transaction_observation_id FOR SHARE;
  IF o.layer <> 'T' OR o.source_id <> NEW.source_id OR o.persisted_at > NEW.input_as_of THEN RAISE EXCEPTION 'manifest TRANSACTION/source/as-of mismatch'; END IF;
  IF p.transaction_id IS NULL OR t.transaction_id IS DISTINCT FROM p.transaction_id THEN RAISE EXCEPTION 'manifest T/P transaction relation mismatch'; END IF;
  IF p.transaction_observation_id IS DISTINCT FROM NEW.transaction_observation_id THEN RAISE EXCEPTION 'manifest requires exact verified P/T observation relation'; END IF;
 END IF;
 FOR ref IN SELECT value FROM jsonb_array_elements(NEW.message_observation_refs) LOOP
  IF jsonb_typeof(ref) <> 'object' OR NOT (ref ? 'observation_id' AND ref ? 'revision_no')
     OR (ref - 'observation_id' - 'revision_no') <> '{}'::jsonb THEN RAISE EXCEPTION 'manifest M reference shape'; END IF;
  SELECT * INTO STRICT o FROM data_observations WHERE id = (ref->>'observation_id')::uuid FOR SHARE;
  SELECT * INTO STRICT m FROM data_messages WHERE observation_id = o.id FOR SHARE;
  IF o.layer <> 'M' OR o.source_id <> NEW.source_id OR o.persisted_at > NEW.input_as_of
     OR o.execution_id IS NULL OR o.revision_no IS NULL OR (ref->>'revision_no') IS NULL
     OR o.revision_no IS DISTINCT FROM (ref->>'revision_no')::bigint OR o.id = ANY(seen)
  THEN RAISE EXCEPTION 'manifest MESSAGE/source/revision/as-of mismatch'; END IF;
  IF p.transaction_id IS NULL OR p.process_id IS NULL OR m.transaction_id IS DISTINCT FROM p.transaction_id OR m.process_id IS DISTINCT FROM p.process_id
  THEN RAISE EXCEPTION 'manifest M/P process relation mismatch'; END IF;
  IF m.process_observation_id IS DISTINCT FROM NEW.process_observation_id THEN RAISE EXCEPTION 'manifest requires exact verified M/P observation relation'; END IF;
  seen := array_append(seen,o.id);
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER input_manifest_validate BEFORE INSERT ON run_input_manifests FOR EACH ROW EXECUTE FUNCTION erd_v03_validate_manifest();

CREATE FUNCTION erd_v03_validate_job() RETURNS trigger LANGUAGE plpgsql AS $$
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
 IF NEW.kind IN ('recommend_rule','recommend_feature','explain') THEN
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
  IF NEW.kind IN ('recommend_rule','recommend_feature','explain') AND NOT EXISTS(SELECT 1 FROM run_llm_results WHERE job_id = NEW.id AND created_attempt_id = a.id) THEN RAISE EXCEPTION 'LLM completion requires result'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER jobs_v03_validate BEFORE INSERT OR UPDATE ON run_jobs FOR EACH ROW EXECUTE FUNCTION erd_v03_validate_job();

CREATE FUNCTION erd_v03_validate_result() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE j run_jobs; a run_job_attempts; attempt_id uuid; token uuid; spec jsonb; manifest run_input_manifests;
BEGIN
 IF TG_TABLE_NAME = 'run_process_decisions' AND NEW.job_id IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO STRICT j FROM run_jobs WHERE id = NEW.job_id FOR UPDATE;
 IF TG_TABLE_NAME = 'run_process_decisions' THEN attempt_id := NEW.result_attempt_id; token := NEW.result_lease_token;
 ELSE attempt_id := NEW.created_attempt_id; token := NEW.lease_token; END IF;
 SELECT * INTO STRICT a FROM run_job_attempts WHERE id = attempt_id AND job_id = j.id FOR UPDATE;
 IF j.contract_version IS DISTINCT FROM 'v03' OR j.status <> 'running' OR j.current_attempt_id IS DISTINCT FROM a.id
  OR a.status <> 'running' OR a.lease_token IS DISTINCT FROM token OR a.lease_until <= clock_timestamp()
 THEN RAISE EXCEPTION 'result requires current owner and unexpired lease'; END IF;
 IF TG_TABLE_NAME = 'run_process_decisions' THEN
  IF j.kind <> 'detect' OR NEW.input_manifest_id IS DISTINCT FROM j.input_manifest_id OR NEW.config_version_id IS DISTINCT FROM j.input_config_version_id
  THEN RAISE EXCEPTION 'decision job kind/input/config mismatch'; END IF;
  SELECT * INTO STRICT manifest FROM run_input_manifests WHERE id = j.input_manifest_id;
  IF NEW.transaction_observation_id IS DISTINCT FROM manifest.transaction_observation_id OR NEW.input_as_of IS DISTINCT FROM manifest.input_as_of
  THEN RAISE EXCEPTION 'decision transaction/as-of differs from fixed manifest'; END IF;
 ELSE
  spec := j.llm_spec;
  IF NEW.kind IS DISTINCT FROM j.kind OR NEW.decision_id IS DISTINCT FROM j.input_decision_id OR NEW.config_version_id IS DISTINCT FROM j.input_config_version_id
   OR NEW.evidence_version IS DISTINCT FROM spec->>'evidence_version' OR NEW.input_evidence_hash IS DISTINCT FROM spec->>'input_evidence_hash'
   OR NEW.model_version IS DISTINCT FROM spec->>'model_version' OR NEW.prompt_version IS DISTINCT FROM spec->>'prompt_version'
   OR NEW.output_schema_version IS DISTINCT FROM spec->>'output_schema_version' OR NEW.reuse_key IS DISTINCT FROM spec->>'reuse_key'
  THEN RAISE EXCEPTION 'LLM result/job lineage mismatch'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER decision_v03_validate BEFORE INSERT ON run_process_decisions FOR EACH ROW EXECUTE FUNCTION erd_v03_validate_result();
CREATE TRIGGER llm_result_validate BEFORE INSERT ON run_llm_results FOR EACH ROW EXECUTE FUNCTION erd_v03_validate_result();

CREATE FUNCTION erd_v03_validate_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE model_input ml_artifacts; fit ml_artifacts;
BEGIN
 IF NEW.kind = 'model_input' OR NEW.model_input_artifact_id IS NOT NULL THEN
  IF NEW.snapshot_id IS NULL OR NEW.feature_set_id IS NULL THEN RAISE EXCEPTION 'model input lineage requires snapshot/feature set'; END IF;
  IF NEW.fit_artifact_id IS NOT NULL THEN
   SELECT * INTO STRICT fit FROM ml_artifacts WHERE id = NEW.fit_artifact_id;
   IF fit.kind <> 'fit' OR fit.snapshot_id IS NULL OR fit.feature_set_id IS DISTINCT FROM NEW.feature_set_id
    OR (SELECT source_id FROM data_snapshots WHERE id = fit.snapshot_id) IS DISTINCT FROM (SELECT source_id FROM data_snapshots WHERE id = NEW.snapshot_id)
   THEN RAISE EXCEPTION 'model input fit lineage mismatch'; END IF;
  END IF;
  IF NEW.model_input_artifact_id IS NOT NULL THEN
   SELECT * INTO STRICT model_input FROM ml_artifacts WHERE id = NEW.model_input_artifact_id;
   IF model_input.kind <> 'model_input' OR model_input.snapshot_id IS DISTINCT FROM NEW.snapshot_id
    OR model_input.feature_set_id IS DISTINCT FROM NEW.feature_set_id OR model_input.fit_artifact_id IS DISTINCT FROM NEW.fit_artifact_id
   THEN RAISE EXCEPTION 'model/model_input lineage mismatch'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER artifact_v03_validate BEFORE INSERT OR UPDATE ON ml_artifacts FOR EACH ROW EXECUTE FUNCTION erd_v03_validate_artifact();

-- JSON M references are not SQL FKs. Protect observations once pinned by a manifest.
CREATE INDEX run_input_manifests_messages_idx ON run_input_manifests USING gin(message_observation_refs);
CREATE FUNCTION erd_v03_protect_observation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_observation_id uuid;
BEGIN
 IF TG_TABLE_NAME = 'data_observations' THEN target_observation_id := OLD.id; ELSE target_observation_id := OLD.observation_id; END IF;
 IF EXISTS(SELECT 1 FROM run_input_manifests WHERE process_observation_id = target_observation_id OR transaction_observation_id = target_observation_id
  OR message_observation_refs @> jsonb_build_array(jsonb_build_object('observation_id',target_observation_id::text)))
  OR EXISTS(SELECT 1 FROM data_message_bodies b WHERE b.observation_id = target_observation_id)
  OR EXISTS(SELECT 1 FROM data_message_body_links l WHERE l.body_observation_id = target_observation_id OR l.message_observation_id = target_observation_id)
 THEN RAISE EXCEPTION 'manifest/BODY-pinned observation is immutable'; END IF;
 IF TG_OP = 'UPDATE' THEN RETURN NEW; END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER observation_manifest_guard BEFORE UPDATE OR DELETE ON data_observations FOR EACH ROW EXECUTE FUNCTION erd_v03_protect_observation();
CREATE TRIGGER transaction_manifest_guard BEFORE UPDATE OR DELETE ON data_transactions FOR EACH ROW EXECUTE FUNCTION erd_v03_protect_observation();
CREATE TRIGGER process_manifest_guard BEFORE UPDATE OR DELETE ON data_processes FOR EACH ROW EXECUTE FUNCTION erd_v03_protect_observation();
CREATE TRIGGER message_manifest_guard BEFORE UPDATE OR DELETE ON data_messages FOR EACH ROW EXECUTE FUNCTION erd_v03_protect_observation();
COMMIT;
