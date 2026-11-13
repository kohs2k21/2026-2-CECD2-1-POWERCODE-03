import crypto from 'node:crypto';
import type { Pool, PoolClient } from 'pg';
import {
  CONTENT_HASH_VERSION, RULE_SET_CONTRACT, RuleSetError, planRuleSetChange, projectHardRules, unscopedMethods,
  type ProtocolRule, type RelationRule, type RuleSetRequest, type RuleSetSnapshot, type SoftRule, type StoredHardRule, type StoredSnapshot,
} from './ruleSetContract.js';
import { CATALOG_VERSION } from './methodCatalog.js';

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
  revision: string; contentHash: string; contentHashVersion: number;
  snapshot: { hardRules?: StoredHardRule[]; softRules?: SoftRule[]; relationRules?: RelationRule[]; protocolRules?: ProtocolRule[] };
  changes: unknown[]; summary: Record<string, number>; note: string | null; restoredFromRevision: string | null; actorId: string; savedAt: string;
};
const revisionOf = (row: RevisionRow) => ({
  revision: Number(row.revision), contentHash: row.contentHash, contentHashVersion: row.contentHashVersion, savedAt: row.savedAt, actorId: row.actorId,
  restoredFromRevision: row.restoredFromRevision === null ? null : Number(row.restoredFromRevision),
  note: row.note, changes: row.changes, summary: row.summary,
});
const counts = ({ hardRules, softRules, relationRules, protocolRules }: RuleSetSnapshot) => ({
  hardEnabled: hardRules.filter((rule) => rule.enabled).length, hardTotal: hardRules.length,
  softEnabled: softRules.filter((rule) => rule.enabled).length, softTotal: softRules.length,
  relationEnabled: relationRules.filter((rule) => rule.enabled).length, relationTotal: relationRules.length,
  protocolEnabled: protocolRules.filter((rule) => rule.enabled).length, protocolTotal: protocolRules.length,
});
/**
 * v1·v2 행은 관계·프로토콜 항목·소프트룰 scope가 없다 → `[]`·`null`로 읽는다(rule-set.md v3.7, 행은 고치지 않음).
 * 응답은 v3 스냅샷 형식(소프트룰 scope 키 항상)으로 낸다.
 */
const softRulesOf = (row: RevisionRow) => (row.snapshot.softRules ?? []).map((rule) => ({ ...rule, scope: rule.scope ?? null }));
const currentSnapshot = (row: RevisionRow): RuleSetSnapshot => ({
  hardRules: projectHardRules(row.snapshot.hardRules ?? []), softRules: softRulesOf(row),
  relationRules: row.snapshot.relationRules ?? [], protocolRules: row.snapshot.protocolRules ?? [],
});
/** 과거 스냅샷은 저장 내용 그대로 비교한다(내장 목록에서 사라진 규칙도 남긴다 — 되돌릴 때 hard_rule_unknown). 없는 배열만 빈 값으로 읽는다. */
const storedSnapshot = (row: RevisionRow): StoredSnapshot => ({
  hardRules: row.snapshot.hardRules ?? [], softRules: softRulesOf(row),
  relationRules: row.snapshot.relationRules ?? [], protocolRules: row.snapshot.protocolRules ?? [],
});

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
 * 켜진 머신러닝 모델 수 = 현재 활성 모델 집합의 모델 수(model-version.md 활성 집합 절, 공통 검사 detection_methods_empty).
 * v04 판정이 쓰는 집합이다. v1·v2 운영의 ops_system_state 단일 구성은 세지 않는다.
 */
export async function activeModelCount(db: Queryable): Promise<number> {
  const { rows: [row] } = await db.query<{ n: number }>(`SELECT jsonb_array_length(COALESCE(active_models,'[]'::jsonb))::int AS n FROM ops_operational_state`);
  if (!row) throw new Error('operational_state_unavailable');
  return row.n;
}

/**
 * 현재 룰 세트에서 scope 없는 켜진 규칙 수(활성 집합 변경의 detection_methods_empty 검사). rule-set.md v3.6 정의 확장과 같다:
 * 거래 단위 내장 하드룰(거래 실패·등록되지 않은 인터페이스)·scope 있는 소프트룰·관계·프로토콜 규칙은 세지 않는다.
 */
export async function enabledRuleCount(db: Queryable): Promise<number> {
  return unscopedMethods(currentSnapshot(await latest(db)), 0);
}

/** 현재 리비전의 번호·내용 hash·소프트룰. 작업 입력 고정용이며 hash는 행의 값 그대로다(재계산 없음). */
export async function readCurrentSoftRules(db: Queryable) {
  const row = await latest(db);
  const { revision, contentHash, contentHashVersion } = revisionOf(row);
  return { revision, contentHash, contentHashVersion, softRules: currentSnapshot(row).softRules };
}

/** 현재 리비전의 내장 하드룰 1개(켜짐·위험도, 내장 목록 해석 그대로). 응답 코드 목록·상세의 코드별 위험도 표시용(response-codes-ui.md 3절). */
export async function readCurrentHardRule(db: Queryable, id: string) {
  const row = await latest(db);
  const rule = currentSnapshot(row).hardRules.find((item) => item.id === id);
  if (!rule) throw new Error('hard_rule_missing');
  return { revision: Number(row.revision), enabled: rule.enabled, severity: rule.severity as unknown };
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
    return { valid: true as const, changes: plan.changes, summary: plan.summary, hardDisabledAfterSave: plan.hardDisabledAfterSave, warnings: plan.warnings };
  } catch (error) {
    if (error instanceof RuleSetError) return { valid: false as const, error: { code: error.code } };
    throw error;
  }
}

/** 새 항목 id: `<prefix><소문자 hex 8자리>`(소프트룰 `soft:<field>-`, 관계 `hard:rel-`, 프로토콜 `hard:proto-`). 현재 스냅샷과 겹치면 다시 뽑는다(rule-set.md v3.4). */
const newRuleId = (prefix: string, taken: Set<string>) => {
  for (;;) {
    const id = `${prefix}${crypto.randomBytes(4).toString('hex')}`;
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
      nextRevision: revision, assignId: newRuleId,
    });
    const { rows: [saved] } = await client.query<{ appliedAt: string }>(`INSERT INTO ops_rule_set_revisions
        (revision,content_hash,content_hash_version,snapshot,changes,summary,note,restored_from_revision,saved_by_actor_id,saved_at)
      VALUES($1,$2,$9,$3::jsonb,$4::jsonb,$5::jsonb,$6,$7,$8,clock_timestamp()) RETURNING ${ISO('saved_at')} AS "appliedAt"`,
    [revision, plan.contentHash, JSON.stringify(plan.snapshot), JSON.stringify(plan.changes), JSON.stringify(plan.summary),
      request.note, request.restoreFromRevision, actorId, CONTENT_HASH_VERSION]);
    await client.query('COMMIT');
    return {
      revision, previousRevision: revision - 1, contentHash: plan.contentHash, appliedAt: saved.appliedAt, appliesFrom: 'next_decision' as const,
      changes: plan.changes, summary: plan.summary, warnings: plan.warnings,
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
