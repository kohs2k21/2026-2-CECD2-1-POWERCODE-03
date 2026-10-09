-- 독립된 큐 스키마이며 ERD 초안의 테이블은 생성하거나 변경하지 않는다.
CREATE SCHEMA IF NOT EXISTS powercode_queue;

CREATE TABLE IF NOT EXISTS powercode_queue.detection_logs (
    id uuid PRIMARY KEY,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'leased', 'done', 'dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    lease_token uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    last_error text,
    CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX IF NOT EXISTS detection_logs_ready_idx
    ON powercode_queue.detection_logs (available_at, created_at)
    WHERE status IN ('pending', 'leased');
CREATE INDEX IF NOT EXISTS detection_logs_lease_idx
    ON powercode_queue.detection_logs (lease_until)
    WHERE status = 'leased';

CREATE TABLE IF NOT EXISTS powercode_queue.training_jobs (
    id uuid PRIMARY KEY,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'leased', 'done', 'dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    lease_token uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    last_error text,
    CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX IF NOT EXISTS training_jobs_ready_idx
    ON powercode_queue.training_jobs (available_at, created_at)
    WHERE status IN ('pending', 'leased');
CREATE INDEX IF NOT EXISTS training_jobs_lease_idx
    ON powercode_queue.training_jobs (lease_until)
    WHERE status = 'leased';
