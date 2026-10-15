-- Optional DBA setup after 008. Privileged operator CLI, not an HTTP user role.
-- Provision a separate LOGIN externally; do not put a password in this file.
DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='powercode_demo') THEN CREATE ROLE powercode_demo NOLOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA public,powercode_queue TO powercode_demo;
GRANT SELECT ON ops_sources,data_observations,data_executions,data_processes,data_transactions,data_messages,
 data_label_evidence,data_snapshots,data_snapshot_members,data_feature_materializations,ml_feature_versions,ml_feature_sets,ml_feature_set_members,
 ml_rule_versions,ml_config_versions,ml_config_models,ml_config_rules,ml_artifacts,run_jobs,run_job_attempts,
 run_job_snapshots,run_process_decisions,run_input_manifests,run_evaluations,ops_system_state,
 ops_demo_runs,data_demo_inputs,ops_demo_passes,data_demo_deliveries TO powercode_demo;
GRANT INSERT ON ops_sources,data_executions,data_observations,data_processes,data_transactions,data_messages,
 data_label_evidence,data_demo_inputs,data_demo_deliveries,data_feature_materializations,ml_feature_versions,ml_feature_sets,ml_feature_set_members,
 ml_artifacts,ml_config_versions,ml_config_models,ml_config_rules,run_input_manifests,run_job_snapshots TO powercode_demo;
GRANT INSERT,UPDATE ON ops_demo_runs,ops_demo_passes,run_jobs,run_job_attempts,data_snapshots,data_snapshot_members TO powercode_demo;
GRANT SELECT,INSERT,UPDATE ON powercode_queue.training_jobs,powercode_queue.detection_logs TO powercode_demo;
-- Manifest validation takes FOR SHARE row locks, which require UPDATE on a column.
GRANT UPDATE(id) ON data_observations TO powercode_demo;
GRANT UPDATE(observation_id) ON data_processes,data_transactions,data_messages TO powercode_demo;
-- No grants to BODY values, DELETE operations, applications or the active pointer.

-- Current shared job helper reads input selection/origin when 009 is installed.
GRANT SELECT ON ops_input_controls,data_input_origins TO powercode_demo;
