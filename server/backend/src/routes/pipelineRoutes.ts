import { Router, Request, Response, NextFunction } from 'express';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, AuthenticatedRequest, requireAdmin } from '../middleware/auth.js';
import crypto from 'node:crypto';
import { UserRepository } from '../repository/userRepository.js';
import { streamDecisionEvents } from '../services/decisionStream.js';
import reportRoutes from './reportRoutes.js';

const router = Router();
router.use(authenticateToken as any);
router.use('/reports', reportRoutes);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function route(handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) {
  return (req: Request, res: Response, next: NextFunction): void => {
    void handler(req as AuthenticatedRequest, res).catch(next);
  };
}

router.get('/events', route(async (req, res) => {
  const cursor = req.headers['last-event-id'] ?? req.query.after ?? '0';
  if (typeof cursor !== 'string' || !/^(0|[1-9]\d{0,18})$/.test(cursor) || BigInt(cursor) > 9223372036854775807n) {
    res.status(400).json({code:'invalid_event_cursor'});return;
  }
  // include=interims: 중간 판정 기록 이벤트(012 event_kind first_anomaly·evidence_added·superseded·no_final)도 `event: interim`으로 보낸다.
  // 없으면 기존과 같이 최종 판정 이벤트(`event: decision`)만 보낸다(기존 소비자 호환). 커서는 두 경우 모두 run_decision_events 순번 하나.
  const include = req.query.include;
  if (include !== undefined && include !== 'interims') { res.status(400).json({code:'invalid_event_include'});return; }
  const interims = include === 'interims';
  await streamDecisionEvents(res, {
    after: cursor,
    authorize: async () => {
      const user = await UserRepository.getInstance().getById(req.user!.id);
      return !!user && user.userType === req.user!.userType && user.email === req.user!.email &&
        (req.tokenExpiresAt === undefined || Date.now() < req.tokenExpiresAt);
    },
    eventName: (event) => event.eventKind === 'final' ? 'decision' : 'interim',
    readEvents: async (after) => {
      const result = await getDatabasePool().query(`SELECT e.id::text AS "eventId",e.event_kind AS "eventKind",d.id,d.status,
          d.config_version_id AS "configVersionId",d.rule_status AS "ruleStatus",d.ml_status AS "mlStatus",
          d.decision_code AS "decisionCode",d.anomaly_score AS "anomalyScore",d.evidence,
          o.source_id AS "sourceId",d.decided_at AS "decidedAt",d.process_observation_id AS "processObservationId",
          i.id AS "interimId",i.job_id AS "jobId",a.attempt_no AS "attemptNo",i.seq,i.evidence_version AS "evidenceVersion",
          i.created_at AS "createdAt",m.source_id AS "interimSourceId",m.process_observation_id AS "interimProcessObservationId"
          FROM run_decision_events e
          LEFT JOIN run_process_decisions d ON e.event_kind='final' AND d.id=e.decision_id
          LEFT JOIN data_observations o ON o.id=d.process_observation_id
          LEFT JOIN run_decision_interims i ON e.event_kind<>'final' AND i.id=e.interim_id
          LEFT JOIN run_job_attempts a ON a.id=i.attempt_id
          LEFT JOIN run_jobs j ON j.id=i.job_id
          LEFT JOIN run_input_manifests m ON m.id=j.input_manifest_id
          WHERE e.id>$1::bigint AND ($2::boolean OR e.event_kind='final')
            AND ((e.event_kind='final' AND d.id IS NOT NULL) OR (e.event_kind<>'final' AND i.id IS NOT NULL))
          ORDER BY e.id LIMIT 100`,[after, interims]);
      return result.rows.map((row) => row.eventKind === 'final'
        ? { eventId: row.eventId, eventKind: 'final', id: row.id, status: row.status, configVersionId: row.configVersionId,
            ruleStatus: row.ruleStatus, mlStatus: row.mlStatus, decisionCode: row.decisionCode, anomalyScore: row.anomalyScore,
            evidence: row.evidence, sourceId: row.sourceId, decidedAt: row.decidedAt, processObservationId: row.processObservationId }
        // 중간 판정 기록 이벤트: 기록 식별·시도만(payload 스냅샷은 GET /api/analysis/decisions/:id/interims로 읽는다).
        : { eventId: row.eventId, eventKind: row.eventKind, interimId: row.interimId, jobId: row.jobId, attemptNo: row.attemptNo,
            seq: row.seq, evidenceVersion: row.evidenceVersion, sourceId: row.interimSourceId,
            processObservationId: row.interimProcessObservationId, createdAt: row.createdAt });
    },
  });
}));

