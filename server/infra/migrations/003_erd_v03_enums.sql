-- v0.3 additive enum preparation. Commit before 004 uses the new values.
-- Existing 001/002 and physical queue rows are preserved. PostgreSQL 17+.
BEGIN;
ALTER TYPE source_layer ADD VALUE IF NOT EXISTS 'B';
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'detect';
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'recommend_feature';
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'explain';
ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'blocked';
ALTER TYPE artifact_kind ADD VALUE IF NOT EXISTS 'model_input';
COMMIT;
