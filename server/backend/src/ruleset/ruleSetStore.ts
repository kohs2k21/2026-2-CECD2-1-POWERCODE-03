import crypto from 'node:crypto';
import type { Pool, PoolClient } from 'pg';
import {
  CONTENT_HASH_VERSION, RULE_SET_CONTRACT, RuleSetError, planRuleSetChange, projectHardRules,
  type RuleSetRequest, type RuleSetSnapshot, type SoftRule, type StoredHardRule, type StoredSnapshot,
} from './ruleSetContract.js';
import { CATALOG_VERSION } from './softRuleCatalog.js';

/**
 * 룰 세트 저장소(rule-set.md 저장소 절, 표 ops_rule_set_revisions). 현재 상태 = 최대 revision 행, append-only.
 * 저장은 트랜잭션 1개: 공통 잠금 → 현재 리비전 비교 → 검증 → revision+1 INSERT. 어느 단계에서 실패해도 운영 룰은 바뀌지 않는다.
 */
type Queryable = Pick<Pool, 'query'>;

/** 룰 세트·활성 집합·정책 저장이 함께 쓰는 공통 advisory lock 키(detection-decision.md 7.4 교차 저장 직렬화). */
export const RULE_SET_LOCK = [81739, 0] as const;

const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
// 별칭이 문자열이므로 정렬은 반드시 r.revision으로 한다(ORDER BY revision은 '9' > '10'이 된다).
const COLUMNS = `r.revision::text AS revision,content_hash AS "contentHash",content_hash_version AS "contentHashVersion",snapshot,changes,summary,note,
  restored_from_revision::text AS "restoredFromRevision",saved_by_actor_id AS "actorId",${ISO('saved_at')} AS "savedAt"`;

type RevisionRow = {
  revision: string; contentHash: string; contentHashVersion: number; snapshot: { hardRules?: StoredHardRule[]; softRules?: SoftRule[] };
  changes: unknown[]; summary: Record<string, number>; note: string | null; restoredFromRevision: string | null; actorId: string; savedAt: string;
};
const revisionOf = (row: RevisionRow) => ({
  revision: Number(row.revision), contentHash: row.contentHash, contentHashVersion: row.contentHashVersion, savedAt: row.savedAt, actorId: row.actorId,
  restoredFromRevision: row.restoredFromRevision === null ? null : Number(row.restoredFromRevision),
  note: row.note, changes: row.changes, summary: row.summary,
});
const counts = ({ hardRules, softRules }: RuleSetSnapshot) => ({
  hardEnabled: hardRules.filter((rule) => rule.enabled).length, hardTotal: hardRules.length,
  softEnabled: softRules.filter((rule) => rule.enabled).length, softTotal: softRules.length,
});
const currentSnapshot = (row: RevisionRow): RuleSetSnapshot => ({ hardRules: projectHardRules(row.snapshot.hardRules ?? []), softRules: row.snapshot.softRules ?? [] });
/** 과거 스냅샷은 저장 내용 그대로 비교한다(내장 목록에서 사라진 규칙도 남긴다 — 되돌릴 때 hard_rule_unknown). */
const storedSnapshot = (row: RevisionRow): StoredSnapshot => ({ hardRules: row.snapshot.hardRules ?? [], softRules: row.snapshot.softRules ?? [] });

async function latest(db: Queryable): Promise<RevisionRow> {
  const { rows: [row] } = await db.query<RevisionRow>(`SELECT ${COLUMNS} FROM ops_rule_set_revisions r ORDER BY r.revision DESC LIMIT 1`);
  // r0은 마이그레이션이 넣는다. 행이 없으면 빈 룰로 대신하지 않고 저장소 오류로 둔다.
  if (!row) throw new Error('rule_set_revision_row_missing');
  return row;
}
async function atRevision(db: Queryable, revision: number): Promise<RevisionRow | null> {
  const { rows: [row] } = await db.query<RevisionRow>(`SELECT ${COLUMNS} FROM ops_rule_set_revisions r WHERE r.revision=$1`, [revision]);
  return row ?? null;
}
/**
 * 켜진 머신러닝 모델 수. 활성 모델 집합 저장소가 아직 없으므로 v1·v2 투영으로 센다(model-version.md):
 * ops_system_state.active_config_version_id가 가리키는 구성의 ml_config_models 행 수(활성 구성이 없으면 0).
 */
