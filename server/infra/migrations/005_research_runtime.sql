BEGIN;
CREATE TABLE IF NOT EXISTS ops_window_monitors (
 id uuid PRIMARY KEY,
 source_id uuid NOT NULL REFERENCES ops_sources(id),
 algorithm text NOT NULL CHECK (algorithm IN ('if','kmeans')),
 model_hash text NOT NULL,
 history_start timestamptz NOT NULL,
 lateness_seconds integer NOT NULL CHECK (lateness_seconds BETWEEN 0 AND 3600),
 synthetic_prior_accepted boolean NOT NULL DEFAULT false,
 enabled boolean NOT NULL DEFAULT true,
 last_error text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(source_id,algorithm,model_hash,history_start)
);
CREATE TABLE IF NOT EXISTS run_window_decisions (
 id uuid PRIMARY KEY,
 monitor_id uuid NOT NULL REFERENCES ops_window_monitors(id),
 source_id uuid NOT NULL REFERENCES ops_sources(id),
 stream_key text NOT NULL,
 adapter_type text NOT NULL,
 process_id text NOT NULL,
 window_start timestamptz NOT NULL,
 window_end timestamptz NOT NULL,
 model_hash text NOT NULL,
 input_hash text NOT NULL,
 margins jsonb NOT NULL,
 anomalies jsonb NOT NULL,
 evidence jsonb NOT NULL,
 decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(monitor_id,stream_key,window_start)
);
CREATE INDEX IF NOT EXISTS window_decisions_latest_idx ON run_window_decisions(decided_at DESC);
COMMIT;
