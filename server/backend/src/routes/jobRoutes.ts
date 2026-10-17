import crypto from 'node:crypto';
import { Router, Request, Response, NextFunction } from 'express';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, AuthenticatedRequest } from '../middleware/auth.js';
import { parseRuleDraft, parseRuleKey, InvalidRuleError, bindRecommendationEvidence } from '../rules/ruleContract.js';
import { writeRule, RuleConflictError } from '../rules/ruleRepository.js';
import { TRAINER_CAPABILITIES, featureSetCapability } from './trainerContract.js';

const router = Router();
router.use(authenticateToken as any);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UTC_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const KINDS = new Set(['recommend_feature', 'recommend_rule', 'train', 'evaluate', 'explain']);

class JobRequestError extends Error {
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

type JobInput = {
  kind: string; sourceId: string; from?: string; until?: string;
  featureSetId?: string; configVersionId?: string; snapshotId?: string;
  purpose?: string; ruleVersionIds?: string[]; decisionId?: string; savedEvidence?: Record<string, unknown>; savedEvidenceJson?: string;
  detectionPolicy?: { mode: string; deadlineMs: number; combination: string };
  modelKey?: string;
};

function parseRequest(value: unknown): JobInput {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new JobRequestError('invalid_job_request');
  const body = value as Record<string, unknown>;
  if (typeof body.kind !== 'string' || !KINDS.has(body.kind) || !uuid(body.sourceId)) {
    throw new JobRequestError('invalid_job_request');
  }
  const permitted = ['kind', 'sourceId', 'from', 'until', 'featureSetId', 'configVersionId', 'snapshotId', 'purpose', 'ruleVersionIds', 'decisionId', 'detectionPolicy', 'modelKey'];
  if (Object.keys(body).some((key) => !permitted.includes(key))) throw new JobRequestError('invalid_job_request');
  const input: JobInput = { kind: body.kind, sourceId: body.sourceId };
  if (body.kind === 'explain') {
    if (!uuid(body.decisionId) || Object.keys(body).some(key => !['kind','sourceId','decisionId'].includes(key))) {
      throw new JobRequestError('invalid_explanation_request');
    }
    input.decisionId = body.decisionId;
  } else if (body.kind === 'evaluate') {
    if (!uuid(body.configVersionId) || !uuid(body.snapshotId) ||
        !['validation', 'test'].includes(String(body.purpose))) {
      throw new JobRequestError('invalid_evaluation_request');
    }
    input.configVersionId = body.configVersionId;
    input.snapshotId = body.snapshotId;
    input.purpose = String(body.purpose);
    if (body.from !== undefined || body.until !== undefined || body.featureSetId !== undefined || body.ruleVersionIds !== undefined) {
      throw new JobRequestError('invalid_evaluation_request');
    }
  } else {
    if (typeof body.from !== 'string' || typeof body.until !== 'string' ||
        !UTC_TIME.test(body.from) || !UTC_TIME.test(body.until) ||
        !Number.isFinite(Date.parse(body.from)) || !Number.isFinite(Date.parse(body.until)) ||
        Date.parse(body.from) >= Date.parse(body.until) ||
        Date.parse(body.until) - Date.parse(body.from) > 366 * 86400000) {
      throw new JobRequestError('invalid_time_range');
    }
    input.from = body.from;
    input.until = body.until;
    if (body.kind !== 'recommend_feature') {
      if (!uuid(body.featureSetId)) throw new JobRequestError('feature_set_required');
      input.featureSetId = body.featureSetId;
    }
    if (body.kind === 'train') {
      const policy = body.detectionPolicy as JobInput['detectionPolicy'];
      if (!policy || typeof policy !== 'object' || Array.isArray(policy) ||
          Object.keys(policy).some(key => !['mode','deadlineMs','combination'].includes(key)) ||
          !['rule_only','ml_only','parallel'].includes(policy.mode) ||
          !Number.isInteger(policy.deadlineMs) || policy.deadlineMs < 100 || policy.deadlineMs > 30000 ||
          policy.combination !== (policy.mode === 'parallel' ? 'any_anomaly' : 'selected_path')) {
        throw new JobRequestError('detection_policy_required');
      }
      input.detectionPolicy = policy;
      if (policy.mode === 'rule_only' && body.modelKey !== undefined) throw new JobRequestError('model_not_selected', 422);
      if (body.modelKey !== undefined && body.modelKey !== 'iforest') throw new JobRequestError('unsupported_model', 422);
      // Omission keeps the historical request hash and default IF recipe.
      if (body.modelKey !== undefined) input.modelKey = 'iforest';
      const rules = body.ruleVersionIds ?? [];
      if (!Array.isArray(rules) || rules.length > 32 || !rules.every(uuid) || new Set(rules).size !== rules.length) {
        throw new JobRequestError('invalid_rule_versions');
      }
      if (policy.mode !== 'ml_only' && !rules.length) throw new JobRequestError('rules_required');
      if (policy.mode === 'ml_only' && rules.length) throw new JobRequestError('ml_only_rules_not_selected');
      input.ruleVersionIds = rules;
    } else if (body.ruleVersionIds !== undefined) {
      throw new JobRequestError('invalid_job_request');
    }
    if (body.snapshotId !== undefined) {
      if (!uuid(body.snapshotId) || body.kind === 'recommend_feature') throw new JobRequestError('invalid_snapshot_reference');
      input.snapshotId = body.snapshotId;
    }
    if (body.configVersionId !== undefined || body.purpose !== undefined) {
      throw new JobRequestError('invalid_job_request');
    }
  }
  if (body.kind !== 'train' && body.detectionPolicy !== undefined) throw new JobRequestError('invalid_job_request');
  if (body.kind !== 'train' && body.modelKey !== undefined) throw new JobRequestError('invalid_job_request');
  if (body.kind !== 'explain' && body.decisionId !== undefined) throw new JobRequestError('invalid_job_request');
  return input;
}

router.post('/', route(async (req, res) => {
  if (req.user!.userType !== 'admin' && ['recommend_feature','recommend_rule','train','evaluate'].includes(req.body?.kind)) throw new JobRequestError('admin_required',403);
  const input = parseRequest(req.body);
  if (['recommend_feature', 'recommend_rule', 'train', 'evaluate'].includes(input.kind) && req.user!.userType !== 'admin') {
    throw new JobRequestError('admin_required', 403);
  }
  const header = req.headers['idempotency-key'];
  if (header !== undefined && (typeof header !== 'string' || !/^[a-zA-Z0-9_.:-]{1,128}$/.test(header))) {
    throw new JobRequestError('invalid_idempotency_key');
  }
  const idempotencyKey = header ?? crypto.randomUUID();
  let requestHash = crypto.createHash('sha256').update(JSON.stringify(input)).digest('hex');
  const client = await getDatabasePool().connect();
  try {
    await client.query('BEGIN');
    const source = await client.query('SELECT 1 FROM ops_sources WHERE id = $1', [input.sourceId]);
    if (!source.rowCount) throw new JobRequestError('source_not_found', 404);
    if (input.featureSetId) {
      const set = await client.query('SELECT 1 FROM ml_feature_sets WHERE id = $1', [input.featureSetId]);
      if (!set.rowCount) throw new JobRequestError('feature_set_not_found', 404);
    }
    if (input.snapshotId && input.kind !== 'evaluate') {
      const snapshot = await client.query(`SELECT 1 FROM data_snapshots WHERE id=$1 AND source_id=$2
        AND status='ready' AND (selection_spec->>'from')::timestamptz=$3::timestamptz
        AND (selection_spec->>'until')::timestamptz=$4::timestamptz`,
        [input.snapshotId,input.sourceId,input.from,input.until]);
      if (!snapshot.rowCount) throw new JobRequestError('snapshot_conditions_mismatch',409);
    }
    if (input.kind === 'train') {
      const members = await client.query(`SELECT m.output_name AS name,m.output_position AS position,
        v.expression,v.requires_fit AS "requiresFit" FROM ml_feature_set_members m
        JOIN ml_feature_versions v ON v.id=m.feature_version_id
        WHERE m.feature_set_id=$1 AND m.model_input ORDER BY m.output_position`, [input.featureSetId]);
      const support = featureSetCapability(members.rows);
      if (!support.supported) throw new JobRequestError(support.reason ?? 'unsupported_feature_set', 422);
      if (input.detectionPolicy?.mode === 'rule_only' && members.rows.some(member => member.requiresFit)) {
        throw new JobRequestError('rule_only_fitted_features_unsupported', 422);
      }
      const rules = input.ruleVersionIds ?? [];
      if (rules.length) {
        const found = await client.query(
          `SELECT count(*)::integer AS count FROM ml_rule_versions
            WHERE id = ANY($1::uuid[]) AND rule_spec->>'state' <> 'retired'
              AND (input_spec->>'featureSetId' IS NULL OR input_spec->>'featureSetId' = $2)`,
          [rules, input.featureSetId],
        );
        if (found.rows[0].count !== rules.length) throw new JobRequestError('rule_version_not_found', 404);
      }
    }
    if (input.kind === 'evaluate') {
      const refs = await client.query(
        `SELECT 1 FROM ml_config_versions AS c JOIN data_snapshots AS s ON s.id = $2
          WHERE c.id = $1 AND c.source_id = $3 AND s.source_id = $3 AND s.status = 'ready'`,
        [input.configVersionId, input.snapshotId, input.sourceId],
      );
      if (!refs.rowCount) throw new JobRequestError('evaluation_inputs_not_ready', 409);
    }
    if (input.kind === 'explain') {
      const saved = await client.query(`SELECT d.id,d.config_version_id,d.status,d.rule_status,d.ml_status,
        d.decision_code,d.anomaly_score,d.evidence,d.input_manifest_hash
        FROM run_process_decisions d JOIN data_observations o ON o.id=d.process_observation_id
        WHERE d.id=$1 AND o.source_id=$2`, [input.decisionId,input.sourceId]);
      if (!saved.rowCount) throw new JobRequestError('decision_not_found',404);
      const decision = saved.rows[0];
      input.configVersionId = decision.config_version_id;
      input.savedEvidence = {decisionId: decision.id, configVersionId: decision.config_version_id,
        status: decision.status, ruleStatus: decision.rule_status, mlStatus: decision.ml_status,
        decisionCode: decision.decision_code, anomalyScore: decision.anomaly_score === null ? null : String(decision.anomaly_score),
        inputManifestHash: decision.input_manifest_hash,
        rules: decision.evidence.rules ?? [], models: decision.evidence.models ?? []};
      input.savedEvidenceJson = JSON.stringify(input.savedEvidence);
      requestHash = crypto.createHash('sha256').update(JSON.stringify(input)).digest('hex');
    }
    const id = crypto.randomUUID();
    const recommendation = ['recommend_feature', 'recommend_rule'].includes(input.kind);
    const explanation = input.kind === 'explain';
    const llmSpec = recommendation || explanation ? {
      evidence_version: explanation ? 'saved-decision-v1' : 'source-period-request-v1',
      input_evidence_hash: explanation ? crypto.createHash('sha256').update(input.savedEvidenceJson!).digest('hex') : requestHash,
      model_version: explanation ? 'qwen-base-6a9e13bd' : input.kind === 'recommend_feature' ? 'feature_v5' : 'rule_v2',
      prompt_version: explanation ? 'grounded-facts-v1' : 'feature-rule-design-v0.2',
      output_schema_version: explanation ? 'fact-ids-checks-v1' : 'safe-process-derived-v2',
      reuse_key: crypto.createHash('sha256').update(JSON.stringify([req.user!.id, input, explanation ? null : idempotencyKey, explanation ? 'grounded-facts-v1' : 'design-v0.2'])).digest('hex'),
    } : null;
    if (llmSpec) {
      await client.query('SELECT pg_advisory_xact_lock(81734,hashtext($1))',[llmSpec.reuse_key]);
      // Check caller idempotency before evidence-based reuse.
      const prior = await client.query(`SELECT id,status,kind,input_spec AS "inputSpec",request_hash
        FROM run_jobs WHERE requested_by_actor_id=$1 AND kind=$2 AND idempotency_key=$3`,
        [req.user!.id,input.kind,idempotencyKey]);
      if (prior.rowCount && prior.rows[0].request_hash !== requestHash) throw new JobRequestError('idempotency_payload_conflict',409);
      const cached = await client.query(`SELECT id,status,kind,input_spec AS "inputSpec" FROM run_jobs
        WHERE requested_by_actor_id=$1 AND llm_spec->>'reuse_key'=$2 AND status IN ('queued','running','succeeded')
        ORDER BY created_at DESC LIMIT 1`,[req.user!.id,llmSpec.reuse_key]);
      if (cached.rowCount) {
        await client.query('COMMIT');
        res.status(200).json(cached.rows[0]);
        return;
      }
    }
    const inserted = await client.query(
      `INSERT INTO run_jobs
       (id, kind, status, requested_by_actor_id, requester_role, idempotency_key,
        request_hash, input_config_version_id, input_snapshot_id, input_spec,
        max_attempts, attempt_count, next_run_at, created_at,
        contract_version,queue,producer_kind,producer_key,llm_spec,input_decision_id)
       VALUES ($1, $2, 'queued', $3, $4, $5, $6, $7, $8, $9::jsonb, 3, 0,
               clock_timestamp(), clock_timestamp(),$10,$11,$12,$13,$14::jsonb,$15)
       ON CONFLICT (requested_by_actor_id, kind, idempotency_key) DO NOTHING
       RETURNING id, status, kind, input_spec AS "inputSpec"`,
      [id, input.kind, req.user!.id, req.user!.userType, idempotencyKey,
        requestHash, input.configVersionId ?? null, input.snapshotId ?? null, JSON.stringify(input),
        'v03', explanation ? 'llm_explanation' : recommendation ? 'llm_recommendation' : 'training',
        'user', req.user!.id,
        llmSpec ? JSON.stringify(llmSpec) : null, input.decisionId ?? null],
    );
    let row = inserted.rows[0];
    if (!row) {
      const existing = await client.query(
        `SELECT id, status, kind, input_spec AS "inputSpec", request_hash
           FROM run_jobs WHERE requested_by_actor_id = $1 AND kind = $2 AND idempotency_key = $3`,
        [req.user!.id, input.kind, idempotencyKey],
      );
      row = existing.rows[0];
      if (!row || row.request_hash !== requestHash) throw new JobRequestError('idempotency_payload_conflict', 409);
    } else {
      await client.query(
        `INSERT INTO ${explanation ? 'powercode_queue.llm_explanation_jobs' : recommendation ? 'powercode_queue.llm_recommendation_jobs' : 'powercode_queue.training_jobs'} (id, payload)
         VALUES ($1, $2::jsonb)`,
        [crypto.randomUUID(), JSON.stringify({ schemaVersion: 1, jobId: id, enqueuedAt: new Date().toISOString() })],
      );
    }
    await client.query('COMMIT');
    res.status(inserted.rowCount ? 201 : 200).json(row);
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}));

router.get('/capabilities', route(async (_req, res) => {
  res.json(TRAINER_CAPABILITIES);
}));

router.get('/feature-sets', route(async (req, res) => {
  if (!uuid(req.query.sourceId)) throw new JobRequestError('invalid_source_id');
  const pool = getDatabasePool();
  const source = await pool.query('SELECT 1 FROM ops_sources WHERE id=$1', [req.query.sourceId]);
  if (!source.rowCount) throw new JobRequestError('source_not_found', 404);
  const result = await pool.query(`SELECT f.id, f.content_hash AS "contentHash",
    COALESCE(jsonb_agg(jsonb_build_object('name',m.output_name,'position',m.output_position,
      'expression',v.expression,'requiresFit',v.requires_fit) ORDER BY m.output_position)
      FILTER (WHERE m.feature_version_id IS NOT NULL),'[]'::jsonb) AS members
    FROM ml_feature_sets f LEFT JOIN ml_feature_set_members m ON m.feature_set_id=f.id AND m.model_input
    LEFT JOIN ml_feature_versions v ON v.id=m.feature_version_id
    GROUP BY f.id ORDER BY f.created_at DESC,f.id LIMIT 201`);
  res.json({ scope: 'global_registered_catalog', sourceId: req.query.sourceId,
    hasMore: result.rows.length > 200,
    items: result.rows.slice(0, 200).map(row => ({ id: row.id, contentHash: row.contentHash,
      featureNames: row.members.map((m: { name: string }) => m.name),
      ...featureSetCapability(row.members) })) });
}));

router.get('/', route(async (req, res) => {
  const result = await getDatabasePool().query(
    `SELECT id, kind, status, input_spec AS "inputSpec", attempt_count AS "attemptCount",
            max_attempts AS "maxAttempts", next_run_at AS "nextRunAt",
            result_summary AS "resultSummary", error_code AS "errorCode",
            created_at AS "createdAt", finished_at AS "finishedAt"
       FROM run_jobs WHERE requested_by_actor_id = $1 OR $2::boolean
      ORDER BY created_at DESC LIMIT 50`,
    [req.user!.id, req.user!.userType === 'admin'],
  );
  res.json({ items: result.rows });
}));

router.get('/:id', route(async (req, res) => {
  if (!uuid(req.params.id)) throw new JobRequestError('invalid_job_id');
  const result = await getDatabasePool().query(
    `SELECT id, kind, status, input_spec AS "inputSpec", attempt_count AS "attemptCount",
            max_attempts AS "maxAttempts", next_run_at AS "nextRunAt",
            result_summary AS "resultSummary", error_code AS "errorCode",
            created_at AS "createdAt", finished_at AS "finishedAt"
       FROM run_jobs WHERE id = $1 AND (requested_by_actor_id = $2 OR $3::boolean)`,
    [req.params.id, req.user!.id, req.user!.userType === 'admin'],
  );
  if (!result.rowCount) throw new JobRequestError('job_not_found', 404);
  res.json(result.rows[0]);
}));

router.post('/:id/recommendations/:index/adopt', route(async (req, res) => {
  if (!uuid(req.params.id) || !/^(0|[1-9]\d?)$/.test(req.params.index)) {
    throw new JobRequestError('invalid_recommendation_reference');
  }
  const result = await getDatabasePool().query(
      `SELECT result_summary, input_spec FROM run_jobs
      WHERE id = $1 AND kind = 'recommend_rule' AND status = 'succeeded'
        AND (requested_by_actor_id = $2 OR $3::boolean)`,
    [req.params.id, req.user!.id, req.user!.userType === 'admin'],
  );
  if (!result.rowCount) throw new JobRequestError('recommendation_not_found', 404);
  const suggestions = result.rows[0].result_summary?.suggestions;
  const selected = Array.isArray(suggestions) ? suggestions[Number(req.params.index)] : undefined;
  if (!selected || typeof selected !== 'object') throw new JobRequestError('recommendation_not_found', 404);
  try {
    const key = parseRuleKey(selected.ruleKey);
    let documents = parseRuleDraft({ displayName: selected.displayName,
      featureSetId: result.rows[0].input_spec.featureSetId,
      condition: selected.condition, outcome: selected.outcome });
    if (selected.evidenceSpec && typeof selected.evidenceSpec === 'object' && !Array.isArray(selected.evidenceSpec)) {
      documents = bindRecommendationEvidence(documents, req.params.id, selected.evidenceSpec);
    }
    const rule = await writeRule(key, req.user!.id, 'create', documents);
    res.status(201).json(rule);
  } catch (error) {
    if (error instanceof InvalidRuleError) throw new JobRequestError('invalid_recommendation', 422);
    if (error instanceof RuleConflictError) throw new JobRequestError(error.code, 409);
    throw error;
  }
}));

router.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
  if (error instanceof JobRequestError) {
    res.status(error.status).json({ code: error.code });
    return;
  }
  res.status(503).json({ code: 'job_storage_unavailable' });
});

export default router;
