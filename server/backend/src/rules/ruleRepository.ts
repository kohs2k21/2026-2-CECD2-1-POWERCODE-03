import crypto from 'node:crypto';
import { PoolClient } from 'pg';
import { getDatabasePool } from '../config/database.js';
import { RuleDocuments, retireRule } from './ruleContract.js';

export class RuleConflictError extends Error {
  constructor(public readonly code: string) { super(code); }
}

export interface RuleVersion {
  id: string;
  ruleKey: string;
  version: number;
  ruleSpec: Record<string, unknown>;
  inputSpec: Record<string, unknown>;
  evidenceSpec: Record<string, unknown>;
  contentHash: string;
  createdByActorId: string;
  createdAt: Date;
}

const SELECT_COLUMNS = 'id, rule_key AS "ruleKey", version, rule_spec AS "ruleSpec", input_spec AS "inputSpec", evidence_spec AS "evidenceSpec", content_hash AS "contentHash", created_by_actor_id AS "createdByActorId", created_at AS "createdAt"';

async function latest(client: PoolClient, ruleKey: string): Promise<RuleVersion | null> {
  const result = await client.query<RuleVersion>(
    `SELECT ${SELECT_COLUMNS} FROM ml_rule_versions WHERE rule_key = $1 ORDER BY version DESC LIMIT 1`,
    [ruleKey],
  );
  return result.rows[0] ?? null;
}

async function insert(client: PoolClient, ruleKey: string, version: number, documents: RuleDocuments, actorId: string): Promise<RuleVersion> {
  const featureSetId = documents.inputSpec.featureSetId;
  const featureNames = (documents.inputSpec.fields as string[])
    .filter((field) => field.startsWith('F.')).map((field) => field.slice(2));
  if (featureSetId) {
    // 파생변수 참조는 고정된 변수셋에 실제 포함된 출력 이름만 허용한다.
    const available = await client.query<{ output_name: string }>(
      `SELECT output_name FROM ml_feature_set_members
        WHERE feature_set_id = $1 AND model_input`, [featureSetId],
    );
    const names = new Set(available.rows.map((row) => row.output_name));
    if (!available.rowCount || featureNames.some((name) => !names.has(name))) {
      throw new RuleConflictError('feature_set_mismatch');
    }
  }
  const result = await client.query<RuleVersion>(
    `INSERT INTO ml_rule_versions
     (id, rule_key, version, rule_spec, input_spec, evidence_spec, content_hash, created_by_actor_id, created_at)
     VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7, $8, now())
     RETURNING ${SELECT_COLUMNS}`,
    [crypto.randomUUID(), ruleKey, version, JSON.stringify(documents.ruleSpec),
      JSON.stringify(documents.inputSpec), JSON.stringify(documents.evidenceSpec), documents.contentHash, actorId],
  );
  return result.rows[0];
}

export async function listRules(limit: number, offset: number, includeRetired: boolean): Promise<RuleVersion[]> {
  const result = await getDatabasePool().query<RuleVersion>(
    `SELECT * FROM (SELECT DISTINCT ON (rule_key) ${SELECT_COLUMNS}
       FROM ml_rule_versions ORDER BY rule_key, version DESC) current_rules
     WHERE $1::boolean OR COALESCE(current_rules."ruleSpec"->>'state', 'draft') <> 'retired'
     ORDER BY "ruleKey" LIMIT $2 OFFSET $3`,
    [includeRetired, limit, offset],
  );
  return result.rows;
}

export async function getRule(ruleKey: string): Promise<RuleVersion | null> {
  const result = await getDatabasePool().query<RuleVersion>(
    `SELECT ${SELECT_COLUMNS} FROM ml_rule_versions WHERE rule_key = $1 ORDER BY version DESC LIMIT 1`,
    [ruleKey],
  );
  return result.rows[0] ?? null;
}

export async function listRuleVersions(ruleKey: string, limit: number, offset: number): Promise<RuleVersion[]> {
  const result = await getDatabasePool().query<RuleVersion>(
    `SELECT ${SELECT_COLUMNS} FROM ml_rule_versions WHERE rule_key = $1 ORDER BY version DESC LIMIT $2 OFFSET $3`,
    [ruleKey, limit, offset],
  );
  return result.rows;
}

export async function getRuleVersion(ruleKey: string, version: number): Promise<RuleVersion | null> {
  const result = await getDatabasePool().query<RuleVersion>(
    `SELECT ${SELECT_COLUMNS} FROM ml_rule_versions WHERE rule_key = $1 AND version = $2`,
    [ruleKey, version],
  );
  return result.rows[0] ?? null;
}

export async function writeRule(
  ruleKey: string, actorId: string, mode: 'create' | 'replace' | 'retire',
  documents?: RuleDocuments, expectedVersion?: number,
): Promise<RuleVersion> {
  const client = await getDatabasePool().connect();
  try {
    await client.query('BEGIN');
    // 규칙 키별로 쓰기를 직렬화해 같은 버전 번호가 동시에 생성되지 않게 한다.
    await client.query('SELECT pg_advisory_xact_lock(94732, hashtext($1))', [ruleKey]);
    const current = await latest(client, ruleKey);
    if (mode === 'create') {
      if (current) throw new RuleConflictError('rule_already_exists');
      if (!documents) throw new Error('Rule documents required');
      const created = await insert(client, ruleKey, 1, documents, actorId);
      await client.query('COMMIT');
      return created;
    }
    if (!current) throw new RuleConflictError('rule_not_found');
    if (current.version !== expectedVersion) throw new RuleConflictError('rule_version_conflict');
    if (mode === 'retire' && current.ruleSpec.state === 'retired') {
      throw new RuleConflictError('rule_already_retired');
    }
    if (mode === 'replace' && !documents) throw new Error('Rule documents required');
    const nextDocuments = mode === 'retire'
      ? retireRule({ ruleSpec: current.ruleSpec, inputSpec: current.inputSpec,
          evidenceSpec: current.evidenceSpec, contentHash: current.contentHash })
      : documents!;
    if (mode === 'replace' && current.contentHash === nextDocuments.contentHash) {
      throw new RuleConflictError('rule_unchanged');
    }
    const created = await insert(client, ruleKey, current.version + 1, nextDocuments, actorId);
    await client.query('COMMIT');
    return created;
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}
