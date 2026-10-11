-- 핵심 테이블 24개. 원천 식별·중복 제거·보존 정책을 검토한 뒤 적용한다.
-- 기존 Express JSON 계정이 기준이며 actor ID는 외래 키가 아닌 문자열 참조다.
BEGIN;

CREATE TYPE source_layer AS ENUM ('T','P','M');
CREATE TYPE audit_role AS ENUM ('user','admin');
CREATE TYPE job_kind AS ENUM ('preview','train','evaluate','recommend_rule');
CREATE TYPE job_status AS ENUM ('queued','running','succeeded','failed');
CREATE TYPE attempt_status AS ENUM ('running','succeeded','failed','expired');
CREATE TYPE snapshot_status AS ENUM ('building','ready','failed');
CREATE TYPE split_role AS ENUM ('train','validation','test','context');
CREATE TYPE artifact_kind AS ENUM ('snapshot','model','fit');
CREATE TYPE application_kind AS ENUM ('apply','restore');
CREATE TYPE application_status AS ENUM ('queued','applying','succeeded','failed');
CREATE TYPE decision_status AS ENUM ('complete','partial','unavailable');
CREATE TYPE component_status AS ENUM ('succeeded','not_ready','failed','skipped');
CREATE TYPE measurement_status AS ENUM ('measured','unavailable');

CREATE TABLE ops_sources (
 id uuid PRIMARY KEY, dataset_key text NOT NULL UNIQUE, display_name text NOT NULL,
 ingestion_method text NOT NULL, schema_version text NOT NULL, clock_basis jsonb NOT NULL,
 field_catalog jsonb NOT NULL, created_at timestamptz NOT NULL
);
CREATE TABLE ops_collection_checkpoints (
 id uuid PRIMARY KEY, source_id uuid NOT NULL REFERENCES ops_sources(id), layer source_layer NOT NULL,
 partition_key text NOT NULL, query_from timestamptz, query_until timestamptz,
 cursor_state jsonb NOT NULL, completed_until timestamptz, last_attempt_at timestamptz,
 last_success_at timestamptz, error_code text, lease_token uuid, lease_until timestamptz,
 revision bigint NOT NULL, UNIQUE(source_id,layer,partition_key)
);
CREATE TABLE data_executions (
 id uuid PRIMARY KEY, source_id uuid NOT NULL REFERENCES ops_sources(id), layer source_layer NOT NULL,
 execution_key text NOT NULL, key_parts jsonb NOT NULL, key_contract_version text NOT NULL,
 first_observed_at timestamptz NOT NULL, UNIQUE(source_id,layer,execution_key), UNIQUE(id,source_id,layer)
);
CREATE TABLE data_observations (
 id uuid PRIMARY KEY, source_id uuid NOT NULL REFERENCES ops_sources(id), layer source_layer NOT NULL,
 execution_id uuid, revision_no bigint, checkpoint_id uuid REFERENCES ops_collection_checkpoints(id),
 dedup_key text NOT NULL, content_hash text NOT NULL, source_revision_token text,
 source_log_time_raw text, source_log_occurred_at timestamptz,
 source_updated_time_raw text, source_updated_at timestamptz,
 api_received_at timestamptz NOT NULL, persisted_at timestamptz NOT NULL,
 clock_status text NOT NULL, parsed_times jsonb NOT NULL, allowed_values jsonb NOT NULL,
 quality_flags jsonb NOT NULL, missing_fields jsonb NOT NULL,
 UNIQUE(source_id,layer,dedup_key), UNIQUE(execution_id,revision_no),
 UNIQUE(id,source_id), UNIQUE(id,execution_id),
 FOREIGN KEY(execution_id,source_id,layer) REFERENCES data_executions(id,source_id,layer),
 CHECK ((execution_id IS NULL AND revision_no IS NULL) OR (execution_id IS NOT NULL AND revision_no IS NOT NULL)),
 CHECK (revision_no IS NULL OR revision_no > 0)
);
CREATE INDEX data_observations_source_persisted_idx ON data_observations(source_id,persisted_at);
CREATE TABLE data_transactions (
 observation_id uuid PRIMARY KEY REFERENCES data_observations(id), transaction_id text,
 interface_id text, process_hub_id text, start_channel_id text, end_channel_id text,
 process_count numeric, status text, response_code text, response_message text,
 interface_type text, category_name text, start_time_raw text, end_time_raw text,
 reported_process_time_ms numeric, retry_count numeric
);
CREATE INDEX data_transactions_lookup_idx ON data_transactions(transaction_id,process_hub_id,retry_count);
CREATE TABLE data_processes (
 observation_id uuid PRIMARY KEY REFERENCES data_observations(id),
 transaction_observation_id uuid REFERENCES data_transactions(observation_id),
 transaction_id text, process_id text, depend_process_id text, process_hub_id text,
 adapter_type text, channel_id text, status text, start_time_raw text, end_time_raw text,
 success_count numeric, error_count numeric, response_code text, response_message text,
 total_count numeric, retry_count numeric
);
CREATE INDEX data_processes_lookup_idx ON data_processes(transaction_id,process_id,retry_count);
CREATE TABLE data_messages (
 observation_id uuid PRIMARY KEY REFERENCES data_observations(id),
 process_observation_id uuid REFERENCES data_processes(observation_id),
 transaction_id text, process_id text, message_id text, process_channel_id text,
 data_type text, data_name text, status text, response_code text, response_message text,
 success_count numeric, error_count numeric, start_time_raw text, end_time_raw text,
 direction text, data_size numeric, process_datetime_raw text, message_index numeric, retry_count numeric
);
CREATE INDEX data_messages_lookup_idx ON data_messages(transaction_id,process_id,message_id);
CREATE TABLE data_snapshots (
 id uuid PRIMARY KEY, source_id uuid NOT NULL REFERENCES ops_sources(id), status snapshot_status NOT NULL,
 observation_cutoff_at timestamptz NOT NULL, selection_spec jsonb NOT NULL, split_spec jsonb NOT NULL,
 schema_version text NOT NULL, manifest_artifact_id uuid, manifest_hash text,
 created_by_actor_id text NOT NULL, created_at timestamptz NOT NULL, ready_at timestamptz,
 UNIQUE(id,source_id)
);
CREATE TABLE data_snapshot_members (
 snapshot_id uuid NOT NULL, source_id uuid NOT NULL, observation_id uuid NOT NULL,
 parent_transaction_observation_id uuid REFERENCES data_transactions(observation_id),
 split split_role NOT NULL, input_as_of timestamptz, label_evidence jsonb,
 PRIMARY KEY(snapshot_id,observation_id),
 FOREIGN KEY(snapshot_id,source_id) REFERENCES data_snapshots(id,source_id),
 FOREIGN KEY(observation_id,source_id) REFERENCES data_observations(id,source_id)
);
CREATE INDEX data_snapshot_members_split_idx ON data_snapshot_members(snapshot_id,split);

