import crypto from 'node:crypto';
import { Router, Request, Response, NextFunction } from 'express';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, AuthenticatedRequest } from '../middleware/auth.js';
import { parseRuleDraft, parseRuleKey, InvalidRuleError, bindRecommendationEvidence } from '../rules/ruleContract.js';
import { writeRule, RuleConflictError } from '../rules/ruleRepository.js';
import { readCurrentSoftRules } from '../ruleset/ruleSetStore.js';
import { TRAINER_CAPABILITIES, TRAINING_EXCLUSION_SUPPORTED, featureSetCapability } from './trainerContract.js';
import { mlMethod, parametersValid } from '../ruleset/methodCatalog.js';
import { excludedIntervalsProblem, parseExcludedIntervals } from '../ruleset/thresholdStats.js';
import { parseSyntheticEvaluation, parseSyntheticRegistration, verifySyntheticBinding, staticSpecHash,
  SyntheticRequestError, type SyntheticEvaluation, type SyntheticBinding } from './syntheticEvaluationContract.js';
import { ThresholdRequestError, checkCandidateSnapshot, checkThresholdRevision, fixThresholdTargets, parseThresholdRequest, thresholdLlmSpec,
  type CandidateTarget, type ThresholdFixedInput, type ThresholdRequest } from './thresholdRecommendationContract.js';
import { readRecommendationCandidates } from '../ruleset/ruleCandidates.js';
import { RuleSetError } from '../ruleset/ruleSetContract.js';
import { ComparisonError, enqueueComparison, parseComparisonRequest } from '../services/comparisonEvaluation.js';
import { idempotencyKeyOf } from './comparisonRoutes.js';
import { JobReadError, parseJobListQuery, readJobDetail, readJobPage } from '../services/jobReadModel.js';
import { GENERATION_SERVICE_UNAVAILABLE, LLM_JOB_KINDS, generationAvailability } from '../services/generationAvailability.js';
import { readEvidenceSummary, resolveItem } from '../services/analysisReadModel.js';
import { POLISH_FAILURE_COOLDOWN_MINUTES, POLISH_MAX_ATTEMPTS, polishInputFor, polishLlmSpec, type PolishInput } from '../services/evidencePolish.js';
/** 다듬을 근거 요약이 없는 판정(이상 신호가 없거나 요약·입력을 만들 수 없음)의 설명 요청 거절 코드(409). */
export const EXPLANATION_NOT_APPLICABLE = 'explanation_not_applicable';
import { INVALID_PROTOCOL_EXPLANATION_REQUEST, PROTOCOL_EXPLANATION_KIND, parseProtocolExplanationRequest, protocolExplanationDecisionId,
  protocolExplanationLlmSpec, protocolExplanationProblem, type ProtocolExplanationInput, type ProtocolExplanationTarget } from './protocolExplanationContract.js';

const router = Router();
router.use(authenticateToken as any);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UTC_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const KINDS = new Set(['recommend_thresholds', 'train', 'evaluate', 'explain', PROTOCOL_EXPLANATION_KIND]);
/** 관리자 전용 작업 종류. 본문 해석 전에 역할을 먼저 본다(explain_protocol: protocol-explanation-job.md 12절 Q-P1). */
const ADMIN_KINDS = ['recommend_thresholds', 'train', 'evaluate', PROTOCOL_EXPLANATION_KIND];
/**
 * 폐지된 작업 종류(llm-recommendation-job.md 폐지 표 — recommend_feature 10/21, recommend_rule 10/21·11/02). 신규 요청은 역할과 무관하게
 * 422 `job_kind_abolished`(작업·큐 메시지를 남기지 않음). 이미 끝난 작업의 읽기(GET /api/jobs, recommend_rule 결과 채택)는 그대로 둔다.
 */
export const ABOLISHED_KINDS = new Set(['recommend_feature', 'recommend_rule']);

export class JobRequestError extends Error {
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
  featureSetId?: string; configVersionId?: string | null; snapshotId?: string;
  purpose?: string; ruleVersionIds?: string[]; decisionId?: string;
  /** explain(다듬기) 접수 입력(evidencePolish.ts PolishInput). */
  templateVersion?: string; sentences?: string[]; stateWords?: string[]; valueTokens?: string[];
  detectionPolicy?: { mode: string; deadlineMs: number; combination: string };
  modelKey?: string;
  parameters?: Record<string, unknown>;
  timeBasis?: string;
  excludedIntervals?: { from: string; until: string; reasonCode: string; note: string | null }[];
  syntheticEvaluation?: SyntheticEvaluation;
  syntheticProfileBinding?: SyntheticBinding;
  target?: ProtocolExplanationTarget;
} & Partial<Omit<ThresholdRequest, 'kind' | 'sourceId'> & ThresholdFixedInput>;