export async function activeModelCount(db: Queryable): Promise<number> {
  const { rows: [row] } = await db.query<{ n: number }>(`SELECT count(*)::int AS n FROM ops_system_state s
    JOIN ml_config_models m ON m.config_version_id=s.active_config_version_id WHERE s.id=1`);
  return row.n;
}

/** 현재 리비전의 번호·내용 hash·소프트룰. 작업 입력 고정용이며 hash는 행의 값 그대로다(재계산 없음). */
export async function readCurrentSoftRules(db: Queryable) {
  const row = await latest(db);
  const { revision, contentHash, contentHashVersion } = revisionOf(row);
  return { revision, contentHash, contentHashVersion, softRules: currentSnapshot(row).softRules };
}

export type ActorRef = { actorId: string; displayName: string };
export type ActorResolver = (actorId: string) => Promise<ActorRef>;

export async function readRuleSet(db: Queryable, actor: ActorResolver) {
  const row = await latest(db);
  const snapshot = currentSnapshot(row);
  const { revision, contentHash, contentHashVersion, savedAt, restoredFromRevision, actorId } = revisionOf(row);
  return {
    contractVersion: RULE_SET_CONTRACT, revision, contentHash, contentHashVersion, savedAt,
    savedBy: await actor(actorId), restoredFromRevision, catalogVersion: CATALOG_VERSION, counts: counts(snapshot), ...snapshot,
  };
}

async function checkExpected(request: RuleSetRequest, row: RevisionRow, actor: ActorResolver) {
  if (request.expectedRevision === Number(row.revision)) return;
  throw new RuleSetError('rule_set_revision_conflict', 409, { currentRevision: Number(row.revision), savedAt: row.savedAt, savedBy: await actor(row.actorId) });
}
async function restoreSource(db: Queryable, request: RuleSetRequest) {
  if (request.restoreFromRevision === null) return null;
  const row = await atRevision(db, request.restoreFromRevision);
  return row ? storedSnapshot(row) : null;
}

/** 저장하지 않고 변경 요약·검증 결과만 낸다. expectedRevision 불일치만 409, 그 밖의 검증 실패는 200 valid:false. */
export async function previewRuleSet(db: Queryable, request: RuleSetRequest, actor: ActorResolver) {
  const row = await latest(db);
  await checkExpected(request, row, actor);
  try {
    const plan = planRuleSetChange({
      request, current: currentSnapshot(row), restore: await restoreSource(db, request), activeModelCount: await activeModelCount(db),
      nextRevision: Number(row.revision) + 1, assignId: () => null,
    });
    return { valid: true as const, changes: plan.changes, summary: plan.summary, hardDisabledAfterSave: plan.hardDisabledAfterSave };
  } catch (error) {
    if (error instanceof RuleSetError) return { valid: false as const, error: { code: error.code } };
    throw error;
  }
}

const newSoftRuleId = (field: string, taken: Set<string>) => {
  for (;;) {
    const id = `soft:${field}-${crypto.randomBytes(4).toString('hex')}`;
    if (!taken.has(id)) return id;
  }
};