CREATE TABLE ml_feature_versions (
 id uuid PRIMARY KEY, definition_key text NOT NULL, version integer NOT NULL,
 display_name text NOT NULL, definition_kind text NOT NULL, expression jsonb NOT NULL,
 output_type text NOT NULL, output_unit text, value_policy jsonb NOT NULL,
 availability_spec jsonb NOT NULL, requires_fit boolean NOT NULL, content_hash text NOT NULL,
 created_by_actor_id text NOT NULL, created_at timestamptz NOT NULL,
 UNIQUE(definition_key,version), CHECK(version > 0)
);
CREATE TABLE ml_feature_sets (
 id uuid PRIMARY KEY, schema_version text NOT NULL, transform_version text NOT NULL,
 output_schema jsonb NOT NULL, content_hash text NOT NULL,
 created_by_actor_id text NOT NULL, created_at timestamptz NOT NULL
);
CREATE TABLE ml_feature_set_members (
 feature_set_id uuid NOT NULL REFERENCES ml_feature_sets(id),
 feature_version_id uuid NOT NULL REFERENCES ml_feature_versions(id), model_input boolean NOT NULL,
 output_position integer, output_name text NOT NULL, transform_settings jsonb NOT NULL,
 PRIMARY KEY(feature_set_id,feature_version_id), UNIQUE(feature_set_id,output_name),
 UNIQUE(feature_set_id,output_position),
 CHECK ((model_input AND output_position IS NOT NULL AND output_position >= 0)
     OR (NOT model_input AND output_position IS NULL))
);
CREATE TABLE ml_rule_versions (
 id uuid PRIMARY KEY, rule_key text NOT NULL, version integer NOT NULL,
 rule_spec jsonb NOT NULL, input_spec jsonb NOT NULL, evidence_spec jsonb NOT NULL,
 content_hash text NOT NULL, created_by_actor_id text NOT NULL, created_at timestamptz NOT NULL,
 UNIQUE(rule_key,version), CHECK(version > 0)
);
CREATE TABLE ml_config_versions (
 id uuid PRIMARY KEY, base_version_id uuid REFERENCES ml_config_versions(id),
 source_id uuid NOT NULL REFERENCES ops_sources(id), configuration_spec jsonb NOT NULL,
 explanation_spec jsonb NOT NULL, content_hash text NOT NULL,
 created_by_actor_id text NOT NULL, created_at timestamptz NOT NULL
);
CREATE TABLE ml_config_models (
 config_version_id uuid NOT NULL REFERENCES ml_config_versions(id), model_key text NOT NULL,
 feature_set_id uuid NOT NULL REFERENCES ml_feature_sets(id), model_artifact_id uuid,
 fit_artifact_id uuid, adapter_spec jsonb NOT NULL, training_settings jsonb NOT NULL,
 threshold_spec jsonb NOT NULL, PRIMARY KEY(config_version_id,model_key)
);
CREATE TABLE ml_config_rules (
 config_version_id uuid NOT NULL REFERENCES ml_config_versions(id),
 rule_version_id uuid NOT NULL REFERENCES ml_rule_versions(id), evaluation_order integer NOT NULL,
 PRIMARY KEY(config_version_id,rule_version_id), UNIQUE(config_version_id,evaluation_order)
);
CREATE TABLE run_jobs (
 id uuid PRIMARY KEY, kind job_kind NOT NULL, status job_status NOT NULL,
 requested_by_actor_id text NOT NULL, requester_role audit_role NOT NULL,
 idempotency_key text, request_hash text NOT NULL,
 input_config_version_id uuid REFERENCES ml_config_versions(id),
 input_snapshot_id uuid REFERENCES data_snapshots(id), input_spec jsonb NOT NULL,
 max_attempts integer NOT NULL, attempt_count integer NOT NULL, current_attempt_id uuid,
 next_run_at timestamptz NOT NULL,
 result_config_version_id uuid REFERENCES ml_config_versions(id), result_artifact_id uuid,
 result_summary jsonb, error_code text, created_at timestamptz NOT NULL, finished_at timestamptz,
 UNIQUE(requested_by_actor_id,kind,idempotency_key),
 CHECK(max_attempts > 0 AND attempt_count >= 0 AND attempt_count <= max_attempts)
);
CREATE INDEX run_jobs_ready_idx ON run_jobs(status,next_run_at,created_at);
CREATE TABLE run_job_attempts (
 id uuid PRIMARY KEY, job_id uuid NOT NULL REFERENCES run_jobs(id), attempt_no integer NOT NULL,
 status attempt_status NOT NULL, worker_id text NOT NULL, lease_token uuid NOT NULL UNIQUE,
 lease_until timestamptz NOT NULL, started_at timestamptz NOT NULL, heartbeat_at timestamptz,
 finished_at timestamptz, error_code text,
 UNIQUE(job_id,attempt_no), UNIQUE(id,job_id), CHECK(attempt_no > 0)
);
CREATE INDEX run_job_attempts_lease_idx ON run_job_attempts(status,lease_until);
CREATE TABLE ml_artifacts (
 id uuid PRIMARY KEY, kind artifact_kind NOT NULL, relative_path text NOT NULL UNIQUE,
 content_hash text NOT NULL, byte_size bigint NOT NULL,
 snapshot_id uuid REFERENCES data_snapshots(id), feature_set_id uuid REFERENCES ml_feature_sets(id),
 fit_artifact_id uuid REFERENCES ml_artifacts(id), created_job_id uuid REFERENCES run_jobs(id),
 created_attempt_id uuid, format_version text NOT NULL, runtime_version text NOT NULL,
 metadata jsonb NOT NULL, created_at timestamptz NOT NULL, CHECK(byte_size >= 0),
 CHECK(relative_path !~ '(^/|(^|/)\.\.(/|$))')
);
CREATE TABLE run_evaluations (
 id uuid PRIMARY KEY, job_id uuid NOT NULL UNIQUE REFERENCES run_jobs(id),
 config_version_id uuid NOT NULL REFERENCES ml_config_versions(id),
 snapshot_id uuid NOT NULL REFERENCES data_snapshots(id), purpose split_role NOT NULL,
 evaluation_spec_version text NOT NULL, evaluation_spec_hash text NOT NULL,
 evaluation_spec jsonb NOT NULL, applicability text NOT NULL,
 population_count bigint NOT NULL, scored_count bigint NOT NULL, unavailable_count bigint NOT NULL,
 metrics jsonb NOT NULL, reason_codes jsonb NOT NULL, completed_at timestamptz NOT NULL,
 UNIQUE(id,config_version_id), CHECK(purpose IN ('validation','test')),
 CHECK(population_count >= 0 AND scored_count >= 0 AND unavailable_count >= 0),
 CHECK(scored_count + unavailable_count <= population_count)
);
CREATE INDEX run_evaluations_comparison_idx ON run_evaluations(snapshot_id,evaluation_spec_hash,purpose);
CREATE INDEX run_evaluations_config_time_idx ON run_evaluations(config_version_id,completed_at);
CREATE TABLE run_applications (
 id uuid PRIMARY KEY, operation application_kind NOT NULL,
 target_config_version_id uuid NOT NULL REFERENCES ml_config_versions(id),
 previous_config_version_id uuid REFERENCES ml_config_versions(id),
 expected_active_version_id uuid REFERENCES ml_config_versions(id),
 expected_system_revision bigint NOT NULL, evaluation_id uuid,
 requested_by_actor_id text NOT NULL, requester_role audit_role NOT NULL,
 approved_at timestamptz NOT NULL, idempotency_key text, request_hash text NOT NULL,
 status application_status NOT NULL, lease_token uuid, lease_until timestamptz,
 worker_id text, gate_evidence jsonb NOT NULL, transition_audit jsonb NOT NULL,
 error_code text, created_at timestamptz NOT NULL, activated_at timestamptz,
 UNIQUE(requested_by_actor_id,idempotency_key),
 FOREIGN KEY(evaluation_id,target_config_version_id) REFERENCES run_evaluations(id,config_version_id)
);
CREATE INDEX run_applications_status_idx ON run_applications(status,created_at);
CREATE TABLE ops_system_state (
 id smallint PRIMARY KEY CHECK(id = 1),
 active_config_version_id uuid REFERENCES ml_config_versions(id),
 active_application_id uuid REFERENCES run_applications(id),
 revision bigint NOT NULL, detector_state jsonb NOT NULL, changed_at timestamptz NOT NULL,
 CHECK(revision >= 0)
);
CREATE TABLE run_process_decisions (
 id uuid PRIMARY KEY, event_sequence bigint GENERATED BY DEFAULT AS IDENTITY UNIQUE,
 execution_id uuid NOT NULL REFERENCES data_executions(id),
 process_observation_id uuid NOT NULL REFERENCES data_processes(observation_id),
 transaction_observation_id uuid REFERENCES data_transactions(observation_id),
 config_version_id uuid NOT NULL REFERENCES ml_config_versions(id),
 input_manifest_hash text NOT NULL, input_as_of timestamptz, status decision_status NOT NULL,
 rule_status component_status NOT NULL, ml_status component_status NOT NULL,
 decision_code text, anomaly_score numeric, evidence jsonb NOT NULL,
 source_log_occurred_at timestamptz, decided_at timestamptz NOT NULL,
 decision_committed_at timestamptz, source_latency_ms numeric,
 latency_status measurement_status NOT NULL, published_at timestamptz,
 UNIQUE(execution_id,process_observation_id,config_version_id,input_manifest_hash),
 FOREIGN KEY(process_observation_id,execution_id) REFERENCES data_observations(id,execution_id),
 CHECK((latency_status = 'unavailable' AND source_latency_ms IS NULL)
    OR (latency_status = 'measured' AND source_latency_ms IS NOT NULL))
);
CREATE INDEX run_process_decisions_watermark_idx ON run_process_decisions(decision_committed_at,id);
CREATE TABLE ops_storage_measurements (
 id uuid PRIMARY KEY, storage_key text NOT NULL, filesystem_key text,
 target_kind text NOT NULL, status measurement_status NOT NULL,
 capacity_bytes bigint, used_bytes bigint, available_bytes bigint, database_bytes bigint,
 writer_access_verified boolean NOT NULL, warning_spec_version text NOT NULL,
 warning_state text NOT NULL, reason_code text, measured_at timestamptz NOT NULL,
 CHECK(capacity_bytes IS NULL OR capacity_bytes >= 0),
 CHECK(used_bytes IS NULL OR used_bytes >= 0),
 CHECK(available_bytes IS NULL OR available_bytes >= 0),
 CHECK(database_bytes IS NULL OR database_bytes >= 0)
);
CREATE INDEX ops_storage_measurements_time_idx ON ops_storage_measurements(storage_key,measured_at);

-- 순환하는 선택적 참조는 대상 테이블을 모두 생성한 뒤 외래 키를 추가한다.
ALTER TABLE data_snapshots ADD FOREIGN KEY(manifest_artifact_id) REFERENCES ml_artifacts(id);
ALTER TABLE ml_config_models ADD FOREIGN KEY(model_artifact_id) REFERENCES ml_artifacts(id);
ALTER TABLE ml_config_models ADD FOREIGN KEY(fit_artifact_id) REFERENCES ml_artifacts(id);
ALTER TABLE run_jobs ADD FOREIGN KEY(result_artifact_id) REFERENCES ml_artifacts(id);
ALTER TABLE run_jobs ADD FOREIGN KEY(current_attempt_id,id) REFERENCES run_job_attempts(id,job_id);
ALTER TABLE ml_artifacts ADD FOREIGN KEY(created_attempt_id,created_job_id) REFERENCES run_job_attempts(id,job_id);

COMMIT;
