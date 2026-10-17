import crypto from 'node:crypto';
import { Router, Request, Response, NextFunction } from 'express';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, AuthenticatedRequest, requireAdmin } from '../middleware/auth.js';
import { hasStatusRulePolicy } from '../services/statusRulePolicy.js';

const router = Router();
router.use(authenticateToken as any);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

class ConfigError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}

function route(handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) {
  return (req: Request, res: Response, next: NextFunction): void => {
    void handler(req as AuthenticatedRequest, res).catch(next);
  };
}

function uuid(value: unknown): value is string {
  return typeof value === 'string' && UUID.test(value);
}

router.get('/active', route(async (_req, res) => {
  const result = await getDatabasePool().query(
    `SELECT active_config_version_id AS "configVersionId", revision, changed_at AS "changedAt",
            detector_state AS "detectorState" FROM ops_system_state WHERE id = 1`,
  );
  const row = result.rows[0];
  res.json(row ? { ...row, revision: Number(row.revision) } :
    { configVersionId: null, revision: 0, detectorState: {} });
}));

router.get('/applications', requireAdmin as any, route(async (_req, res) => {
  const result = await getDatabasePool().query(
    `SELECT id, operation, status,
            target_config_version_id AS "targetConfigVersionId",
            evaluation_id AS "evaluationId", activated_at AS "activatedAt"
       FROM run_applications WHERE status = 'succeeded'
      ORDER BY activated_at DESC LIMIT 50`,
  );
  res.json({ items: result.rows });
}));

router.get('/:id', route(async (req, res) => {
  if (!uuid(req.params.id)) throw new ConfigError('invalid_config_id');
  const result = await getDatabasePool().query(
    `SELECT id, source_id AS "sourceId", configuration_spec AS "configurationSpec",
            content_hash AS "contentHash", created_at AS "createdAt"
       FROM ml_config_versions WHERE id = $1`,
    [req.params.id],
  );
  if (!result.rowCount) throw new ConfigError('config_not_found', 404);
  res.json(result.rows[0]);
}));