/** 학습 모델·파라미터 접수 검증(trainer-service.md). 워커 `resolve_training_method`와 같은 카탈로그·같은 코드이고, 키 생략은 기본 IF다. */
function checkTrainingMethod(modelKey: unknown, parameters: unknown): void {
  const method = mlMethod(modelKey === undefined ? 'iforest' : modelKey);
  if (!method) throw new JobRequestError('unsupported_model', 422);
  if (!method.executable.process) throw new JobRequestError(`method_not_executable:${method.methodId}`, 422);
  if (!parametersValid(method, parameters)) throw new JobRequestError(`method_parameters_invalid:${method.methodId}`, 422);
}

/**
 * 이 키가 하나라도 있는 기간 지정 train은 모델 버전(v3) 학습이다(model-version.md 생성 경로 (A), 워커 `REQUEST_KEYS`와 같은 판별).
 * `evaluationSetId`는 계약의 v3 키지만 API가 아직 받지 않는다(허용 키 밖이라 400 `invalid_job_request`).
 */
const MODEL_VERSION_TRAIN_KEYS = ['modelKey', 'parameters', 'excludedIntervals', 'timeBasis'] as const;

type TrainingSupport = { timeBasis: string; excludedIntervals: boolean };
const TRAINING_SUPPORT: TrainingSupport = { timeBasis: TRAINER_CAPABILITIES.snapshot.timeBasis, excludedIntervals: TRAINING_EXCLUSION_SUPPORTED };

/**
 * 학습 기간 기준·제외 구간. 형식은 계약대로 400이고, Trainer가 적용하지 못하는 값은 무시하지 않고 422로 거절한다
 * (model-version.md `config_schema_not_supported_yet`). 접수한 값은 작업 입력에 그대로 싣는다. 시각은 UTC, `reasonCode` 생략은 `other`(trainer-service.md).
 * `persisted_at`은 키 생략과 같으므로 남기지 않는다.
 */
function trainingScope(body: Record<string, unknown>, period: { from: string; until: string }, support: TrainingSupport): Pick<JobInput, 'timeBasis' | 'excludedIntervals'> {
  const { timeBasis, excludedIntervals } = body;
  if (timeBasis !== undefined && timeBasis !== 'persisted_at' && timeBasis !== 'esb_occurred_at') throw new JobRequestError('invalid_job_request');
  const intervals = excludedIntervals === undefined ? undefined : parseExcludedIntervals(excludedIntervals, { nullNote: true });
  if (intervals === null) throw new JobRequestError('invalid_job_request');
  const problem = intervals && excludedIntervalsProblem(intervals, period);
  if (problem) throw new JobRequestError(problem);
  if ((timeBasis === 'esb_occurred_at' && support.timeBasis !== timeBasis) || (intervals && !support.excludedIntervals)) {
    throw new JobRequestError('config_schema_not_supported_yet', 422);
  }
  const items = excludedIntervals as { reasonCode?: string; note?: string | null }[];
  return {
    ...(timeBasis !== undefined && timeBasis !== 'persisted_at' ? { timeBasis } : {}),
    ...(intervals ? { excludedIntervals: intervals.map((item, index) => ({ ...item, reasonCode: items[index].reasonCode ?? 'other', note: items[index].note ?? null })) } : {}),
  };
}

