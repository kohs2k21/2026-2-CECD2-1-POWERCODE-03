-- Apply separately as DBA; bind login roles with GRANT <role> TO <login>.
-- Passwords and existing owners are not changed. Runtime login roles must not be superusers.
DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='powercode_api') THEN CREATE ROLE powercode_api NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='powercode_collector') THEN CREATE ROLE powercode_collector NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='powercode_detector') THEN CREATE ROLE powercode_detector NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='powercode_trainer') THEN CREATE ROLE powercode_trainer NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='powercode_llm') THEN CREATE ROLE powercode_llm NOLOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA public,powercode_queue TO powercode_api,powercode_collector,powercode_detector,powercode_trainer,powercode_llm;
GRANT SELECT ON ops_sources,data_observations,data_executions,data_processes,data_transactions,data_messages,
 data_label_evidence,data_snapshots,data_snapshot_members,ml_feature_versions,ml_feature_sets,ml_feature_set_members,
 ml_rule_versions,ml_config_versions,ml_config_models,ml_config_rules,ml_artifacts,
 run_jobs,run_job_attempts,run_job_snapshots,run_process_decisions,run_input_manifests,run_llm_results,
 run_evaluations,run_applications,ops_system_state,run_decision_events
 TO powercode_api,powercode_collector,powercode_detector,powercode_trainer,powercode_llm;
GRANT SELECT ON data_feature_materializations,ops_pipeline_measurements,ops_feature_drift_measurements,
 ops_window_monitors,run_window_decisions,ops_api_poll_state,ops_api_catalog TO powercode_api;
GRANT INSERT,UPDATE ON run_jobs TO powercode_api,powercode_detector,powercode_trainer,powercode_llm;
GRANT INSERT,UPDATE ON run_job_attempts TO powercode_detector,powercode_trainer,powercode_llm;
GRANT INSERT ON run_jobs,run_input_manifests TO powercode_collector;
GRANT INSERT ON ml_rule_versions,data_label_evidence TO powercode_api;
GRANT INSERT,UPDATE ON run_applications,ops_system_state TO powercode_api,powercode_detector;
GRANT SELECT,INSERT,UPDATE ON ops_sources,data_executions,data_observations,data_processes,data_transactions,data_messages,
 ops_collection_checkpoints,ops_api_poll_state TO powercode_collector;
GRANT SELECT,INSERT ON ops_api_catalog TO powercode_collector;
GRANT SELECT,INSERT ON data_feature_materializations TO powercode_detector,powercode_trainer,powercode_llm;
GRANT INSERT ON ops_pipeline_measurements,run_process_decisions,run_decision_events TO powercode_detector;
GRANT USAGE ON SEQUENCE run_decision_events_id_seq TO powercode_detector;
GRANT INSERT ON run_input_manifests TO powercode_detector;
-- FOR SHARE in the manifest validator requires UPDATE privilege on a column.
GRANT UPDATE(id) ON data_observations TO powercode_detector;
GRANT UPDATE(observation_id) ON data_processes,data_transactions,data_messages TO powercode_detector;
GRANT INSERT,UPDATE ON data_snapshots TO powercode_trainer,powercode_llm;
GRANT INSERT,UPDATE ON data_snapshot_members TO powercode_trainer,powercode_llm;
GRANT INSERT ON run_job_snapshots TO powercode_trainer,powercode_llm;
GRANT INSERT ON ml_feature_versions,ml_feature_sets,ml_feature_set_members TO powercode_llm;
GRANT INSERT ON ml_artifacts,ml_config_versions,ml_config_models,ml_config_rules,run_evaluations TO powercode_trainer;
GRANT INSERT ON run_llm_results TO powercode_llm;
GRANT SELECT,INSERT,UPDATE ON powercode_queue.detection_logs TO powercode_collector,powercode_detector;
GRANT SELECT,UPDATE ON powercode_queue.feature_observations TO powercode_detector;
GRANT INSERT ON powercode_queue.training_jobs,powercode_queue.llm_recommendation_jobs,powercode_queue.llm_explanation_jobs TO powercode_api;
GRANT SELECT,UPDATE ON powercode_queue.training_jobs TO powercode_trainer,powercode_llm;
GRANT SELECT,UPDATE ON powercode_queue.llm_recommendation_jobs,powercode_queue.llm_explanation_jobs TO powercode_llm;
GRANT SELECT ON powercode_queue.feature_observations,powercode_queue.detection_logs,powercode_queue.training_jobs,
 powercode_queue.llm_recommendation_jobs,powercode_queue.llm_explanation_jobs TO powercode_api;
-- BODY value columns have no runtime role grants. Optional monitor/window jobs use their own DBA-provided role.

GRANT SELECT,INSERT ON data_api_snapshots,ops_api_poll_samples TO powercode_collector;
GRANT SELECT ON data_api_snapshots,ops_api_poll_samples TO powercode_api;

-- 009: shared API/CSV input. Body grants expose only reference/collection metadata.
GRANT SELECT ON ops_input_controls,ops_input_runs,data_input_replays,data_input_replay_rows,data_input_origins
 TO powercode_api,powercode_collector,powercode_detector;
GRANT INSERT,UPDATE ON ops_input_controls,ops_input_runs TO powercode_api,powercode_collector;
GRANT INSERT,UPDATE ON data_input_replays TO powercode_collector;
GRANT INSERT ON data_input_replay_rows,data_input_origins TO powercode_collector;
GRANT INSERT(observation_id,source_id,message_id,collection_status,field_states,representation,access_policy_version,retention_policy_version)
 ON data_message_bodies TO powercode_collector;
GRANT SELECT(observation_id) ON data_message_bodies TO powercode_collector,powercode_detector;

-- 010: isolated prepared CSV metadata. Detector/collector have no access.
GRANT SELECT,INSERT ON data_prepared_datasets TO powercode_api;
GRANT SELECT,UPDATE ON data_prepared_datasets TO powercode_trainer;
GRANT INSERT ON ml_feature_versions,ml_feature_sets,ml_feature_set_members TO powercode_trainer;

-- 011: evaluation sets and configuration comparisons. API registers queued sets; Trainer completes sets/comparisons.
GRANT SELECT,INSERT ON data_evaluation_sets TO powercode_api;
GRANT SELECT,UPDATE ON data_evaluation_sets TO powercode_trainer;
-- LLM workers also reconcile dead training-queue jobs; the 011 job-failure trigger then fails the queued set.
GRANT SELECT(job_id,status),UPDATE(status,error_code) ON data_evaluation_sets TO powercode_llm;
GRANT SELECT ON run_config_comparisons,run_config_comparison_items TO powercode_api,powercode_trainer;
GRANT INSERT ON run_config_comparisons,run_config_comparison_items TO powercode_trainer;