router.post('/:id/applications', requireAdmin as any, route(async (req, res) => {
  if (!uuid(req.params.id)) throw new ConfigError('invalid_config_id');
  const body = req.body;
  if (!body || typeof body !== 'object' || Array.isArray(body) ||
      Object.keys(body).some((key) => !['operation', 'evaluationId', 'expectedRevision', 'expectedActiveVersionId'].includes(key)) ||
      !['apply', 'restore'].includes(body.operation) || !uuid(body.evaluationId) ||
      !Number.isSafeInteger(body.expectedRevision) || body.expectedRevision < 0 ||
      !(body.expectedActiveVersionId === null || uuid(body.expectedActiveVersionId))) {
    throw new ConfigError('invalid_application_request');
  }
  const header = req.headers['idempotency-key'];
  if (header !== undefined && (typeof header !== 'string' || !/^[a-zA-Z0-9_.:-]{1,128}$/.test(header))) {
    throw new ConfigError('invalid_idempotency_key');
  }
  const hash = crypto.createHash('sha256').update(JSON.stringify({ id: req.params.id, body })).digest('hex');
  const client = await getDatabasePool().connect();
  try {
    await client.query('BEGIN');
    await client.query(
      `INSERT INTO ops_system_state (id, active_config_version_id, active_application_id,
                                    revision, detector_state, changed_at)
       VALUES (1, NULL, NULL, 0, '{}'::jsonb, clock_timestamp()) ON CONFLICT DO NOTHING`,
    );
    const state = await client.query(
      `SELECT active_config_version_id, revision FROM ops_system_state WHERE id = 1 FOR UPDATE`,
    );
    // pg 드라이버는 bigint를 문자열로 반환하므로 요청의 안전한 정수와 명시적으로 비교한다.
    if (!Number.isSafeInteger(Number(state.rows[0].revision)) ||
        Number(state.rows[0].revision) !== body.expectedRevision ||
        state.rows[0].active_config_version_id !== body.expectedActiveVersionId) {
      throw new ConfigError('active_config_changed', 409);
    }
    const gate = await client.query(
      `SELECT e.id FROM run_evaluations AS e
         JOIN ml_config_versions AS c ON c.id = e.config_version_id
        WHERE e.id = $1 AND e.config_version_id = $2 AND e.applicability = 'valid'
          AND e.purpose = 'validation' AND e.scored_count > 0
          AND e.unavailable_count = 0
          AND NOT EXISTS (SELECT 1 FROM data_prepared_datasets d WHERE d.snapshot_id=e.snapshot_id)`,
      [body.evaluationId, req.params.id],
    );
    if (!gate.rowCount) throw new ConfigError('evaluation_gate_not_met', 409);
    const config = await client.query('SELECT configuration_spec,source_id FROM ml_config_versions WHERE id=$1',[req.params.id]);
    if (config.rows[0]?.configuration_spec?.operationalEligibility?.eligible === false ||
        config.rows[0]?.configuration_spec?.preparedDatasetId ||
        (await client.query(`SELECT 1 FROM ml_config_models cm JOIN ml_artifacts a ON a.id=cm.model_artifact_id
          JOIN data_prepared_datasets d ON d.snapshot_id=a.snapshot_id WHERE cm.config_version_id=$1 LIMIT 1`,[req.params.id])).rowCount) {
      throw new ConfigError('prepared_detector_reproduction_unverified',409);
    }
    const mode = config.rows[0]?.configuration_spec?.detectionPolicy?.mode ?? 'parallel';
    if (config.rows[0]?.configuration_spec?.statusRulePolicy !== undefined &&
        !hasStatusRulePolicy(config.rows[0].configuration_spec, config.rows[0].source_id)) {
      throw new ConfigError('status_rule_policy_invalid',409);
    }
    const ruleCount = await client.query('SELECT count(*)::integer AS count FROM ml_config_rules WHERE config_version_id=$1',[req.params.id]);
    if (mode !== 'ml_only' && !ruleCount.rows[0].count && config.rows[0]?.configuration_spec?.detectionPolicy &&
        !hasStatusRulePolicy(config.rows[0]?.configuration_spec, config.rows[0]?.source_id)) {
      throw new ConfigError('rules_unavailable',409);
    }
    const model = await client.query(
      `SELECT 1 FROM ml_config_models AS cm
         JOIN ml_artifacts AS m ON m.id = cm.model_artifact_id AND m.kind = 'model'
         JOIN ml_artifacts AS f ON f.id = cm.fit_artifact_id AND f.kind = 'fit'
        WHERE cm.config_version_id = $1 AND cm.model_key = 'iforest'
          AND cm.feature_set_id = m.feature_set_id AND cm.feature_set_id = f.feature_set_id
          AND m.fit_artifact_id = f.id AND m.snapshot_id = f.snapshot_id`,
      [req.params.id],
    );
    if (mode !== 'rule_only' && model.rowCount !== 1) throw new ConfigError('model_artifacts_not_ready', 409);
    const mismatchedRule = await client.query(
      `SELECT 1 FROM ml_config_rules AS cr
         JOIN ml_rule_versions AS rv ON rv.id = cr.rule_version_id
         JOIN ml_config_versions AS c ON c.id = cr.config_version_id
        WHERE cr.config_version_id = $1
          AND rv.input_spec->>'featureSetId' IS NOT NULL
          AND rv.input_spec->>'featureSetId' <> c.configuration_spec->>'featureSetId' LIMIT 1`,
      [req.params.id],
    );
    if (mismatchedRule.rowCount) throw new ConfigError('rule_feature_set_mismatch', 409);
    const id = crypto.randomUUID();
    const result = await client.query(
      `INSERT INTO run_applications
       (id, operation, target_config_version_id, previous_config_version_id,
        expected_active_version_id, expected_system_revision, evaluation_id,
        requested_by_actor_id, requester_role, approved_at, idempotency_key,
        request_hash, status, gate_evidence, transition_audit, created_at)
       VALUES ($1, $2, $3, $4, $4, $5, $6, $7, 'admin', clock_timestamp(),
               $8, $9, 'queued', $10::jsonb, '{}'::jsonb, clock_timestamp())
       ON CONFLICT (requested_by_actor_id, idempotency_key) DO NOTHING
       RETURNING id, status`,
      [id, body.operation, req.params.id, body.expectedActiveVersionId,
        body.expectedRevision, body.evaluationId, req.user!.id, header ?? null,
        hash, JSON.stringify({ evaluationId: body.evaluationId, gateVersion: 1 })],
    );
    let row = result.rows[0];
    if (!row) {
      const previous = await client.query(
        `SELECT id, status, request_hash FROM run_applications
          WHERE requested_by_actor_id = $1 AND idempotency_key = $2`,
        [req.user!.id, header],
      );
      row = previous.rows[0];
      if (!row || row.request_hash !== hash) throw new ConfigError('idempotency_payload_conflict', 409);
    }
    await client.query('COMMIT');
    res.status(result.rowCount ? 201 : 200).json(row);
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}));

router.get('/applications/:id', route(async (req, res) => {
  if (!uuid(req.params.id)) throw new ConfigError('invalid_application_id');
  const result = await getDatabasePool().query(
    `SELECT id, operation, status, target_config_version_id AS "targetConfigVersionId",
            error_code AS "errorCode", created_at AS "createdAt", activated_at AS "activatedAt"
       FROM run_applications WHERE id = $1 AND (requested_by_actor_id = $2 OR $3::boolean)`,
    [req.params.id, req.user!.id, req.user!.userType === 'admin'],
  );
  if (!result.rowCount) throw new ConfigError('application_not_found', 404);
  res.json(result.rows[0]);
}));

router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
  if (error instanceof ConfigError) {
    res.status(error.status).json({ code: error.code });
    return;
  }
  res.status(503).json({ code: 'configuration_storage_unavailable' });
});

export default router;