export function parseRequest(value: unknown, support: TrainingSupport = TRAINING_SUPPORT): JobInput {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new JobRequestError('invalid_job_request');
  const body = value as Record<string, unknown>;
  if (typeof body.kind === 'string' && ABOLISHED_KINDS.has(body.kind)) throw new JobRequestError('job_kind_abolished', 422);
  // explain_protocol은 sourceId를 포함한 모든 형식 위반이 자기 코드다(protocol-explanation-job.md 1절).
  if (body.kind === PROTOCOL_EXPLANATION_KIND) {
    const parsed = parseProtocolExplanationRequest(body);
    if (!parsed) throw new JobRequestError(INVALID_PROTOCOL_EXPLANATION_REQUEST);
    return parsed;
  }
  if (typeof body.kind !== 'string' || !KINDS.has(body.kind) || !uuid(body.sourceId)) {
    throw new JobRequestError('invalid_job_request');
  }
  if (body.kind === 'recommend_thresholds') return parseThresholdRequest(body, body.sourceId);
  const permitted = ['kind', 'sourceId', 'from', 'until', 'featureSetId', 'configVersionId', 'snapshotId', 'purpose', 'ruleVersionIds', 'decisionId', 'detectionPolicy', 'modelKey', 'parameters', 'timeBasis', 'excludedIntervals', 'syntheticEvaluation'];
  if (Object.keys(body).some((key) => !permitted.includes(key))) throw new JobRequestError('invalid_job_request');
  const input: JobInput = { kind: body.kind, sourceId: body.sourceId };
  if (body.kind === 'train' && uuid(body.snapshotId) && body.from === undefined && body.until === undefined) {
    if (Object.keys(body).some(key => !['kind','sourceId','snapshotId','detectionPolicy','modelKey'].includes(key))) throw new JobRequestError('invalid_prepared_training_request');
    const policy = body.detectionPolicy as JobInput['detectionPolicy'];
    if (!policy || typeof policy !== 'object' || Array.isArray(policy) ||
        Object.keys(policy).some(key => !['mode','deadlineMs','combination'].includes(key)) || policy.mode !== 'ml_only' ||
        policy.combination !== 'selected_path' || !Number.isInteger(policy.deadlineMs) || policy.deadlineMs < 100 || policy.deadlineMs > 30000) throw new JobRequestError('prepared_ml_only_required',422);
    checkTrainingMethod(body.modelKey, undefined);
    // prepared 경로는 IF 고정이다(prepared_training.py).
    if (body.modelKey !== undefined && body.modelKey !== 'iforest') throw new JobRequestError('unsupported_model',422);
    input.snapshotId = body.snapshotId;
    input.detectionPolicy = policy;
    if (body.modelKey !== undefined) input.modelKey = 'iforest';
    return input;
  }
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
    if (body.syntheticEvaluation !== undefined) input.syntheticEvaluation = parseSyntheticEvaluation(body.syntheticEvaluation);
    if (body.from !== undefined || body.until !== undefined || body.featureSetId !== undefined || body.ruleVersionIds !== undefined) {
      throw new JobRequestError('invalid_evaluation_request');
    }
  } else {
    // 여기는 기간을 지정한 train만 온다(recommend_thresholds·prepared train·explain·evaluate는 위에서 처리, 폐지 종류는 422).
    if (typeof body.from !== 'string' || typeof body.until !== 'string' ||
        !UTC_TIME.test(body.from) || !UTC_TIME.test(body.until) ||
        !Number.isFinite(Date.parse(body.from)) || !Number.isFinite(Date.parse(body.until)) ||
        Date.parse(body.from) >= Date.parse(body.until) ||
        Date.parse(body.until) - Date.parse(body.from) > 366 * 86400000) {
      throw new JobRequestError('invalid_time_range');
    }
    input.from = body.from;
    input.until = body.until;
    if (!uuid(body.featureSetId)) throw new JobRequestError('feature_set_required');
    input.featureSetId = body.featureSetId;
    if (body.kind === 'train' && MODEL_VERSION_TRAIN_KEYS.some((key) => body[key] !== undefined)) {
      // 모델 버전(v3) 학습(model-version.md 생성 경로 (A)): 종합 판정 정책은 운영 설정 전역 값이고 룰은 룰 세트가 판정 시점에 적용한다.
      if (body.detectionPolicy !== undefined) throw new JobRequestError('invalid_detection_policy');
      if (body.ruleVersionIds !== undefined) throw new JobRequestError('invalid_job_request');
      checkTrainingMethod(body.modelKey, body.parameters);
      Object.assign(input, trainingScope(body, { from: body.from, until: body.until }, support));
      // 워커는 input_spec에 v3 키가 있어야 v3로 판별한다(`trainer/model_version.py` is_model_version_request).
      // `timeBasis=persisted_at`·`parameters: null`은 남기지 않으므로 modelKey를 항상 싣는다(생략은 기본 IF).
      input.modelKey = (body.modelKey ?? 'iforest') as string;
      if (body.parameters !== undefined && body.parameters !== null) input.parameters = body.parameters as Record<string, unknown>;
    } else if (body.kind === 'train') {
      // v1 형식(v3 키 없음): v1·v2 구성 생성 경로. 정책은 지금처럼 필수다.
      const policy = body.detectionPolicy as JobInput['detectionPolicy'];
      if (!policy || typeof policy !== 'object' || Array.isArray(policy) ||
          Object.keys(policy).some(key => !['mode','deadlineMs','combination'].includes(key)) ||
          !['rule_only','ml_only','parallel'].includes(policy.mode) ||
          !Number.isInteger(policy.deadlineMs) || policy.deadlineMs < 100 || policy.deadlineMs > 30000 ||
          policy.combination !== (policy.mode === 'parallel' ? 'any_anomaly' : 'selected_path')) {
        throw new JobRequestError('detection_policy_required');
      }
      input.detectionPolicy = policy;
      // 키 생략은 기본 IF다. 워커와 같은 카탈로그로 실행 가능 여부를 본다.
      checkTrainingMethod(undefined, undefined);
      const rules = body.ruleVersionIds ?? [];
      if (!Array.isArray(rules) || rules.length > 32 || !rules.every(uuid) || new Set(rules).size !== rules.length) {
        throw new JobRequestError('invalid_rule_versions');
      }
      if (policy.mode === 'ml_only' && rules.length) throw new JobRequestError('ml_only_rules_not_selected');
      input.ruleVersionIds = rules;
    }
    if (body.snapshotId !== undefined) {
      if (!uuid(body.snapshotId)) throw new JobRequestError('invalid_snapshot_reference');
      input.snapshotId = body.snapshotId;
    }
    if (body.configVersionId !== undefined || body.purpose !== undefined) {
      throw new JobRequestError('invalid_job_request');
    }
  }
  if (body.kind !== 'train' && body.detectionPolicy !== undefined) throw new JobRequestError('invalid_job_request');
  if (body.kind !== 'train' && ['modelKey', 'parameters', 'timeBasis', 'excludedIntervals'].some((key) => body[key] !== undefined)) throw new JobRequestError('invalid_job_request');
  if (body.kind !== 'explain' && body.decisionId !== undefined) throw new JobRequestError('invalid_job_request');
  if (body.kind !== 'evaluate' && body.syntheticEvaluation !== undefined) throw new JobRequestError('invalid_job_request');
  return input;
}

