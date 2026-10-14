import { Router, Request, Response, NextFunction } from 'express';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, AuthenticatedRequest, requireAdmin } from '../middleware/auth.js';
import crypto from 'node:crypto';

const router = Router();
router.use(authenticateToken as any);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function route(handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) {
  return (req: Request, res: Response, next: NextFunction): void => {
    void handler(req as AuthenticatedRequest, res).catch(next);
  };
}

router.get('/decisions', route(async (req, res) => {
  const value = req.query.limit;
  if (value !== undefined && (typeof value !== 'string' || !/^[1-9]\d?$|^100$/.test(value))) {
    res.status(400).json({ code: 'invalid_limit' });
    return;
  }
  const limit = value ? Number(value) : 50;
  const result = await getDatabasePool().query(
    `SELECT id, process_observation_id AS "processObservationId",
            config_version_id AS "configVersionId", status,
            rule_status AS "ruleStatus", ml_status AS "mlStatus",
            decision_code AS "decisionCode", anomaly_score AS "anomalyScore",
            evidence, decided_at AS "decidedAt"
       FROM run_process_decisions ORDER BY decided_at DESC, id DESC LIMIT $1`,
    [limit],
  );
  res.json({ items: result.rows });
}));

router.get('/sources', route(async (_req, res) => {
  const result = await getDatabasePool().query(
    `SELECT id, dataset_key AS "datasetKey", display_name AS "displayName",
            created_at AS "createdAt" FROM ops_sources
      ORDER BY created_at DESC LIMIT 100`,
  );
  res.json({ items: result.rows });
}));

router.get('/windows', route(async (_req, res) => {
  const result = await getDatabasePool().query(
    `SELECT id,source_id AS "sourceId",adapter_type AS "adapterType",process_id AS "processId",
            window_start AS "windowStart",window_end AS "windowEnd",model_hash AS "modelHash",
            margins,anomalies,evidence,decided_at AS "decidedAt"
       FROM run_window_decisions ORDER BY decided_at DESC,window_start DESC LIMIT 50`,
  );
  res.json({ items: result.rows });
}));

router.get('/window-monitors', requireAdmin as any, route(async (_req, res) => {
  const result = await getDatabasePool().query(
    `SELECT id,source_id AS "sourceId",algorithm,enabled,last_error AS "lastError",
            history_start AS "historyStart",synthetic_prior_accepted AS "syntheticPriorAccepted"
       FROM ops_window_monitors ORDER BY created_at DESC LIMIT 100`,
  );
  res.json({ items: result.rows });
}));

router.get('/observations', requireAdmin as any, route(async (req, res) => {
  const sourceId = req.query.sourceId;
  if (typeof sourceId !== 'string' || !UUID.test(sourceId)) {
    res.status(400).json({ code: 'invalid_source_id' });
    return;
  }
  const result = await getDatabasePool().query(
    `SELECT o.id, o.persisted_at AS "persistedAt", p.process_id AS "processId",
            p.status, p.response_code AS "responseCode",
            p.error_count AS "errorCount", p.success_count AS "successCount",
            l.label, COALESCE(l.revision_no, 0) AS "labelRevision",
            l.reason_code AS "reasonCode"
       FROM data_observations AS o
       JOIN data_processes AS p ON p.observation_id = o.id
       LEFT JOIN LATERAL (
         SELECT label, revision_no, reason_code FROM data_label_evidence
          WHERE observation_id = o.id ORDER BY revision_no DESC LIMIT 1
       ) AS l ON true
      WHERE o.source_id = $1 ORDER BY o.persisted_at DESC, o.id DESC LIMIT 50`,
    [sourceId],
  );
  res.json({ items: result.rows });
}));