router.get('/collector/metrics', requireAdmin as any, route(async (req,res) => {
  const sourceId = req.query.sourceId;
  if (typeof sourceId !== 'string' || !UUID.test(sourceId)) {res.status(400).json({code:'invalid_source_id'});return;}
  const result = await getDatabasePool().query(`WITH samples AS (
      SELECT p.*,extract(epoch FROM started_at-lag(started_at) OVER (PARTITION BY endpoint ORDER BY started_at)) AS gap
      FROM ops_api_poll_samples p WHERE source_id=$1 AND started_at>=clock_timestamp()-interval '10 minutes')
    SELECT endpoint,count(*)::integer AS requests,count(*) FILTER(WHERE error_code IS NOT NULL)::integer AS failures,
      count(*) FILTER(WHERE item_count=0)::integer AS "emptyResponses",
      count(*) FILTER(WHERE item_count>0)::integer AS "nonemptyResponses",
      percentile_cont(.95) WITHIN GROUP(ORDER BY duration_ms) AS "p95Ms",
      percentile_cont(.99) WITHIN GROUP(ORDER BY duration_ms) AS "p99Ms",
      percentile_cont(.95) WITHIN GROUP(ORDER BY db_commit_ms) AS "persistP95Ms",
      percentile_cont(.95) WITHIN GROUP(ORDER BY gap) AS "pollGapP95Seconds",
      max(started_at) AS "lastSampleAt" FROM samples GROUP BY endpoint ORDER BY endpoint`,[sourceId]);
  res.json({items:result.rows,window:'10m',sourceLatencyMs:null,sourceClockStatus:'unknown'});
}));

router.get('/collector', requireAdmin as any, route(async (_req,res) => {
  const result = await getDatabasePool().query(`SELECT source_id AS "sourceId",endpoint,checkpoint,
      last_attempt_at AS "lastAttemptAt",last_success_at AS "lastSuccessAt",error_code AS "errorCode"
      FROM ops_api_poll_state ORDER BY source_id,endpoint`);
  res.json({items:result.rows});
}));

router.get('/decisions', route(async (req, res) => {
  const value = req.query.limit;
  if (value !== undefined && (typeof value !== 'string' || !/^[1-9]\d?$|^100$/.test(value))) {
    res.status(400).json({ code: 'invalid_limit' });
    return;
  }
  const limit = value ? Number(value) : 50;
  const result = await getDatabasePool().query(
    `SELECT d.id, o.source_id AS "sourceId", process_observation_id AS "processObservationId",
            config_version_id AS "configVersionId", status,
            rule_status AS "ruleStatus", ml_status AS "mlStatus",
            decision_code AS "decisionCode", anomaly_score AS "anomalyScore",
            evidence, decided_at AS "decidedAt"
       FROM run_process_decisions d JOIN data_observations o ON o.id=d.process_observation_id
       ORDER BY decided_at DESC, d.id DESC LIMIT $1`,
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
       FROM powercode_queue.training_jobs GROUP BY status
     UNION ALL SELECT 'llm_recommendation', status, count(*)::integer,
       EXTRACT(EPOCH FROM clock_timestamp() - min(created_at))::numeric
       FROM powercode_queue.llm_recommendation_jobs GROUP BY status
     UNION ALL SELECT 'llm_explanation', status, count(*)::integer,
       EXTRACT(EPOCH FROM clock_timestamp() - min(created_at))::numeric
       FROM powercode_queue.llm_explanation_jobs GROUP BY status`,
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