router.post('/', route(async (req, res) => {
  // 비교 평가(config-comparison-evaluation.md v1): 검증·기준선 고정이 다른 작업과 달라 따로 처리한다. 응답 모양은 다른 작업과 같다.
  if (req.body?.kind === 'compare') {
    if (req.user!.userType !== 'admin') throw new JobRequestError('admin_required', 403);
    const key = idempotencyKeyOf(req);
    const { created, job } = await enqueueComparison(getDatabasePool(), parseComparisonRequest(req.body), req.user!.id, key);
    res.status(created ? 201 : 200).json(job);
    return;
  }
  if (req.user!.userType !== 'admin' && ADMIN_KINDS.includes(req.body?.kind)) throw new JobRequestError('admin_required',403);
  const input = parseRequest(req.body);
  if (ADMIN_KINDS.includes(input.kind) && req.user!.userType !== 'admin') {
    throw new JobRequestError('admin_required', 403);
  }
  const header = req.headers['idempotency-key'];
  if (header !== undefined && (typeof header !== 'string' || !/^[a-zA-Z0-9_.:-]{1,128}$/.test(header))) {
    throw new JobRequestError('invalid_idempotency_key');
  }
  // LLM 생성 서비스가 꺼져 있으면 LLM 작업은 접수하지 않는다(llm-recommendation-job.md '생성 서비스 가용성' 절).
  // 트랜잭션·멱등 처리보다 먼저 확인해 작업 행·큐 메시지·멱등키를 남기지 않는다(같은 키로 다시 요청할 수 있음).
  const availability = LLM_JOB_KINDS.has(input.kind) ? await generationAvailability().check() : null;
  if (availability && !availability.available) throw new JobRequestError(GENERATION_SERVICE_UNAVAILABLE, 503);
  // 설명(다듬기) 작업은 생성 서비스가 알린 다듬기 모델 id를 계보(model_version)에 넣는다. 모르면 접수하지 않는다(같은 503).
  if (input.kind === 'explain' && !availability?.polishModel) throw new JobRequestError(GENERATION_SERVICE_UNAVAILABLE, 503);
  const idempotencyKey = header ?? crypto.randomUUID();
  let requestHash = crypto.createHash('sha256').update(JSON.stringify(input)).digest('hex');
  const client = await getDatabasePool().connect();
  try {
    await client.query('BEGIN');
    const source = await client.query('SELECT schema_version,field_catalog FROM ops_sources WHERE id = $1', [input.sourceId]);
    if (!source.rowCount) throw new JobRequestError('source_not_found', 404);
    if (input.syntheticEvaluation) {
      input.syntheticProfileBinding = verifySyntheticBinding(input.syntheticEvaluation, source.rows[0].schema_version, source.rows[0].field_catalog);
      requestHash = crypto.createHash('sha256').update(JSON.stringify(input)).digest('hex');
    }
    if (input.featureSetId) {
      const set = await client.query('SELECT 1 FROM ml_feature_sets WHERE id = $1', [input.featureSetId]);
      if (!set.rowCount) throw new JobRequestError('feature_set_not_found', 404);
    }
    const preparedTrain = input.kind === 'train' && input.from === undefined;
    if (preparedTrain) {
      const dataset = await client.query(`SELECT 1 FROM data_prepared_datasets d JOIN data_snapshots s ON s.id=d.snapshot_id
        WHERE d.snapshot_id=$1 AND d.source_id=$2 AND d.status='ready' AND s.status='ready' AND s.schema_version='prepared-features-v1'`,
        [input.snapshotId,input.sourceId]);
      if (!dataset.rowCount) throw new JobRequestError('prepared_dataset_not_ready',409);
    }
    if (input.snapshotId && input.kind !== 'evaluate' && !preparedTrain) {
      const snapshot = await client.query(`SELECT 1 FROM data_snapshots WHERE id=$1 AND source_id=$2
        AND status='ready' AND (selection_spec->>'from')::timestamptz=$3::timestamptz
        AND (selection_spec->>'until')::timestamptz=$4::timestamptz`,
        [input.snapshotId,input.sourceId,input.from,input.until]);
      if (!snapshot.rowCount) throw new JobRequestError('snapshot_conditions_mismatch',409);
    }
    if (input.kind === 'train' && !preparedTrain) {
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
        `SELECT s.schema_version FROM ml_config_versions AS c JOIN data_snapshots AS s ON s.id = $2
          WHERE c.id = $1 AND c.source_id = $3 AND s.source_id = $3 AND s.status = 'ready'`,
        [input.configVersionId, input.snapshotId, input.sourceId],
      );
      if (!refs.rowCount) throw new JobRequestError('evaluation_inputs_not_ready', 409);
      if (input.syntheticEvaluation && refs.rows[0].schema_version !== 'process-v1') {
        throw new JobRequestError('synthetic_snapshot_schema_unsupported', 422);
      }
    }
    if (input.kind === 'explain') {
      // AI 판정 근거 요약 다듬기(접수 입력 summary-sentences-v1): 판정 상세와 같은 조립 함수로 코드 문장을 다시 만들어 보낸다.
      // 최종 판정이고 그 원천의 판정이어야 하며(404), 이상 신호가 있고 요약·입력을 만들 수 있어야 접수한다(409).
      // id 비교는 대소문자 무시(uuid 열 비교와 같음). 작업 입력에는 저장된(소문자) id를 쓴다.
      const item = await resolveItem(client, input.decisionId!);
      if (!item || item.kind !== 'final' || String(item.decisionId).toLowerCase() !== input.decisionId!.toLowerCase()
        || String(item.sourceId).toLowerCase() !== input.sourceId.toLowerCase()) throw new JobRequestError('decision_not_found',404);
      const polish = polishInputFor(item, item.signal === 'yes' ? await readEvidenceSummary(client, item) : null);
      if (!polish) throw new JobRequestError(EXPLANATION_NOT_APPLICABLE,409);
      Object.assign(input, polish);
      requestHash = crypto.createHash('sha256').update(JSON.stringify(input)).digest('hex');
    }
    const protocolExplanation = input.kind === PROTOCOL_EXPLANATION_KIND ? input as ProtocolExplanationInput : null;
    if (protocolExplanation) {
      // 접수 때 대상·사실을 확인한다(protocol-explanation-job.md 10절, 12절 Q-P6). 거절하면 작업·큐 메시지·멱등키를 남기지 않는다.
      const problem = await protocolExplanationProblem(client, protocolExplanation);
      if (problem) throw new JobRequestError(problem);
    }
    const thresholds = input.kind === 'recommend_thresholds';
    if (thresholds) {
      const request = input as ThresholdRequest;
      const { snapshotId, from, until } = request.data;
      if (snapshotId) {
        const snapshot = await client.query(`SELECT 1 FROM data_snapshots WHERE id=$1 AND source_id=$2 AND status='ready' AND schema_version='process-v1'
          AND ($3::timestamptz IS NULL OR (selection_spec->>'from')::timestamptz=$3::timestamptz)
          AND ($4::timestamptz IS NULL OR (selection_spec->>'until')::timestamptz=$4::timestamptz)`,
          [snapshotId, input.sourceId, from, until]);
        if (!snapshot.rowCount) throw new JobRequestError('snapshot_conditions_mismatch', 409);
      }
      const current = await readCurrentSoftRules(client);
      checkThresholdRevision(request, current);
      // (10차) 처리 시간 후보: 한 작업·자격·같은 snapshot을 접수에서 확인한다(llm-recommendation-job.md 10차 요청 표). 값은 워커가 다시 계산한다.
      let candidates: CandidateTarget[] = [];
      if (request.scope.candidateIds) {
        try {
          const found = await readRecommendationCandidates(client, request.scope.candidateIds);
          checkCandidateSnapshot(request, found.snapshotId);
          candidates = found.items;
        } catch (error) {
          if (error instanceof RuleSetError) throw new ThresholdRequestError(error.code, error.status);
          throw error;
        }
      }
      Object.assign(input, fixThresholdTargets(request, current, candidates));
      requestHash = crypto.createHash('sha256').update(JSON.stringify(input)).digest('hex');
    }
    const id = crypto.randomUUID();
    const explanation = input.kind === 'explain';
    // 설명 큐(llm_explanation): explain·explain_protocol(protocol-explanation-job.md 8절).
    const explanationQueue = explanation || protocolExplanation !== null;
    // 설명(다듬기)의 reuse_key는 요청자 없이 판정마다 공유한다(evidencePolish.ts). 이전 형식 작업 행은 그대로 둔다(키가 달라 섞이지 않음).
    const llmSpec = thresholds ? thresholdLlmSpec(input as ThresholdRequest & ThresholdFixedInput, requestHash, req.user!.id, idempotencyKey)
      : protocolExplanation ? protocolExplanationLlmSpec(protocolExplanation, requestHash, req.user!.id)
        : explanation ? polishLlmSpec(input as unknown as PolishInput, availability!.polishModel!) : null;
    if (llmSpec) {
      await client.query('SELECT pg_advisory_xact_lock(81734,hashtext($1))',[llmSpec.reuse_key]);
      // Check caller idempotency before evidence-based reuse.
      const prior = await client.query(`SELECT id,status,kind,input_spec AS "inputSpec",request_hash
        FROM run_jobs WHERE requested_by_actor_id=$1 AND kind=$2 AND idempotency_key=$3`,
        [req.user!.id,input.kind,idempotencyKey]);
      if (prior.rowCount && prior.rows[0].request_hash !== requestHash) throw new JobRequestError('idempotency_payload_conflict',409);
      // 설명: 요청자와 무관하게 같은 키의 진행 중·완료 작업, 없으면 쿨다운 안의 실패 작업(새 작업 없음). 그 밖 종류는 요청자별.
      // 설명 큐(contract_version v03 + queue llm_explanation)로 좁혀 run_jobs_v03_queue_idx(queue,status,…) WHERE contract_version='v03'을 탄다.
      const cached = explanation ? await client.query(`SELECT id,status,kind,input_spec AS "inputSpec" FROM run_jobs
        WHERE contract_version='v03' AND queue='llm_explanation' AND status IN ('queued','running','succeeded','failed')
          AND kind='explain' AND llm_spec->>'reuse_key'=$1
          AND (status<>'failed' OR finished_at > clock_timestamp() - $2::integer * interval '1 minute')
        ORDER BY (status='failed'), created_at DESC LIMIT 1`,[llmSpec.reuse_key, POLISH_FAILURE_COOLDOWN_MINUTES])
        : await client.query(`SELECT id,status,kind,input_spec AS "inputSpec" FROM run_jobs
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
       VALUES ($1, $2, 'queued', $3, $4, $5, $6, $7, $8, $9::jsonb, $16, 0,
               clock_timestamp(), clock_timestamp(),$10,$11,$12,$13,$14::jsonb,$15)
       ON CONFLICT (requested_by_actor_id, kind, idempotency_key) DO NOTHING
       RETURNING id, status, kind, input_spec AS "inputSpec"`,
      [id, input.kind, req.user!.id, req.user!.userType, idempotencyKey,
        requestHash, input.configVersionId ?? null, input.snapshotId ?? null, JSON.stringify(input),
        'v03', explanationQueue ? 'llm_explanation' : thresholds ? 'llm_recommendation' : 'training',
        'user', req.user!.id,
        llmSpec ? JSON.stringify(llmSpec) : null,
        protocolExplanation ? protocolExplanationDecisionId(protocolExplanation) : input.decisionId ?? null,
        explanation ? POLISH_MAX_ATTEMPTS : 3],
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
        `INSERT INTO ${explanationQueue ? 'powercode_queue.llm_explanation_jobs' : thresholds ? 'powercode_queue.llm_recommendation_jobs' : 'powercode_queue.training_jobs'} (id, payload)
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

router.put('/sources/:sourceId/synthetic-profiles', route(async (req, res) => {
  if (req.user!.userType !== 'admin') throw new JobRequestError('admin_required', 403);
  if (!uuid(req.params.sourceId)) throw new JobRequestError('invalid_source_id');
  const registration = parseSyntheticRegistration(req.body);
  const client = await getDatabasePool().connect();
  try {
    await client.query('BEGIN');
    const source = await client.query('SELECT schema_version,field_catalog FROM ops_sources WHERE id=$1 FOR UPDATE', [req.params.sourceId]);
    if (!source.rowCount) throw new JobRequestError('source_not_found', 404);
    if (source.rows[0].schema_version !== registration.binding.sourceSchemaVersion) {
      throw new JobRequestError('synthetic_source_schema_mismatch', 409);
    }
    const catalog = source.rows[0].field_catalog;
    if (!catalog || typeof catalog !== 'object' || Array.isArray(catalog) ||
        (catalog.syntheticProfiles !== undefined && (!catalog.syntheticProfiles || typeof catalog.syntheticProfiles !== 'object' || Array.isArray(catalog.syntheticProfiles)))) {
      throw new JobRequestError('synthetic_invalid_source_catalog', 409);
    }
    const bindings = catalog.syntheticProfiles ?? {};
    const existing = bindings[registration.profileId];
    if (existing !== undefined && staticSpecHash(existing) !== staticSpecHash(registration.binding)) {
      throw new JobRequestError('synthetic_profile_binding_conflict', 409);
    }
    if (existing === undefined) await client.query('UPDATE ops_sources SET field_catalog=$2::jsonb WHERE id=$1',
      [req.params.sourceId, JSON.stringify({ ...catalog, syntheticProfiles: { ...bindings, [registration.profileId]: registration.binding } })]);
    await client.query('COMMIT');
    res.status(existing === undefined ? 201 : 200).json({ sourceId: req.params.sourceId, ...registration });
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally { client.release(); }
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

/** 작업 목록·상세(operations-ui.md '작업 목록·작업 상세'). 읽기 규칙은 services/jobReadModel.ts. */
const reader = (req: AuthenticatedRequest) => ({ id: req.user!.id, admin: req.user!.userType === 'admin' });
const readError = (error: unknown) => (error instanceof JobReadError ? new JobRequestError(error.code, error.status) : error);

router.get('/', route(async (req, res) => {
  let filter;
  try { filter = parseJobListQuery(req.query as Record<string, unknown>); } catch (error) { throw readError(error); }
  res.json(await readJobPage(getDatabasePool(), reader(req), filter).catch((error: unknown) => { throw readError(error); }));
}));

router.get('/:id', route(async (req, res) => {
  if (!uuid(req.params.id)) throw new JobRequestError('invalid_job_id');
  const job = await readJobDetail(getDatabasePool(), reader(req), req.params.id);
  if (!job) throw new JobRequestError('job_not_found', 404);
  res.json(job);
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
  if (error instanceof JobRequestError || error instanceof SyntheticRequestError || error instanceof ThresholdRequestError || error instanceof ComparisonError) {
    res.status(error.status).json({ code: error.code });
    return;
  }
  res.status(503).json({ code: 'job_storage_unavailable' });
});

export default router;