router.post('/labels/:observationId', requireAdmin as any, route(async (req, res) => {
  const observationId = req.params.observationId;
  const body = req.body;
  if (!UUID.test(observationId) || !body || typeof body !== 'object' || Array.isArray(body) ||
      Object.keys(body).some((key) => !['label', 'reasonCode', 'expectedRevision'].includes(key)) ||
      !['normal', 'anomaly'].includes(body.label) ||
      typeof body.reasonCode !== 'string' || !/^[A-Z][A-Z0-9_]{2,63}$/.test(body.reasonCode) ||
      !Number.isSafeInteger(body.expectedRevision) || body.expectedRevision < 0) {
    res.status(400).json({ code: 'invalid_label_request' });
    return;
  }
  const client = await getDatabasePool().connect();
  try {
    await client.query('BEGIN');
    await client.query('SELECT pg_advisory_xact_lock(81733, hashtext($1))', [observationId]);
    const observation = await client.query(
      `SELECT 1 FROM data_observations WHERE id = $1 AND layer = 'P'`, [observationId],
    );
    if (!observation.rowCount) {
      await client.query('ROLLBACK');
      res.status(404).json({ code: 'process_observation_not_found' });
      return;
    }
    const current = await client.query(
      `SELECT COALESCE(max(revision_no), 0) AS revision
         FROM data_label_evidence WHERE observation_id = $1`, [observationId],
    );
    if (current.rows[0].revision !== body.expectedRevision) {
      await client.query('ROLLBACK');
      res.status(409).json({ code: 'label_revision_conflict' });
      return;
    }
    const result = await client.query(
      `INSERT INTO data_label_evidence
       (id, observation_id, revision_no, label, reason_code, created_by_actor_id)
       VALUES ($1, $2, $3, $4, $5, $6)
       RETURNING id, revision_no AS "revisionNo", label, reason_code AS "reasonCode"`,
      [crypto.randomUUID(), observationId, body.expectedRevision + 1,
        body.label, body.reasonCode, req.user!.id],
    );
    await client.query('COMMIT');
    res.status(201).json(result.rows[0]);
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}));

router.get('/metrics', requireAdmin as any, route(async (_req, res) => {
  const pool = getDatabasePool();
  const queues = await pool.query(
    `SELECT 'feature' AS queue, status, count(*)::integer AS count,
            EXTRACT(EPOCH FROM clock_timestamp() - min(created_at))::numeric AS oldest_age_seconds
       FROM powercode_queue.feature_observations GROUP BY status
     UNION ALL
     SELECT 'detection', status, count(*)::integer,
            EXTRACT(EPOCH FROM clock_timestamp() - min(created_at))::numeric
       FROM powercode_queue.detection_logs GROUP BY status
     UNION ALL
     SELECT 'training', status, count(*)::integer,
            EXTRACT(EPOCH FROM clock_timestamp() - min(created_at))::numeric
       FROM powercode_queue.training_jobs GROUP BY status`,
  );
  const stages = await pool.query(
    `SELECT stage, count(*)::integer AS count,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms::double precision) AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms::double precision) AS p95_ms,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms::double precision) AS p99_ms
       FROM ops_pipeline_measurements
      WHERE measured_at >= clock_timestamp() - interval '1 hour' GROUP BY stage`,
  );
  res.json({ queues: queues.rows, stages: stages.rows, window: '1h' });
}));

router.get('/drift', requireAdmin as any, route(async (_req, res) => {
  const result = await getDatabasePool().query(
    `SELECT DISTINCT ON (d.config_version_id, d.feature_set_id, d.feature_name)
            d.config_version_id AS "configVersionId",
            d.feature_set_id AS "featureSetId", d.feature_name AS "featureName",
            d.baseline_count AS "baselineCount", d.recent_count AS "recentCount",
            d.baseline_missing_rate AS "baselineMissingRate",
            d.recent_missing_rate AS "recentMissingRate",
            d.review_required AS "reviewRequired", d.reason_code AS "reasonCode",
            d.measured_at AS "measuredAt"
       FROM ops_feature_drift_measurements AS d
       JOIN ops_system_state AS s ON s.active_config_version_id = d.config_version_id
      ORDER BY d.config_version_id, d.feature_set_id, d.feature_name, d.measured_at DESC`,
  );
  res.json({ items: result.rows });
}));

router.use((_error: unknown, _req: Request, res: Response, _next: NextFunction) => {
  res.status(503).json({ code: 'pipeline_storage_unavailable' });
});

export default router;
