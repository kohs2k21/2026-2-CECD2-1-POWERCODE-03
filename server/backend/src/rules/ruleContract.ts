import crypto from 'node:crypto';

type JsonObject = Record<string, unknown>;
type RuleCondition = { all: RuleCondition[] } | { any: RuleCondition[] } | {
  field: string;
  operator: string;
  value: string | string[];
};

const NUMERIC_FIELDS = new Set([
  'P.success_count', 'P.error_count', 'P.total_count', 'P.retry_count',
]);
const TEXT_FIELDS = new Set([
  'P.status', 'P.response_code', 'P.adapter_type', 'P.channel_id',
  'P.process_hub_id', 'P.process_id',
]);
const DECIMAL = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;
const FEATURE_FIELD = /^F\.[a-z][a-z0-9_]{1,63}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const RULE_KEY = /^[a-z][a-z0-9_]{2,63}$/;
const DECISION_CODE = /^[A-Z][A-Z0-9_]{2,63}$/;

export class InvalidRuleError extends Error {
  constructor(public readonly code: string) {
    super(code);
  }
}

function object(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function onlyKeys(value: JsonObject, keys: readonly string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key));
}

function nonEmptyText(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.trim() === value && value.length > 0 && value.length <= max;
}

function parseCondition(value: unknown, depth: number, fields: Set<string>, nodes: { count: number }): RuleCondition {
  if (!object(value) || depth > 3 || ++nodes.count > 32) {
    throw new InvalidRuleError('invalid_condition');
  }
  const keys = Object.keys(value);
  if (keys.length === 1 && (keys[0] === 'all' || keys[0] === 'any')) {
    const operator = keys[0] as 'all' | 'any';
    const children = value[operator];
    if (!Array.isArray(children) || children.length < 1 || children.length > 8) {
      throw new InvalidRuleError('invalid_condition');
    }
    const parsed = children.map((child) => parseCondition(child, depth + 1, fields, nodes));
    return operator === 'all' ? { all: parsed } : { any: parsed };
  }
  if (keys.length !== 3 || !onlyKeys(value, ['field', 'operator', 'value'])) {
    throw new InvalidRuleError('invalid_condition');
  }
  const { field, operator, value: operand } = value;
  if (typeof field !== 'string' || typeof operator !== 'string') {
    throw new InvalidRuleError('invalid_condition');
  }
  if (NUMERIC_FIELDS.has(field) || FEATURE_FIELD.test(field)) {
    if (!['eq', 'ne', 'gt', 'gte', 'lt', 'lte'].includes(operator) ||
        typeof operand !== 'string' || operand.length > 40 || !DECIMAL.test(operand)) {
      throw new InvalidRuleError('invalid_condition');
    }
    fields.add(field);
    return { field, operator, value: operand };
  }
  if (TEXT_FIELDS.has(field)) {
    if (operator === 'in') {
      if (!Array.isArray(operand) || operand.length < 1 || operand.length > 20 ||
          !operand.every((item) => nonEmptyText(item, 128))) {
        throw new InvalidRuleError('invalid_condition');
      }
      fields.add(field);
      return { field, operator, value: [...new Set(operand)] };
    }
    if (!['eq', 'ne'].includes(operator) || !nonEmptyText(operand, 128)) {
      throw new InvalidRuleError('invalid_condition');
    }
    fields.add(field);
    return { field, operator, value: operand };
  }
  throw new InvalidRuleError('unsupported_rule_field');
}

export interface RuleDocuments {
  ruleSpec: JsonObject;
  inputSpec: JsonObject;
  evidenceSpec: JsonObject;
  contentHash: string;
}

export function parseRuleKey(value: unknown): string {
  if (typeof value !== 'string' || !RULE_KEY.test(value)) {
    throw new InvalidRuleError('invalid_rule_key');
  }
  return value;
}

export function parseRuleDraft(value: unknown): RuleDocuments {
  if (!object(value) || !onlyKeys(value, ['displayName', 'description', 'condition', 'outcome', 'featureSetId'])) {
    throw new InvalidRuleError('invalid_rule_payload');
  }
  const { displayName, description, outcome } = value;
  if (!nonEmptyText(displayName, 120) ||
      (description !== undefined && (typeof description !== 'string' || description.length > 1000)) ||
      !object(outcome) || !onlyKeys(outcome, ['decisionCode', 'severity']) ||
      typeof outcome.decisionCode !== 'string' || !DECISION_CODE.test(outcome.decisionCode) ||
      typeof outcome.severity !== 'string' ||
      !['info', 'warning', 'critical'].includes(outcome.severity)) {
    throw new InvalidRuleError('invalid_rule_payload');
  }
  const fields = new Set<string>();
  const condition = parseCondition(value.condition, 0, fields, { count: 0 });
  const featureSetId = value.featureSetId;
  if ((featureSetId !== undefined && (typeof featureSetId !== 'string' || !UUID.test(featureSetId))) ||
      ([...fields].some((field) => field.startsWith('F.')) && featureSetId === undefined)) {
    throw new InvalidRuleError('feature_set_required');
  }
  const orderedFields = [...fields].sort();
  const ruleSpec: JsonObject = {
    schemaVersion: 1,
    state: 'draft',
    displayName,
    description: description ?? '',
    condition,
    outcome: { decisionCode: outcome.decisionCode, severity: outcome.severity },
  };
  const inputSpec: JsonObject = {
    schemaVersion: 1,
    layer: 'P',
    fields: orderedFields,
    missingPolicy: 'unavailable',
    ...(featureSetId ? { featureSetId } : {}),
  };
  const evidenceSpec: JsonObject = {
    schemaVersion: 1,
    fields: orderedFields,
    includeRawMessages: false,
  };
  return makeDocuments(ruleSpec, inputSpec, evidenceSpec);
}

function makeDocuments(ruleSpec: JsonObject, inputSpec: JsonObject, evidenceSpec: JsonObject): RuleDocuments {
  const contentHash = crypto.createHash('sha256')
    .update(JSON.stringify({ ruleSpec, inputSpec, evidenceSpec }))
    .digest('hex');
  return { ruleSpec, inputSpec, evidenceSpec, contentHash };
}

export function bindRecommendationEvidence(documents: RuleDocuments, jobId: string, evidence: JsonObject): RuleDocuments {
  return makeDocuments(documents.ruleSpec, documents.inputSpec,
    { ...documents.evidenceSpec, recommendationJobId: jobId, research: evidence });
}

export function retireRule(documents: RuleDocuments): RuleDocuments {
  return makeDocuments({ ...documents.ruleSpec, state: 'retired' },
    documents.inputSpec, documents.evidenceSpec);
}

export function parseExpectedVersion(value: unknown): number {
  if (typeof value !== 'string' || !/^"[1-9]\d*"$/.test(value)) {
    throw new InvalidRuleError('if_match_required');
  }
  const version = Number(value.slice(1, -1));
  if (!Number.isSafeInteger(version)) {
    throw new InvalidRuleError('if_match_required');
  }
  return version;
}