export async function saveRuleSet(pool: Pool, request: RuleSetRequest, actorId: string, actor: ActorResolver) {
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('SELECT pg_advisory_xact_lock($1,$2)', [...RULE_SET_LOCK]);
    const row = await latest(client);
    await checkExpected(request, row, actor);
    const revision = Number(row.revision) + 1;
    const plan = planRuleSetChange({
      request, current: currentSnapshot(row), restore: await restoreSource(client, request), activeModelCount: await activeModelCount(client),
      nextRevision: revision, assignId: newSoftRuleId,
    });
    const { rows: [saved] } = await client.query<{ appliedAt: string }>(`INSERT INTO ops_rule_set_revisions
        (revision,content_hash,content_hash_version,snapshot,changes,summary,note,restored_from_revision,saved_by_actor_id,saved_at)
      VALUES($1,$2,$9,$3::jsonb,$4::jsonb,$5::jsonb,$6,$7,$8,clock_timestamp()) RETURNING ${ISO('saved_at')} AS "appliedAt"`,
    [revision, plan.contentHash, JSON.stringify(plan.snapshot), JSON.stringify(plan.changes), JSON.stringify(plan.summary),
      request.note, request.restoreFromRevision, actorId, CONTENT_HASH_VERSION]);
    await client.query('COMMIT');
    return {
      revision, previousRevision: revision - 1, contentHash: plan.contentHash, appliedAt: saved.appliedAt, appliesFrom: 'next_decision' as const,
      changes: plan.changes, summary: plan.summary,
    };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

const HISTORY_LIMIT_DEFAULT = 50;
const HISTORY_LIMIT_MAX = 100;
export function parseHistoryQuery(query: Record<string, unknown>) {
  if (Object.keys(query).some((key) => !['limit', 'cursor'].includes(key))) throw new RuleSetError('unknown_rule_set_filter');
  const { limit, cursor } = query;
  if (limit !== undefined && (typeof limit !== 'string' || !/^[1-9]\d{0,2}$/.test(limit) || Number(limit) > HISTORY_LIMIT_MAX)) throw new RuleSetError('invalid_limit');
  let before: number | null = null;
  if (cursor !== undefined) {
    try {
      if (typeof cursor !== 'string' || !/^[A-Za-z0-9_-]{1,256}$/.test(cursor)) throw new Error();
      const parsed = JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8')) as { revision?: unknown };
      if (Object.keys(parsed).join() !== 'revision' || !Number.isSafeInteger(parsed.revision) || (parsed.revision as number) < 0) throw new Error();
      before = parsed.revision as number;
    } catch { throw new RuleSetError('invalid_cursor'); }
  }
  return { limit: limit === undefined ? HISTORY_LIMIT_DEFAULT : Number(limit), before };
}

const historyItem = async (row: RevisionRow, actor: ActorResolver) => {
  const { revision, savedAt, actorId, note, restoredFromRevision, contentHash, contentHashVersion, summary, changes } = revisionOf(row);
  return { revision, savedAt, savedBy: await actor(actorId), note, restoredFromRevision, contentHash, contentHashVersion, summary, changes };
};

export async function readHistory(db: Queryable, filter: { limit: number; before: number | null }, actor: ActorResolver) {
  const { rows } = await db.query<RevisionRow>(`SELECT ${COLUMNS} FROM ops_rule_set_revisions r
    WHERE $1::bigint IS NULL OR r.revision<$1 ORDER BY r.revision DESC LIMIT $2`, [filter.before, filter.limit + 1]);
  const page = rows.slice(0, filter.limit);
  const hasMore = rows.length > filter.limit;
  const last = page.at(-1);
  return {
    items: await Promise.all(page.map((row) => historyItem(row, actor))),
    pagination: {
      limit: filter.limit, hasMore,
      nextCursor: hasMore && last ? Buffer.from(JSON.stringify({ revision: Number(last.revision) })).toString('base64url') : null,
    },
  };
}

export async function readHistoryItem(db: Queryable, revision: number, actor: ActorResolver) {
  const row = await atRevision(db, revision);
  if (!row) throw new RuleSetError('rule_set_revision_not_found', 404);
  return { ...(await historyItem(row, actor)), snapshot: storedSnapshot(row) };
}
